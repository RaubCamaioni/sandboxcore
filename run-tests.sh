#!/bin/sh
# Run the test suite in a clean container: Python 3.12 (the sandbox's Python) and Go. Arguments go to
# pytest. The tests need Linux (FIFOs, loop.add_reader, sys.monitoring); this works from WSL or any
# podman host.
set -e
cd "$(dirname "$0")"
IMAGE=localhost/sandboxcore-test:latest
podman build -q -f containers/Containerfile.test -t "$IMAGE" containers >/dev/null
exec podman run --rm -v "$PWD":/src:ro "$IMAGE" sh -c \
  'cp -r /src /w && cd /w && pip install -q --root-user-action=ignore --disable-pip-version-check --no-deps -e . &&
   (cd runtimes/go && go vet ./... && go test ./...) && python -m pytest -p no:cacheprovider "$@"' \
  run-tests "$@"
