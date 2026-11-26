"""Opt-in workspace resume through the common exact conversation selector."""

from __future__ import annotations

import argparse
import sys
from typing import Any, NoReturn


def selected(args: argparse.Namespace) -> bool:
    enabled = bool(getattr(args, "bridge", False))
    if not enabled and any(
        getattr(args, name, None) for name in ("all_projects", "machine", "namespace")
    ):
        raise argparse.ArgumentTypeError(
            "--all-projects, --machine and --namespace require --bridge",
        )
    if enabled and (
        getattr(args, "force", False)
        or any(getattr(args, name, None) is not None for name in (
            "worktree_writer_file", "retained_recovery_file",
        ))
    ):
        raise argparse.ArgumentTypeError(
            "--bridge does not support takeover, writer or recovery inputs; "
            "use their existing resume contracts without --bridge",
        )
    return enabled


def resume(args: argparse.Namespace, target: str, *, worktree_only: bool) -> None:
    from . import __main__ as core
    from .bridge_listing_cli import _scope
    from .client import BridgeClientError, BridgeConnectionError
    from .hosting_admission_cli import configure_client
    from .resume_cli_contract import confirmed_resume

    identity: dict[str, Any] = {"requested_target": target}

    def fail(reason: str, detail: Any) -> NoReturn:
        if getattr(args, "json", False):
            core._json_out({**identity, "reason": reason, "error": detail})
        else:
            print(f"[FAIL] Could not resume {target}: {detail}", file=sys.stderr)
        raise SystemExit(1)

    try:
        scope = _scope(args)
        client = core._get_client(ensure=False, propagate_errors=True)
        configure_client(client, args)
        row = client.resolve_bridge(target, local_only=True, **scope)["bridge"]
        worktree_id, native = row.get("worktree_id"), row.get("session_id")
        if (
            not isinstance(worktree_id, str) or not worktree_id
            or not isinstance(native, str) or not native
            or worktree_only and worktree_id != target
        ):
            fail(
                "bridge_resume_unavailable",
                "The selected conversation has no exact workspace/native resume owner",
            )
        identity.update(worktree_id=worktree_id, native_session_id=native)
        cli = bool(getattr(args, "cli", False))
        result = client.resume_worktree_exact(
            worktree_id, native, mode="cli" if cli else "acp",
            request_timeout=core._startup_request_timeout(resume=True, fresh_fallback=True),
        )
        if not confirmed_resume(result, worktree_id, cli=cli) or result["acp_session_id"] != native:
            fail(
                "resume_response_unconfirmed",
                "Resume did not confirm the selected native identity; "
                "the request may have started and was not replayed",
            )
    except BridgeClientError as exc:
        detail = exc.detail
        reason = (
            detail.get("reason") or detail.get("code")
            if isinstance(detail, dict) else None
        )
        fail(reason or "bridge_resume_unavailable", detail)
    except BridgeConnectionError as exc:
        fail("bridge_resume_unavailable", str(exc))
    except ValueError as exc:
        fail("bridge_resume_unavailable", str(exc))
    if getattr(args, "json", False):
        core._json_out({**result, "verb": "loaded"})
    else:
        print(
            f"[OK] Worktree {worktree_id} loaded as session "
            f"{result['session_id']} ({result.get('status', '')})",
        )


def register(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--bridge", action="store_true",
        help="Resolve one local-provider workspace conversation and pin its native identity",
    )
    parser.add_argument("--all-projects", action="store_true", help="Select across projects")
    parser.add_argument("--machine", help="Filter the conversation's machine; requires --bridge")
    parser.add_argument("--namespace", help="Filter the conversation's namespace; requires --bridge")
