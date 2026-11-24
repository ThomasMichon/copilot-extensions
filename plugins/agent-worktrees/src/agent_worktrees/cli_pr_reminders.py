"""Optional PR reminders shared by CLI compatibility surfaces."""

from __future__ import annotations

import sys
from typing import Any

from . import config as cfg
from .pr_contract import PRFlowProfile, PRReminder


def reminder_for(
    config: cfg.Config,
    verb: str,
    *,
    ok: bool = True,
    state: str = "",
    reason: str = "",
    flow: PRFlowProfile | None = None,
) -> PRReminder | None:
    """Build this repo's stay-on-rails PR reminder for ``verb`` (or ``None``)."""
    from . import pr_config
    from . import pr_contract as pc

    try:
        resolved_flow = flow or pr_config._pr_flow_profile(config.default_repo)
        return pc.pr_reminder(resolved_flow, verb, state, ok=ok, reason=reason)
    except Exception:
        return None


def emit(
    reminder: PRReminder | None, *, use_json: bool, result: dict[str, Any] | None = None,
) -> None:
    """Surface a PR reminder."""
    if reminder is None:
        return
    if use_json:
        if result is not None:
            result["reminder"] = reminder.as_dict()
    else:
        print(reminder.text(), file=sys.stderr)
