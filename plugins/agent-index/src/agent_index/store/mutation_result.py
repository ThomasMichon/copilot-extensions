"""Attempt-local observations of successful participating writer commits."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from .logical_projection import current_projection


@dataclass
class MutationResult:
    """Diagnostics, never source-cursor, cache-coverage or recovery authority."""

    available: bool
    write_commits: int | None
    logical_commits: int | None
    tables: list[str] | None
    epoch: str | None = None
    generation: int | None = None


@dataclass
class _Observation:
    domain: object
    result: MutationResult


_current: ContextVar[_Observation | None] = ContextVar("source_mutation_result", default=None)


@contextmanager
def observe_mutations(domain: object | None) -> Iterator[MutationResult]:
    """Observe this execution context and domain without extending its exclusion."""
    result = MutationResult(domain is not None, 0, 0, []) if domain is not None else (
        MutationResult(False, None, None, None)
    )
    token = _current.set(_Observation(domain, result) if domain is not None else None)
    try:
        yield result
    finally:
        _current.reset(token)


def record_write(
    domain: object,
    logical_tables: set[str] | None,
    physical: dict[str, Any] | None,
    receipt: Callable[[], dict],
) -> None:
    """Called only after the outer writer successfully publishes its clean receipt."""
    observation = _current.get()
    if observation is None or observation.domain is not domain:
        return
    result = observation.result
    assert result.write_commits is not None
    result.write_commits += 1
    if logical_tables is None:
        result.logical_commits = None
        result.tables = None
    else:
        if result.logical_commits is not None:
            result.logical_commits += bool(logical_tables)
        if result.tables is not None:
            result.tables = sorted(set(result.tables) | logical_tables)
    projection = current_projection(receipt(), physical)
    result.epoch = projection["epoch"] if projection is not None else None
    result.generation = projection["generation"] if projection is not None else None
