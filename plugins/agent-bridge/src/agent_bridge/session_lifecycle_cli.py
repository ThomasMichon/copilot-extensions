"""Session lifecycle, handoff, and ACP-agent commands for ``agent-bridge``."""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

from agent_procutil import no_window_kwargs

from . import _peer_launch

_legacy_runtime_modules: dict[Path, ModuleType] = {}
_legacy_runtime_lock = threading.Lock()


def _reset_legacy_runtime_lock() -> None:
    global _legacy_runtime_lock
    _legacy_runtime_lock = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_legacy_runtime_lock)


def _core():
    from . import __main__ as core

    return core


def _cmd_stop(args: argparse.Namespace) -> None:
    from .client import BridgeClientError

    client = _core()._get_client()
    if getattr(args, "grace", None) is not None or getattr(args, "json", False):
        _cmd_stop_phased(client, args)
        return
    try:
        client.stop_session(
            args.session_id,
            force=getattr(args, "force", False),
            reap_host=getattr(args, "reap_host", False),
        )
    except BridgeClientError as exc:
        # Mirror _cmd_end's own 404 handling (confirmed asymmetric --
        # copilot-extensions#<TODO-issue>): a session already gone is this
        # command's success case, not a failure -- a caller like
        # agent-dispatch's cool_dormant_bodies() treats a nonzero exit as
        # "could not stop it, retry later" with no way to tell "genuinely
        # still busy" apart from "already gone, nothing to stop", which
        # left a resume-failed reservation retrying this exact call every
        # 60s forever since there was never anything left to successfully
        # stop.
        if exc.status == 404:
            print(f"[OK] Session {args.session_id} already stopped")
            return
        print(f"[FAIL] Could not stop session {args.session_id}: {exc.detail}")
        sys.exit(1)
    print(f"[OK] Session {args.session_id} stopped")


def _grace_seconds(value: str) -> float:
    """A finite, non-negative ``--grace`` (``nan``/``inf`` would never time out)."""
    import math

    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number of seconds: {value!r}") from None
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError("must be a finite number of seconds, 0 or more")
    return seconds


def _cmd_stop_phased(client, args: argparse.Namespace) -> None:
    """``stop --grace``/``--json``: the cooperative, phased stop (session_stop)."""
    from .session_stop import STOP_BUSY_EXIT, STOP_UNCONFIRMED_EXIT, STOP_UNSUPPORTED_EXIT, run_stop

    as_json = getattr(args, "json", False)
    result = run_stop(
        client, args.session_id, grace=getattr(args, "grace", None),
        force=getattr(args, "force", False), reap_host=getattr(args, "reap_host", False),
        on_phase=None if as_json else (lambda name: print(f"[..] {args.session_id}: {name}")),
    )
    if as_json:
        print(json.dumps(result, indent=2))
    outcome = result["outcome"]
    if outcome in ("stopped", "already_stopped"):
        if not as_json:
            if result["acknowledged"] is False:
                print("    (the agent didn't wind down within the grace period; stopped anyway)")
            print(f"[OK] Session {args.session_id} {outcome.replace('_', ' ')}")
        return
    if not as_json:
        print(f"[FAIL] Could not stop session {args.session_id}: {result.get('error')}", file=sys.stderr)
    sys.exit({"refused_busy": STOP_BUSY_EXIT, "refused_unsupported": STOP_UNSUPPORTED_EXIT}.get(
        outcome, STOP_UNCONFIRMED_EXIT))


def _cmd_end(args: argparse.Namespace) -> None:
    from .client import BridgeClientError

    client = _core()._get_client()
    try:
        end_kwargs = {"force": getattr(args, "force", False)}
        if getattr(args, "if_idle", False):
            end_kwargs["if_idle"] = True
        client.end_session(args.session_id, **end_kwargs)
    except BridgeClientError as exc:
        if exc.status == 404:
            print(f"[OK] Session {args.session_id} already ended")
            return
        print(f"[FAIL] Could not end session {args.session_id}: {exc.detail}")
        sys.exit(1)
    print(f"[OK] Session {args.session_id} ended")


