"""The runner calls the entrypoint with args.json and writes result.json; `finish` post-processes it."""

import json

import pytest

from sandboxcore.python.runner import main


@pytest.fixture
def tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the runner changes into the run directory

    def _tool(code, args=None):
        path = tmp_path / "t.py"
        path.write_text(code)
        (tmp_path / "args.json").write_text(json.dumps(args or {}))
        return path

    return _tool


def result(tmp_path):
    return json.loads((tmp_path / "result.json").read_text())


def run(path, **kw):
    main(["--file", str(path), "--workdir", str(path.parent)], **kw)


def test_the_entrypoint_gets_args_json(tmp_path, tool):
    run(tool("def t(a: int, b: str) -> str:\n    return b * a\n", {"a": 2, "b": "x"}))
    assert result(tmp_path) == "xx"


def test_a_tool_exception_becomes_a_runtime_error_string(tmp_path, tool):
    run(tool("def t():\n    raise KeyError('k')\n"))
    assert result(tmp_path).startswith("Runtime Error: t KeyError")


def test_finish_turns_the_result_into_what_is_stored(tmp_path, tool):
    run(tool("def t():\n    return 21\n"), finish=lambda value: {"double": value * 2})
    assert result(tmp_path) == {"double": 42}


def test_a_finish_error_is_reported_with_its_label(tmp_path, tool):
    def finish(value):
        raise TypeError("not a model")

    run(tool("def t():\n    return 'text'\n"), finish=finish, finish_label="Export Error")
    assert result(tmp_path) == "Export Error: TypeError: not a model"


def test_finish_is_skipped_when_the_tool_failed(tmp_path, tool):
    calls = []
    run(tool("def t():\n    1 / 0\n"), finish=calls.append)
    assert calls == [] and result(tmp_path).startswith("Runtime Error: t ZeroDivisionError")


def test_a_file_without_its_entrypoint_fails(tool):
    with pytest.raises(ValueError, match="no function named: t"):
        run(tool("def other():\n    return 1\n"))
