"""Uploaded code passes these checks before anything parses or runs it."""

import pytest

from sandboxcore.source import MAX_CODE_BYTES, Source, SourceError, accept_source, check_source


def test_an_upload_becomes_a_source():
    s = accept_source(b"def tool():\r\n    return 1\r\n", "tool.py")
    assert s == Source("python", "tool", "def tool():\n    return 1\n") and s.filename == "tool.py"


@pytest.mark.parametrize("filename", ["../../tool.py", "/tmp/tool.py", "C:\\x\\tool.py", "tool"])
def test_only_the_last_name_component_counts(filename):
    assert accept_source(b"x = 1\n", filename).entrypoint == "tool"


@pytest.mark.parametrize(
    "filename", [None, "", ".py", "..py", "my-tool.py", "1tool.py", "tool name.py", "t\u00e9.py", "a" * 65 + ".py"]
)
def test_the_entrypoint_must_be_a_plain_identifier(filename):
    with pytest.raises(SourceError):
        accept_source(b"x = 1\n", filename)


def test_size_encoding_and_nul_are_checked():
    with pytest.raises(SourceError, match="larger"):
        accept_source(b"#" * (MAX_CODE_BYTES + 1), "t.py")
    with pytest.raises(SourceError, match="UTF-8"):
        accept_source(b"x = '\xff'\n", "t.py")
    with pytest.raises(SourceError, match="NUL"):
        accept_source(b"x = 1\0\n", "t.py")
    with pytest.raises(SourceError, match="language"):
        accept_source(b"x = 1\n", "t.py", language="cobol")


def test_a_hand_built_source_is_checked_again():
    with pytest.raises(SourceError):
        check_source(Source("python", "../escape", "x = 1\n"))
