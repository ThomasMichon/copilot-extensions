from __future__ import annotations

from collections.abc import Iterator
import os

import pytest


@pytest.fixture(autouse=True)
def no_shared_write_umask() -> Iterator[None]:
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)
