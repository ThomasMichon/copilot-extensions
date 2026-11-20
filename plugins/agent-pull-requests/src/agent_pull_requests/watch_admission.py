"""Optional watch admission discovery without changing strict replay JSON."""

from __future__ import annotations

import math
from dataclasses import dataclass

PROTOCOL = "agent-pull-requests.terminal-watch-admission/v1"
PROFILE = "urn:agent-pull-requests:terminal-watch-admission:v1"


@dataclass(frozen=True)
class ReplayAdmission:
    protocol: str
    until: tuple[str, ...]

    def validate_registration(self, *, until: tuple[str, ...], deadline_at: float | None) -> None:
        if (
            isinstance(deadline_at, bool) or not isinstance(deadline_at, (int, float))
            or not math.isfinite(deadline_at)
            or deadline_at <= 0
        ):
            raise ValueError("terminal watches require an original finite deadline")
        if not until or any(name not in self.until for name in until):
            raise ValueError("terminal watches require supported terminal predicates")

    def document(self) -> dict[str, object]:
        return {
            "protocol": self.protocol, "deadline": "finite_original", "until": list(self.until),
        }


TERMINAL_ADMISSION = ReplayAdmission(PROTOCOL, ("merged", "closed", "head_changed"))


def contract() -> dict[str, str]:
    return {
        "protocol": PROTOCOL, "profile": PROFILE,
        "discovery": "watch_capabilities_content_type",
    }


def content_type(admission: ReplayAdmission | None) -> str:
    if admission is None:
        return "application/json"
    if type(admission) is not ReplayAdmission or admission != TERMINAL_ADMISSION:
        raise ValueError("unsupported watch admission profile")
    return f'application/json; profile="{PROFILE}"'


def from_content_type(value: str) -> ReplayAdmission | None:
    profiles = [
        part.strip().partition("=")[2].strip()
        for part in value.split(";")[1:]
        if part.strip().partition("=")[0].strip().lower() == "profile"
    ]
    if not profiles:
        return None
    if profiles not in ([PROFILE], [f'"{PROFILE}"']):
        raise ValueError("unsupported watch admission profile")
    return TERMINAL_ADMISSION
