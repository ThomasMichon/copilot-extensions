"""Observed publication and retirement outcomes for daemon cutover."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .routing import Endpoint


@dataclass
class CutoverResult:
    """Outcome of a cutover attempt."""

    ok: bool
    new_port: int | None = None
    old_endpoint: Endpoint | None = None
    steps: list[str] = field(default_factory=list)
    rolled_back: bool = False
    committed: bool = False
    error: str | None = None
    drain: dict[str, Any] | None = None
    serving_endpoint: Endpoint | None = None
    retirement_requested: bool = False
    retirement_confirmed: bool = False
    retirement_pending: bool = False
    conditional: bool = False
    publication_uncertain: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = {
            "ok": self.ok,
            "new_port": self.new_port,
            "old_port": self.old_endpoint.port if self.old_endpoint else None,
            "steps": self.steps,
            "rolled_back": self.rolled_back,
            "committed": self.committed,
            "error": self.error,
            "drain": self.drain,
        }
        if self.conditional:
            data["retirement_confirmed"] = self.retirement_confirmed
            data["serving_endpoint"] = (
                self.serving_endpoint.to_dict() if self.serving_endpoint else None
            )
            data["retirement_requested"] = self.retirement_requested
        if self.publication_uncertain:
            data["publication_uncertain"] = True
        if self.retirement_pending:
            data["retirement_pending"] = True
        return data
