"""Read-only, identity-pinned cache references; never signal a process."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
import math
import re
import time
from typing import Any, Callable, ContextManager, Iterable, Iterator, Protocol

_REFERENCE = re.compile(r"(?:unsealed-[A-Za-z0-9-]+|installed-[a-fA-F0-9]{64})")

class Unavailable(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _reason(error: Exception) -> str:
    return error.reason if isinstance(error, Unavailable) else "process-reference-unavailable"


class ProcessReader(Protocol):
    principal: str

    def snapshot(self) -> dict[int, tuple[int, str]]: ...
    def pin(self, pid: int, birth: int) -> ContextManager[Any]: ...
    def texts(self, handle: Any) -> Iterable[str]: ...


@dataclass
class References:
    mode: str = "unavailable"
    names: set[str] = field(default_factory=set)
    processes: int = 0
    excluded: int = 0
    reason: str = ""
    _reader: ProcessReader | None = field(default=None, repr=False)
    _identities: dict[int, tuple[int, str]] = field(default_factory=dict, repr=False)
    _check: Callable[[], None] = field(default=lambda: None, repr=False)
    _held: ExitStack | None = field(default=None, repr=False)
    _required: frozenset[int] = field(default_factory=frozenset, repr=False)
    _pins: dict[int, Any] = field(default_factory=dict, repr=False)

    def _inspect(self, records: dict[int, tuple[int, str]]) -> None:
        if self._reader is None or self._held is None:
            raise Unavailable("process-observer-unavailable")
        self.excluded = 0
        for pid, record in records.items():
            self._check()
            if pid in self._identities:
                if self._identities[pid] != record:
                    raise Unavailable("process-identity-changed")
                for text in self._reader.texts(self._pins[pid]):
                    self._check()
                    self.names.update(name.lower() for name in _REFERENCE.findall(text))
                continue
            birth, principal = record
            if principal != self._reader.principal and pid not in self._required:
                self.excluded += 1
                continue
            try:
                process = self._held.enter_context(self._reader.pin(pid, birth))
            except ProcessLookupError:
                continue
            for text in self._reader.texts(process):
                self._check()
                self.names.update(name.lower() for name in _REFERENCE.findall(text))
            self._identities[pid] = record
            self._pins[pid] = process
            self.processes += 1

    def verify(self) -> bool:
        if self.mode != "process-scan":
            return False
        try:
            if self._reader is None:
                raise Unavailable("process-observer-unavailable")
            for _ in range(3):
                self._check()
                records = self._reader.snapshot()
                new = any(
                    (principal == self._reader.principal or pid in self._required)
                    and self._identities.get(pid) != (birth, principal)
                    for pid, (birth, principal) in records.items()
                )
                self._inspect(records)
                if not new:
                    return True
            raise Unavailable("process-census-changed")
        except (OSError, ValueError, TimeoutError) as error:
            self.mode, self.reason = "incomplete", _reason(error)
        return self.mode == "process-scan"


@contextmanager
def observe(
    *, reader: ProcessReader | None = None, seconds: float = 10.0,
    check: Callable[[], None] = lambda: None, required_pids: Iterable[int] = (),
) -> Iterator[References]:
    """Keep process handles pinned until the caller finishes its decision.

    The native reader binds principal information to creation identities.
    Opaque relevant processes and a changing census retain every candidate.
    Only namespace names and counts escape; full environments are not retained.
    """
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("cache process deadline must be finite and positive")
    deadline = time.monotonic() + seconds
    required = frozenset(required_pids)

    def bounded() -> None:
        check()
        if time.monotonic() >= deadline:
            raise TimeoutError("cache process observation deadline")

    result = References(_check=bounded, _required=required)
    with ExitStack() as held:
        try:
            if reader is None:
                from .python_cache_windows import Reader

                reader = Reader(check=bounded)
            before = reader.snapshot()
            result._reader, result._identities, result._held = reader, {}, held
            result._inspect(before)
            result.mode = "process-scan"
            result.verify()
        except (OSError, ValueError, TimeoutError) as error:
            result.mode, result.reason = "incomplete", _reason(error)
        yield result
