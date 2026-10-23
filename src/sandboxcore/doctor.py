"""sandboxcore-doctor: is this system set up to run untrusted code the way sandboxcore expects?

Run it as the user that runs the sandboxes: the app user on a host, or `podman` inside the base image.
Each check prints ok, warn or FAIL with a fix; any FAIL exits 1. `--self-test IMAGE` also proves the
limits are enforced by running two containers that must be killed (the same checks PodmanRunner runs
at startup). Standard library only: the sandboxcore-host package ships this file as it is.

What sandboxcore needs, and why:
- rootless podman: a container breakout lands in an unprivileged user, never root;
- cgroup v2 with cpu, memory and pids delegated to that user: otherwise --memory/--cpus/--pids-limit
  are silently not enforced (rootless limits go through the systemd user session);
- a subordinate uid/gid range: the sandbox user (uid 1001 in the image) maps to a sub-uid that owns
  nothing on the host;
- lingering: the user's systemd manager (and so the delegation) exists without a login;
- unprivileged user namespaces allowed by the kernel.
"""

import argparse
import getpass
import json
import os
import pwd
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass

REQUIRED_CONTROLLERS = ("cpu", "memory", "pids")
SUBID_MIN = 65536
SUBID_SANDBOX_MIN = 1002  # the image's sandbox uid 1001 must fall inside the range
SELFTEST_MEMORY_CODE = "bytearray(200*1024*1024)"
SELFTEST_TIMEOUT_CODE = "import time; time.sleep(30)"
KILLED = (137, 143)
TIMEOUT_EXIT = 255
NOT_STARTED = (125, 126, 127)  # podman could not create or start the container
# the sandbox flags PodmanRunner uses that matter for a container to start at all, nested or not
# (--uts=host: the host seccomp profile denies sethostname one level down)
SELFTEST_FLAGS = (
    "--rm", "--network=none", "--uts=host", "--user=1001", "--read-only", "--cap-drop=ALL",
    "--security-opt=no-new-privileges", "--pids-limit=64", "--entrypoint", "python3",
)


@dataclass
class Check:
    name: str
    status: str  # ok, warn or FAIL
    detail: str
    fix: str = ""


