"""Narrow SDK contracts for existing unannotated Fleet providers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:
    from .ledger import OperationLedger

_Result = TypeVar("_Result")


class CanonicalJSON(Protocol):
    def __call__(self, value: object, *, output: bool = False) -> bytes: ...


class Settlement(Protocol):
    def __call__(self, function: Callable[[], _Result]) -> Awaitable[_Result]: ...


class OperationLedgerFactory(Protocol):
    def __call__(
        self, path: Path, *, authority: str, installation: str,
        enable_enrollment: bool = False,
    ) -> OperationLedger: ...
