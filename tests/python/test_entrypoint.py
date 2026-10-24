"""--inspect reports a tool's entrypoint signature without running any of its code."""

import json
import subprocess
import sys

from sandboxcore.python.entrypoint import inspect_tool


def tool(tmp_path, code, name="t"):
    path = tmp_path / f"{name}.py"
    path.write_text(code)
    return path


def test_parameters_annotations_and_defaults_are_reported(tmp_path):
    code = 'from pathlib import Path\ndef t(a: int, b: str = "x\'y", c: float = 1.5, d: Path = None, e=3):\n    pass\n'
    assert inspect_tool(tool(tmp_path, code)) == {
        "entrypoint": "t",
        "parameters": [
            {"name": "a", "annotation": "int", "default": None},
            {"name": "b", "annotation": "str", "default": "x'y"},
            {"name": "c", "annotation": "float", "default": "1.5"},
            {"name": "d", "annotation": "Path", "default": "None"},
            {"name": "e", "annotation": None, "default": "3"},
        ],
        "returns": None,
    }


def test_nothing_in_the_code_runs(tmp_path):
    marker = tmp_path / "ran"
    code = f"open({str(marker)!r}, 'w')\ndef t(x: __import__('os').getcwd()):\n    pass\n"
    assert inspect_tool(tool(tmp_path, code))["parameters"][0]["annotation"] == "__import__('os').getcwd()"
    assert not marker.exists()  # reported as text; the host's allow-list refuses it


def test_errors_are_reported_not_raised(tmp_path):
    assert inspect_tool(tool(tmp_path, "def t(:\n")) == {"error": "Python Syntax Error: line 1"}
    assert inspect_tool(tool(tmp_path, "def other():\n    pass\n"))["error"].startswith("Entrypoint Not Found")
    # only a top-level function counts
    assert "error" in inspect_tool(tool(tmp_path, "class C:\n    def t(self):\n        pass\n"))
    assert "error" in inspect_tool(tool(tmp_path, "x = (" * 200_000 + "1" + ")" * 200_000 + "\n"))


def test_the_runner_writes_the_report_with_inspect(tmp_path):
    path = tool(tmp_path, f"open({str(tmp_path / 'ran')!r}, 'w')\ndef t(a: int):\n    pass\n")
    subprocess.run(
        [sys.executable, "-m", "sandboxcore.python.runner", "--file", str(path), "--workdir", str(tmp_path), "--inspect"],
        check=True,
        timeout=30,
    )
    report = json.loads((tmp_path / "result.json").read_text())
    assert report["parameters"] == [{"name": "a", "annotation": "int", "default": None}]
    assert not (tmp_path / "ran").exists()


def test_the_declared_return_is_reported(tmp_path):
    code = "import cadquery as cq\ndef box(size: float = 1) -> cq.Workplane:\n    pass\n"
    assert inspect_tool(tool(tmp_path, code, "box"))["returns"] == "cq.Workplane"


def test_no_declared_return_is_none(tmp_path):
    assert inspect_tool(tool(tmp_path, "def t(x: int = 1):\n    pass\n"))["returns"] is None
