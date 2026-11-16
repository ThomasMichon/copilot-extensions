"""Finite interpreter selection and participant environment for Manager installation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
import sys

BOOTSTRAP_PYTHON = "COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON"


@contextmanager
def bootstrap_python_environment() -> Iterator[None]:
    previous = os.environ.get(BOOTSTRAP_PYTHON)
    base = getattr(sys, "_base_executable", None)
    os.environ[BOOTSTRAP_PYTHON] = base if isinstance(base, str) and base else sys.executable
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(BOOTSTRAP_PYTHON, None)
        else:
            os.environ[BOOTSTRAP_PYTHON] = previous


def installation_child_options() -> dict[str, object]:
    from update_driver import participant_environment

    return {"env": {**os.environ, **participant_environment()}}
