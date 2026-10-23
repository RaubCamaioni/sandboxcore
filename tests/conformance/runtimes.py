"""The runtimes in this repository, described for `sandboxcore.conformance`."""

import shutil
import subprocess
import sys
from pathlib import Path

from sandboxcore.conformance import Runtime

GO_MODULE = Path(__file__).resolve().parents[2] / "runtimes" / "go"

PYTHON = Runtime(
    name="python",
    suffix=".py",
    command=lambda tool, run_dir: [
        sys.executable, "-m", "sandboxcore.python.runner", "--file", str(tool), "--workdir", str(run_dir),
    ],
    probe=(
        "def probe():\n"  # 1
        "    total = 0\n"  # 2
        "    for i in range(3):\n"  # 3
        "        total += i\n"  # 4
        "    return total\n"  # 5
    ),
    probe_lines=[2, 3, 4, 5],
    flood="def flood():\n    n = 0\n    for i in range(300_000):\n        n += 1\n    return n\n",
    prepare=lambda run_dir: (run_dir / "args.json").write_text("{}"),
)


def go_runtime(build_dir: Path) -> Runtime | None:
    """The Go reference runtime, built into `build_dir`; None when no Go toolchain is installed."""
    if shutil.which("go") is None:
        return None
    binary = build_dir / "sandboxcore-go-run"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/sandboxcore-go-run"], cwd=GO_MODULE, check=True)
    return Runtime(
        name="go",
        suffix=".go",
        command=lambda tool, run_dir: [
            str(binary), "--file", str(tool), "--workdir", str(run_dir), "--module", str(GO_MODULE),
        ],
        probe=(
            "package main\n"  # 1
            "\n"  # 2
            'import "fmt"\n'  # 3
            "\n"  # 4
            "func main() {\n"  # 5
            "\ttotal := 0\n"  # 6
            "\tfor i := 0; i < 3; i++ {\n"  # 7
            "\t\ttotal += i\n"  # 8
            "\t}\n"  # 9
            "\tfmt.Println(total)\n"  # 10
            "}\n"  # 11
        ),
        probe_lines=[6, 7, 8, 10],
        flood="package main\n\nfunc main() {\n\tn := 0\n\tfor i := 0; i < 300000; i++ {\n\t\tn++\n\t}\n\t_ = n\n}\n",
    )
