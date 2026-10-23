"""A tool's entrypoint signature, read inside the sandbox and checked here; and binding form values to it.

`Sandbox.inspect` runs the runtime with `--inspect`, which reports each parameter's name, annotation
text (in the tool's language) and default. Nothing in that report is trusted: names must be plain
identifiers, every annotation must map to an allowed kind through the language's allow-list, and
sizes are bounded. The result is language neutral:

    str     a line of text            int     a whole number          float   a number
    file    an uploaded file          choice  one of a fixed set of plain values

`Signature.bind` turns submitted values (strings from a form, uploaded files) into a Job's arguments
and files, converting each through its kind; nothing is ever evaluated.
"""

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from sandboxcore.python import annotations as python_annotations
from sandboxcore.source import ENTRYPOINT

MAX_PARAMETERS = 64
MAX_TEXT = 4096  # an annotation or a default, as text
MAX_VALUE = 256 * 1024  # a submitted string value


class SignatureError(ValueError):
    """A signature or a submitted value that is refused; the message can be shown to the visitor."""


@dataclass(frozen=True)
class Parameter:
    name: str
    kind: str  # str, int, float, file or choice
    annotation: str  # canonical annotation text in the tool's language (what is stored)
    default: Optional[str] = None  # as text, as the form shows it; None when there is none
    choices: tuple = ()


@dataclass(frozen=True)
class Signature:
    language: str
    entrypoint: str
    parameters: tuple[Parameter, ...]

    # --- storage: the {name: (annotation, default)} shape the apps keep in their databases ---

    def to_arguments(self) -> dict[str, tuple[str, Optional[str]]]:
        return {p.name: (p.annotation, p.default) for p in self.parameters}

    @classmethod
    def from_arguments(cls, language: str, entrypoint: str, arguments: Mapping[str, Any]) -> "Signature":
        """A stored signature, checked again: a row stored before a rule existed is refused, not trusted."""
        report = [
            {"name": name, "annotation": spec[0], "default": spec[1] if len(spec) > 1 else None}
            for name, spec in arguments.items()
        ]
        return from_report(language, {"entrypoint": entrypoint, "parameters": report})

    # --- binding ---

    def bind(self, values: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """(arguments, files) for a Job from submitted `values`: strings, or file uploads (anything with
        `filename` and `data`, such as an InputFile). A missing value takes the parameter's default.
        Values for names the signature does not have are ignored. SignatureError for a bad value."""
        arguments: dict[str, Any] = {}
        files: dict[str, Any] = {}
        for p in self.parameters:
            value = values.get(p.name)
            if p.kind == "file":
                files[p.name] = value if _is_upload(value) else None
                continue
            if value is None or _is_upload(value):
                value = p.default
            if value is None:
                raise SignatureError(f"Argument Error: {p.name} is required")
            if not isinstance(value, str):
                value = str(value)
            if len(value) > MAX_VALUE:
                raise SignatureError(f"Argument Error: {p.name} is too long")
            arguments[p.name] = _CONVERT[p.kind](p, value)
        return arguments, files


def _is_upload(value) -> bool:
    return hasattr(value, "filename") and hasattr(value, "data")


def _int(p: Parameter, value: str):
    try:
        return int(value.strip())
    except ValueError:
        raise SignatureError(f"Argument Error: {p.name} must be a whole number") from None


def _float(p: Parameter, value: str):
    try:
        number = float(value.strip())
    except ValueError:
        raise SignatureError(f"Argument Error: {p.name} must be a number") from None
    if not math.isfinite(number):
        raise SignatureError(f"Argument Error: {p.name} must be a finite number")
    return number


def _choice(p: Parameter, value: str):
    for choice in p.choices:
        if value == str(choice):
            return choice
    raise SignatureError(f"Argument Error: {p.name} must be one of {', '.join(map(str, p.choices))}")


_CONVERT: dict[str, Callable[[Parameter, str], Any]] = {
    "str": lambda p, value: value,
    "int": _int,
    "float": _float,
    "choice": _choice,
}

# each language's allow-list: annotation text -> (kind, choices, canonical text), ValueError outside it
KIND_OF: dict[str, Callable[[str], tuple[str, tuple, str]]] = {"python": python_annotations.parameter_kind}


def from_report(language: str, report: Any) -> Signature:
    """A Signature from a runtime's --inspect report; SignatureError for anything out of shape."""
    if not isinstance(report, dict):
        raise SignatureError("The code could not be inspected")
    if "error" in report:
        error = report["error"]
        raise SignatureError(error[:300] if isinstance(error, str) else "The code could not be inspected")
    entrypoint, parameters = report.get("entrypoint"), report.get("parameters")
    if not isinstance(entrypoint, str) or not ENTRYPOINT.fullmatch(entrypoint) or not isinstance(parameters, list):
        raise SignatureError("The code could not be inspected")
    if len(parameters) > MAX_PARAMETERS:
        raise SignatureError(f"Annotation Error: at most {MAX_PARAMETERS} arguments")
    kind_of = KIND_OF.get(language)
    if kind_of is None:
        raise SignatureError(f"Unsupported language: {language}")
    checked, seen = [], set()
    for item in parameters:
        if not isinstance(item, dict):
            raise SignatureError("The code could not be inspected")
        name, annotation, default = item.get("name"), item.get("annotation"), item.get("default")
        if not isinstance(name, str) or not ENTRYPOINT.fullmatch(name) or name in seen:
            raise SignatureError("Annotation Error: argument names must be plain identifiers")
        seen.add(name)
        if annotation is None:
            raise SignatureError("Annotation Error: arguments to entrypoint function must have annotations")
        if not isinstance(annotation, str) or len(annotation) > MAX_TEXT:
            raise SignatureError(f"Annotation Error: '{name}' has an unsupported annotation")
        if default is not None and (not isinstance(default, str) or len(default) > MAX_TEXT):
            raise SignatureError(f"Annotation Error: '{name}' has an unsupported default")
        try:
            kind, choices, canonical = kind_of(annotation)
        except ValueError as e:
            raise SignatureError(f"Annotation Error: '{name}': {e}") from None
        checked.append(Parameter(name, kind, canonical, default, choices))
    return Signature(language, entrypoint, tuple(checked))

