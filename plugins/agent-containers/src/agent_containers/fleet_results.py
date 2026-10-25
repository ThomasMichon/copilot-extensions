"""Result types shared by fleet lifecycle callers."""

from dataclasses import dataclass, field


class RecreateMemberError(RuntimeError):
    """A targeted recreation failed after reporting its destructive boundary."""

    def __init__(self, message: str, *, old_container_removed: bool) -> None:
        self.old_container_removed = old_container_removed
        super().__init__(message)


@dataclass
class FleetOperationResult:
    """Per-member result for a fleet lifecycle operation."""

    created: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    unchanged: dict[str, str] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)
    recreated: list[str] = field(default_factory=list)
    captured: list[str] = field(default_factory=list)
    deferred: dict[str, str] = field(default_factory=dict)
    rescues: dict[str, dict] = field(default_factory=dict)
    telemetry_abandoned: list[str] = field(default_factory=list)
