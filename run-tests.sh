#!/bin/sh
# Run the test suite in a clean python:3.12 container (the sandbox's Python). Arguments go to pytest.
# The tests need Linux (FIFOs, loop.add_reader, sys.monitoring); this works from WSL or any podman host.
set -e
cd "$(dirname "$0")"
exec podman run --rm -v "$PWD":/src:ro docker.io/library/python:3.12-slim sh -c \
  'cp -r /src /w && cd /w && pip install -q --root-user-action=ignore --disable-pip-version-check -e ".[dev]" && python -m pytest -p no:cacheprovider "$@"' \
  run-tests "$@"
