"""``agent-codespaces copilot <name> --detach`` / ``--stop`` -- a CLI-mode
Copilot session an *agent* can start, observe, steer and stop without handing
over its own terminal.

The attached ``copilot`` verb (:mod:`agent_codespaces.copilot_venue`) gives a
human the remote muxed session in their terminal. An orchestrating agent needs
the same session without a TTY, surviving the launcher's exit. Everything here
composes existing machinery rather than adding a parallel path:

* ``_ssh_session`` (the ``ssh`` verb's body) supplies the claim, target lock
  and full dispatch-grade venue preparation, then runs the remote
  ``agent-worktrees embody`` -- the detached twin of ``agent-worktrees
  copilot`` -- with the staged CodeSpace plugins folded in as
  ``--copilot-arg=--plugin-dir=...``;
* the Connection Owner keeps the credential relay **and** a host-bridge-daemon
  forward alive for the session's lifetime (a *session tenant* it renews from
  its own venue probe -- :mod:`agent_codespaces.session_forwards`);
* the host bridge's CLI-mode reservation, keyed by a venue-qualified identity
  (``<identity>@<codespace>``) and carrying the venue descriptor, tells the
  launcher exactly which live session registered.

Success is reported only once the session is represented on the host bridge
(the reservation was claimed) and, when seeded, the seed was submitted; a
launch that cannot reach that point tears down what it started.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys
import time
from collections.abc import Callable
from typing import Any

from venue_copilot import (
    MAX_SEED_CHARS,  # noqa: F401 -- re-exported: callers/tests read detach.MAX_SEED_CHARS
    _TRUST_FOLDER,  # noqa: F401 -- re-exported for the trust-folder snippet test
    await_claim as _await_claim,
    bridge_probe_script,
    last_json,
    observe_commands,
    read_seed,
    reserve_with_retry,
    seed_delivery,
    trust_folder_command,
    with_new_session,
)

_BUSY_EXIT = 75
_COORDINATION_EXIT = 78
_RESERVATION_TTL = 900.0  # generous: venue prep + first-run provisioning + seed wait
_RESERVE_RETRY_WINDOW = 90.0


def _progress(stage: str, detail: str = "") -> None:
    print(f"[DETACH] {stage}{': ' + detail if detail else ''}", file=sys.stderr, flush=True)


def _codespace(name: str) -> Any:
    from .lifecycle import list_codespaces

    return next((cs for cs in list_codespaces() if cs.name == name), None)


def plan_for(args: argparse.Namespace, config: Any, codespace: Any = None) -> dict[str, Any]:
    """The detached session's identities, all derived from the CodeSpace name.

    ``scope_id`` is what the remote session registers with on the host bridge
    (unique per CodeSpace, so several CodeSpaces of one repo stay distinct);
    ``mux_session`` is the venue-local tmux session ``embody`` creates (the
    same one the attached ``copilot`` verb re-attaches to). ``codespace`` is
    the caller's own listing entry, when it already has one.
    """
    import os

    repository = getattr(_codespace(args.name) if codespace is None else codespace, "repository", None)
    workspace = config.resolved_workspace_folder_for(repository) or ""
    # Same anchor-identity convention as the attached verb
    # (copilot_venue._resolve_anchor_identity), from one CodeSpace lookup.
    identity = args.worktree_id or f"anchor-{os.path.basename(workspace.rstrip('/')) or args.name}"
    scope = f"{identity}@{args.name}"
    mux = f"wt-{identity}"
    return {
        "codespace": args.name,
        "identity": identity,
        "workspace_folder": workspace or None,
        "anchor": not args.worktree_id,
        "scope_id": scope,
        "mux_session": mux,
        "tenant": f"cli:{scope}",
        "venue": {"kind": "codespace", "target": args.name, "mux_session_name": mux},
    }


def _remote(
    name: str, command: str, *, timeout: float = 60.0, input_bytes: bytes | None = None,
) -> tuple[int, str, str] | None:
    """Run one command on the CodeSpace (optional stdin); ``None`` on transport failure."""

    async def _run() -> tuple[int, str, str]:
        from ssh_manager import ConnectionManager

        from .codespace_config import CodespaceSource
        from .lifecycle import account_for_codespace

        from ._ssh_retry import exec_with_retry

        manager = ConnectionManager()
        source = CodespaceSource(name, account=account_for_codespace(name))
        await manager.ensure_connected(name, source, [])
        try:
            # Transient exit/stderr failures retry inside the shared helper.
            result = await exec_with_retry(
                manager, name, f"bash -lc {shlex.quote(command)}", timeout=timeout,
                input_bytes=input_bytes,
            )
            return result.exit_code, result.stdout or "", result.stderr or ""
        finally:
            await manager.disconnect(name)

    # A connect that fails outright (the config fetch and connect retry
    # transient resets themselves) gets one more try before callers degrade.
    for attempt in range(_CONNECT_ATTEMPTS):
        try:
            return asyncio.run(_run())
        except Exception as exc:  # noqa: BLE001 -- callers degrade explicitly
            _progress("remote-exec-failed", str(exc))
        if attempt + 1 < _CONNECT_ATTEMPTS:
            time.sleep(5.0)
    return None


_CONNECT_ATTEMPTS = 2
_LAUNCH_ATTEMPTS = 2
_last_json = last_json


def _transient(text: str) -> bool:
    from ssh_manager import TRANSIENT_SSH_STDERR

    return bool(TRANSIENT_SSH_STDERR.search(text or ""))


def _is_transient_result(result: Any) -> bool:
    from ssh_manager import is_transient_ssh_failure

    return is_transient_ssh_failure(result)


def parse_reverse_forwards(specs: list[str]) -> dict[int, int]:
    """``["VENUE_PORT:HOST_PORT", ...]`` -> ``{venue_port: host_port}``.

    Each asks the Connection Owner to keep CodeSpace ``127.0.0.1:VENUE_PORT``
    forwarded to this host's ``127.0.0.1:HOST_PORT`` for the session's life
    (for example a host browser's DevTools endpoint on the venue's 9222).
    """
    out: dict[int, int] = {}
    for spec in specs:
        venue, sep, host = str(spec).partition(":")
        try:
            v, h = int(venue), int(host)
        except ValueError:
            v = h = 0
        if not sep or not (0 < v < 65536 and 0 < h < 65536):
            raise ValueError(f"--reverse-forward expects VENUE_PORT:HOST_PORT, got {spec!r}")
        if v in out and out[v] != h:
            raise ValueError(f"--reverse-forward names venue port {v} twice")
        out[v] = h
    return out

def _send_refs(name: str, upload: tuple[str, bytes, list[tuple[str, int]]]) -> str | None:
    """Copy one reference batch into the venue; the worker-facing note, or ``None``."""
    from venue_copilot.refs import send_refs

    return send_refs(
        lambda command, stdin: _remote(name, command, timeout=600.0, input_bytes=stdin),
        upload,
        _progress,
    )


# A fresh CodeSpace can carry the agent-worktrees plugin payload (staged by
# CodeSpace-scoped plugin registration) without its runtime/binstub, and the
# shared best-effort preflight may not have installed agent-bridge (whose
# extension registers the session). Both are hard preconditions here, so the
# launch completes them idempotently: install agent-bridge if absent, and run
# the agent-worktrees payload's own documented installer if the binstub is.
_VENUE_TOOLING = (
    'P="$HOME/.copilot/installed-plugins/copilot-extensions"; '
    '{ [ -d "$P/agent-bridge" ] || copilot plugin install agent-bridge@copilot-extensions >&2 || true; } && '
    '{ command -v agent-worktrees >/dev/null 2>&1 || '
    '{ [ -f "$P/agent-worktrees/scripts/install.sh" ] && bash "$P/agent-worktrees/scripts/install.sh" install >&2; } || true; } && '
    'export PATH="$HOME/.local/bin:$PATH"'
)


def _pane_tail(name: str, mux: str, lines: int = 40) -> str:
    got = _remote(
        name, f"tmux capture-pane -p -t {shlex.quote('=' + mux + ':')} 2>/dev/null | tail -n {lines}",
    )
    return got[1].rstrip() if got else ""


def _bridge_path_ok(name: str, port: int) -> bool:
    """Authenticated round trip from the CodeSpace to the host bridge daemon.

    Proves the Owner's forward is actually serving (a live ``ssh -R`` process
    alone does not prove its remote bind succeeded) and that the provisioned
    token is accepted -- i.e. the session will be able to register.
    """
    probe = bridge_probe_script(port)
    got = None
    for attempt in range(_PROBE_ATTEMPTS):
        got = _remote(name, probe, timeout=30.0)
        if got and got[0] == 0:
            return True
        if attempt + 1 < _PROBE_ATTEMPTS:
            time.sleep(5.0)  # the forward may still be binding
    return False


#: Attempts for the authenticated probe itself (transport retries are in `_remote`).
_PROBE_ATTEMPTS = 2


def _venue_ports_listening(name: str, ports: list[int], *, attempts: int = 6) -> dict[int, bool]:
    """Which venue loopback ports accept a TCP connection (the Owner's extra
    forwards bind a few seconds after the hold; an Owner that predates them never does)."""
    ready: set[int] = set()
    for attempt in range(attempts):
        probe = "; ".join(
            f"(exec 3<>/dev/tcp/127.0.0.1/{int(p)}) 2>/dev/null && echo {int(p)}" for p in ports
        ) + "; true"
        got = _remote(name, probe, timeout=30.0)
        if got:
            ready = {int(t) for t in got[1].split() if t.isdigit()}
        if ready >= set(ports) or attempt + 1 == attempts:
            break
        time.sleep(5.0)
    return {p: p in ready for p in ports}


def _ssh_namespace(args: argparse.Namespace, remote_cmd: str, *, timeout: float) -> argparse.Namespace:
    return argparse.Namespace(
        name=args.name,
        stdio=False,
        remote_cmd=remote_cmd,
        timeout=timeout,
        connect_timeout=None,
        no_provision=False,
        no_relay=False,
        auth_cache_warmup=True,
        repo=None,
        effort=getattr(args, "effort", None),
        session_id=None,
        stage_plugins=[],
        force=getattr(args, "force", False),
        force_claim=getattr(args, "force_claim", False),
    )


def _commands(plan: dict[str, Any], session_id: str, effort: str | None = None) -> dict[str, str]:
    cs = plan["codespace"]
    # Attach and stop connect, so they must name the same claim owner the
    # launch used; without it the caller's worktree is a different owner and
    # the CodeSpace's claim refuses them as busy.
    claim = f" --effort {shlex.quote(effort)}" if effort else ""
    return {
        **observe_commands(session_id),
        "attach": f"agent-codespaces copilot {cs}{claim}",
        "stop": f"agent-codespaces copilot {cs} --stop{claim}",
    }


def _reserve(plan: dict[str, Any]) -> dict[str, Any]:
    return reserve_with_retry(
        plan["scope_id"],
        plan["venue"],
        ttl_seconds=_RESERVATION_TTL,
        retry_window=_RESERVE_RETRY_WINDOW,
        on_wait=lambda: _progress(
            "waiting", "another launch on this CodeSpace holds the reservation",
        ),
    )


def _fail(message: str, plan: dict[str, Any], **extra: Any) -> int:
    print(f"[FAIL] {message}", file=sys.stderr)
    print(json.dumps({"ok": False, "error": message, **plan, **extra}, indent=2))
    return 1


def cmd_detach(
    args: argparse.Namespace, *, ssh_session: Callable[..., int],
) -> int:
    """Start (or rejoin) the CodeSpace's detached CLI-mode session; JSON out."""
    from venue_copilot import build_copilot_remote_command, release_cli_mode, resolve_daemon_port

    from . import connection_owner as owner
    from .config import load_merged_config
    from .session_forwards import await_owner_bridge_forward
    from .worktrees import ContextRefused

    config = load_merged_config()
    try:
        codespace = _codespace(args.name)
    except RuntimeError:
        codespace = False  # listing unavailable: unknown, not missing
    plan = plan_for(args, config, codespace)
    if codespace is None:
        # Fail before claiming or holding anything: a deleted CodeSpace
        # otherwise surfaces much later as a misleading forward timeout.
        return _fail(f"CodeSpace '{args.name}' was not found under any GitHub account", plan)
    try:
        seed = read_seed(args)
    except (OSError, ValueError) as exc:
        return _fail(str(exc), plan)
    copilot_args = with_new_session(list(getattr(args, "copilot_args", None) or []))
    try:
        reverse_forwards = parse_reverse_forwards(getattr(args, "reverse_forwards", None) or [])
    except ValueError as exc:
        return _fail(str(exc), plan)
    ref_files = list(getattr(args, "ref_files", None) or [])
    if ref_files:
        from venue_copilot.refs import RefFileError, build_refs_upload, batch_id

        try:
            refs_upload = build_refs_upload(ref_files, batch_id(plan["scope_id"]))
        except (OSError, RefFileError) as exc:
            return _fail(str(exc), plan)
    if getattr(args, "dry_run", False):
        print(json.dumps({"ok": True, "dry_run": True, **plan, "seed_len": len(seed or ""),
                          "copilot_args": copilot_args, "reverse_forwards": reverse_forwards,
                          "ref_files": [n for n, _ in refs_upload[2]] if ref_files else []}, indent=2))
        return 0

    from .copilot_venue import claim_or_exit_code

    claim_rc = claim_or_exit_code(args)
    if claim_rc is not None:
        return claim_rc
    daemon_port = resolve_daemon_port()
    if not daemon_port:
        return _fail("the host agent-bridge daemon is not running (no routing table)", plan)
    if not owner.ensure_owner_running(config):
        return _fail(
            "the Connection Owner could not be started; a detached session needs it "
            "to keep the relay and bridge forwards alive after this command exits",
            plan,
        )

    _progress("hold", f"Owner session tenant {plan['tenant']} (bridge port {daemon_port})")
    held = owner.get_hold(args.name)
    prior_session = dict((held.sessions.get(plan["tenant"]) if held else None) or {}) or None
    prior_forwards = dict(getattr(held, "reverse_forwards", None) or {})
    owner.hold(
        args.name, plan["tenant"], daemon_port=daemon_port,
        mux_session=plan["mux_session"], fresh=True,
        **({"reverse_forwards": reverse_forwards} if reverse_forwards else {}),
    )
    ok = False
    created = False
    reservation: dict[str, Any] | None = None
    try:
        from .copilot_venue import _ensure_agent_bridge_plugin

        _progress("prepare", "agent-bridge plugin + registration credentials on the venue")
        _ensure_agent_bridge_plugin(args.name)
        _progress("forward", "waiting for the Owner's bridge forward")
        if not asyncio.run(await_owner_bridge_forward(args.name, timeout=90.0)):
            return _fail("the Connection Owner did not bring up the bridge forward", plan)
        if not _bridge_path_ok(args.name, daemon_port):
            return _fail(
                "the CodeSpace cannot reach the host bridge through the forward "
                "(authenticated probe failed)", plan,
            )
        reservation = _reserve(plan)
        _progress("reserved", reservation.get("reservation_id", ""))
        refs_note_text = None
        if ref_files:
            refs_note_text = _send_refs(args.name, refs_upload)
            if refs_note_text is None:
                return _fail("could not copy the reference files to the CodeSpace", plan)
            seed = f"{seed.rstrip()}\n\n{refs_note_text}" if seed else refs_note_text

        captured: dict[str, Any] = {}
        typed_seed, seed_prefix = seed_delivery(seed, plan["scope_id"])

        def builder(plugin_dirs: list[str]) -> str:
            extra = [f"--plugin-dir={d}" for d in plugin_dirs] + copilot_args
            captured["plugin_dirs"] = list(plugin_dirs)
            command = seed_prefix + build_copilot_remote_command(
                plan["identity"], anchor=plan["anchor"], driver=args.driver, seed=typed_seed,
                ensure_mux=args.ensure_mux, detach=True, bridge_scope_id=plan["scope_id"],
                copilot_args=extra, login_shell=False,
            )
            # Run from the product checkout, exactly like headless dispatch's
            # `cd <workspace> && copilot --acp`: embody resolves its project
            # (and the anchor) from the working directory. A CodeSpace's
            # checkout is often not an adopted agent-worktrees project yet;
            # adopt it once, as the anchor-only (base-repo) project it is.
            workspace = plan.get("workspace_folder")
            if not workspace:
                return command
            prefix = (
                f"cd {shlex.quote(workspace)} && {_VENUE_TOOLING} && "
                f"{trust_folder_command(workspace)}"
            )
            if plan["anchor"]:
                label = plan["identity"].removeprefix("anchor-")
                prefix += (
                    " && { agent-worktrees get project >/dev/null 2>&1 || "
                    f"agent-worktrees register {shlex.quote(label)} --base-repo --no-agent >&2; }}"
                )
            return f"{prefix} && {command}"

        def sink(result: Any) -> int:
            captured["result"] = result
            return int(getattr(result, "exit_code", 1))

        _progress("launch", "venue prep + `agent-worktrees embody` (can take minutes on first use)")
        # `embody` is idempotent (it rejoins a running session and never
        # re-seeds it), so a launch whose *transport* failed is retried.
        for attempt in range(_LAUNCH_ATTEMPTS):
            captured.pop("result", None)
            try:
                rc = ssh_session(
                    _ssh_namespace(args, "agent-worktrees embody", timeout=args.register_timeout + 300.0),
                    remote_cmd_builder=builder, result_sink=sink, settle_on_disconnect=False,
                )
            except ContextRefused as exc:
                print(f"[BLOCKED] CodeSpace installation context refused: {exc}", file=sys.stderr)
                return _COORDINATION_EXIT
            except (RuntimeError, OSError) as exc:
                if attempt + 1 < _LAUNCH_ATTEMPTS and _transient(str(exc)):
                    _progress("launch-retry", f"transient connection failure: {str(exc)[:200]}")
                    time.sleep(10.0)
                    continue
                return _fail(f"could not connect to the CodeSpace ({exc}); retry the same command", plan)
            result = captured.get("result")
            if (
                attempt + 1 < _LAUNCH_ATTEMPTS and result is not None
                and not _last_json(getattr(result, "stdout", "") or "")
                and _is_transient_result(result)
            ):
                _progress("launch-retry", f"transient SSH failure (exit {getattr(result, 'exit_code', '?')})")
                time.sleep(10.0)
                continue
            break
        if rc in (_BUSY_EXIT, _COORDINATION_EXIT) and "result" not in captured:
            return rc
        result = captured.get("result")
        stdout = getattr(result, "stdout", "") or ""
        stderr = getattr(result, "stderr", "") or ""
        embodied = _last_json(stdout)
        if "agent-worktrees: command not found" in stderr + stdout:
            return _fail(
                "agent-worktrees is not installed on the CodeSpace and could not be "
                "provisioned (no plugin payload); enable agent-worktrees@copilot-extensions "
                "among the harness's CodeSpace plugins, or install it on the venue", plan,
            )
        if "unrecognized arguments" in stderr or "unrecognized arguments" in stdout:
            return _fail(
                "the venue's agent-worktrees is too old for detached launch "
                "(--bridge-scope-id/--copilot-arg); update agent-worktrees on the "
                "CodeSpace", plan, detail=stderr.strip()[-2000:],
            )
        if rc != 0 or not embodied.get("ok"):
            return _fail(
                f"remote embody failed: {embodied.get('error') or stderr.strip()[-2000:] or f'exit {rc}'}",
                plan,
            )
        created = bool(embodied.get("created"))
        actual_mux = embodied.get("session")
        if actual_mux and actual_mux != plan["mux_session"]:
            # The venue named its session differently than predicted; the
            # venue's answer wins for the Owner's liveness probe and --stop.
            _progress("identity", f"venue mux session is {actual_mux} (predicted {plan['mux_session']})")
            plan["mux_session"] = actual_mux
            plan["venue"]["mux_session_name"] = actual_mux
            owner.hold(args.name, plan["tenant"], daemon_port=daemon_port, mux_session=actual_mux)
        if created and seed and not embodied.get("seed_submitted"):
            return _fail(
                "Copilot never reached a ready prompt, so the seed was not submitted "
                f"({embodied.get('seed_reason') or 'unknown'})",
                plan, pane_tail=_pane_tail(args.name, plan["mux_session"]),
            )
        _progress("register", "waiting for the session to register with the host bridge")
        session_id = _await_claim(plan["scope_id"], reservation["reservation_id"], args.register_timeout)
        if not session_id:
            return _fail(
                "the session is running but never registered with the host bridge "
                "(unrepresented). If it was started without --detach, attach with "
                f"`agent-codespaces copilot {args.name}` or stop it with --stop.",
                plan, pane_tail=_pane_tail(args.name, plan["mux_session"]),
            )
        owner.hold(
            args.name, plan["tenant"], daemon_port=daemon_port,
            mux_session=plan["mux_session"], confirmed=True,
        )
        refs_delivered = None
        if refs_note_text:
            # A new session got the note in its seed; a running one is told now.
            from venue_copilot.refs import deliver_note

            refs_delivered = "seed" if created else (
                "message" if deliver_note(session_id, refs_note_text) else "failed"
            )
        ok = True
        forwards_ready = (
            _venue_ports_listening(args.name, sorted(reverse_forwards)) if reverse_forwards else {}
        )
        print(json.dumps({
            "ok": True, **plan, "session_id": session_id, "created": created,
            **({"ref_files": refs_note_text.splitlines()[1:], "refs_delivered": refs_delivered}
               if refs_note_text else {}),
            "resumed": not created, "seeded": bool(created and seed),
            "plugin_dirs": captured.get("plugin_dirs", []),
            **({"reverse_forwards": reverse_forwards,
                "reverse_forwards_ready": forwards_ready} if reverse_forwards else {}),
            "commands": _commands(plan, session_id, getattr(args, "effort", None)),
        }, indent=2))
        return 0
    finally:
        if reservation:
            release_cli_mode(plan["scope_id"], reservation_id=reservation.get("reservation_id"))
        if not ok:
            if created:
                _progress("cleanup", f"stopping the unrepresented session {plan['mux_session']}")
                _remote(args.name, f"tmux kill-session -t {shlex.quote('=' + plan['mux_session'])}")
            if prior_session and not created:
                # A failed rejoin must not cut off the session that is still
                # running: put its tenant back exactly as it was.
                owner.hold(
                    args.name, plan["tenant"], daemon_port=daemon_port,
                    mux_session=prior_session["mux_session"], restore=prior_session,
                    reverse_forwards=prior_forwards,
                )
            else:
                owner.release(args.name, plan["tenant"])


