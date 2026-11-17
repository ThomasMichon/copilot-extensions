"""Inputs and atomic commit hook for an owner-qualified native metadata import."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import sqlite3
from typing import Any


@dataclass(frozen=True)
class MetadataImport:
    session_id: str
    target_json: str
    native_session_id: str
    agent_name: str | None
    caller_id: str
    config: dict[str, Any]
    commit: Callable[[sqlite3.Connection], None]

    def require(self, target_json: str, native_session_id: str, agent_name: str | None) -> None:
        if (target_json, native_session_id, agent_name) != (
            self.target_json, self.native_session_id, self.agent_name,
        ):
            raise ValueError("Retained metadata import changed its qualified owner inputs")
