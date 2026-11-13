"""Credential-free unlock-warming status and conservative client rendering."""

from __future__ import annotations

from dataclasses import dataclass

CONTRACT = "agent-vault.cache-warm"
VERSION = 1
STATES = frozenset(
    {
        "disabled",
        "idle",
        "not-queued",
        "queued",
        "running",
        "partial",
        "complete",
        "failed",
        "cancelled",
    }
)
REASONS = frozenset(
    {
        "service-closing",
        "configuration-unavailable",
        "no-cache-sources",
        "unknown-unlock-generation",
        "capacity",
        "deadline",
        "cache-disabled",
        "unlock-generation-changed",
        "cache-unavailable",
        "credential-custody-busy",
        "locked",
    }
)


@dataclass
class WarmState:
    state: str
    reason: str = ""
    selected: int = 0
    cached: int = 0
    failed: int = 0
    failed_sources: int = 0
    truncated: bool = False

    def describe(self) -> dict[str, object]:
        return {
            "contract": CONTRACT,
            "version": VERSION,
            "state": self.state,
            "reason": self.reason,
            "selected": self.selected,
            "cached": self.cached,
            "failed": self.failed,
            "remaining": self.selected - self.cached - self.failed,
            "failed_sources": self.failed_sources,
            "truncated": self.truncated,
        }


def format_cache_warm(value: object) -> str:
    if not isinstance(value, dict) or value.get("contract") != CONTRACT:
        return "unknown"
    if type(value.get("version")) is not int or value["version"] != VERSION:
        return "unknown"
    state, reason = value.get("state"), value.get("reason", "")
    if not isinstance(state, str) or state not in STATES or not isinstance(reason, str):
        return "unknown"
    counts = [
        value.get(name) for name in ("selected", "cached", "failed", "remaining", "failed_sources")
    ]
    if any(type(number) is not int or number < 0 for number in counts):
        return "unknown"
    selected, cached, failed, remaining, sources = counts
    truncated = value.get("truncated")
    if type(truncated) is not bool or selected != cached + failed + remaining:
        return "unknown"
    if state == "complete" and (cached != selected or failed or sources or truncated or reason):
        return "unknown"
    detail = f"{state} ({cached}/{selected} cached; {failed} failed; {sources} source failures)"
    if reason in REASONS:
        detail += f", {reason}"
    if truncated:
        detail += ", target list truncated"
    return detail
