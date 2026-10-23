"""Read a Python tool's entrypoint signature without running it. Runs inside the sandbox.

`--inspect` on the runner writes this report to result.json instead of calling the tool:

    {"entrypoint": "tool", "parameters": [{"name": "x", "annotation": "int", "default": "3"}, ...]}
    {"error": "Python Syntax Error: line 4"}

Parsing untrusted code is itself a risk (a parser can be driven into deep recursion or huge memory),
so it happens here, under the sandbox's limits, and never in the web app. The host trusts nothing in
the report: `sandboxcore.signature` checks every field against the allow-list.
"""

import ast
from pathlib import Path


def inspect_tool(file: Path) -> dict:
    try:
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=file.name)
    except SyntaxError as e:
        return {"error": f"Python Syntax Error: line {e.lineno}"}
    except (ValueError, RecursionError, MemoryError):
        return {"error": "Python Syntax Error: the code could not be parsed"}

    name = file.stem
    entry = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)
    if entry is None:
        return {"error": "Entrypoint Not Found: entrypoint function matching file name missing"}

    args = entry.args.args
    defaults = [None] * (len(args) - len(entry.args.defaults)) + list(entry.args.defaults)
    parameters = []
    for arg, default in zip(args, defaults):
        parameters.append(
            {
                "name": arg.arg,
                "annotation": ast.unparse(arg.annotation) if arg.annotation is not None else None,
                "default": _default_text(default),
            }
        )
    return {"entrypoint": name, "parameters": parameters}


def _default_text(node) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ast.unparse(node)
