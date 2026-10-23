#!/bin/sh
# Run the integration tests against a real rootless podman on this (Linux) host: builds the reference
# sandbox image, then runs pytest on the host with the image configured. Arguments go to pytest.
# Needs cgroup v2 with the memory and pids controllers delegated (the startup self-test checks them).
set -e
cd "$(dirname "$0")"
IMAGE=localhost/sandboxcore-sandbox:test
podman build -q -f containers/Containerfile.sandbox -t "$IMAGE" . >/dev/null
# pytest for the host's python3, without touching the system: a --target install made in a container
DEPS="${XDG_CACHE_HOME:-$HOME/.cache}/sandboxcore-test-deps"
if [ ! -d "$DEPS/pytest" ]; then
  mkdir -p "$DEPS"
  podman run --rm -v "$DEPS":/out:Z docker.io/library/python:3.12-slim \
    pip install -q --root-user-action=ignore --disable-pip-version-check --target /out pytest pytest-asyncio
fi
SANDBOX_INTEGRATION=1 SANDBOX_IMAGE="$IMAGE" PYTHONPATH="$PWD/src:$DEPS" \
  exec python3 -m pytest -p no:cacheprovider -m integration "$@"
