# Podman-in-podman sandbox runner

Date: 2026-10-19
Status: approved design, awaiting implementation plan

## Goal

Replace `isolate` as the execution engine for uploaded Python tools with rootless
podman containers launched by a podman that runs *inside* the application
container. Each tool run gets its own container with no network, 0.5 CPU, 1 GB of
memory, a process cap, and a hard 10-second stop. The tool contract (`args.json`
in, `result.json` and output files out) and the web UI do not change.

Success: pywebtools.com runs every existing public tool through the new engine
with no user-visible regression, enforced limits are verified on the server, and
adding another language later is "add a runtime image".

## Why nesting, and what it costs

The owner chose podman-in-podman over a host podman socket for a self-contained
app image and a path to user-supplied runtimes. Probing on the production server
(Ubuntu 24.04, cgroup v2, 2 vCPU, 3.9 GB, rootless podman 4.9.3 as user `pytools`)
established what nesting needs for *enforced* limits:

| Requirement | Reason (observed) |
|---|---|
| systemd as PID 1 in the app container, `podman` user with a systemd user session | Rootless podman applies cgroup limits only through the systemd cgroup manager. With `cgroupfs` the inner podman created no cgroups at all (`memory.max` stayed `max`). |
| `Delegate=cpu cpuset io memory pids` drop-in for `user@.service` inside the image | Without it only `memory pids` reach the user session; `--cpus` fails with "controller cpu is not available". |
| `newuidmap`/`newgidmap` keep their file capability (`cap_setuid=ep`); **no setuid bit** | File capabilities work in this nesting. A setuid-root helper cannot open another user's `uid_map` here (no `CAP_SYS_PTRACE` in the bounding set). |
| Inner `containers.conf`: `default_sysctls = []` | `/proc/sys` is read-only in the outer container; the default `ping_group_range` sysctl fails. |
| Inner storage: overlay with `mount_program = fuse-overlayfs` | Native overlay is unavailable to the inner podman. |
| Outer run flag `--security-opt unmask=ALL` | The kernel refuses a fresh `/proc` mount while podman's default masked paths cover parts of the outer `/proc`. Inherent to nesting with private namespaces. |
| Sandboxes run with `--uts=host` | The host seccomp profile denies `sethostname` without `CAP_SYS_ADMIN`. Sharing the app container's UTS namespace avoids the call; a sandbox cannot change the hostname (verified). The outer container keeps the default seccomp profile. |
| Sandboxes run as the image's unprivileged user (`--user=1001`) with the run directory mounted `:U` | Container uid 1001 maps to a sub-UID of the inner `podman` user (outer UID 3000), so a sandbox shares no UID with the app: a breakout cannot read the app's secrets or database. `:U` re-owns the run directory to the sandbox before the run; the app re-owns it afterwards with `podman unshare chown -R 0:0`. **Not `--userns=auto`:** it was probed successfully with a 50 MB image, but with the real 2.66 GB sandbox image the inner podman (fuse-overlayfs, no idmapped mounts) must create a re-owned copy of every layer per UID mapping; the self-test failed with `creating /etc/mtab symlink: cannot allocate memory` and production runs would pay minutes and ~2.7 GB of disk per concurrent sandbox. Sandboxes therefore share one UID among themselves; they still cannot reach each other's files, because each sees only its own run directory in its own mount and PID namespaces. |

Measured with that recipe: `--memory=64m` OOM-killed a 200 MB allocation (exit 137),
`--cpus=0.5` gave 1.02 CPU-seconds in 2 s, `--pids-limit=16` refused the 17th fork,
`--network=none` left the network unreachable, `--read-only --cap-drop=ALL` gave
`CapEff: 0`, warm start 0.27 s (0.40 s with `--userns=auto`), `--timeout=3` killed
`sleep 30` at 3.1 s.

The accepted concession is `unmask=ALL` on the app container: its unprivileged
"root" can then read a few host statistics (`/proc/timer_list`, `/proc/sched_debug`,
`/proc/interrupts`) it otherwise could not. It gains no write access or privilege.
Sandboxes keep the normal masks.

Not a requirement: the crun version. crun 1.28 changed nothing; the
`quay.io/podman/stable` image only ran without unmasking because its bundled
`containers.conf` uses host namespaces and disables cgroups, which is unsuitable
for sandboxing.

## Components

### App image `raubcamaioni/pywebtools` (`containers/Containerfile.prod`)

- Base `registry.access.redhat.com/ubi9/ubi-init` (systemd PID 1).
- Packages: `podman`, `fuse-overlayfs`, `shadow-utils`, `python3.12`.
- User `podman` (uid 1000, home `/home/podman`), `/etc/subuid` and `/etc/subgid`
  `podman:2000:60000` (must fit in the 65,536 UIDs the outer container receives),
  `/var/lib/systemd/linger/podman`.
- `/etc/systemd/system/user@.service.d/container.conf`:
  `Delegate=cpu cpuset io memory pids`, `PAMName=` (no logind in the image),
  `Environment=XDG_RUNTIME_DIR=/run/user/%i`; `user@1000.service` wanted by
  `multi-user.target`.
