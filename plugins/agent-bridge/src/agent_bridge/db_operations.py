"""Typed operations supplied by the existing Database mixin composition."""

from __future__ import annotations

from contextlib import AbstractContextManager
import sqlite3
from typing import Any, Protocol

from .runtime_custody import RuntimeAdmission, RuntimeInstallation


class DatabaseOperations(Protocol):
    _write_lock: AbstractContextManager[bool]

    def _get_conn(self) -> sqlite3.Connection: ...

    def execute_read(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]: ...

    def execute_write(
        self, sql: str, params: tuple[Any, ...] = (), *, membership: bool = False,
    ) -> sqlite3.Cursor: ...

    def _runtime_transaction(
        self, *, write: bool, full_sync: bool = True, membership: bool = False,
    ) -> AbstractContextManager[sqlite3.Connection]: ...

    def _runtime_installation(self, installation: RuntimeInstallation) -> str: ...

    def _runtime_admission(
        self, conn: sqlite3.Connection, identity: str, admission: RuntimeAdmission, *, cohort: str,
    ) -> None: ...

    def _execute_runtime_admitted_write(
        self, statement: str, parameters: tuple[object, ...], admission: RuntimeAdmission | None,
        *, membership: bool = False,
    ) -> sqlite3.Cursor: ...
