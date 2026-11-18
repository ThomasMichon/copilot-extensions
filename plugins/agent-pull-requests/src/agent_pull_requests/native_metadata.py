"""Validate the selected GitHub metadata owner's per-action evidence."""

from __future__ import annotations

from typing import Any


def failure(args: Any, error: str) -> dict[str, Any]:
    """Retain the selected PR after an unavailable or invalid owner response."""
    actions = {}
    if args.title is not None:
        actions["title"] = {"status": "unknown"}
    if args.body is not None or args.body_file:
        actions["description"] = {"status": "unknown"}
    return {
        "schema": "agent-worktrees.pr-metadata",
        "version": 1,
        "provider": "github",
        "repo": args.repo,
        "number": args.number,
        "publication_retained": True,
        "ok": False,
        "actions": actions,
        "error": error,
    }


def validate_github(args: Any, payload: dict[str, Any], returncode: int) -> None:
    """Keep malformed or partial native metadata responses unconfirmed."""
    expected = set()
    if args.title is not None:
        expected.add("title")
    if args.body is not None or args.body_file:
        expected.add("description")
    actions = payload.get("actions")
    valid = (
        payload.get("schema") == "agent-worktrees.pr-metadata"
        and type(payload.get("version")) is int
        and payload["version"] == 1
        and payload.get("provider") == "github"
        and payload.get("publication_retained") is True
        and type(payload.get("ok")) is bool
        and isinstance(actions, dict)
        and set(actions) == expected
    )
    if valid and isinstance(actions, dict):
        for action in actions.values():
            if (
                not isinstance(action, dict)
                or action.get("status") not in ("not_attempted", "confirmed", "failed", "unknown")
                or action["status"] == "confirmed"
                and type(action.get("changed")) is not bool
            ):
                valid = False
                break
    if valid and isinstance(actions, dict):
        confirmed = bool(actions) and all(
            action["status"] == "confirmed" for action in actions.values()
        )
        valid = payload["ok"] == confirmed and (
            not returncode and not payload.get("error")
            if confirmed
            else isinstance(payload.get("error"), str) and bool(payload["error"].strip())
        )
    if not valid:
        raise RuntimeError(
            "Native GitHub metadata response is unconfirmed; reconcile this PR before retrying"
        )
