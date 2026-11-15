"""Narrow SDK contracts for existing unannotated Fleet providers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:
    from agent_procutil.daemon_instances import Purpose

    from .ledger import OperationLedger

_Result = TypeVar("_Result")
_Function = TypeVar("_Function", bound=Callable[..., object])


class CanonicalJSON(Protocol):
    def __call__(self, value: object, *, output: bool = False) -> bytes: ...


class Settlement(Protocol):
    def __call__(self, function: Callable[[], _Result]) -> Awaitable[_Result]: ...


class DaemonDecorator(Protocol):
    def __call__(self, function: _Function) -> _Function: ...


class DaemonEntrypoint(Protocol):
    def __call__(
        self, service: str, *, source: str | Path,
        runtime_root: str | Path | Callable[..., str | Path] | None = None,
        purpose: Purpose | None = None,
    ) -> DaemonDecorator: ...


class OperationLedgerFactory(Protocol):
    def __call__(
        self, path: Path, *, authority: str, installation: str,
        enable_enrollment: bool = False,
    ) -> OperationLedger: ...
