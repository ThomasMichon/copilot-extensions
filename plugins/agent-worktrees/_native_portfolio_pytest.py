"""Explicit source-test plugin entry point; not part of the runtime package."""

import os
import sys
from pathlib import Path

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    configured = os.environ.get("PYTEST_PLUGINS", "")
    retained = [name for name in configured.split(",") if name.strip() not in ("", __name__)]
    if retained:
        os.environ["PYTEST_PLUGINS"] = ",".join(retained)
    else:
        os.environ.pop("PYTEST_PLUGINS", None)
    if "P" not in config.option.reportchars:
        config.option.reportchars += "P"
    tests = str(Path(__file__).parent / "tests")
    if tests not in sys.path:
        sys.path.insert(0, tests)
    from hook_native_portfolio import register

    register(config)
