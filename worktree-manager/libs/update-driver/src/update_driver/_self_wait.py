"""Refuse a wait that the waiter's own running request makes unsatisfiable."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

from .participant import ParticipantError, _current_custody

_TERMINAL = frozenset({"completed", "interrupted", "blocked"})


def refuse_self_wait(root: Path, request_id: str, state: str) -> None:
    """A running participant can never see queued work settle; refuse instead of hanging."""
    current = cast("tuple[Any, str] | None", _current_custody.get())
    if current is None or state in _TERMINAL:
        return
    custody = current[0]
    if custody._process_id == os.getpid() and Path(custody.root) == root:
        raise ParticipantError(
            f"running request {custody.request_id} cannot wait for request {request_id}: "
            "the serial queue admits nothing until the waiter itself settles",
        )
