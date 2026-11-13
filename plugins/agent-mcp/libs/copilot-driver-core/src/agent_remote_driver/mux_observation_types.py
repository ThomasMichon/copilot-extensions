"""Shared result and error identities for module and finite-file consumers."""

from dataclasses import dataclass


class ObservationUnavailable(RuntimeError):
    """No authoritative native presence result was obtained."""


@dataclass(frozen=True)
class Pane:
    pane_id: str
    dead: bool
    active: bool
    path: str


@dataclass(frozen=True)
class Observation:
    status: str
    reason: str = ""
    incarnation: str = ""
    clients: int | None = None
    panes: tuple[Pane, ...] = ()

    def presence(self) -> bool:
        if self.status not in {"present", "absent"}:
            raise ObservationUnavailable("PSMux observation unavailable: " + self.reason)
        return self.status == "present"
