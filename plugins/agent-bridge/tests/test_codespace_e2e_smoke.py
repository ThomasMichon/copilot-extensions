"""Opt-in end-to-end smoke tests for the CodeSpace Session-Host path.

These exercise the **real** remote stack the fast unit suite (fakes / monkeypatch)
cannot: `gh` CodeSpace SSH, the far-side Session Host bootstrap, the credential
relay, and ACP driven over the forwarded loopback -- including the reattach path
this session-host work hardened (protocol-aware turn boundaries, adopt-not-respawn
resume, refuse-classic-mode).

They are **SKIPPED unless the caller supplies the target explicitly**. There are
deliberately **NO defaults** -- a CodeSpace name, its repo, its workspace checkout
path, and its ACP launch command are account- and environment-specific and must
never be hardcoded into a test. The calling agent/operator passes them via the
environment; if any is missing the whole module skips.

Required environment (ALL must be set, or the module is skipped):

    AGENT_BRIDGE_E2E_CODESPACE     raw or friendly CodeSpace name. Must be
                                   Available (or resumable on connect) for the
                                   currently-active `gh` account.
    AGENT_BRIDGE_E2E_REPO          owner/repo the CodeSpace hosts (handed to the
                                   far-side spawner, e.g. "example-org/example-web-codespaces").
    AGENT_BRIDGE_E2E_WORKSPACE     absolute workspace checkout path ON the
                                   CodeSpace, used as the ACP session cwd
                                   (e.g. "/workspaces/example-web").
    AGENT_BRIDGE_E2E_ACP_COMMAND   the far-side shell command that launches
                                   copilot in ACP mode, e.g.
                                   "cd /workspaces/example-web && copilot --acp --stdio".
                                   Passed verbatim -- no part of it is derived.

Optional operational overrides (timeouts only -- NOT target identity, so these
carry internal constants when unset):

    AGENT_BRIDGE_E2E_BOOT_TIMEOUT     seconds to allow for CodeSpace boot + host
                                      bootstrap + cold ACP session/new (default 420).
    AGENT_BRIDGE_E2E_TURN_TIMEOUT     seconds to allow for a single smoke turn
                                      (default 240).

Preconditions the caller owns (not asserted here):
  * `gh auth` is the account that OWNS the CodeSpace (the resolver is
    active-account sensitive -- see the account-flip gotcha).
  * If a turn needs ADO/git, the daemon's credential relay is reachable; these
    smoke turns are intentionally trivial and need neither.

Run (from the plugin dir), with the env set:

    pytest tests/test_codespace_e2e_smoke.py -v

These tests hit real infrastructure and take minutes; they never run as part of
the default suite (the module skips when the env is absent).
"""

from __future__ import annotations

import asyncio
import os

import pytest

from agent_bridge.db import Database
from agent_bridge.models import PhasedTimeouts, SessionStatus
from agent_bridge.session_manager import SessionManager
from agent_bridge.transport import SpawnTarget

# --- opt-in gate: skip the whole module unless the caller configured a target.
_REQUIRED = (
    "AGENT_BRIDGE_E2E_CODESPACE",
    "AGENT_BRIDGE_E2E_REPO",
    "AGENT_BRIDGE_E2E_WORKSPACE",
    "AGENT_BRIDGE_E2E_ACP_COMMAND",
)
_missing = [name for name in _REQUIRED if not os.environ.get(name)]
if _missing:
    pytest.skip(
        "CodeSpace e2e smoke tests are opt-in and take no defaults. Set "
        + ", ".join(_missing)
        + " (see this module's docstring for what each must contain).",
        allow_module_level=True,
    )

_CODESPACE = os.environ["AGENT_BRIDGE_E2E_CODESPACE"]
_REPO = os.environ["AGENT_BRIDGE_E2E_REPO"]
_WORKSPACE = os.environ["AGENT_BRIDGE_E2E_WORKSPACE"]
_ACP_COMMAND = os.environ["AGENT_BRIDGE_E2E_ACP_COMMAND"]
_BOOT_TIMEOUT = float(os.environ.get("AGENT_BRIDGE_E2E_BOOT_TIMEOUT", "420"))
_TURN_TIMEOUT = float(os.environ.get("AGENT_BRIDGE_E2E_TURN_TIMEOUT", "240"))

# A trivial, tool-free instruction -- we assert the turn *completes* (a terminal
# stop reason), not any exact wording (model output varies).
_SMOKE_PROMPT = (
    "Reply with the single word PONG and nothing else. Do not call any tools."
)


