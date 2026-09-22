"""CodeSpace preparation and claims for the shared native control channel."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys

from ssh_manager.native_channel import InputPump, emit_progress

from . import execution_claims

CAPABILITY = "codespace-native-transport-v1"


async def serve(args, manager, ssh_config, relay_env: str, *, reconnect=None) -> int:
    from ssh_manager.native_channel import serve as serve_channel
    from .lease import _lease_lock, CoordinationRejected

    identity = (args.execution_id, args.generation)

    def require_owner():
        with _lease_lock():
            row = execution_claims._read().get(args.name)
            if row is None or row["mode"] != "native":
                raise CoordinationRejected("native execution reservation is unavailable")
            execution_claims.assert_access(args.name, identity, args.effort)

    async def retire(result):
        from .sessions import sync_codespace_sessions

        result["recovery"] = {"ok": False, "detail": "session recovery pending; venue preserved"}
        execution_claims.release(args.name, args.effort, identity, proof=result)
        args.native_retirement_recorded = True
        args.native_retired = not result.get("noLaunch", False)
        try:
            result["recovery"] = (
                {"ok": True, "skipped": True, "detail": "native execution never launched"}
                if result.get("noLaunch") else await asyncio.to_thread(sync_codespace_sessions, args.name)
            )
        except Exception:
            result["recovery"] = {"ok": False, "detail": "session recovery remains pending; venue preserved"}
        execution_claims.update_recovery(args.name, args.effort, identity, result["recovery"])

    return await serve_channel(
        args, manager, ssh_config, relay_env, require_owner=require_owner,
        mark_launch=lambda: execution_claims.mark(args.name, args.effort, identity, launchRequested=True),
        retire=retire, output=sys.stdout, reconnect=reconnect,
    )


def command(args) -> int:
    from . import __main__ as cli
    from . import gh_account, lifecycle
    from .lease import ClaimConflict
    from ssh_manager import TargetBusyError

    if os.environ.get("AGENT_CODESPACES_DISABLE_CLAIM"):
        raise RuntimeError("native hosting requires provider claims; claim bypass is not supported")
    try:
        execution_claims.reserve(args.name, args.effort, args.execution_id, args.generation, "native")
    except (ClaimConflict, TargetBusyError):
        print(json.dumps({"event": "rejected", "code": "venue_busy"}), flush=True)
        return 75
    args.native_input = InputPump()
    print(json.dumps({"event": "reserved", "executionId": args.execution_id, "generation": args.generation}), flush=True)
    args.native_progress = lambda phase, status="started": emit_progress(args, phase, status)
    args.native_progress("local-config")
    account = lifecycle.account_for_codespace(args.name)
    if account:
        token = gh_account.token_for_account(account)
        if not token:
            raise RuntimeError("Native CodeSpace account token is unavailable; refusing ambient authentication")
        os.environ["GH_TOKEN"] = token
        os.environ.pop("GITHUB_TOKEN", None)
        # The native session launches the Copilot CLI, which needs a
        # Copilot-entitled GitHub bearer in its own environment. Reuse the same
        # account-bound host token (never an ambient one) and hand it to the
        # launch-prelude builder so it is injected as COPILOT_GITHUB_TOKEN over
        # the secure stdin-carried prelude -- headless auth with no device-code.
        args.copilot_token = token
    if sys.platform != "win32":
        def interrupted(*_):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, interrupted)
    try:
        return cli._cmd_ssh(args)
    except asyncio.CancelledError:
        return 0
    finally:
        if getattr(args, "native_cleanup_complete", False) and not getattr(args, "native_retirement_recorded", False):
            execution_claims.mark(
                args.name, args.effort, (args.execution_id, args.generation), infrastructureStopped=True,
            )