- `/etc/containers/containers.conf.d/nested.conf`: `[containers] default_sysctls = []`.
- `/home/podman/.config/containers/storage.conf`: overlay + fuse-overlayfs.
- Server package installed into `/venvs/server`; the systemd **user** unit
  `pywebtools.service` (enabled for the `podman` user) runs
  `/venvs/server/bin/pywebtool` with `EnvironmentFile=/run/secrets/pywebtools.env`,
  `Restart=always`.
- `STOPSIGNAL SIGRTMIN+3`, `CMD ["/sbin/init"]`.

### Sandbox image `raubcamaioni/pywebtools-sandbox` (`containers/Containerfile.sandbox`)

`python:3.12-slim-bookworm` + `libgl1 libxrender1 libglib2.0-0 libgomp1`, the
`sandbox` package installed into `/venvs/sandbox`, entrypoint
`/venvs/sandbox/bin/sandbox`. No server code. This is runtime image #1.

### Host (`pytools` user, done by hand once)

`~/.config/systemd/user/pywebtools.service` runs:

```
podman run --rm --name pywebtools \
  --device /dev/fuse --security-opt unmask=ALL \
  --memory=3g --pids-limit=4096 \
  -v %h/pywebtools/.secrets:/run/secrets/pywebtools.env:ro \
  -v %h/pywebtools/data:/data \
  -v pywebtools-inner-store:/home/podman/.local/share/containers \
  -p 172.18.0.1:8080:8080 \
  docker.io/raubcamaioni/pywebtools:latest
```

No `--userns=keep-id` (systemd must be container-root). `.secrets` and `data/` are
owned by the host UID that container uid 1000 maps to, set with
`podman unshare chown 1000:1000 …`. `.secrets` gains the `SANDBOX_*` variables below
and drops `SANDBOX`. The 5-minute update timer is unchanged: it pulls the app image
and restarts the service when it changes.

## Runtime behaviour

### Configuration (environment, all with defaults)

| Variable | Default |
|---|---|
| `SANDBOX_IMAGE` | `docker.io/raubcamaioni/pywebtools-sandbox:latest` |
| `SANDBOX_MAX_CONCURRENT` | `2` |
| `SANDBOX_QUEUE_SECONDS` | `10` |
| `SANDBOX_TIMEOUT_SECONDS` | `10` |
| `SANDBOX_MEMORY` | `1g` |
| `SANDBOX_CPUS` | `0.5` |
| `SANDBOX_PIDS` | `64` |
| `SANDBOX_RUN_DIR` | `/tmp/pywebtools-runs` |

`app/__init__.py`'s `_ENVIRONMENT` gains these fields with defaults; only the
existing secrets remain mandatory. `SANDBOX` (path to the old venv) is removed.

Capacity: 2 sandboxes × 1 GB inside the app container's 3 GB leaves about 1 GB for
the app itself; 2 × 0.5 CPU leaves one vCPU for the app, nginx and the OS.

### One run

1. `POST /tool/{id}` writes `<tool>.py` and `args.json` into a fresh directory under
   `SANDBOX_RUN_DIR` (replacing today's temp dir; same delayed-delete task).
2. `PodmanRunner.run(tool, dir)` (new module `app/utility/sandbox.py`, replacing
   `IsolationWorkers`) acquires one of `SANDBOX_MAX_CONCURRENT` slots, waiting at
   most `SANDBOX_QUEUE_SECONDS`; on expiry the route returns
   `503 Server busy, try again`.
3. It launches, via `asyncio.create_subprocess_exec`:

   ```
   podman run --name run-<id> \
     --network=none --uts=host --user=1001 \
     --cpus=0.5 --memory=1g --memory-swap=1g --pids-limit=64 \
     --read-only --tmpfs /tmp:rw,size=64m \
     --cap-drop=ALL --security-opt=no-new-privileges \
     --timeout=10 \
     -v <dir>:<dir>:U \
     <SANDBOX_IMAGE> --file <dir>/<tool>.py --workdir <dir>
   ```

   The run directory keeps its own absolute path inside the sandbox (as it did under
   `isolate`): `args.json` carries absolute upload paths and `result.json` carries
   absolute output paths that `/download/{path}` serves from the app's filesystem.

   The sandbox process runs as the image's `app` user (container uid 1001, outer
   UID 3000), with no capabilities. It shares no UID with the app; `:U` gives it
   ownership of its run directory.
4. The app waits with a 12-second safety net; if podman's own `--timeout` did not
   end the run, it runs `podman kill run-<id>` and logs a warning. Output captured
   from `podman run` is a 64 KB ring buffer per stream, and the container log is
   capped (`--log-opt max-size=1mb`), so a tool that floods stdout cannot grow the
   app's memory or the inner image store.
