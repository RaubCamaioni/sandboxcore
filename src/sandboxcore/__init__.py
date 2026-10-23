"""Shared sandbox core: run untrusted code in rootless podman containers and read back what it made.

- `sandboxcore.host`: the web app's side (podman runner, limits, outcomes, trace channel, run directories)
- `sandboxcore.contract`: the file and channel names the host and every sandbox runtime agree on
- `sandboxcore.serializer`: the JSON encoding of args.json and result.json
- `sandboxcore.python`: the Python language (entrypoint annotations on the host, the runner in the sandbox)

Everything is standard library only, so the same package installs in the app and in each sandbox image.
"""

__version__ = "0.1.0"
