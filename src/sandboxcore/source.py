"""Taking uploaded code: the checks every upload passes before anything parses or runs it.

`accept_source` turns the bytes and file name a visitor uploaded into a `Source`: bounded in size,
strict UTF-8, no NUL bytes, `\\n` line endings, and an entrypoint name that is a plain identifier
(it names the tool's file in the run directory). It never parses the code: what the code declares
is read inside the sandbox (`Sandbox.inspect`).
"""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

LANGUAGES = {"python": ".py"}
MAX_CODE_BYTES = 256 * 1024
ENTRYPOINT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


class SourceError(ValueError):
    """Uploaded code that is refused; the message can be shown to the uploader."""


@dataclass(frozen=True)
class Source:
    language: str
    entrypoint: str  # the function to call, and the tool file's name without its suffix
    code: str

    @property
    def filename(self) -> str:
        return self.entrypoint + LANGUAGES[self.language]


def accept_source(data: bytes, filename: str | None, language: str = "python", max_bytes: int = MAX_CODE_BYTES) -> Source:
    """The upload as a Source, or SourceError saying why it is refused."""
    if len(data) > max_bytes:
        raise SourceError(f"Code is larger than {max_bytes // 1024} KB")
    try:
        code = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise SourceError(f"Code must be UTF-8 text (byte {e.start} is not)") from None
    entrypoint = PurePosixPath((filename or "").replace("\\", "/")).name
    suffix = LANGUAGES.get(language)
    if suffix is None:
        raise SourceError(f"Unsupported language: {language}")
    if entrypoint.endswith(suffix):
        entrypoint = entrypoint[: -len(suffix)]
    return check_source(Source(language, entrypoint, code.replace("\r\n", "\n").replace("\r", "\n")), max_bytes)


def check_source(source: Source, max_bytes: int = MAX_CODE_BYTES) -> Source:
    """`source` if it keeps every rule, else SourceError. A Source built by hand is checked again here."""
    if source.language not in LANGUAGES:
        raise SourceError(f"Unsupported language: {source.language}")
    if not ENTRYPOINT.fullmatch(source.entrypoint):
        raise SourceError("The file name must be a plain identifier (letters, digits and _), like my_tool.py")
    if len(source.code.encode("utf-8")) > max_bytes:
        raise SourceError(f"Code is larger than {max_bytes // 1024} KB")
    if "\0" in source.code:
        raise SourceError("Code must not contain NUL characters")
    return source