5. It reads `podman inspect --format '{{.State.ExitCode}} {{.State.OOMKilled}}'`
   and `podman logs --tail 20`, then `podman rm -f run-<id>`, then
   `podman unshare chown -R 0:0 <dir>` so the app owns the results again (the
   `podman` user is root over its sub-UID range inside `podman unshare`). Every
   post-run podman call has a 15 s timeout and never raises, so a stalled podman
   cannot pin a slot. Only then is the outcome classified: if the run directory
   exceeds `SANDBOX_MAX_OUTPUT_MB` (default 64) its contents are deleted and the
   run fails with `Tool output exceeded the 64 MB limit`; `result.json` is accepted
   only as a regular, non-symlinked file of at most 16 MB (anything else counts as
   a crash). The existing delayed-delete task removes the directory afterwards.
6. Outcome mapping:

   | State | Response |
   |---|---|
   | `result.json` present | rendered as today (tool exceptions are already inside it) |
   | OOMKilled | `Tool exceeded the 1 GB memory limit` |
   | killed by timeout (exit 137/143 after ≥ timeout) | `Tool exceeded the 10 second limit` |
   | anything else without `result.json` | `Tool failed to run` + sanitized stderr tail; full stderr logged |

   HTMX requests get the message rendered in the result area; plain requests get
   JSON `{"detail": ...}` with status 200 for tool outcomes and 503 for capacity or
   sandbox-unavailable conditions.

### Startup and health

On app start, `PodmanRunner.start()` runs as a background task so pages are served
immediately; `/healthz` reports `starting` until it finishes, and any exception
inside it marks the sandbox unavailable (with the retry loop) instead of crashing
the app. It:

1. Waits until `podman info` succeeds (inner podman ready; up to 60 s, 15 s per call).
2. Removes leftover `run-*` containers from a previous crash.
3. Pulls `SANDBOX_IMAGE`; if the pull fails but a local copy exists, continues with
   it; otherwise marks the sandbox unavailable and retries every 60 s.
4. **Self-test, fail closed:** runs a throwaway sandbox with `--memory=64m` that
   allocates 200 MB and one with `--timeout=2` that sleeps. If the first is not
   OOM-killed or the second is not killed in time, the sandbox is marked
   unavailable and tool runs return `503 Sandbox unavailable` until a restart
   fixes it. This turns "limits silently ignored" into a visible failure.
5. Re-pulls `SANDBOX_IMAGE` every 10 minutes so sandbox-only releases are picked
   up without an app restart (new runs use the new local `latest`).

`GET /healthz` returns `200 {"sandbox": "ready"}` or `503 {"sandbox": "unavailable",
"reason": ...}`.

## CI

`.github/workflows/main.yml` gets two build jobs, `app` (Containerfile.prod) and
`sandbox` (Containerfile.sandbox), both pushing `latest`, `main` and `sha-<short>`
tags on pushes to `main`, build-only on pull requests. The Docker Hub repository
`raubcamaioni/pywebtools-sandbox` is created by the first push.

## Testing

- **Unit tests** (`server/tests/`, pytest, written before the code): podman
  command built from configuration; outcome classification from inspect output,
  exit codes and `result.json` presence; slot acquisition, queue timeout and
  `503` with a fake `podman`; environment defaults.
- **Integration tests** (pytest `-m integration`, run on the server inside the app
  container, skipped elsewhere): `lower_case_text` happy path; 200 MB allocation →
  memory message; infinite loop → timeout message within 12 s; 40 forks → run fails
  with the pids limit; network connect → fails; file output downloadable; two
  concurrent runs succeed while a third waits; the sandbox process reports uid 1001
  and cannot read the app's secrets file; the run directory is owned by the app
  user again after the run.
- **Deploy smoke:** `POST https://pywebtools.com/tool/8` (`input=hi`,
  `output=hexadecimal`) returns `"0x68 0x69"`, the baseline recorded before the
  migration; `/healthz` reports ready.

## Rollout

1. Land the code and both Containerfiles; CI publishes both images.
2. Update the host unit, re-own `.secrets` and `data/`, add the `SANDBOX_*`
   variables, restart the service. The old transitional image stays on disk; the
   previous unit file is kept as `pywebtools.service.pre-nested` for rollback.
3. Watch startup logs for the self-test, then run the deploy smoke.
4. Expected tool downtime: the time to pull the sandbox image inside the container
   on first start (a few minutes); pages stay up throughout.

## Out of scope (step 3)

Other languages and their runtime images, user-supplied images, build pipelines
for compiled tools, a language-neutral argument manifest, per-tool resource or
network overrides.

## Risks

- The nested recipe depends on podman, crun, systemd and kernel behaviour that can
  change under updates. Mitigation: the fail-closed startup self-test and the
  integration suite after each deploy.
- `/tmp` in a systemd container is a tmpfs, so run directories (uploads *and* tool
  output) count against the app's memory. The host unit mounts a dedicated tmpfs at
  `/tmp/pywebtools-runs` capped at 512 MB (`--tmpfs /tmp/pywebtools-runs:rw,size=512m,mode=1777`),
  a run's output above 64 MB is discarded immediately, and directories are deleted
  10 minutes after the run.
