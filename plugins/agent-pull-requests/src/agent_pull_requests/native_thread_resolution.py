"""Optional selected-thread forwarding; no bulk, raw forge or retry fallback."""

from __future__ import annotations

from argparse import Namespace
import json
import re
import subprocess

from .native_command import run_command


def failure(args: Namespace, error: str, *, provider: str | None = None,
            status: str = "not-attempted") -> dict:
    """Preserve the exact request even when owner output is lost."""
    reopening = args.command == "reopen-thread"
    return {
        "schema": "agent-worktrees.pr-thread-reopen" if reopening else "agent-worktrees.pr-thread-resolve",
        "version": 1,
        "repo": args.repo, "number": args.number, "thread": args.thread,
        "operation_id": args.operation_id, "provider": provider, "actor": None, "ok": False,
        "expected_candidate": args.expected_candidate, "expected_thread": args.expected_thread,
        "observed_candidate": None, "observed_thread": None,
        "reason": "owner-unconfirmed" if status == "unknown" else "admission-refused",
        "error": error, "actions": {"reopening" if reopening else "resolution": {"status": status}},
    }


def _validate(body: dict, args: Namespace, provider: str, code: int) -> None:
    if not isinstance(body, dict):
        raise ValueError("Invalid selected-thread response")
    reopening = args.command == "reopen-thread"
    actions = body.get("actions")
    action = actions.get("reopening" if reopening else "resolution") if isinstance(actions, dict) else None
    status = action.get("status") if isinstance(action, dict) else None
    confirmed = status == "confirmed"
    if (
        body.get("schema") != (
            "agent-worktrees.pr-thread-reopen" if reopening else "agent-worktrees.pr-thread-resolve"
        )
        or type(body.get("version")) is not int or body["version"] != 1
        or body.get("repo") != args.repo
        or any(type(body.get(key)) is not int or body[key] != getattr(args, key)
               for key in ("number", "thread"))
        or any(body.get(key) != getattr(args, key) for key in (
            "operation_id", "expected_candidate", "expected_thread",
        ))
        or status not in ("not-attempted", "confirmed", "failed", "unknown")
        or not (body.get("provider") == provider or status == "not-attempted"
                and "provider" in body and body["provider"] is None)
        or type(body.get("ok")) is not bool or body["ok"] != confirmed or bool(code) == confirmed
        or not isinstance(body.get("reason"), str) or not body["reason"]
        or confirmed and body.get("error")
        or not confirmed and (not isinstance(body.get("error"), str) or not body["error"].strip())
    ):
        raise ValueError("Inconsistent selected-thread response")
    if confirmed:
        candidate, thread = body.get("observed_candidate"), body.get("observed_thread")
        if (
            not isinstance(body.get("actor"), str) or not body["actor"].strip()
            or not isinstance(candidate, dict) or candidate.get("fingerprint") != args.expected_candidate
            or not isinstance(thread, dict) or type(thread.get("id")) is not int
            or thread["id"] != args.thread or type(thread.get("status")) not in (int, str)
            or thread.get("status") not in (("active", 1) if reopening else ("closed", 4))
            or not isinstance(thread.get("fingerprint"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", thread["fingerprint"]) is None
        ):
            raise ValueError("Selected-thread confirmation lacks candidate, actor or readback evidence")


def run(args: Namespace, prefix: list[str]) -> tuple[int, dict]:
    """Negotiate the selected ADO owner, dispatch once and preserve its envelope."""
    from .native_author import capability, config_options

    if any(type(value) is not int or not 0 < value <= 2**63 - 1
           for value in (args.number, args.thread)):
        raise ValueError("Native PR/thread IDs must be positive")
    if (
        re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", args.operation_id) is None
        or any(re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
               for value in (args.expected_candidate, args.expected_thread))
    ):
        raise ValueError("Exact candidate/thread fingerprints and a bounded operation ID are required")
    reopening = args.command == "reopen-thread"
    operation = "thread-reopen" if reopening else "thread-resolve"
    support = capability(args, prefix, operation)
    argv = [
        *prefix, f"pr-{operation}", f"--repo={args.repo}", "--json",
        f"--number={args.number}", f"--thread={args.thread}",
        f"--expected-candidate={args.expected_candidate}", f"--expected-thread={args.expected_thread}",
        f"--operation-id={args.operation_id}", *config_options(args),
    ]
    body = None
    try:
        response = run_command(argv, timeout=90.0)
        body = json.loads(response.stdout)
        _validate(body, args, support["provider"], response.returncode)
        return response.returncode, body
    except (ValueError, TypeError, OSError, RuntimeError, subprocess.SubprocessError, UnicodeError):
        result = failure(
            args, (
                "Selected native reopening response is unconfirmed; retain this operation ID, do not replay"
                if reopening else
                "Selected native resolution response is unconfirmed; retain this operation ID, do not replay"
            ),
            provider=support["provider"], status="unknown",
        )
        if isinstance(body, dict) and all(body.get(key) == getattr(args, key) for key in (
            "repo", "number", "thread", "operation_id", "expected_candidate", "expected_thread",
        )):
            result["owner_observation"] = {
                key: body.get(key) for key in ("actor", "observed_candidate", "observed_thread", "reason", "error")
            }
        return 1, result
