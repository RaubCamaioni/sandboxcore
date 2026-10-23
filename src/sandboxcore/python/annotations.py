"""Entrypoint parameter annotations: checked at upload, resolved at run time, never evaluated.

Annotations come from code that anyone with a GitHub login can upload, and they are read by the web
app, which runs outside the sandbox. Parsing the file proves only that it is valid Python, and any
expression is a valid annotation (`x: __import__("os").system("...")` parses and formats cleanly),
so annotation text must never reach `eval`. Only these shapes are accepted:

    str    int    float    Path    Literal["a", "b", 3]

`check_annotation` runs at upload and returns the canonical text that is stored. `resolve_annotation`
runs at run time: it parses the stored text again with `ast` and maps it to a type through the same
allow-list, so a row stored before this check existed is refused instead of executed.
"""

import ast
import re
from pathlib import Path
from typing import Literal

SIMPLE_TYPES = {"str": str, "int": int, "float": float, "Path": Path}

# Literal values are shown as form choices and parsed by render.parser_literal: letters, digits, _ and .
LITERAL_TEXT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.]*")
MAX_LITERAL_VALUES = 256
MAX_ANNOTATION_LENGTH = 4096

ALLOWED = "str, int, float, Path or Literal[...] of plain values (letters, digits, _ and .)"


class AnnotationError(ValueError):
    pass


def check_annotation(node: ast.expr) -> str:
    """The canonical text of an accepted annotation node; AnnotationError for anything else."""
    _type_of(node)
    return ast.unparse(node)


def resolve_annotation(text: str):
    """The Python type for stored annotation text, through the allow-list; AnnotationError otherwise."""
    if not isinstance(text, str) or len(text) > MAX_ANNOTATION_LENGTH:
        raise AnnotationError(f"annotation must be {ALLOWED}")
    try:
        node = ast.parse(text, mode="eval").body
    except SyntaxError:
        raise AnnotationError(f"annotation must be {ALLOWED}") from None
    return _type_of(node)


def _type_of(node: ast.expr):
    if isinstance(node, ast.Name) and node.id in SIMPLE_TYPES:
        return SIMPLE_TYPES[node.id]
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "Literal":
        elements = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        if not elements or len(elements) > MAX_LITERAL_VALUES:
            raise AnnotationError(f"Literal needs 1 to {MAX_LITERAL_VALUES} values")
        values = []
        for element in elements:
            # a constant only: no names, calls, attributes or arithmetic; bool is an int subclass, so exclude it
            if not isinstance(element, ast.Constant) or type(element.value) not in (str, int, float):
                raise AnnotationError(f"Literal values must be plain strings or numbers, not {ast.unparse(element)}")
            if isinstance(element.value, str) and not LITERAL_TEXT.fullmatch(element.value):
                raise AnnotationError(f"Literal value {element.value!r} may use only letters, digits, _ and .")
            values.append(element.value)
        return Literal[tuple(values)]
    raise AnnotationError(f"annotation must be {ALLOWED}, not {ast.unparse(node)}")
