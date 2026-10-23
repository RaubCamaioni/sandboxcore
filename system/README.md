# System setup

sandboxcore is only as safe as the system under it. Its runner refuses to start until a self-test
shows that memory and time limits are enforced, but the system has to make that possible. This
directory is that system: the host package, the base image for app containers, and a doctor that
checks both.

## What the system must provide

| Requirement | Why | Provided by |
|---|---|---|
| Rootless podman, run by an unprivileged user | A container breakout lands in that user, never root | `sandboxcore-host` (podman, uidmap), `sandboxcore-adduser` |
| A sub-uid/gid range for that user | The sandbox uid 1001 maps to a sub-uid that owns nothing on the host | `sandboxcore-adduser` (65536 ids, never overlapping another user's) |
| cgroup v2 with `cpu cpuset io memory pids` delegated to the user's systemd manager | Rootless podman enforces `--cpus/--memory/--pids-limit` through it; undelegated, `--cpus` fails and memory/pids limits are silently off | `sandboxcore-host` (`user@.service.d/sandboxcore-delegate.conf`) |
| Lingering | The user's manager, and with it the delegation, exists without a login | `sandboxcore-adduser` |
| Unprivileged user namespaces | Rootless containers need them | the kernel default on Debian 11+ and Ubuntu 24.04 (podman's AppArmor profile allows it) |
| Inside the app container: systemd, a delegated user manager, fuse-overlayfs, `default_sysctls = []` | Nested podman: see [the runner design](../docs/history/2026-10-19-podman-sandbox-runner-design.md) | the base image |
| The app container run with `--device /dev/fuse --security-opt unmask=ALL` | fuse-overlayfs for the inner store; a fresh `/proc` per sandbox | `debian/root/usr/share/sandboxcore-host/app.container` (example unit) |

## Host: `sandboxcore-host` (Debian/Ubuntu)

```sh
sh system/debian/build.sh                      # -> dist/sandboxcore-host_<version>_all.deb
sudo apt install ./dist/sandboxcore-host_*_all.deb
sudo sandboxcore-adduser pytools               # idempotent
sudo -u pytools XDG_RUNTIME_DIR=/run/user/$(id -u pytools) sandboxcore-doctor
```

A user manager that was already running gets the delegation after `systemctl restart user@<uid>.service`,
which stops that user's services.

## App containers: the base image

`image/Containerfile.base` (built from the repository root) is what both apps' `Containerfile.prod`
used to repeat. An app image adds its own venv and a user unit for `podman`; the header of the
Containerfile shows how. Check a running app container:

```sh
podman exec --user podman -e XDG_RUNTIME_DIR=/run/user/1000 <app> sandboxcore-doctor --self-test <sandbox image>
```

## The doctor

`sandboxcore-doctor` (from the Python package, the host package, or the base image) checks every row
of the table above and prints a fix for each failure. With `--self-test IMAGE`, it runs two containers
that must be killed, the memory and timeout checks `PodmanRunner` runs at startup. It exits 1 on any
failure, so it can gate a deploy.

Verified 2026-10-23: the base image boots under rootless podman on WSL (Ubuntu 26.04, podman 5.8), and
its doctor passes every check including the self-test one level down. The `.deb` installs on
`ubuntu:24.04` with its dependencies, and `sandboxcore-adduser` gives non-overlapping ranges and is
idempotent.