def _cmd_resume(args: argparse.Namespace) -> None:
    from .resume_handoff_cli import run_resume
    from .hosting_admission_cli import configure_client
    from .resume_cli_contract import resume_target

    try:
        target, worktree_only = resume_target(args)
        writer_path = getattr(args, "worktree_writer_file", None)
        recovery_path = getattr(args, "retained_recovery_file", None)
        if (writer_path is not None or recovery_path is not None) and (
            worktree_only or getattr(args, "force", False) or getattr(args, "cli", False)
            or not getattr(args, "strict", False)
        ):
            raise argparse.ArgumentTypeError(
                "--worktree-writer-file requires --strict resume of an exact ACP session ID",
            )
        if recovery_path is not None and writer_path is not None:
            raise argparse.ArgumentTypeError("Retained recovery includes its writer; supply only one input file")
    except argparse.ArgumentTypeError as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        raise SystemExit(2) from error
    from .workspace_creation_cli import read_writer_requirement

    writer = read_writer_requirement(writer_path) if writer_path is not None else None
    recovery = None
    if recovery_path is not None:
        from .models import ResumeSessionRequest

        try:
            if recovery_path == "-":
                raw = sys.stdin.read(16385)
            else:
                from pathlib import Path
                with Path(recovery_path).open(encoding="utf-8") as stream:
                    raw = stream.read(16385)
            if len(raw) > 16384:
                raise ValueError("Recovery input exceeds its budget")
            body = ResumeSessionRequest.model_validate_json(raw)
            if body.worktree_writer is None or body.retained_recovery is None:
                raise ValueError("Recovery requires its original writer and operation")
            writer, recovery = body.worktree_writer.model_dump(), body.retained_recovery.model_dump()
        except (OSError, ValueError) as error:
            print(f"[FAIL] Invalid retained recovery request: {error}", file=sys.stderr)
            raise SystemExit(2) from error
    core = _core()
    client = core._get_client(**({"ensure": False} if recovery is not None else {}))
    configure_client(client, args)
    run_resume(
        client=client,
        target=target,
        worktree_only=worktree_only,
        worktree_writer=writer,
        retained_recovery=recovery,
        reclaim=bool(getattr(args, "force", False)),
        strict=bool(getattr(args, "strict", False)),
        cli=bool(getattr(args, "cli", False)),
        as_json=bool(getattr(args, "json", False)),
        json_out=core._json_out,
        match_agents=core._match_agents,
        startup_request_timeout=core._startup_request_timeout,
    )


def _cmd_handoff(args: argparse.Namespace) -> None:
    from .resume_handoff_cli import run_handoff

    core = _core()
    run_handoff(
        client=core._get_client(),
        target=args.session_id,
        reason=getattr(args, "reason", None),
        seed=not getattr(args, "no_seed", False),
        match_agents=core._match_agents,
    )


def _cmd_handoff_request(args: argparse.Namespace) -> None:
    from .client import BridgeClientError

    core = _core()
    client = core._get_client()
    worktree_id = args.worktree_id
    session_id = args.session_id
    try:
        successor = client.handoff_request(
            worktree_id,
            session_id=session_id,
            seed_text=args.seed,
            handoff_token=args.handoff_token,
        )
    except BridgeClientError as exc:
        if exc.status == 404:
            print(
                f"[SKIP] No current session for worktree {worktree_id} matches "
                f"{session_id}; agent-bridge left the worktree untouched.",
                file=sys.stderr,
            )
            sys.exit(1)
        if exc.status == 409:
            print(f"[FAIL] Cannot hand off worktree {worktree_id}: {exc.detail}", file=sys.stderr)
            sys.exit(1)
        print(f"[FAIL] Could not request handoff for worktree {worktree_id}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    payload = {
        "accepted": True,
        "worktree_id": worktree_id,
        "requested_session_id": session_id,
        "handoff_token": args.handoff_token,
        "successor_session_id": successor.get("session_id"),
        "successor_acp_session_id": successor.get("acp_session_id"),
        "successor_status": successor.get("status"),
    }
    if args.json:
        core._json_out(payload)
        return
    print(
        f"[OK] agent-bridge accepted handoff request for worktree {worktree_id} "
        f"session {session_id} -> successor "
        f"{payload['successor_session_id'] or '(unknown)'} "
        f"({payload['successor_status'] or 'unknown'})"
    )


