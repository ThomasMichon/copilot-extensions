"""Bounded reviewer-read diagnostics, separate from delivery and authority."""

from __future__ import annotations

from collections.abc import Callable
import json
import logging
import re
from typing import Any

LOGGER = "agent_pull_requests.reviewer_reads"
_LOG = logging.getLogger(LOGGER)
_LIMIT = 8
_REASONS = frozenset({
    "activity-unavailable", "candidate-unavailable", "author-unavailable",
    "review-activity-unavailable", "review-dismissed", "reviewed-base-unknown",
    "review-order-unavailable", "evidence-incomplete-or-stale", "selection-unknown",
    "observation-behind-feedback", "candidate-cursor-mismatch",
})


def _integer(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def _hex(value: object, lengths: tuple[int, ...] = (64,)) -> str | None:
    return value if isinstance(value, str) and len(value) in lengths and re.fullmatch(
        r"[a-fA-F0-9]+", value,
    ) else None


def _reviewer(value: object) -> str | None:
    return value if isinstance(value, str) and len(value) <= 128 and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[bot\])?", value,
    ) else None


def _summary(page: dict[str, Any]) -> dict[str, Any]:
    inventory = page["inventory"]
    now, observed = _integer(page["now"]), _integer(inventory.get("observed_at"))
    actions, unavailable = page["next_actions"], page["unavailable"]
    return {
        "projection_cursor": _hex(page["cursor"]), "event_cursor": _integer(page["event_cursor"]),
        "inventory_outcome": inventory["outcome"] if inventory["outcome"] in {
            "complete", "partial", "unavailable", "unknown",
        } else "unknown",
        "inventory_age_seconds": now - observed if (
            now is not None and observed is not None and now >= observed
        ) else None,
        "actions": [{
            "pr": _integer(row["pr"]),
            "action": row["action"] if row["action"] in {
                "initial_verdict", "new_verdict", "rebase",
            } else "unknown",
            "reason": row["reason"] if row["reason"] in {
                "author_create", "author_update", "stale_branch",
            } else "unknown",
            "candidate": {name: _hex(row["candidate"].get(name), (40, 64))
                          for name in ("head", "base")},
        } for row in actions[:_LIMIT]],
        "unavailable": [{
            "pr": _integer(row["pr"]),
            "reason": row["reason"] if row["reason"] in _REASONS else "unknown",
        } for row in unavailable[:_LIMIT]],
        "action_count": len(actions), "unavailable_count": len(unavailable),
        "more": page["more"] is True,
        "truncated": len(actions) > _LIMIT or len(unavailable) > _LIMIT,
    }


def read(
    operation: Callable[[], dict[str, Any]], *, target: str, repository_key: str,
    payload: dict[str, Any], stream: bool = False,
) -> dict[str, Any]:
    """Observe one admitted read after transaction retries, never another query."""
    record: dict[str, Any] = {
        "schema": "agent-pull-requests.reviewer-read", "version": 1,
        "target": target, "repository_key": repository_key,
        "reviewer": _reviewer(payload.get("reviewer")),
        "view": "stream" if stream else "next-actions",
        "requested_cursor": _hex(payload.get("cursor")),
        "projection_cursor": None, "event_cursor": None, "inventory_age_seconds": None,
        "outcome": "failed", "error": "read-unavailable",
    }
    try:
        result = operation()
    except Exception:
        raise
    else:
        record.update(outcome="projected", error=None)
        try:
            page = next(value["view"] for name, value in result["frames"] if name == "next-actions") if (
                stream
            ) else result
            record.update(_summary(page))
        except Exception:
            record["error"] = "trace-summary-unavailable"
        return result
    finally:
        try:
            _LOG.info("%s", json.dumps(record, allow_nan=False, separators=(",", ":")))
        except Exception:
            pass
