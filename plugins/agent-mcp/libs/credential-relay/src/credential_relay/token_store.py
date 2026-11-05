"""Strict, bounded reads of the providers' file-backed relay-token maps."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

_T = TypeVar("_T")
_RETRY_DELAYS = (0.02, 0.04, 0.08, 0.16, 0.32)


def retry_token_store_io(operation: Callable[[], _T]) -> _T:
    """Retry Windows sharing/access contention, never parse or ordinary I/O errors."""
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return operation()
        except OSError as error:
            if (
                getattr(error, "winerror", None) not in (5, 32, 33)
                or attempt == len(_RETRY_DELAYS)
            ):
                raise
            time.sleep(_RETRY_DELAYS[attempt])
    raise AssertionError("unreachable")


def read_token_store(path: Path) -> dict[str, Any]:
    """Read a token map; only confirmed absence is an empty store.

    Mutation callers must hold their provider's transaction lock. Authorization
    readers may fail closed on these errors, but must never persist that fallback.
    """
    try:
        content = retry_token_store_io(lambda: path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("relay token store must contain a JSON object")
    return data