def _agent_bridge_owner_root() -> Path:
    return _core().install_dir()


def _agent_bridge_installation_context() -> str:
    explicit_context = os.environ.get(_peer_launch.CONTEXT_ENV, "")
    own_root = _agent_bridge_owner_root()
    if not explicit_context and (
        own_root.name == "agent-bridge"
        and own_root.parent.name == "plugins"
        and own_root.parent.parent.parent.name == "marketplaces"
    ):
        explicit_context = str(own_root / "install.json")
    return explicit_context


def _agent_worktrees_launch_prefix(*, allow_path_fallback: bool = True) -> list[str] | None:
    from .acquisition_worktree_writer import current

    writer = current()
    if writer is not None:
        return list(writer.prefix)
    selection = _agent_worktrees_selection(allow_path_fallback=allow_path_fallback)
    return selection[0] if selection else None


def _agent_worktrees_selection(
    *, allow_path_fallback: bool = True,
) -> tuple[list[str], Path | None] | None:
    selection = _agent_worktrees_runtime_selection(allow_path_fallback=allow_path_fallback)
    return (selection[0], selection[1]) if selection else None


def _agent_worktrees_runtime_selection(
    *, allow_path_fallback: bool = True,
) -> tuple[list[str], Path | None, Path | None] | None:
    """Resolve execution and observation together; PATH has no attributable root."""
    core = _core()
    explicit_context = _agent_bridge_installation_context()
    own_root = _agent_bridge_owner_root()
    if not explicit_context:
        selection = _legacy_worktrees_selection(own_root)
        if selection:
            return selection
        if not allow_path_fallback:
            return None
        exe = shutil.which("agent-worktrees")  # marketplace-isolation: allow legacy-compatibility
        return ([exe], None, None) if exe else None
    try:
        own = core._peer_launch.validate_owner("agent-bridge", _agent_bridge_owner_root(), explicit_context)
    except (OSError, ValueError, ImportError) as error:
        raise core._peer_launch.ContextRefused(f"agent-bridge installation context refused: {error}") from error
    peer_root = Path(own["cellRoot"]) / "plugins" / "agent-worktrees"
    if not peer_root.exists() and not peer_root.is_symlink():
        return None
    try:
        prefix = core._peer_launch.launch_prefix("agent-bridge", Path(own["pluginRoot"]), explicit_context, "agent-worktrees")
        return (prefix, peer_root, None) if prefix else None
    except (OSError, ValueError, ImportError) as error:
        raise core._peer_launch.ContextRefused(f"same-cell agent-worktrees resolution failed: {error}") from error


def _agent_worktrees_pinned_selection() -> tuple[tuple[str, ...], Path, Path]:
    """Retain an attributable command for explicit writer qualification/use."""
    own_root = _agent_bridge_owner_root()
    context = _agent_bridge_installation_context()
    try:
        if context:
            prefix, python = _peer_launch.pinned_launch_prefix(
                "agent-bridge", own_root, context, "agent-worktrees",
            )
            root = own_root.parent / "agent-worktrees"
        else:
            runtime = _legacy_worktrees_runtime_prefix(own_root)
            if runtime is None:
                raise ValueError("completed worktree owner runtime is unavailable")
            root = _legacy_worktrees_owner_root(own_root)
            python = Path(runtime[0])
            identity = _peer_launch.retained_runtime_identity(root, python, "agent_worktrees")
            prefix = [
                sys.executable, "-I", "-X", "utf8", "-m",
                "agent_bridge.worktree_owner_launch", str(own_root),
                "--pinned-runtime", str(python), "--runtime-identity", identity,
            ]
        return tuple(prefix), root, python
    except (OSError, ValueError, ImportError) as error:
        raise _peer_launch.ContextRefused(f"agent-worktrees pinned selection refused: {error}") from error


