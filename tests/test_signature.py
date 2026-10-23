"""The host trusts nothing in an --inspect report, and binds submitted values through each parameter's kind."""

import pytest

from sandboxcore.host import InputFile
from sandboxcore.signature import Parameter, Signature, SignatureError, from_report


def report(*parameters, entrypoint="t"):
    return {"entrypoint": entrypoint, "parameters": list(parameters)}


def p(name, annotation, default=None):
    return {"name": name, "annotation": annotation, "default": default}


def test_a_report_becomes_a_signature():
    s = from_report("python", report(p("a", "int"), p("b", "Literal[ 'x' , 'y' ]", "x"), p("f", "Path", "None")))
    assert s.parameters == (
        Parameter("a", "int", "int"),
        Parameter("b", "choice", "Literal['x', 'y']", "x", ("x", "y")),  # stored in canonical form
        Parameter("f", "file", "Path", "None"),
    )
    assert s.to_arguments() == {"a": ("int", None), "b": ("Literal['x', 'y']", "x"), "f": ("Path", "None")}


HOSTILE = {
    "not a dict": [1],
    "an error": {"error": "Python Syntax Error: line 3"},
    "a bad entrypoint": report(entrypoint="../x"),
    "parameters not a list": {"entrypoint": "t", "parameters": "a"},
    "too many parameters": report(*[p(f"a{i}", "int") for i in range(65)]),
    "a bad name": report(p("a-b", "int")),
    "a duplicate name": report(p("a", "int"), p("a", "str")),
    "no annotation": report(p("a", None)),
    "code as an annotation": report(p("a", "__import__('os').system('id')")),
    "a long annotation": report(p("a", "str" + " " * 5000)),
    "a non-string default": report(p("a", "int", 3)),
    "a non-dict parameter": report("a"),
}


@pytest.mark.parametrize("bad", HOSTILE.values(), ids=HOSTILE.keys())
def test_anything_out_of_shape_is_refused(bad):
    with pytest.raises(SignatureError):
        from_report("python", bad)


def test_the_runtime_error_text_is_passed_on_bounded():
    with pytest.raises(SignatureError, match="^Python Syntax Error: line 3$"):
        from_report("python", {"error": "Python Syntax Error: line 3"})
    with pytest.raises(SignatureError) as e:
        from_report("python", {"error": "x" * 10_000})
    assert len(str(e.value)) == 300


SIG = from_report(
    "python",
    report(
        p("text", "str", "hi"),
        p("n", "int"),
        p("x", "float", "0.5"),
        p("mode", "Literal['png', 'jpg', 3]", "png"),
        p("data", "Path", "None"),
    ),
)


def test_values_are_converted_by_kind():
    upload = InputFile("a.png", b"x")
    arguments, files = SIG.bind({"n": " 12 ", "x": "2.5", "mode": "3", "data": upload, "stray": "ignored"})
    assert arguments == {"n": 12, "x": 2.5, "mode": 3}  # text was not submitted: the tool's default applies
    assert files == {"data": upload}


def test_missing_values_are_left_to_the_tools_own_defaults():
    assert SIG.bind({"n": "1"}) == ({"n": 1}, {})
    assert SIG.bind({"data": "not a file"}) == ({}, {"data": None})


def test_a_file_for_a_value_parameter_is_refused():
    with pytest.raises(SignatureError, match="not a file"):
        SIG.bind({"n": InputFile("x", b"1")})


@pytest.mark.parametrize(
    "values",
    [{"n": "1.5"}, {"n": "1", "x": "nan"}, {"n": "1", "x": "inf"}, {"n": "1", "mode": "gif"}, {"n": "9" * 5000}],
)
def test_bad_values_are_refused(values):
    with pytest.raises(SignatureError):
        SIG.bind(values)


def test_a_stored_signature_is_checked_again():
    assert Signature.from_arguments("python", "t", SIG.to_arguments()) == SIG
    with pytest.raises(SignatureError):
        Signature.from_arguments("python", "t", {"a": ("open('/etc/passwd')", None)})