def _run(*argv, timeout=60) -> tuple[int, str, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{argv[0]}: timed out"
    return p.returncode, p.stdout, p.stderr


# --- pure checks (tested without a system) -------------------------------------------------


def check_user(uid: int) -> Check:
    if uid == 0:
        return Check("rootless", "FAIL", "running as root", "run sandboxes as an unprivileged user (sandboxcore-adduser)")
    return Check("rootless", "ok", f"uid {uid}")


def check_podman_info(info: dict) -> list[Check]:
    host = info.get("host", {})
    checks = []
    rootless = host.get("security", {}).get("rootless")
    checks.append(
        Check("podman rootless", "ok", "rootless") if rootless
        else Check("podman rootless", "FAIL", "podman runs rootful", "run podman as the unprivileged user, not root")
    )
    version = host.get("cgroupVersion")
    checks.append(
        Check("cgroup v2", "ok", "v2") if version == "v2"
        else Check("cgroup v2", "FAIL", f"cgroup {version}", "boot with the unified hierarchy (systemd.unified_cgroup_hierarchy=1)")
    )
    manager = host.get("cgroupManager")
    checks.append(
        Check("cgroup manager", "ok", "systemd") if manager == "systemd"
        else Check(
            "cgroup manager", "FAIL", f"{manager}: rootless limits need the systemd manager",
            "give the user a systemd session: loginctl enable-linger <user>, and log in through it",
        )
    )
    controllers = set(host.get("cgroupControllers") or [])
    missing = [c for c in REQUIRED_CONTROLLERS if c not in controllers]
    checks.append(
        Check("delegated controllers", "ok", " ".join(sorted(controllers)))
        if not missing
        else Check(
            "delegated controllers", "FAIL", f"missing {' '.join(missing)}: limits would not be enforced",
            "install sandboxcore-host (Delegate=cpu cpuset io memory pids for user@.service), then "
            "systemctl restart user@<uid>.service",
        )
    )
    driver = info.get("store", {}).get("graphDriverName")
    checks.append(
        Check("storage driver", "ok", driver) if driver == "overlay"
        else Check("storage driver", "warn", f"{driver}: slow and disk hungry", "use overlay (fuse-overlayfs when nested)")
    )
    return checks


def check_subids(name: str, user: str, uid: int, text: str, in_container: bool = False) -> Check:
    count = 0
    for line in text.splitlines():
        parts = line.strip().split(":")
        if len(parts) == 3 and parts[0] in (user, str(uid)) and parts[2].isdigit():
            count += int(parts[2])
    fix = f"usermod --add-sub{name[3:]}s <start>-<end> {user} (sandboxcore-adduser does this)"
    if count < SUBID_SANDBOX_MIN:
        return Check(f"/etc/{name}", "FAIL", f"{count} ids for {user}: the sandbox uid 1001 cannot be mapped", fix)
    if count < SUBID_MIN and not in_container:  # nested, the range must fit in the outer 65536: smaller by design
        return Check(f"/etc/{name}", "warn", f"{count} ids for {user} (enough for uid 1001, below {SUBID_MIN})", fix)
    return Check(f"/etc/{name}", "ok", f"{count} ids for {user}")


def check_userns(clone: str | None, max_namespaces: str | None) -> Check:
    if clone is not None and clone.strip() == "0":
        return Check("user namespaces", "FAIL", "kernel.unprivileged_userns_clone = 0", "sysctl kernel.unprivileged_userns_clone=1")
    if max_namespaces is not None and max_namespaces.strip() == "0":
        return Check("user namespaces", "FAIL", "user.max_user_namespaces = 0", "sysctl user.max_user_namespaces=28633")
    return Check("user namespaces", "ok", "unprivileged user namespaces allowed")


def check_selftest(memory_rc: int, timeout_rc: int, stderr: str = "") -> list[Check]:
    if memory_rc in NOT_STARTED or timeout_rc in NOT_STARTED:
        return [
            Check("self-test", "FAIL", f"the test containers did not start (exit {memory_rc}/{timeout_rc}): "
                  f"{stderr.strip()[-300:]}", "pull the image; check the error above")
        ]
    return [
        Check("memory limit enforced", "ok", f"exit {memory_rc}") if memory_rc in KILLED
        else Check("memory limit enforced", "FAIL", f"a 200 MB allocation under --memory=64m exited {memory_rc}",
                   "the memory controller is not delegated (see delegated controllers)"),
        Check("timeout enforced", "ok", f"exit {timeout_rc}") if timeout_rc in (*KILLED, TIMEOUT_EXIT)
        else Check("timeout enforced", "FAIL", f"sleep 30 under --timeout=2 exited {timeout_rc}", "upgrade podman (4.0+)"),
    ]


# --- the system ------------------------------------------------------------------------------


def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def diagnose(image: str | None = None) -> list[Check]:
    uid = os.getuid()
    user = pwd.getpwuid(uid).pw_name if uid != 0 else getpass.getuser()
    checks = [check_user(uid)]

    if shutil.which("podman") is None:
        checks.append(Check("podman", "FAIL", "not installed", "apt install podman (sandboxcore-host depends on it)"))
        return checks
    rc, out, err = _run("podman", "info", "--format", "json")
    if rc != 0:
        checks.append(Check("podman", "FAIL", (err or out).strip()[:300], "fix podman info first"))
        return checks
    checks.append(Check("podman", "ok", json.loads(out).get("version", {}).get("Version", "?")))
    checks += check_podman_info(json.loads(out))

    for name in ("subuid", "subgid"):
        text = _read(f"/etc/{name}")
        checks.append(
            check_subids(name, user, uid, text, os.path.exists("/run/.containerenv")) if text is not None
            else Check(f"/etc/{name}", "FAIL", "missing", "apt install uidmap; sandboxcore-adduser <user>")
        )
    for helper in ("newuidmap", "newgidmap"):
        if shutil.which(helper) is None:
            checks.append(Check(helper, "FAIL", "not installed", "apt install uidmap"))

    checks.append(
        check_userns(_read("/proc/sys/kernel/unprivileged_userns_clone"), _read("/proc/sys/user/max_user_namespaces"))
    )
    linger = os.path.exists(f"/var/lib/systemd/linger/{user}")
    checks.append(
        Check("linger", "ok", "enabled") if linger
        else Check("linger", "warn", "not enabled: the user's systemd manager stops at logout",
                   f"loginctl enable-linger {user}")
    )
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    checks.append(
        Check("XDG_RUNTIME_DIR", "ok", runtime_dir) if runtime_dir and os.path.isdir(runtime_dir)
        else Check("XDG_RUNTIME_DIR", "warn", "unset: podman may fall back to cgroupfs",
                   f"export XDG_RUNTIME_DIR=/run/user/{uid} (a login or systemd --user session sets it)")
    )
    if image:
        mem, _, err = _run(
            "podman", "run", *SELFTEST_FLAGS, "--memory=64m", "--memory-swap=64m", image, "-c", SELFTEST_MEMORY_CODE
        )
        tmo, _, err2 = _run("podman", "run", *SELFTEST_FLAGS, "--timeout=2", image, "-c", SELFTEST_TIMEOUT_CODE, timeout=90)
        checks += check_selftest(mem, tmo, err or err2)
    return checks


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sandboxcore-doctor", description=__doc__.split("\n")[0])
    parser.add_argument("--self-test", metavar="IMAGE", help="also run two containers that must be killed")
    parser.add_argument("--json", action="store_true", help="print the checks as JSON")
    args = parser.parse_args(argv)
    checks = diagnose(args.self_test)
    if args.json:
        print(json.dumps([asdict(c) for c in checks], indent=2))
    else:
        for c in checks:
            print(f"{c.status:4}  {c.name}: {c.detail}" + (f"\n      fix: {c.fix}" if c.fix and c.status != "ok" else ""))
    return 1 if any(c.status == "FAIL" for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