def _legacy_worktrees_launch_prefix(own_root: Path) -> list[str] | None:
    selection = _legacy_worktrees_selection(own_root)
    return selection[0] if selection else None


def _legacy_worktrees_selection(
    own_root: Path,
) -> tuple[list[str], Path, Path] | None:
    runtime = _legacy_worktrees_runtime_prefix(own_root)
    if not runtime:
        return None
    return ([
        sys.executable, "-I", "-X", "utf8", "-m",
        "agent_bridge.worktree_owner_launch", str(own_root),
    ], Path.home() / ".agent-worktrees", Path(runtime[0]))  # marketplace-isolation: allow legacy-compatibility


def _legacy_worktrees_owner_root(own_root: Path) -> Path:
    from .install_paths import uses_legacy_install_dir

    if not uses_legacy_install_dir(own_root):
        raise _peer_launch.ContextRefused("non-legacy bridge requires an installation context")
    root = Path.home() / ".agent-worktrees"  # marketplace-isolation: allow legacy-compatibility
    tombstone = root / ".installation-ownership.json"
    if tombstone.exists() or tombstone.is_symlink():
        raise _peer_launch.ContextRefused("legacy worktree owner has installation-cell ownership")
    return root


def _legacy_worktrees_runtime_prefix(own_root: Path) -> list[str] | None:
    """Use the installed resolver, not an interactive shell's executable search."""
    root = _legacy_worktrees_owner_root(own_root)
    resolver = own_root / "scripts" / "versioned_runtime.py"
    if not resolver.is_file():
        return None
    with _legacy_runtime_lock:
        key = resolver.resolve()
        module = _legacy_runtime_modules.get(key)
        if module is None:
            spec = importlib.util.spec_from_file_location("_bridge_versioned_runtime", key)
            if spec is None or spec.loader is None:
                raise _peer_launch.ContextRefused("installed runtime resolver is unavailable")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _legacy_runtime_modules[key] = module
    python = module.resolve_python(root)
    return [str(python), "-I", "-X", "utf8", "-m", "agent_worktrees"] if python else None


def _agent_worktrees_environment(inherited: dict[str, str] | None = None) -> dict[str, str]:
    """Keep registry and Python routing with the resolved owner installation."""
    excluded = {
        "AGENT_HOME", "AGENT_RT_ROOT", "AGENT_RT_PY", "COPILOT_PLUGIN_ROOT",
        "VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "__PYVENV_LAUNCHER__",
    }
    source = os.environ if inherited is None else inherited
    return {key: value for key, value in source.items() if key.upper() not in excluded}


