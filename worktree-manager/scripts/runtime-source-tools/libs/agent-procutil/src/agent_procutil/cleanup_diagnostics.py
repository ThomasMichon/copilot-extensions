"""Passive, public-safe snapshots of recorded owned-cleanup evidence."""

from __future__ import annotations

from dataclasses import dataclass, fields
from inspect import getattr_static
from types import GetSetDescriptorType, MemberDescriptorType

_PHASES = frozenset({
    "unknown", "job-retire", "process-wait", "pipe-completion", "pipe-join",
    "stream-capture-start", "stream-capture-finish", "stream-close",
    "process-handle-close", "spawn-release", "spawn-thread-close",
    "spawn-job-close",
})
_SETTLEMENTS = frozenset({"unknown", "confirmed", "unconfirmed"})


@dataclass
class _CleanupSettlement:
    process: str = "unknown"
    job: str = "unknown"
    output: str = "unknown"


@dataclass(frozen=True)
class CleanupDiagnostics:
    """Creation-time evidence, never a current resource or cleanup-status probe.

    ``unconfirmed`` records an unsuccessful confirmation operation, not proof
    that a process is alive. ``None`` custody means no recorded evidence.
    """

    phase: str = "unknown"
    native_error_kind: str = "unknown"
    native_error_code: int | None = None
    process_settlement: str = "unknown"
    job_settlement: str = "unknown"
    output_settlement: str = "unknown"
    process_retained: bool | None = None
    job_retained: bool | None = None
    resources_retained: bool | None = None
    cleanup_complete: bool | None = None

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "phase":
                safe: str | int | bool | None = (
                    value if type(value) is str and value in _PHASES else "unknown"
                )
            elif field.name.endswith("_settlement"):
                safe = value if type(value) is str and value in _SETTLEMENTS else "unknown"
            elif field.name == "native_error_kind":
                safe = value if type(value) is str and value in {"errno", "winerror"} else "unknown"
            elif field.name == "native_error_code":
                safe = value if type(value) is int and -(2**31) <= value < 2**32 else None
            else:
                safe = value if type(value) is bool else None
            object.__setattr__(self, field.name, safe)
        if self.native_error_kind == "unknown" or self.native_error_code is None:
            object.__setattr__(self, "native_error_kind", "unknown")
            object.__setattr__(self, "native_error_code", None)

    def public_fields(self) -> dict[str, str | int | bool | None]:
        """Return only the versioned scalar allowlist, without exception text."""
        return {
            "schema": 1,
            "phase": self.phase,
            "native_error_kind": self.native_error_kind,
            "native_error_code": self.native_error_code,
            "process_settlement": self.process_settlement,
            "job_settlement": self.job_settlement,
            "output_settlement": self.output_settlement,
            "process_retained": self.process_retained,
            "job_retained": self.job_retained,
            "resources_retained": self.resources_retained,
            "cleanup_complete": self.cleanup_complete,
        }


def recorded_native_error(error: BaseException | None) -> tuple[str, int | None]:
    """Select an already-recorded OSError code; never parse or traverse causes."""
    if isinstance(error, OSError):
        for name in ("winerror", "errno"):
            descriptor = vars(OSError).get(name)
            value = (
                descriptor.__get__(error) if descriptor is not None
                else BaseException.__dict__["__dict__"].__get__(error).get(name)
            )
            if type(value) is int and -(2**31) <= value < 2**32:
                return name, value
    return "unknown", None


def _recorded_fields(value: object) -> dict[str, object]:
    descriptor = getattr_static(value, "__dict__", None)
    if not isinstance(descriptor, (GetSetDescriptorType, MemberDescriptorType)):
        return {}
    if descriptor.__name__ != "__dict__":
        return {}
    try:
        namespace = descriptor.__get__(value)
    except (AttributeError, TypeError):
        return {}
    return namespace if type(namespace) is dict else {}


def recorded_job_settlement(job: object) -> str:
    """Read only the native owner's existing retirement-confirmation record."""
    retirement = _recorded_fields(job).get("_native_retirement")
    confirmed = _recorded_fields(retirement).get("confirmed")
    return "confirmed" if confirmed is True else "unconfirmed" if confirmed is False else "unknown"
