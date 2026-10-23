"""Every runtime in this repository follows the line trace protocol (docs/trace-protocol.md)."""

import pytest

from containerwebcore.conformance import CHECKS
from tests.conformance.runtimes import PYTHON, go_runtime


@pytest.fixture(scope="session")
def runtimes(tmp_path_factory):
    found = {"python": PYTHON}
    go = go_runtime(tmp_path_factory.mktemp("go-runtime"))
    if go is not None:
        found["go"] = go
    return found


@pytest.mark.parametrize("check", sorted(CHECKS))
@pytest.mark.parametrize("language", ["python", "go"])
def test_runtime_follows_the_trace_protocol(runtimes, language, check, tmp_path):
    if language not in runtimes:
        pytest.skip(f"no {language} toolchain")
    CHECKS[check](runtimes[language], tmp_path)