def _cmd_handoff_check(args: argparse.Namespace) -> None:
    core = _core()
    try:
        prefix = core._agent_worktrees_launch_prefix()
    except core._peer_launch.ContextRefused as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        sys.exit(1)
    if not prefix:
        if _agent_bridge_installation_context():
            print("[FAIL] agent-worktrees is not installed in this installation cell; cannot check handoffs.", file=sys.stderr)
        else:
            print("[FAIL] agent-worktrees has no installed runtime or compatible command; cannot check handoffs.", file=sys.stderr)
        sys.exit(1)
    argv = [*prefix, "handoffs-check", "--json"]
    argv += ["--worktree-id", args.worktree_id] if args.worktree_id else ["--all"]
    if args.execute:
        argv.append("--execute")
    child_env = _agent_worktrees_environment()
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=child_env, **no_window_kwargs())
    except Exception as exc:
        print(f"[FAIL] could not run agent-worktrees handoffs-check: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        payload = json.loads(result.stdout or "{}")
    except Exception:
        payload = {"error": "unparseable agent-worktrees output", "raw": result.stdout, "stderr": result.stderr}
    if result.returncode != 0 and "findings" not in payload and not payload.get("error"):
        payload = {"error": f"agent-worktrees handoffs-check exited {result.returncode}", "stderr": result.stderr}
    if payload.get("error"):
        if args.json:
            core._json_out(payload)
        else:
            print(f"[FAIL] handoff-check: {payload['error']}", file=sys.stderr)
            if payload.get("stderr"):
                print(payload["stderr"], file=sys.stderr)
        sys.exit(1)
    if args.json:
        core._json_out(payload)
    elif not payload.get("findings"):
        print("[OK] handoff-check: no stalled predecessor retirements found.")
    else:
        for finding in payload["findings"]:
            wt = finding.get("worktree_id")
            pred = finding.get("predecessor_session_id")
            pane = finding.get("retire_pane")
            if not finding.get("executed"):
                print(f"  {wt}: predecessor {pred} still alive (pane {pane})")
            elif finding.get("retired"):
                print(f"[OK] {wt}: retired predecessor {pred} (pane {pane})")
            else:
                print(f"[FAIL] {wt}: could not retire predecessor {pred}", file=sys.stderr)
    sys.exit(result.returncode)


def _cmd_questions(args: argparse.Namespace) -> None:
    from .client import BridgeClientError

    core = _core()
    try:
        client = core._get_client(ensure=False)
        audit = getattr(args, "inspection_id", None)
        if audit and getattr(args, "via", "native") != "mux":
            print("[FAIL] --inspection-id requires --via mux.", file=sys.stderr)
            raise SystemExit(2)
        result = (
            (client.mux_question_answer(args.session_id, audit) if audit else client.mux_questions(args.session_id))
            if getattr(args, "via", "native") == "mux"
            else client.live_questions(args.session_id)
        )
    except BridgeClientError as exc:
        print(f"[FAIL] {exc.detail}", file=sys.stderr)
        raise SystemExit(1) from exc
    core._json_out(result)


def _cmd_answer(args: argparse.Namespace) -> None:
    from .client import BridgeClientError

    if getattr(args, "via", "native") == "mux":
        from .mux_question_cli import answer

        return answer(args)
    if any(getattr(args, name, None) is not None for name in ("inspection_id", "option", "text")):
        print("[FAIL] mux answer selectors require --via mux.", file=sys.stderr)
        raise SystemExit(2)
    core = _core()
    generation = getattr(args, "generation", None)
    client = core._get_client(**({"ensure": False} if generation is not None else {}))
    caller_id = core._caller_id_for(args)
    sid = args.session_id
    option_id = getattr(args, "option_id", None)
    if generation is not None and (not args.request_id or option_id is not None or args.tool_call_id):
        print("[FAIL] --generation requires --request-id and no permission/tool-call selector.", file=sys.stderr)
        raise SystemExit(2)
    if option_id is not None:
        request_id = getattr(args, "request_id", None)
        if (
            not request_id or args.decline or args.cancel
            or args.fields or args.content_json or args.tool_call_id is not None
        ):
            print("[FAIL] A permission option requires --request-id and no form/decline/cancel arguments.", file=sys.stderr)
            raise SystemExit(2)
        try:
            client.answer_permission(sid, request_id, option_id)
        except BridgeClientError as exc:
            print(f"[FAIL] {exc.detail}", file=sys.stderr)
            raise SystemExit(1)
        print(f"[OK] Submitted correlated permission answer on {sid}.")
        return
    action = "accept"
    if args.decline:
        action = "decline"
    elif args.cancel:
        action = "cancel"

    content: dict[str, Any] = {}
    if action == "accept":
        if args.content_json:
            try:
                content = json.loads(args.content_json)
                if not isinstance(content, dict):
                    raise ValueError("content must be a JSON object")
            except Exception as exc:
                print(f"[FAIL] --json is not a valid JSON object: {exc}", file=sys.stderr)
                sys.exit(1)
        else:
            for pair in args.fields:
                key, sep, value = pair.partition("=")
                if not sep:
                    print(f"[FAIL] --field must be KEY=VALUE (got {pair!r})", file=sys.stderr)
                    sys.exit(1)
                content[key.strip()] = value

    request_id = getattr(args, "request_id", None)
    if generation is not None:
        try:
            snapshot = client.live_questions(sid)
            pending = [
                q for q in snapshot.get("requests", [])
                if q.get("request_id") == request_id
            ] if snapshot.get("generation") == generation else []
            if len(pending) != 1:
                raise BridgeClientError(409, "the exact native question/generation is not pending")
            result = client.answer_live_question(
                sid, request_id, generation, content, family=pending[0]["family"],
                sender=core._live_sender_label(args), action=action,
            )
        except BridgeClientError as exc:
            print(f"[FAIL] {exc.detail}", file=sys.stderr)
            raise SystemExit(1) from exc
        core._json_out(result)
        if result.get("accepted") is not True:
            raise SystemExit(1)
        return
    tool_call_id = args.tool_call_id
    if not tool_call_id:
        try:
            st = client.get_session_status(sid, caller_id=caller_id)
        except BridgeClientError as exc:
            print(f"[FAIL] {exc.detail}", file=sys.stderr)
            sys.exit(1)
        pending = st.get("pending_ask_user") or []
        if request_id is not None:
            pending = [q for q in pending if q.get("request_id") == request_id]
        if not pending:
            print(f"[FAIL] Session {sid} has no parked ask_user to answer.", file=sys.stderr)
            sys.exit(1)
        if len(pending) > 1:
            ids = ", ".join(q.get("tool_call_id", "?") for q in pending)
            print(f"[FAIL] {len(pending)} questions are pending on {sid}; pass --tool-call-id (one of: {ids}).", file=sys.stderr)
            sys.exit(1)
        tool_call_id = pending[0].get("tool_call_id")

    try:
        client.answer_ask_user(
            sid, tool_call_id, content, action=action,
            **({"request_id": request_id} if request_id is not None else {}),
        )
    except BridgeClientError as exc:
        if exc.status == 404:
            print(f"[FAIL] Session {sid} not found", file=sys.stderr)
        else:
            print(f"[FAIL] {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] Answered ask_user ({action}) on {sid}; the agent's turn continues.")


def _cmd_agent(args: argparse.Namespace) -> None:
    import asyncio
    from pathlib import Path

    from . import telemetry
    from .acp_agent import BridgeAgent
    from .agent_registry import build_resolver
    from .config import load_config
    from .db import Database
    from .session_manager import session_manager_from_config

    log = logging.getLogger("agent-bridge")
    cfg = load_config()
    if not telemetry.load_sink_from_config():
        telemetry.load_sink_from_env()
    db_path = Path(cfg.db_path).expanduser()
    db = Database(db_path)
    sm = session_manager_from_config(db, cfg)
    resolver = build_resolver(cfg)
    sm.set_resolver(resolver)
    agent_name = getattr(args, "agent", None)
    if not agent_name:
        print("[FAIL] --agent is required for agent mode", file=sys.stderr)
        sys.exit(1)
    canonical_agent = resolver.canonical_agent_name(agent_name) if resolver else None
    if resolver and canonical_agent is None:
        available = list(resolver.agents.keys())
        print(f"[FAIL] Agent '{agent_name}' not found. Available: {available}", file=sys.stderr)
        sys.exit(1)
    if canonical_agent:
        agent_name = canonical_agent
    bridge_agent = BridgeAgent(sm, resolver=resolver, default_agent=agent_name)
    log.info("Starting ACP agent mode (agent=%s)", agent_name)

    async def _run() -> None:
        from acp import run_agent

        try:
            await run_agent(bridge_agent)
        finally:
            await bridge_agent.cleanup()

    asyncio.run(_run())


def register_session_lifecycle_commands(sub: argparse._SubParsersAction) -> None:
    stop_p = sub.add_parser("stop", help="Stop a session (with --grace, ask the agent to wind down first)")
    stop_p.add_argument("session_id", help="Session ID")
    stop_p.add_argument("--force", action="store_true", help="Tear down even with active background sub-agent tasks (kills them), skipping any --grace notice. Prefer waiting for them to finish.")
    stop_p.add_argument("--grace", type=_grace_seconds, default=None, metavar="SECONDS", help="Cooperative stop: queue a wind-down notice (never interrupting a running turn), wait up to SECONDS for the agent to act on it and settle, then stop the session either way. A notice still queued at the deadline is withdrawn.")
    stop_p.add_argument("--reap-host", action="store_true", help="Also retire the owned Session Host child instead of preserving it for reattachment")
    stop_p.set_defaults(func=_cmd_stop)

    end_p = sub.add_parser("end", help="End (delete) a session")
    end_p.add_argument("session_id", help="Session ID")
    end_p.add_argument("--force", action="store_true", help="Tear down even with active background sub-agent tasks (kills them). Prefer waiting for them to finish.")
    end_p.add_argument("--if-idle", action="store_true", help="End only if the session is still idle or stopped and has no queued prompts")
    end_p.set_defaults(func=_cmd_end)

    resume_p = sub.add_parser("resume", help="Resume a stopped session, or load/take-over a worktree by handle")
    from .hosting_admission_cli import add_argument as add_hosting_argument
    add_hosting_argument(resume_p)
    from .resume_cli_contract import exact_worktree_id
    resume_target = resume_p.add_mutually_exclusive_group(required=True)
    resume_target.add_argument("session_id", nargs="?", metavar="target", help="Session ID (owned ACP session) or worktree handle to load")
    resume_target.add_argument("--worktree-id", type=exact_worktree_id, help="Resume only this exact worktree; never select a session alias or singleton alternative")
    resume_p.add_argument("--force", "--reclaim", dest="force", action="store_true", help="Break-glass take-over: adopt the worktree even if a live interactive CLI holds it (stop that CLI first)")
    resume_p.add_argument("--strict", action="store_true", help="Identity-preserving contract: refuse (409) rather than silently start a fresh replacement conversation when the worktree has no existing session or its resume fails")
    resume_p.add_argument("--worktree-writer-file", metavar="PATH", help="Original keyed writer JSON for strict exact-session ACP resume; - reads stdin")
    resume_p.add_argument("--retained-recovery-file", metavar="PATH", help="Once-only retained resume request including its writer; requires --strict; - reads stdin")
    resume_p.add_argument("--cli", action="store_true", help="Resume the exact native head in a headed/muxed CLI instead of headless ACP")
    resume_p.set_defaults(func=_cmd_resume)

    from . import restart_worktree_cli

    restart_worktree_cli.add_parser(sub)

    handoff_p = sub.add_parser("handoff", help="Retire a session/worktree and continue in a fresh successor in place")
    handoff_p.add_argument("session_id", metavar="target", help="Session ID (owned ACP session) or worktree handle to hand off")
    handoff_p.add_argument("--reason", default=None, help="Free-form reason carried on the session_handoff event (default: context-pressure)")
    handoff_p.add_argument("--no-seed", action="store_true", help="Do not seed the successor's opening turn with the brief (the caller drives it instead)")
    handoff_p.set_defaults(func=_cmd_handoff)

    handoff_request_p = sub.add_parser("handoff-request", help="Request an externally-seeded in-place handoff for a worktree")
    handoff_request_p.add_argument("--worktree-id", required=True, help="Worktree handle whose current session should be handed off")
    handoff_request_p.add_argument("--session-id", required=True, help="Current bridge or ACP session id expected to own the worktree")
    handoff_request_p.add_argument("--handoff-token", default=None, help="Opaque handoff correlation token carried on the event/result")
    handoff_request_p.add_argument("--seed", required=True, help="Exact opening-turn text to seed into the successor session")
    handoff_request_p.add_argument("--json", action="store_true", help="Emit JSON.")
    handoff_request_p.set_defaults(func=_cmd_handoff_request)

    handoff_check_p = sub.add_parser("handoff-check", help="Diagnose (and with --execute, finish) a stalled handoff-cutover predecessor retirement for a mux/CLI-hosted worktree")
    handoff_check_g = handoff_check_p.add_mutually_exclusive_group(required=True)
    handoff_check_g.add_argument("--worktree-id", default=None, help="Check only this worktree")
    handoff_check_g.add_argument("--all", action="store_true", help="Check every tracked worktree")
    handoff_check_p.add_argument("--execute", action="store_true", help="Retire each found stale predecessor now (default: read-only report)")
    handoff_check_p.add_argument("--json", action="store_true", help="Emit JSON.")
    handoff_check_p.set_defaults(func=_cmd_handoff_check)

    questions_p = sub.add_parser("questions", help="Inspect fresh native questions in an exact live interactive session (JSON)")
    questions_p.add_argument("session_id", help="Exact native session ID, not a worktree/alias")
    questions_p.add_argument("--via", choices=["native", "mux"], default="native", help="Explicit degraded mux inspection when native answering is unavailable")
    questions_p.add_argument("--inspection-id", help="Read the original mux audit instead of inspecting a new form")
    questions_p.set_defaults(func=_cmd_questions)

    answer_p = sub.add_parser("answer", help="Answer an owned ask_user or an exact live interactive question")
    answer_p.add_argument("session_id", help="Session ID")
    answer_p.add_argument("--via", choices=["native", "mux"], default="native")
    answer_p.add_argument("--inspection-id", help="One-use exact inspected mux form token from questions --via mux")
    answer_p.add_argument("--option", type=int, help="One-based inspected mux choice; requires --via mux")
    answer_p.add_argument("--text", help="Printable single-line answer through inspected Other; requires --via mux")
    answer_p.add_argument("--field", dest="fields", action="append", default=[], metavar="KEY=VALUE", help="A form field answer (repeatable). Values are strings; use --json for numbers/booleans/complex values.")
    answer_p.add_argument("--json", dest="content_json", default=None, help="Full answer content as a JSON object (overrides --field).")
    answer_p.add_argument("--tool-call-id", dest="tool_call_id", default=None, help="Which parked question to answer (defaults to the sole pending one; required when more than one is outstanding -- see `status`).")
    answer_p.add_argument("--request-id", default=None, help="Exact live answer token from a remote approval event/status; required for remote forms.")
    answer_p.add_argument("--generation", default=None, help="Live interactive extension generation from questions; requires its exact --request-id. Never approves tool permissions.")
    answer_p.add_argument("--option-id", default=None, help="Select one exact offered permission option instead of answering a form; requires --request-id.")
    answer_p.add_argument("--decline", action="store_true", help="Decline the question instead of submitting an answer.")
    answer_p.add_argument("--cancel", action="store_true", help="Cancel the question instead of submitting an answer.")
    answer_p.set_defaults(func=_cmd_answer)

    agent_p = sub.add_parser("agent", help="Run as an ACP agent on stdio")
    agent_p.add_argument("--agent", required=True, help="Name of the downstream agent to route to")
    agent_p.set_defaults(func=_cmd_agent)
