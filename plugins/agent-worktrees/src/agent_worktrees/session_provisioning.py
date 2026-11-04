"""Optional session provisioning preview through the canonical planner."""

from __future__ import annotations

from pathlib import Path


def preview_services(cwd: str) -> str | None:
    """Return requested service names, or None when no reconciliation is needed."""
    from . import reconcile

    plan = reconcile.build_plan(Path(cwd), save=False)
    if plan.get("action") != "reconcile":
        return None
    return ", ".join(
        dict.fromkeys(
            str(item.get("service"))
            for item in plan.get("updates", [])
            if isinstance(item, dict) and item.get("service")
        )
    )