def _target() -> SpawnTarget:
    """Build the CodeSpace SpawnTarget entirely from caller-supplied values.

    Carries both the structured ``codespace`` block (the modern provider seam)
    and an equivalent ``agent_codespaces`` stdio ``spawn_command`` -- exactly what
    the real bridge provider registers -- so this drives the production
    CodeSpace Session-Host branch, not a shortcut.
    """
    codespace = {
        "name": _CODESPACE,
        "repo": _REPO,
        "acp_command": _ACP_COMMAND,
        "workspace_folder": _WORKSPACE,
    }
    spawn_command = [
        "python", "-m", "agent_codespaces", "ssh", "--stdio",
        _CODESPACE, "--repo", _REPO, "--remote-cmd", _ACP_COMMAND,
    ]
    return SpawnTarget(type="command", spawn_command=spawn_command, codespace=codespace)


def _manager(tmp_path) -> SessionManager:
    """A Session-Host-enabled manager with codespace-patient timeouts, isolated
    to a temp DB + host-state dir so a smoke run touches no real daemon state."""
    db = Database(tmp_path / "e2e.db")
    timeouts = PhasedTimeouts(
        codespace_boot=_BOOT_TIMEOUT,
        ssh_connect=_BOOT_TIMEOUT,
        session_start=_BOOT_TIMEOUT,
        session_new=_BOOT_TIMEOUT,
        command=_TURN_TIMEOUT,
    )
    return SessionManager(
        db,
        timeouts=timeouts,
        session_host_state_dir=str(tmp_path / "hosts"),
    )


async def _run_turn(mgr: SessionManager, session, prompt: str) -> str | None:
    """Submit one prompt and wait for its turn to complete; return the terminal
    stop reason (or None if none was recorded)."""
    await mgr.submit_prompt(session.session_id, prompt)
    task = session._prompt_task
    assert task is not None, "submit_prompt did not schedule a prompt task"
    await asyncio.wait_for(asyncio.shield(task), timeout=_TURN_TIMEOUT)
    stops = [
        e.data.get("stop_reason")
        for e in session.event_log.get_events()
        if e.event == "turn_complete"
    ]
    return stops[-1] if stops else None


@pytest.mark.asyncio
async def test_e2e_dispatch_and_single_turn(tmp_path):
    """Flow 1 -- cold dispatch to a CodeSpace runs one ACP turn end to end.

    Validates: CodeSpace boot/resume -> far-side Session Host bootstrap -> `-L`
    forward -> ACP initialize + session/new -> a turn completing over the relay.
    """
    mgr = _manager(tmp_path)
    session = await mgr.start_session(_target(), agent_name=_CODESPACE)
    try:
        assert session.status == SessionStatus.IDLE, (
            f"session did not reach IDLE (status={session.status}); "
            f"inspect events: {[e.event for e in session.event_log.get_events()]}"
        )
        assert session.pid is not None, "no child pid -- Session Host child not live"
        stop_reason = await _run_turn(mgr, session, _SMOKE_PROMPT)
        assert session.status == SessionStatus.IDLE
        assert stop_reason not in (None, "cancelled", "error"), (
            f"turn did not complete cleanly (stop_reason={stop_reason!r})"
        )
    finally:
        await mgr.end_session(session.session_id, force=True)


@pytest.mark.asyncio
async def test_e2e_reattach_adopts_same_child(tmp_path):
    """Flow 2 -- a stop + resume REATTACHES the surviving CodeSpace child rather
    than respawning it (the core survive-a-hiccup guarantee).

    Validates: after a detach (the graceful analogue of a transport drop), the
    far-side Session Host keeps copilot alive; resume adopts the SAME ACP session
    id and the SAME child pid -- no respawn, no lost session -- and the reattached
    session can run another turn.
    """
    mgr = _manager(tmp_path)
    session = await mgr.start_session(_target(), agent_name=_CODESPACE)
    try:
        assert session.status == SessionStatus.IDLE
        orig_acp = session.acp_session_id
        orig_pid = session.pid
        assert orig_acp and orig_pid is not None

        # Detach the front (child + far-side host survive), then resume.
        await mgr.stop_session(session.session_id)
        assert session.status == SessionStatus.STOPPED

        resumed = await mgr.resume_session(session.session_id)
        assert resumed.status == SessionStatus.IDLE
        assert resumed.acp_session_id == orig_acp, "resume did not adopt the ACP session"
        assert resumed.pid == orig_pid, (
            f"resume respawned the child (pid {resumed.pid} != {orig_pid}) "
            "instead of reattaching the survivor"
        )

        # Prove the reattached session is live.
        stop_reason = await _run_turn(mgr, resumed, _SMOKE_PROMPT)
        assert stop_reason not in (None, "cancelled", "error")
    finally:
        await mgr.end_session(session.session_id, force=True)
