"""The Python language.

`annotations` runs in the web app: it checks and resolves an entrypoint's parameter annotations.
`runner`, `tracer` and `entrypoint` run inside the sandbox image: they call the entrypoint, stream its
line numbers, and (`--inspect`) report its signature without running it.
"""
