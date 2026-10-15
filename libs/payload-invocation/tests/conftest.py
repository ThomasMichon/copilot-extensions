"""Register the shared governance fixtures' marker for direct suite execution."""
from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "installation_context_smoke: bounded installation-context contracts",
    )
    config.addinivalue_line(
        "markers", "derived_diagnostic_full: all-copy native CLI acceptance portfolio",
    )