def cmd_stop(args: argparse.Namespace, *, ssh_session: Callable[..., int]) -> int:
    """Stop the CodeSpace's detached session, then release what it held.

    The kill runs through the ordinary ``ssh`` path so the borrowing
    worktree's claim is settled on disconnect exactly as for any other
    finished use (a still-dirty checkout keeps it active). Forwards are
    released only after the mux session is verified gone -- or when GitHub
    reports the CodeSpace Shutdown, which is never booted just to kill nothing.
    """
    from venue_copilot import deregister_live_session, live_session_for, release_cli_mode

    from . import connection_owner as owner
    from .config import load_merged_config
    from .lifecycle import _SHUTDOWN_STATE
    from .worktrees import ContextRefused

    codespace = _codespace(args.name)
    plan = plan_for(args, load_merged_config(), codespace)
    held = owner.get_hold(args.name)
    recorded = (held.sessions.get(plan["tenant"]) or {}).get("mux_session") if held else None
    if recorded:
        plan["mux_session"] = recorded
    # Look the session up before the kill: an abruptly stopped CLI never
    # deregisters itself, so --stop removes exactly this venue's row.
    row = live_session_for(plan["scope_id"])
    session_id = row.get("session_id") if (row.get("venue") or {}).get("target") == args.name else None

    def released(**extra: Any) -> int:
        release_cli_mode(plan["scope_id"])
        owner.release(args.name, plan["tenant"])
        deregistered = bool(session_id) and deregister_live_session(session_id)
        print(json.dumps({"ok": True, "stopped": True, **extra,
                          "deregistered": session_id if deregistered else None, **plan}, indent=2))
        return 0

    if getattr(codespace, "state", None) == _SHUTDOWN_STATE:
        # A stopped CodeSpace runs nothing, so its mux session is gone by
        # construction; never boot it just to kill nothing. Its claim is left
        # for the ordinary release/retire step, which settles it.
        return released(already_shutdown=True)
    target = shlex.quote("=" + plan["mux_session"])
    script = (
        f"tmux kill-session -t {target} 2>/dev/null; sleep 1; "
        f"if tmux has-session -t {target} 2>/dev/null; then echo STILL_RUNNING; exit 3; fi; "
        "echo STOPPED"
    )
    captured: dict[str, Any] = {}

    def sink(result: Any) -> int:
        captured["result"] = result
        return int(getattr(result, "exit_code", 1))

    for attempt in range(_LAUNCH_ATTEMPTS):  # the verified kill is idempotent
        try:
            rc = ssh_session(
                _ssh_namespace(args, f"bash -lc {shlex.quote(script)}", timeout=120.0),
                result_sink=sink,
            )
        except ContextRefused as exc:
            print(f"[BLOCKED] CodeSpace installation context refused: {exc}", file=sys.stderr)
            return _COORDINATION_EXIT
        except (RuntimeError, OSError) as exc:
            if attempt + 1 < _LAUNCH_ATTEMPTS and _transient(str(exc)):
                time.sleep(5.0)
                continue
            return _fail(f"could not connect to the CodeSpace ({exc}); nothing was released", plan)
        result = captured.get("result")
        if attempt + 1 < _LAUNCH_ATTEMPTS and result is not None and _is_transient_result(result):
            time.sleep(5.0)
            continue
        break
    stdout = getattr(captured.get("result"), "stdout", "") or ""
    if rc != 0 or "STOPPED" not in stdout:
        return _fail("could not verify the session stopped; nothing was released", plan, exit_code=rc)
    return released()
