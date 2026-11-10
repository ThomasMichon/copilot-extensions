"""Capability-selected review requests; acceptance is not a review verdict."""
from __future__ import annotations

import json
import subprocess

from .native_command import run_command


def failure(repo, number, error, *, provider=None, status="not-attempted"):
    """Keep local refusals and post-dispatch uncertainty on the owner's contract."""
    return {
        "schema": "agent-worktrees.pr-review-request", "version": 1,
        "repo": repo, "number": number, "provider": provider, "ok": False,
        "supported": status == "unknown", "requested": False, "reviewer": "",
        "actions": {"review_request": {"status": status}}, "error": error,
    }


def run(args, prefix):
    """Negotiate the selected owner, invoke once and validate its exact result."""
    from .native_author import capability, config_options

    if args.number < 1:
        raise ValueError("--number must be positive")
    support = capability(args, prefix, "reviewer-request")
    argv = [
        *prefix, "pr-author-nudge", f"--repo={args.repo}", "--json",
        f"--number={args.number}", *config_options(args),
    ]
    try:
        response = run_command(argv, timeout=90.0)
        body = json.loads(response.stdout)
        _validate(body, args.repo, args.number, support["provider"], response.returncode)
        return (0 if body["ok"] else response.returncode or 1), body
    except (ValueError, TypeError, OSError, RuntimeError, subprocess.SubprocessError):
        return 1, failure(
            args.repo, args.number,
            "Native reviewer-request response is unconfirmed; observe this PR before another attempt",
            provider=support["provider"], status="unknown",
        )


def _validate(body, repo, number, provider, code):
    if not isinstance(body, dict):
        raise ValueError("Invalid reviewer-request response")
    actions = body.get("actions")
    action = actions.get("review_request") if isinstance(actions, dict) else None
    status = action.get("status") if isinstance(action, dict) else None
    ok = status in ("accepted", "no-op")
    if (
        body.get("schema") != "agent-worktrees.pr-review-request"
        or type(body.get("version")) is not int or body["version"] != 1
        or body.get("repo") != repo or type(body.get("number")) is not int or body["number"] != number
        or status not in ("not-attempted", "no-op", "accepted", "failed", "unknown")
        or not (
            body.get("provider") == provider
            or status == "not-attempted" and "provider" in body and body["provider"] is None
        )
        or type(body.get("ok")) is not bool or body["ok"] != ok
        or bool(code) == ok
        or type(body.get("supported")) is not bool
        or body["supported"] != (status in ("accepted", "failed", "unknown"))
        or type(body.get("requested")) is not bool or body["requested"] != (status == "accepted")
        or not isinstance(body.get("reviewer"), str)
        or status == "accepted" and not body["reviewer"].strip()
        or not ok and (not isinstance(body.get("error"), str) or not body["error"].strip())
        or ok and body.get("error")
    ):
        raise ValueError("Inconsistent reviewer-request response")
