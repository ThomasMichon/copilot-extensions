"""Session manager -- lifecycle, persistence, and event routing.

Manages all active sessions. Each session wraps one ACP client (which
owns the subprocess) and an EventLog for SSE streaming. State is
persisted to SQLite so sessions survive service restarts.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import os
import random
import re
import shlex
import shutil
import subprocess
import time
import uuid
from dataclasses import is_dataclass, replace
from datetime import datetime, timezone
from typing import Any

from agent_procutil import no_window_flags

from .acp_client import AcpClient
from .connect import ConnectError, ConnectStage, ConnectTracker
from .db import Database
from .events import EventLog
from .models import (
    AutoHandoffPolicy,
    ContextThresholds,
    PhasedTimeouts,
    RetentionConfig,
    ServiceConfig,
    SessionStatus,
)
from .transport import AgentProcess, SpawnTarget, _agent_worktrees_python, spawn

log = logging.getLogger("agent-bridge")

_REQUEST_OVERRIDES_KEY = "_agent_bridge_request_overrides"

# The resume recovery ladder (#1468): a resume stall is Copilot CLI's ACP
# startup race, not a broken session -- so on a failed/stalled resume attempt we
# stop the wedged child and re-resume (a fresh Copilot launch re-rolls the race)
# against the SAME persisted ACP session, preserving prior-turn context. Only
# after this many rounds all fail is the resume surfaced as a failure (the
# caller's end+create is then the last resort).
_MAX_RESUME_ROUNDS = 3


def _resolve_relay_launch_env(
    codespace_name: str, relay_port: int | None
) -> tuple[str, int | None]:
    """Resolve ``(prelude, port)`` for a detached CodeSpace launch's relay env.

    Process-boundary **only** (#1643): shell out to ``agent-codespaces
    relay-launch-env`` (from agent-codespaces' **own** venv) so a fix there
    reaches the dispatch path with **no agent-bridge redeploy** (retires the #733
    class). ``relay_port`` is the daemon's actually-bound live port (agent-bridge's
    own signal), injected via ``--relay-port``.

    There is **no** in-process ``agent_codespaces`` import fallback: the daemon
    runs from its own isolated venv where a provider package is neither importable
    nor on ``PATH`` (see :mod:`agent_bridge.provider_sources`). When the binstub is
    absent or the CLI fails, returns ``("", None)`` and the launch proceeds
    auth-light (fine for ACP + non-ADO turns).
    """
    binstub = shutil.which("agent-codespaces")  # marketplace-isolation: allow provider-management
    if not binstub:
        log.info(
            "agent-codespaces binstub absent -- launching Session Host "
            "auth-light for %s", codespace_name,
        )
        return "", None
    argv = [binstub, "relay-launch-env", codespace_name]
    if relay_port is not None:
        argv += ["--relay-port", str(relay_port)]
    try:
        r = subprocess.run(
            argv, capture_output=True, text=True, timeout=15,
            creationflags=no_window_flags(),
        )
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            return data.get("prelude", ""), data.get("port")
        log.warning(
            "agent-codespaces relay-launch-env exited %s -- launching Session "
            "Host auth-light for %s", r.returncode, codespace_name,
        )
    except Exception:
        log.warning(
            "agent-codespaces relay-launch-env CLI failed -- launching Session "
            "Host auth-light for %s", codespace_name, exc_info=True,
        )
    return "", None


def _resolve_codespace_ai_plugin_dirs(
    codespace_name: str, repo: str | None, repo_dir: str | None = None,
) -> list[str]:
    """Resolve the CodeSpace repo's OWN enabled ``.ai`` plugin dirs to fold into
    the Session-Host launch as ``--plugin-dir`` (dotfiles#1274 WS1-skills).

    **In-agent-bridge resolve** (PR2, dotfiles#1422): ships the vendored
    ``plugin_resolve`` package to the CodeSpace over the transport-exec seam
    (:mod:`target_exec` -> ``agent-codespaces ssh --remote-cmd``) and runs the
    canonical resolver against the workspace checkout -- the *same* logic as the
    local :func:`repo_own_plugins.repo_plugin_dir_args`. Replaces the retired
    ``agent-codespaces resolve-ai-plugin-dirs`` shell-out (agent-bridge is the
    session brain; agent-codespaces is the transport).

    ``repo_dir`` is the target's **concrete** ``workspace_folder`` (e.g.
    ``/workspaces/example-web``); the codespace spawn command frequently carries
    **no** ``--repo`` but ``workspace_folder`` is always known (parsed from the
    launch ``cd``), so the resolve keys off ``repo_dir``. ``repo`` is accepted
    for call-site compatibility.

    ``copilot --acp`` ignores ``enabledPlugins`` and only surfaces plugin skills
    via ``--plugin-dir``, so without this a dispatched agent never loads the
    product repo's own in-repo ``.ai`` skills/MCP. Best-effort: returns ``[]`` on
    any failure so the dispatch proceeds unchanged, never blocking the connect.
    """
    if not repo_dir:
        return []
    from . import repo_own_plugins_remote as rpr

    session = {"agent_name": f"codespace:{codespace_name}"}
    resolved, unresolved = rpr.resolve_remote_repo_ai_plugin_dirs(session, repo_dir)
    if resolved:
        log.info(
            "Resolved %d repo-own .ai plugin(s) for %s at %s -> --plugin-dir: %s",
            len(resolved), codespace_name, repo_dir, resolved,
        )
    if unresolved:
        log.info(
            "repo-own plugins for %s not locally resolvable (remote marketplace "
            "or missing) -- NOT staged: %s", codespace_name, unresolved,
        )
    return resolved


async def _resolve_remote_ai_plugin_dirs(
    transport: Any,
    venue_name: str,
    repo_dir: str | None,
) -> list[str]:
    """Resolve repo-own plugins through the selected remote venue transport."""
    if not repo_dir:
        return []
    from . import repo_own_plugins_remote as rpr

    resolved, unresolved = await rpr.resolve_remote_repo_ai_plugin_dirs_via(
        transport.run,
        repo_dir,
    )
    if resolved:
        log.info(
            "Resolved %d repo-own .ai plugin(s) for %s at %s: %s",
            len(resolved),
            venue_name,
            repo_dir,
            resolved,
        )
    if unresolved:
        log.info(
            "Repo-own plugins for %s were not remote-local directories: %s",
            venue_name,
            unresolved,
        )
    return resolved


def _append_plugin_dirs(acp_command: str, plugin_dirs: list[str]) -> str:
    """Append shell-safe Copilot ``--plugin-dir`` arguments."""
    return acp_command + "".join(
        f" --plugin-dir={shlex.quote(directory)}"
        for directory in plugin_dirs
    )


def _container_remote_child_argv(
    container_target: dict[str, Any],
    prepared: dict[str, Any],
    plugin_dirs: list[str],
    *,
    acp_command_override: str | None = None,
) -> list[str]:
    """Build the far-side child command from provider env + bridge policy."""
    acp_command = _append_plugin_dirs(
        acp_command_override
        or str(prepared.get("acp_command") or container_target["acp_command"]),
        plugin_dirs,
    )
    remote_env = prepared.get("remote_env")
    if remote_env:
        env_path = shlex.quote(str(remote_env))
        acp_command = f". {env_path}; rm -f {env_path}; {acp_command}"
    return ["bash", "-lc", acp_command]


def _failed_acp_handshake_command() -> str:
    """Return a deterministic non-ACP child that rejects initialize."""
    script = (
        "import json,sys;"
        "request=json.loads(sys.stdin.readline());"
        "print(json.dumps({'jsonrpc':'2.0','id':request.get('id'),"
        "'error':{'code':-32603,'message':'injected handshake failure'}}),"
        "flush=True)"
    )
    return f"exec python3 -c {shlex.quote(script)}"


# ``agent-codespaces claim`` exits with this code on a live claim conflict
# (a different, still-alive worktree already controls the CodeSpace). Kept in
# sync with ``agent_codespaces.__main__._BUSY_EXIT``.
_CODESPACE_BUSY_EXIT = 75
_CODESPACE_COORDINATION_EXIT = 78


class CodespaceClaimConflictError(Exception):
    """Raised when a CodeSpace is exclusively claimed by another worktree.

    A CodeSpace is fronted by exactly one agent-bridge Session Host (#897), so a
    second worktree dispatching to an already-claimed CodeSpace is bounced here
    rather than clobbering the incumbent's control. Carries the CLI's actionable
    guidance (let the owner finish, dispatch elsewhere, or take over with
    ``--force-claim``).
    """

    def __init__(self, codespace: str, owner: str, detail: str) -> None:
        self.codespace = codespace
        self.owner = owner
        self.detail = detail
        super().__init__(
            detail
            or (
                f"CodeSpace '{codespace}' is exclusively claimed by another "
                f"worktree; refusing to dispatch '{owner}' over it."
            )
        )


class CodespaceCoordinationRejectedError(Exception):
    """Raised when durable coordination rejects a CodeSpace dispatch."""

    def __init__(self, codespace: str, owner: str, detail: str) -> None:
        self.codespace = codespace
        self.owner = owner
        self.detail = detail
        super().__init__(
            detail
            or (
                f"CodeSpace '{codespace}' cannot be claimed for '{owner}' "
                "until durable coordination is repaired."
            )
        )


class RemoteHostRecoveryPendingError(RuntimeError):
    """Remote Session Host liveness is inconclusive; never spawn a duplicate."""


def _codespace_claim_key(target: "SpawnTarget") -> tuple[str, str] | None:
    """Resolve ``(codespace_name, owner_worktree)`` for a CodeSpace target.

    The owner is the *caller's* worktree (the dispatcher), carried on
    ``target.caller_worktree`` -- the same key agent-codespaces uses. Returns
    ``None`` when the target is not a resolvable CodeSpace or has no owner to key
    a claim on (degrade-safe: no owner -> no claim). Deterministic from the
    persisted target, so it survives a daemon restart (unlike a per-session
    in-memory attribute).
    """
    name: str | None = None
    cs = getattr(target, "codespace", None)
    if isinstance(cs, dict) and cs.get("name"):
        name = cs["name"]
    elif getattr(target, "spawn_command", None):
        from .session_host.codespace_transport import parse_codespace_target

        parsed = parse_codespace_target(target.spawn_command)
        if parsed:
            name = parsed.get("name")
    if not name:
        return None
    owner = getattr(target, "caller_worktree", None)
    if not owner:
        return None
    return name, owner


def _claim_codespace(
    codespace_name: str,
    owner: str,
    *,
    holder_ref: str | None = None,
) -> tuple[str, str]:
    """Acquire the exclusive, worktree-keyed CodeSpace claim before the
    Session-Host transport is established (#897 Increment B step 2).

    Session-Host dispatch never runs ``agent-codespaces ssh``, so the
    direct-path claim enforcement is bypassed for a bridge dispatch -- this is
    where the daemon closes that gap. Shells the ``agent-codespaces claim`` seam
    rather than importing ``agent_codespaces`` in the bridge venv (#796), so the
    two separately-versioned plugin venvs stay decoupled; mirrors
    ``gh_account``'s shell-out-to-a-sibling-binstub pattern.

    Returns ``("ok", "")`` on success or a degrade-safe skip,
    ``("conflict", detail)`` for a live owner conflict, or
    ``("coordination-rejected", detail)`` for a compatible binding rejection.
    """
    if os.environ.get("AGENT_CODESPACES_DISABLE_CLAIM") and not holder_ref:
        return "ok", ""
    if not codespace_name or (not owner and not holder_ref):
        return "ok", ""
    binstub = shutil.which("agent-codespaces")  # marketplace-isolation: allow provider-management
    if not binstub:
        return "ok", ""
    creationflags = no_window_flags()
    command = [binstub, "claim", codespace_name]
    if owner:
        command.extend(["--owner", owner])
    if holder_ref:
        command.extend(["--holder-ref", holder_ref])
    try:
        result = subprocess.run(
            command,
            capture_output=True, text=True, timeout=30,
            creationflags=creationflags,
        )
    except Exception as exc:
        log.info("CodeSpace claim skipped for %s: %s", codespace_name, exc)
        return "ok", ""
    if result.returncode == _CODESPACE_BUSY_EXIT:
        return "conflict", (result.stderr or result.stdout or "").strip()
    if result.returncode == _CODESPACE_COORDINATION_EXIT:
        return (
            "coordination-rejected",
            (result.stderr or result.stdout or "").strip(),
        )
    if result.returncode != 0:
        # Any other non-zero is a bookkeeping error, not a conflict -- never
        # block the dispatch on it (degrade-safe, mirroring the direct path).
        log.info(
            "CodeSpace claim for %s exited %s: %s",
            codespace_name, result.returncode, (result.stderr or "").strip(),
        )
    return "ok", ""


def _release_codespace_claim(codespace_name: str, owner: str) -> bool:
    """Release a Session-Host CodeSpace claim and report success.

    Ordinary teardown remains best-effort, while destructive parity rollback
    uses the return value to avoid claiming cleanup before ownership is gone.
    """
    if os.environ.get("AGENT_CODESPACES_DISABLE_CLAIM"):
        return True
    if not owner or not codespace_name:
        return True
    binstub = shutil.which("agent-codespaces")  # marketplace-isolation: allow provider-management
    if not binstub:
        return False
    creationflags = no_window_flags()
    try:
        result = subprocess.run(
            [binstub, "release-claim", codespace_name, "--owner", owner],
            capture_output=True, text=True, timeout=30,
            creationflags=creationflags,
        )
    except Exception:
        return False
    return result.returncode == 0


# Session states that "occupy" a workspace -- a workspace with a session
# in any of these states cannot accept a second concurrent session.
# STOPPED is included because it is resumable (the ACP session persists),
# so it still owns the workspace until explicitly ended.
_ACTIVE_STATES = frozenset({
    SessionStatus.STARTING,
    SessionStatus.RUNNING,
    SessionStatus.IDLE,
    SessionStatus.STOPPING,
    SessionStatus.STOPPED,
})


class SessionConflictError(Exception):
    """Raised when an agent already has an active session and concurrent
    sessions are not allowed.

    CodeSpace (command-type) agents share a single checkout that cannot be
    safely multiplexed, so only one active session is permitted per agent.
    """

    def __init__(self, agent_name: str, existing_session_id: str) -> None:
        self.agent_name = agent_name
        self.existing_session_id = existing_session_id
        super().__init__(
            f"Agent '{agent_name}' already has an active session "
            f"{existing_session_id}; only one session per CodeSpace is "
            "allowed. Reuse it (send to the session id) or end it first."
        )


class SessionBusyError(Exception):
    """Raised when a stop/end is refused because the session is hosting active
    background sub-agents.

    Tearing the Copilot process down would kill the in-process background
    agents it is running (e.g. the PR daemon, or another agent session a
    conversation is waiting on). Callers that genuinely intend to abandon that
    work pass ``force=True`` to override.
    """

    def __init__(self, session_id: str, active_background_tasks: list[str]) -> None:
        self.session_id = session_id
        self.active_background_tasks = active_background_tasks
        tasks = ", ".join(active_background_tasks) or "(unknown)"
        super().__init__(
            f"Session {session_id} has active background tasks [{tasks}]; "
            "tearing it down would kill them. Wait for them to finish, then "
            "end the session."
        )


class DaemonDrainingError(Exception):
    """Raised when new work is refused because the daemon is draining.

    During a zero-downtime handoff the daemon stops accepting new sessions and
    new turns so in-flight work can settle before it exits. Callers should
    retry against the routing-table endpoint -- by the time they retry, the
    successor daemon owns the route and answers.
    """

    def __init__(self, what: str = "request") -> None:
        self.what = what
        super().__init__(
            f"agent-bridge is draining for a redeploy and is not accepting a "
            f"new {what}; retry shortly (the successor daemon will answer)."
        )


class ProviderTargetRefreshError(RuntimeError):
    """A persisted provider target cannot be refreshed safely."""

    public_message = (
        "Provider target could not be refreshed safely; retry after repairing "
        "provider configuration or recreate the session."
    )


def _workspace_key(
    agent_name: str | None,
    target: SpawnTarget,
    caller_id: str | None,
) -> tuple | None:
    """Compute the concurrency key for a session, or None if unguarded.

    A "workspace" is a checkout that can hold at most one active session.

    - Command-type (CodeSpace / provider) agents share one checkout that
      cannot be multiplexed, so the key is the agent name alone -- every
      caller maps to the same single session regardless of worktree.
    - Local / SSH / worktree agents can run concurrent sessions against
      separate checkouts (each local worktree has its own caller_id), so
      they are not hard-guarded here (returns None).
    """
    if agent_name and target.type == "command":
        return ("agent", agent_name)
    return None

# -- Name generator ----------------------------------------------------------

_ADJECTIVES = [
    "swift", "bright", "calm", "deft", "eager", "fair", "keen", "bold",
    "warm", "wise", "neat", "glad", "true", "pure", "crisp", "clear",
]
_NOUNS = [
    "falcon", "cedar", "river", "spark", "forge", "bloom", "ridge", "crest",
    "grove", "haven", "quest", "drift", "flame", "stone", "brook", "dawn",
]


def _generate_name() -> str:
    return f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}"  # noqa: S311


# Structured milestone markers: a dispatched agent reports progress with lines
# like ``PROGRESS: build=ok`` or ``PROGRESS commit=<sha> pr=123`` (the colon is
# optional, matching the dispatch skill's documented convention). The bridge
# captures the latest value per key and exposes it in status, so a watcher gets
# ground-truth milestones (did it build? push? open a PR?) without grepping the
# free-text feed or shelling into the host (#46.3 / #46.4).
_PROGRESS_LINE_RE = re.compile(r"\bPROGRESS:?\s+(.+)")
_PROGRESS_KV_RE = re.compile(r"(\w+)=(\S+)")


def _parse_progress_markers(text: str) -> dict[str, str]:
    """Extract ``PROGRESS: key=value`` milestone markers from agent text."""
    found: dict[str, str] = {}
    if not text or "PROGRESS" not in text:
        return found
    for line in text.splitlines():
        m = _PROGRESS_LINE_RE.search(line)
        if not m:
            continue
        for key, value in _PROGRESS_KV_RE.findall(m.group(1)):
            found[key] = value
    return found


async def _cleanup_worktree(target: SpawnTarget, turn_count: int) -> None:
    """Attempt to clean up the worktree associated with a session.

    For 0-turn sessions (unused worktrees), runs agent-worktrees cleanup
    with --include-unused to remove worktrees that have no commits. For
    sessions with turns, logs a notice -- manual finalization is required.
    """
    worktree_id = target.worktree_id
    if not worktree_id or not target.project:
        return

    if turn_count > 0:
        log.info(
            "Worktree %s has %d turn(s) -- skipping automatic cleanup "
            "(manual finalization required)",
            worktree_id, turn_count,
        )
        return

    # 0-turn session: run cleanup --clean --include-unused to remove
    # all accumulated unused worktrees (including this one)
    # Resolve the agent-worktrees runtime interpreter via the junction-free
    # current-version marker (the .venv junction is retired; see
    # _agent_worktrees_python).
    try:
        python = _agent_worktrees_python()
    except RuntimeError as exc:
        log.warning("Cannot cleanup worktree %s: %s", worktree_id, exc)
        return

    env = os.environ.copy()
    aw_lib = os.path.join(os.path.expanduser("~"), ".agent-worktrees", "lib")
    if os.path.isdir(aw_lib):
        env["PYTHONPATH"] = aw_lib
    env["PYTHONUTF8"] = "1"

    # Global --project (before the subcommand); the ambient $WORKTREE_PROJECT
    # identity fallback was retired (cwd-resolution Phase 3) and this cleanup
    # runs from a neutral daemon cwd outside the target repo.
    cmd = [python, "-m", "agent_worktrees", "--project", target.project,
           "cleanup", "--clean", "--include-unused"]
    log.info("Cleaning up unused worktrees (session %s was 0-turn): %s", worktree_id, " ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            log.info("Worktree cleanup completed successfully")
            if stdout:
                for line in stdout.decode(errors="replace").strip().splitlines():
                    log.debug("cleanup: %s", line)
        else:
            err = stderr.decode(errors="replace").strip()
            log.warning("Worktree cleanup failed (exit %d): %s", proc.returncode, err)
    except Exception as exc:
        log.warning("Worktree cleanup error: %s", exc)


def _venue_workspace_cwd(target: SpawnTarget) -> str | None:
    """The venue's concrete workspace folder (a container fleet's repo checkout),
    surfaced by the provider's ``namespace-resolve`` as ``venue.workspace_folder``.

    Used as the ACP ``session/new`` cwd so a dispatched agent runs from the repo
    checkout inside the venue rather than the home-dir default (the agent
    otherwise works blind). Returns ``None`` when the provider surfaced no
    workspace, preserving the existing default. This is the ACP *session* cwd
    (interpreted inside the venue by the launched Copilot), NOT the host spawn
    subprocess cwd -- ``target.cwd`` is left untouched.
    """
    venue = getattr(target, "venue", None)
    if isinstance(venue, dict):
        ws = venue.get("workspace_folder")
        if isinstance(ws, str) and ws.strip():
            return ws.strip()
    return None


def _default_cwd(target: SpawnTarget) -> str:
    """Derive a plausible default CWD for a spawn target.

    The ACP runtime validates this path before it creates or loads a session, so
    the fallback must be a directory that is sensible for the target platform
    even when the SSH profile does not expose a user name.
    """
    user = target.user
    if target.ssh_shell in ("pwsh", "powershell", "cmd"):
        if not user or user.lower() == "root":
            return "C:\\"
        return f"C:\\Users\\{user}"
    if user == "root":
        return "/root"
    if not user:
        return "/"
    return f"/home/{user}"


# Liveness (#145): a RUNNING session whose ACP event stream has produced no
# frame for this long -- while its transport is still alive -- is treated as a
# silent mid-turn *stall* (distinct from a healthy long reasoning step). Chosen
# so a normal deep-reasoning step never trips it: modern models routinely think
# silently (no ACP frame, no tool call) for 3-4 minutes on a hard step -- live
# dispatch traces show single reasoning turns of 191-223s (12k+ reasoning
# tokens) -- so the earlier 180s cutoff cried "stalled" on healthy thinking and
# made the operator recreate a working session (dotfiles#1276). 300s clears the
# observed deep-think band with margin while still catching a genuine wedge.
_STALL_AFTER_S = 300.0


class Session:
    """In-memory state for a single agent-bridge session."""

    def __init__(
        self,
        session_id: str,
        name: str,
        target: SpawnTarget,
        agent_name: str | None = None,
        caller_id: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.name = name
        self.agent_name = agent_name
        self.caller_id = caller_id
        self.target = target
        self.client: AcpClient | None = None
        self.status = SessionStatus.CREATED
        # Status read from durable storage during daemon startup before
        # rehydrate converts an adoptable session to STOPPED. Used only by the
        # startup reattach path to decide whether a surviving Session Host needs
        # an explicit driver nudge.
        self.restart_status: str | None = None
        self.turn_count = 0
        self.context_size: int | None = None
        self.context_used: int | None = None
        self.usage_model: str | None = None
        # Per-session model / reasoning-effort override (agent-bridge create
        # --model/--effort), re-applied to the ACP client on within-daemon
        # resume / reattach. In-memory only (a survivable child already holds the
        # value it was given at session/new; not persisted across daemon restart).
        self.model_override: str | None = None
        self.effort_override: str | None = None
        self.last_usage_at: float | None = None
        self._crossed_thresholds: set[str] = set()
        self.created_at = time.time()
        self.updated_at = self.created_at
        # Liveness tracking (#145). ``last_output_at`` advances on EVERY ACP
        # frame -- unlike ``updated_at``, which only moves at turn boundaries, so
        # a healthy long turn is otherwise indistinguishable from a wedge.
        # ``last_heartbeat_at`` is a periodic transport-liveness beat. Together
        # they separate a *stalled* agent (output stale, channel alive) from a
        # *dead* channel (heartbeat stale). In-memory only; live sessions only.
        self.last_output_at: float | None = None
        self.last_heartbeat_at: float | None = None
        # Count of active event subscribers (SSE streams / attached fronts).
        # Drives the idle reaper (#1826): a session with zero subscribers is
        # "unwatched" and eligible for idle reclamation. In-memory only.
        self.subscriber_count = 0
        self.event_log: EventLog | None = None
        self.acp_session_id: str | None = None
        # Effective per-session MCP configuration for any within-daemon fresh
        # recreation. Deliberately in-memory only: MCP definitions may contain
        # launch credentials and must not be serialized into sessions.db.
        self.mcp_servers: list[dict[str, Any]] = []
        self.parity_fault_result: dict[str, Any] | None = None
        # Structured milestone markers the dispatched agent has reported via
        # `PROGRESS: key=value` lines (e.g. build=ok, commit=<sha>, pr=<id>) --
        # captured from agent_message text and surfaced in status (#46.3).
        self.progress: dict[str, str] = {}
        self._prompt_task: asyncio.Task | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._turn_start_lock = asyncio.Lock()
        # Set when a context-pressure handoff is owed but the session is not yet
        # idle (usage crosses critical mid-turn). The turn-settle path fires the
        # deferred handoff once the session is idle. In-memory only.
        self._handoff_pending = False

    @property
    def pid(self) -> int | None:
        if self.client and self.client.is_running:
            return self.client.pid
        return None

    @property
    def active_background_tasks(self) -> list[str]:
        """Copilot agent_ids of background sub-agents this session is hosting.

        Empty when the client is gone or no sub-agents are running. Surfaced in
        status and used to gate teardown (see SessionBusyError).
        """
        if self.client:
            return self.client.active_background_tasks
        return []

    @property
    def has_active_background_tasks(self) -> bool:
        return bool(self.client and self.client.has_active_background_tasks)

    @property
    def context_pct(self) -> float | None:
        """Context usage as a percentage, or None if unknown."""
        if self.context_size and self.context_used is not None:
            return round(self.context_used / self.context_size * 100, 1)
        return None

    def touch(self) -> None:
        self.updated_at = time.time()

    def note_heartbeat(self, now: float | None = None) -> None:
        """Record that the transport was confirmed alive (periodic beat)."""
        self.last_heartbeat_at = now if now is not None else time.time()

    def liveness_state(
        self, now: float | None = None, stall_after_s: float = _STALL_AFTER_S,
    ) -> str | None:
        """Derive a liveness signal for a RUNNING session, else ``None``.

        Uses output-flow vs transport-liveness -- which the turn-boundary
        ``updated_at`` cannot (#145):

        - ``active``       -- an ACP frame flowed within ``stall_after_s``.
        - ``stalled``      -- transport alive (client running) but no ACP frame
                              for ``stall_after_s`` (silent mid-turn stall).
        - ``disconnected`` -- transport is gone (client not running).

        Returns ``None`` for non-RUNNING sessions (liveness is about an
        in-flight turn; idle/stopped/ended have nothing to stall).
        """
        if self.status != SessionStatus.RUNNING:
            return None
        now = now if now is not None else time.time()
        if not (self.client and self.client.is_running):
            return "disconnected"
        if self.last_output_at is None:
            return "active"
        if now - self.last_output_at > stall_after_s:
            return "stalled"
        return "active"

    def is_at_rest(self) -> bool:
        """Return whether the durable ACP event tail says the turn ended."""
        if self.status == SessionStatus.IDLE:
            return True
        if self.status != SessionStatus.RUNNING or self.event_log is None:
            return False
        return (
            self.event_log.telemetry_conversation_state
            in {"end-turn", "cancelled"}
            and self.event_log.active_tool_call(include_nested=False) is None
        )

    def public_state(self) -> tuple[SessionStatus, bool, str | None]:
        """Return a consistent status, at-rest, and liveness projection."""
        at_rest = self.is_at_rest()
        return (
            SessionStatus.IDLE if at_rest else self.status,
            at_rest,
            None if at_rest else self.liveness_state(),
        )


class SessionManager:
    """Manages all agent-bridge sessions with SQLite persistence."""

    MAX_SESSIONS = 100

    # A drain that outlives this many seconds with no handoff completing is
    # treated as stuck/aborted and auto-released so the daemon self-heals
    # instead of returning 503 forever (#1757). Generous enough to cover a slow
    # real cutover (health probe + full drain_timeout), short enough that an
    # aborted cutover does not strand the daemon for hours.
    DRAIN_AUTO_RELEASE_S = 900.0
    # How often the watchdog logs a "still draining" WARN while the gate is open.
    DRAIN_WARN_INTERVAL_S = 60.0

    def __init__(
        self,
        db: Database,
        *,
        context_thresholds: ContextThresholds | None = None,
        auto_handoff: AutoHandoffPolicy | None = None,
        timeouts: PhasedTimeouts | None = None,
        retention: RetentionConfig | None = None,
        drain_auto_release_s: float | None = None,
        drain_warn_interval_s: float | None = None,
        session_host_state_dir: str | None = None,
        session_host_stale_reap_seconds: float = 0.0,
        graceful_cancel_settle_seconds: float = 45.0,
        cancel_turns_on_redeploy: bool = False,
        idle_reap_ttl_seconds: float = 0.0,
        live_stall_interrupt_after_s: float = 900.0,
        session_host_unexpected_reap_seconds: float = 60.0,
        session_host_active_reap_seconds: float = 0.0,
    ) -> None:
        self._db = db
        self._sessions: dict[str, Session] = {}
        self._resolver: Any | None = None
        # Session-Host mode is now the ONLY mode (dotfiles#1478): every local and
        # CodeSpace child lives in a survivable Session Host that outlives a
        # frontend restart. The host index is the durable session_id ->
        # host-endpoint map used to reattach. (ssh/command targets still use the
        # process-owned transport in start_session -- an SshSpawner/ElevatedSpawner
        # to host those far-side is the remaining gap, ThomasMichon/copilot-extensions#566.)
        self._session_host_stale_reap_seconds = session_host_stale_reap_seconds
        self._graceful_cancel_settle_seconds = graceful_cancel_settle_seconds
        # Redeploy turn-cancel policy (dotfiles#1661). Default False = the
        # invariant: a frontend redeploy/cutover/shutdown DETACHES in-flight
        # turns (leaves them running on their Session Host for reattach) rather
        # than cancelling them. Cancelling the remote task is an explicit host
        # action only (interrupt_turn / explicit stop). True restores the legacy
        # cancel-then-Resume behavior.
        self._cancel_turns_on_redeploy = cancel_turns_on_redeploy
        # Idle-session reaper TTL (#1826): stop an idle, unwatched session past
        # this many seconds to free its Copilot child (resumable via replay).
        # 0 disables. Only acts in Session-Host mode.
        self._idle_reap_ttl_seconds = idle_reap_ttl_seconds
        # Live-stall interrupt threshold (#2427, Phase 5): the watchdog
        # interrupts a RUNNING session that is liveness 'stalled' AND still has a
        # live _prompt_task once its silence exceeds this many seconds. Distinct
        # from (and much larger than) the 180s stall so a legitimately long tool
        # call is not aborted. 0 disables the live-stall interrupt entirely.
        self._live_stall_interrupt_after_s = live_stall_interrupt_after_s
        # Session-host self-reap grace (#51): how long an idle, front-less child
        # lingers after an *unexpected* disconnect before the host reaps itself.
        # Handed to every LocalSpawner-launched host. 0 disables the timer (the
        # graceful-detach fast path still reaps a reapable child promptly).
        self._session_host_unexpected_reap_seconds = session_host_unexpected_reap_seconds
        # Bounded keep-alive for an ACTIVE (mid-turn / active background work)
        # front-less child after an unexpected disconnect (#145): the detached
        # host holds it this long so a reconnecting front can resume the in-flight
        # turn, then lets it go (the session stays resumable via fresh child +
        # load_session replay). 0 disables (legacy: an active child lives until
        # its own stop). Handed to every LocalSpawner/CodeSpaceSpawner.
        self._session_host_active_reap_seconds = session_host_active_reap_seconds
        self._host_index: Any = None
        self._remote_recovery_inconclusive: set[str] = set()
        self._remote_recovery_skipped: set[str] = set()
        # Live remote-boundary forwards (session_id -> LocalForward). Held so a
        # CodeSpace/mesh Session Host's -L forward can be refreshed on reattach
        # and torn down on teardown. Empty for local hosts.
        self._forwards: dict[str, Any] = {}
        # Live dedicated credential-relay supervisors (session_id -> relays).
        # These are intentionally separate from the frontend-refreshed -L above:
        # their lifetime follows the remote Session Host/child.
        self._relays: dict[str, list[Any]] = {}
        # Cross-process ownership for trusted container SSH/Session-Host use.
        # This is bridge-owned (not held by agent-containers' launch wrapper)
        # and follows the remote Host record's lifetime.
        self._container_locks: dict[str, tuple[Any, str]] = {}
        self._container_lock_sessions: dict[str, str] = {}
        # Strong refs to in-flight best-effort remote-reap tasks (so they are not
        # GC'd mid-flight); each removes itself on completion.
        self._remote_reap_tasks: set[Any] = set()
        from pathlib import Path as _Path

        from .session_host.host_index import HostIndex
        # Default the host state dir next to the DB (isolated per Database) rather
        # than a hardcoded ~/.agent-bridge/hosts. In production the DB is
        # ~/.agent-bridge/sessions.db, so this still resolves to
        # ~/.agent-bridge/hosts -- identical behavior -- but a test/embedded use
        # with a temp DB gets an isolated index instead of writing into (and
        # sharing, across parallel runs) the real developer/CI home. Now that
        # Session Hosts are always on the index is ALWAYS constructed, so this
        # isolation matters (dotfiles#1478 review).
        if session_host_state_dir:
            sd = _Path(session_host_state_dir).expanduser()
        else:
            sd = _Path(self._db.db_path).expanduser().parent / "hosts"
        sd.mkdir(parents=True, exist_ok=True)
        self._host_index = HostIndex(sd / "index.json")
        self._thresholds = context_thresholds or ContextThresholds()
        # Context-pressure handoff policy (off by default). When enabled, a
        # session crossing the critical threshold rolls the worktree in place
        # instead of dead-ending. Strong refs to in-flight auto-handoff tasks so
        # they are not GC'd mid-cutover (each removes itself on completion).
        self._auto_handoff = auto_handoff or AutoHandoffPolicy()
        self._auto_handoff_tasks: set[asyncio.Task[None]] = set()
        self._timeouts = timeouts or PhasedTimeouts()
        self._retention = retention or RetentionConfig()
        # Drain gate: when True the daemon refuses *new* sessions and *new*
        # turns so in-flight work can settle before a zero-downtime handoff.
        # Set via drain()/set_draining(); never persisted (a fresh daemon
        # starts un-drained). Teardown (stop/end) is *never* gated -- it is the
        # operation the drain is waiting for (#1755).
        self._draining = False
        # Drain observability + bounded lifetime (#1757). When the gate opens we
        # record when/why/by-whom and arm a watchdog that WARNs on an interval
        # and finally auto-releases the gate if no cutover ever retires this
        # daemon -- so a stuck/aborted drain self-heals rather than 503'ing new
        # work (including the operator's own diagnosis session) forever.
        self._draining_since: float | None = None
        self._drain_reason: str | None = None
        self._drain_source: str | None = None
        self._drain_watchdog: asyncio.Task[None] | None = None
        self._drain_auto_release_s = (
            self.DRAIN_AUTO_RELEASE_S if drain_auto_release_s is None
            else float(drain_auto_release_s)
        )
        self._drain_warn_interval_s = (
            self.DRAIN_WARN_INTERVAL_S if drain_warn_interval_s is None
            else float(drain_warn_interval_s)
        )
        self._rehydrate()

    def set_resolver(self, resolver: Any) -> None:
        """Attach the live resolver used for safe provider target refresh."""
        self._resolver = resolver

    @staticmethod
    def _provider_backed_target(target: SpawnTarget) -> bool:
        """Whether a persisted target carries namespace-provider metadata."""
        return any(
            isinstance(value, dict) and bool(value)
            for value in (target.codespace, target.container, target.venue)
        )

    async def _refresh_provider_target(self, session: Session) -> None:
        """Re-resolve a stopped provider session before spawning a new child.

        Surviving Session Hosts are reattached before this seam, so only a
        genuinely fresh launch adopts the current provider declaration.
        Session-owned placement, caller identity, and request environment stay
        bound to the existing bridge session.
        """
        if (
            self._resolver is None
            or not session.agent_name
            or not self._provider_backed_target(session.target)
        ):
            return

        persisted = session.target
        persisted_venue = (
            persisted.venue if isinstance(persisted.venue, dict) else {}
        )
        overrides = persisted_venue.get(_REQUEST_OVERRIDES_KEY)
        if not isinstance(overrides, dict):
            raise ProviderTargetRefreshError(
                "Provider target predates override provenance; recreate the "
                "session to adopt the current provider configuration"
            )
        request_env = overrides.get("env")
        request_copilot_args = overrides.get("copilot_args")
        if not isinstance(request_env, dict) or not isinstance(
            request_copilot_args, list
        ):
            raise ProviderTargetRefreshError(
                "Provider target has invalid override provenance; recreate the "
                "session to adopt the current provider configuration"
            )
        try:
            resolved = await self._resolver.resolve_async(session.agent_name)
        except Exception as exc:
            log.warning(
                "Could not refresh provider target for session %s (%s)",
                session.session_id,
                session.agent_name,
                exc_info=True,
            )
            raise ProviderTargetRefreshError(
                "Current provider target could not be resolved; repair provider "
                "configuration and retry"
            ) from exc
        refreshed = replace(
            resolved,
            cwd=persisted.cwd,
            project=persisted.project,
            worktree_id=persisted.worktree_id,
            caller_worktree=persisted.caller_worktree,
            caller_owner_ref=persisted.caller_owner_ref,
            env={**resolved.env, **request_env},
            copilot_args=[
                *resolved.copilot_args,
                *request_copilot_args,
            ],
            venue={
                **(resolved.venue or {}),
                _REQUEST_OVERRIDES_KEY: {
                    "env": dict(request_env),
                    "copilot_args": list(request_copilot_args),
                },
            },
        )
        session.target = refreshed
        self._db.update_session_target(
            session.session_id,
            refreshed.to_json(),
            refreshed.cwd,
        )
        log.info(
            "Refreshed provider target for stopped session %s (%s)",
            session.session_id,
            session.agent_name,
        )

    @property
    def is_draining(self) -> bool:
        """True once drain() has begun -- new sessions/turns are refused."""
        return self._draining

    def set_draining(
        self,
        value: bool,
        *,
        reason: str | None = None,
        source: str | None = None,
    ) -> None:
        """Open (True) or release (False) the drain gate.

        Logs the transition (with ``source``/``reason``) so a drained daemon is
        never invisible, and -- on open -- arms a watchdog that bounds how long
        the daemon may sit drained before auto-releasing (#1757). Idempotent: a
        call that does not change the gate state is a quiet no-op (the existing
        watchdog and its ``since`` timestamp are preserved).
        """
        value = bool(value)
        if value == self._draining:
            return
        self._draining = value
        if value:
            self._draining_since = time.time()
            self._drain_reason = reason
            self._drain_source = source
            log.info(
                "Drain gate OPENED (source=%s reason=%s) -- refusing new "
                "sessions/turns; reads and teardown still served",
                source or "?", reason or "?",
            )
            self._arm_drain_watchdog()
        else:
            held = (
                time.time() - self._draining_since
                if self._draining_since is not None else 0.0
            )
            log.info(
                "Drain gate RELEASED (source=%s) after %.0fs -- accepting new "
                "work", source or "?", held,
            )
            self._draining_since = None
            self._drain_reason = None
            self._drain_source = None
            self._cancel_drain_watchdog()

    def drain_status(self) -> dict[str, Any]:
        """Snapshot of the drain gate for /health and monitoring (#1757).

        Exposes *how long* the daemon has been drained and when the watchdog
        will auto-release, so a stuck drain is visible without grepping logs.
        """
        now = time.time()
        since = self._draining_since
        held = (now - since) if since is not None else None
        auto_at = (
            since + self._drain_auto_release_s
            if since is not None and self._drain_auto_release_s > 0 else None
        )
        return {
            "draining": self._draining,
            "since": (
                datetime.fromtimestamp(since, tz=timezone.utc).isoformat()
                if since is not None else None
            ),
            "held_s": round(held, 1) if held is not None else None,
            "reason": self._drain_reason,
            "source": self._drain_source,
            "auto_release_at": (
                datetime.fromtimestamp(auto_at, tz=timezone.utc).isoformat()
                if auto_at is not None else None
            ),
            # Live Session Host census (dotfiles#1656): how many independent
            # Session Hosts (each owning a possibly-mid-turn child) this daemon
            # is currently fronting. Surfaced on /health so a drain/cutover never
            # looks "clean" while live hosts it must preserve go unaccounted for.
            "live_host_count": self.live_host_count,
        }

    def _arm_drain_watchdog(self) -> None:
        """Start the bounded-drain watchdog if an event loop is running."""
        self._cancel_drain_watchdog()
        if self._drain_auto_release_s <= 0:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop (e.g. a synchronous unit test toggling the gate).
            # The bounded-lifetime backstop is a no-op here; the gate can still
            # be released manually or by the next drain() call under a loop.
            return
        self._drain_watchdog = loop.create_task(self._drain_watchdog_loop())

    def _cancel_drain_watchdog(self) -> None:
        wd = self._drain_watchdog
        self._drain_watchdog = None
        if wd is not None and not wd.done():
            wd.cancel()

    async def _drain_watchdog_loop(self) -> None:
        """Bound how long the daemon may sit drained (#1757).

        WARNs on an interval while the gate is open, then auto-releases it once
        the drain outlives ``_drain_auto_release_s`` with no cutover retiring
        the daemon. A completed handoff shuts the process down before this
        fires; a manual undrain cancels it. This is the self-heal for an
        aborted cutover (or a diagnosis session that can't get in because it is
        itself 503'd) that would otherwise leave the daemon drained forever.
        """
        interval = max(1.0, self._drain_warn_interval_s)
        deadline = (
            (self._draining_since or time.time()) + self._drain_auto_release_s
        )
        try:
            while self._draining:
                await asyncio.sleep(interval)
                if not self._draining:
                    return
                held = (
                    time.time() - self._draining_since
                    if self._draining_since is not None else 0.0
                )
                if time.time() >= deadline:
                    log.warning(
                        "Drain gate open %.0fs (source=%s reason=%s) with no "
                        "handoff completing -- auto-releasing to self-heal (a "
                        "cutover likely aborted)",
                        held, self._drain_source or "?",
                        self._drain_reason or "?",
                    )
                    self.set_draining(False, source="watchdog-auto-release")
                    return
                log.warning(
                    "Still draining after %.0fs (source=%s reason=%s); "
                    "auto-release at %.0fs",
                    held, self._drain_source or "?", self._drain_reason or "?",
                    self._drain_auto_release_s,
                )
        except asyncio.CancelledError:
            return

    @property
    def cancel_turns_on_redeploy(self) -> bool:
        """Whether a frontend redeploy/cutover/shutdown cancels in-flight turns.

        Default False = the dotfiles#1661 invariant (detach-only; the remote
        turn keeps running on its Session Host for reattach). Read by the app
        lifespan shutdown to pass ``cancel_turn`` to ``stop_session``.
        """
        return self._cancel_turns_on_redeploy

    @property
    def live_host_count(self) -> int:
        """How many live Session Hosts this daemon is currently fronting.

        Each host independently owns a (possibly mid-turn) child that survives a
        frontend restart. Surfaced on /health and in the drain result so a
        drain/cutover never looks "clean" while live hosts it must preserve go
        unaccounted for (dotfiles#1656)."""
        return len(self._live_host_records())

    def busy_sessions(self) -> list[str]:
        """Session IDs that must not be torn down: actively streaming a turn
        (RUNNING) or mid connect/resume (STARTING), hosting active background
        sub-agents (the dev57 busy oracle), or backed by a live **remote**
        Session Host whose far-side child may be mid-work while the local status
        is stale (dotfiles#1633).

        Remote-boundary correctness: ``codespace:``/``ssh`` sessions are the
        majority of hosts, and their turn runs across the boundary -- so the
        local status alone is not a reliable "idle" signal, and keying busy
        purely off it let ``drain`` report a false-clean "0 busy" and tear a live
        remote turn down. A live remote host is therefore counted unless its
        session is *at-rest* ``IDLE`` (which a cutover preserves via host
        reattach, so it need not block drain; the idle reaper is likewise
        remote-aware). This is the signal drain() waits on."""
        busy: set[str] = set()
        for sid, session in self._sessions.items():
            if session.status in (SessionStatus.RUNNING, SessionStatus.STARTING) \
                    or session.has_active_background_tasks:
                busy.add(sid)
        for sid in self._live_remote_host_sessions():
            session = self._sessions.get(sid)
            if session is not None and session.status == SessionStatus.IDLE:
                # At-rest remote host -> preserved across a cutover by host
                # reattach; does not need to block drain.
                continue
            busy.add(sid)
        return sorted(busy)

    async def graceful_cancel_for_redeploy(
        self,
        *,
        settle_timeout: float | None = None,
        exclude_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Prepare in-flight turns for a frontend redeploy / cutover / shutdown.

        **Default (detach-only, the dotfiles#1661 invariant).** A frontend
        restart is a *transport* event, not an explicit host-agent cancel, so
        this does **not** touch the remote agent's turn. The Session Host keeps
        running the child and buffers its frames ("tmux for the agent"), so the
        restarted frontend reattaches and the SAME in-flight turn continues with
        no gap and no re-run. Cancelling the remote task is reserved for explicit
        host actions (``interrupt_turn`` / an explicit stop).

        **Legacy (opt-in, ``cancel_turns_on_redeploy=True``).** Restores the old
        behavior: inject an ACP ``session/cancel`` into every RUNNING turn
        (except ``exclude_session_id`` -- e.g. the agent updating its own
        bridge), flag each host-backed session ``resume_on_reattach`` so the
        restarted frontend sends a single "Resume", and wait up to the settle
        budget for the cancelled turns to stop.

        Returns a summary (``mode`` is ``"detach-only"`` or ``"cancel"``).
        """
        import asyncio as _asyncio

        targets = [
            sid for sid, s in self._sessions.items()
            if s.status == SessionStatus.RUNNING and sid != exclude_session_id
        ]

        if not self._cancel_turns_on_redeploy:
            # Detach-only (dotfiles#1661): leave every in-flight turn running on
            # its Session Host; the successor frontend reattaches and continues
            # it. No ACP cancel, no Resume nudge, no settle wait.
            if targets:
                log.info(
                    "Redeploy detach-only: leaving %d in-flight turn(s) running "
                    "on their Session Host(s) for reattach (no cancel): %s",
                    len(targets), ", ".join(targets),
                )
            return {
                "cancelled": [], "preserved": targets, "settled": True,
                "mode": "detach-only", "enabled": True,
            }

        # --- opt-in legacy: assertively cancel in-flight turns ---------------
        settle = (self._graceful_cancel_settle_seconds
                  if settle_timeout is None else settle_timeout)
        cancelled: list[str] = []
        for sid in targets:
            session = self._sessions.get(sid)
            if session is None or session.client is None:
                continue
            with contextlib.suppress(Exception):
                await session.client.cancel_prompt()
            if self._host_index is not None:
                with contextlib.suppress(Exception):
                    self._host_index.set_resume_flag(sid, True)
            cancelled.append(sid)
        if cancelled:
            log.info(
                "Graceful-cancel: sent ACP cancel to %d in-flight turn(s); "
                "waiting up to %.0fs to settle: %s",
                len(cancelled), settle, ", ".join(cancelled),
            )
        deadline = time.monotonic() + max(0.0, settle)
        still = [s for s in cancelled
                 if (self._sessions.get(s) is not None
                     and self._sessions[s].status == SessionStatus.RUNNING)]
        while still and time.monotonic() < deadline:
            await _asyncio.sleep(0.5)
            still = [s for s in cancelled
                     if (self._sessions.get(s) is not None
                         and self._sessions[s].status == SessionStatus.RUNNING)]
        if still:
            log.warning(
                "Graceful-cancel: %d turn(s) did not settle within %.0fs "
                "(proceeding anyway): %s", len(still), settle, ", ".join(still),
            )
        return {
            "cancelled": cancelled, "preserved": [], "settled": not still,
            "mode": "cancel", "enabled": True,
        }

    async def drain(
        self,
        *,
        timeout: float = 300.0,
        poll: float = 1.0,
        force: bool = False,
        reason: str | None = None,
        source: str = "drain-endpoint",
        exclude_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Open the drain gate and wait for in-flight work to settle.

        Refuses new sessions/turns immediately, then blocks until no session is
        busy (see busy_sessions) or ``timeout`` seconds elapse. The OS service
        manager (systemd ExecStop / the Windows pre-stop hook) and the cutover
        orchestrator call this *before* the process exits so an active turn is
        never hard-killed. Returns a summary; ``drained`` is False on timeout
        unless ``force`` is set (the caller accepts interrupting the laggards).

        In **Session-Host mode** the drain is *assertive*: it first
        graceful-cancels in-flight turns (ACP ``session/cancel`` + a
        ``resume_on_reattach`` flag), bounded by ``graceful_cancel_settle_seconds``,
        so a redeploy never blocks the full ``timeout`` on a long turn and a
        session updating its own bridge (``exclude_session_id``) is spared.

        ``source``/``reason`` are recorded for observability (#1757). Note the
        gate stays open after this returns (the successor retires this daemon);
        the watchdog armed here auto-releases it if that handoff never lands.
        Teardown (stop/end) stays permitted throughout -- it is what lets the
        busy sessions this loop waits on settle (#1755).
        """
        import asyncio as _asyncio

        self.set_draining(True, reason=reason, source=source)
        await self.graceful_cancel_for_redeploy(
            exclude_session_id=exclude_session_id,
        )
        # Detach-only redeploy (dotfiles#1661): a session backed by a live
        # Session Host is PRESERVED across the restart (its turn keeps running on
        # the host and the successor reattaches), so the drain must not block
        # waiting for it to "settle" -- it never will, and it doesn't need to.
        # Only genuinely non-preservable busy work (process-owned command/ssh
        # turns, background sub-agents) is waited on. When cancelling is opt-in,
        # nothing is preserved and every busy session is waited on as before.
        preserved: set[str] = (
            {r.session_id for r in self._live_host_records()}
            if not self._cancel_turns_on_redeploy else set()
        )
        deadline = time.monotonic() + max(0.0, timeout)
        busy = [s for s in self.busy_sessions()
                if s != exclude_session_id and s not in preserved]
        log.info(
            "Drain started: %d session(s) busy%s, timeout=%.0fs%s",
            len(busy),
            f" ({len(preserved)} host-backed preserved for reattach)"
            if preserved else "",
            timeout, " (force)" if force else "",
        )
        while busy and time.monotonic() < deadline:
            await _asyncio.sleep(poll)
            busy = [s for s in self.busy_sessions()
                    if s != exclude_session_id and s not in preserved]

        drained = not busy
        if drained:
            log.info(
                "Drain complete: no busy sessions remain%s",
                f" ({len(preserved)} host-backed turn(s) preserved for reattach: "
                f"{', '.join(sorted(preserved))})" if preserved else "",
            )
        elif force:
            log.warning(
                "Drain timed out after %.0fs with %d busy session(s) -- "
                "forcing past: %s", timeout, len(busy), ", ".join(busy),
            )
        else:
            log.warning(
                "Drain timed out after %.0fs; %d session(s) still busy: %s",
                timeout, len(busy), ", ".join(busy),
            )
        return {
            "drained": drained or force,
            "clean": drained,
            "forced": bool(force and not drained),
            "busy_sessions": busy,
            # Live Session Host census (dotfiles#1656): a host-backed turn is
            # PRESERVED across the restart (detached, its turn keeps running on
            # the host, the successor reattaches) rather than drained. Surface it
            # explicitly so a `clean` drain never hides an unaccounted-for live
            # host -- an operator/cutover sees "clean, N turns preserved for
            # reattach", not a bare clean.
            "preserved": sorted(preserved),
            "live_host_count": self.live_host_count,
            "timeout": timeout,
        }


    @property
    def db(self) -> Database:
        """The backing database (used by routes for cursor persistence)."""
        return self._db

    def _mark_session_failed(self, session: Session, *, trigger: str) -> None:
        """Persist and publish one authoritative failed transition."""
        session.status = SessionStatus.FAILED
        self._db.update_session_status(
            session.session_id, SessionStatus.FAILED.value, time.time()
        )
        if session.event_log:
            session.event_log.append(
                "session_state_changed",
                {"status": SessionStatus.FAILED.value, "trigger": trigger},
            )

    @staticmethod
    def _capture_progress(session: Session, event_type: str, data: dict) -> None:
        """Update a session's structured progress from a captured event (#46.3)."""
        # Every ACP frame is fresh output -- stamp it so liveness reflects the
        # real event stream, not just turn boundaries (#145).
        session.last_output_at = time.time()
        if event_type == "agent_message":
            markers = _parse_progress_markers(data.get("text", ""))
            if markers:
                session.progress.update(markers)

    def _rehydrate(self) -> None:
        """Reload session metadata from DB on startup.

        Running processes are gone after a restart, so any session that
        was RUNNING/IDLE/STARTING gets marked STOPPED (resumable).
        Sessions that were ENDED get cleaned up. Incomplete turns are
        marked as interrupted.
        """
        rows = self._db.list_sessions()
        now = time.time()
        for row in rows:
            sid = row["id"]
            status = row["status"]

            if status == SessionStatus.ENDED.value:
                # Defense-in-depth: a single session's cleanup must never brick
                # daemon startup -- log and skip on failure rather than aborting
                # rehydrate (and thus the whole service).
                try:
                    self._db.delete_session(sid)
                except Exception:
                    log.warning(
                        "Failed to clean up ENDED session %s on startup",
                        sid, exc_info=True,
                    )
                continue

            target_json = row.get("target_json")
            if target_json:
                target = SpawnTarget.from_json(target_json)
            else:
                target = SpawnTarget(
                    type=row.get("target_type", "local"),
                    cwd=row.get("target_dir", "."),
                )

            session = Session(
                session_id=sid,
                name=row["name"],
                target=target,
                agent_name=row.get("agent_name"),
                caller_id=row.get("caller_id"),
            )
            session.created_at = row["created_at"]
            session.updated_at = row["updated_at"]
            session.acp_session_id = row.get("acp_session_id")
            session.restart_status = status

            # Mark formerly-active sessions as stopped
            interrupted_on_restart = False
            if status in (
                SessionStatus.RUNNING.value,
                SessionStatus.IDLE.value,
                SessionStatus.STARTING.value,
            ):
                session.status = SessionStatus.STOPPED
                self._db.update_session_status(sid, SessionStatus.STOPPED.value, now)
                log.info("Session %s (%s) marked STOPPED after restart", sid, session.name)

                # Mark incomplete turns as interrupted
                for turn in self._db.get_turns(sid):
                    if turn.get("completed_at") is None:
                        self._db.update_turn(
                            sid, turn["turn_index"],
                            stop_reason="interrupted",
                            completed_at=now,
                        )
                        interrupted_on_restart = True
            else:
                session.status = SessionStatus(status)

            # Restore event log from DB
            session.event_log = EventLog.from_db(
                self._db,
                sid,
                acp_session_id=session.acp_session_id,
                worktree_id=target.worktree_id,
            )
            if status in (
                SessionStatus.RUNNING.value,
                SessionStatus.IDLE.value,
                SessionStatus.STARTING.value,
            ):
                # Persist the restart boundary the DB state already records.
                # A formerly-running turn cannot remain open in telemetry/SSE
                # after its process is gone.
                if (
                    interrupted_on_restart
                    and session.event_log.telemetry_conversation_state
                    in {None, "sending", "responding"}
                ):
                    session.event_log.append(
                        "turn_complete", {"stop_reason": "interrupted"}
                    )
                session.event_log.append(
                    "session_state_changed",
                    {"status": SessionStatus.STOPPED.value, "trigger": "daemon_restart"},
                )
            session.turn_count = len(self._db.get_turns(sid))

            # Rebuild structured progress from the restored agent messages so a
            # daemon restart preserves reported milestones (#46.3).
            for ev in session.event_log.get_events(0):
                self._capture_progress(session, ev.event, ev.data)

            # Restore context usage from DB
            session.context_size = row.get("context_size")
            session.context_used = row.get("context_used")
            session.usage_model = row.get("usage_model")
            session.last_usage_at = row.get("last_usage_at")

            self._sessions[sid] = session

        log.info("Rehydrated %d sessions from DB", len(self._sessions))

        # Startup GC: prune aged terminal/disconnected sessions and compact
        # the DB so a long-lived daemon's sessions.db doesn't grow without
        # bound (a single big dispatch can otherwise leave tens of GB of
        # freelist pages -- see RetentionConfig).
        try:
            self.gc(reason="startup")
        except Exception:
            log.warning("Startup GC failed", exc_info=True)

    def gc(self, *, now: float | None = None, reason: str = "manual") -> dict[str, Any]:
        """Garbage-collect terminal/disconnected sessions and compact the DB.

        Prunes the bridge's relay metadata (session row + turns + events +
        delivery cursors) for sessions in a terminal state (per
        ``RetentionConfig.statuses``) whose last update is older than the
        retention window, then optionally VACUUMs to return freed pages to the
        OS. Live sessions -- and any whose ACP client is still running -- are
        never touched. The canonical Copilot session history lives outside
        this DB and is unaffected.

        Returns a summary dict: ``enabled``, ``pruned`` (ids), ``pruned_count``,
        ``vacuumed`` (bool), ``reclaimed_bytes``.
        """
        ret = self._retention
        result: dict[str, Any] = {
            "enabled": ret.enabled,
            "pruned": [],
            "pruned_count": 0,
            "vacuumed": False,
            "reclaimed_bytes": 0,
        }
        if not ret.enabled:
            return result

        now = now if now is not None else time.time()
        cutoff = now - ret.max_age_hours * 3600.0
        eligible = self._db.gc_eligible_session_ids(ret.statuses, cutoff)

        pruned: list[str] = []
        for sid in eligible:
            # Safety: never prune a session whose client is still running,
            # even if its persisted status looks terminal.
            sess = self._sessions.get(sid)
            if sess is not None and sess.client and sess.client.is_running:
                continue
            try:
                self._db.delete_session(sid)
            except Exception:
                log.warning("GC: failed to prune session %s", sid, exc_info=True)
                continue
            self._sessions.pop(sid, None)
            pruned.append(sid)

        result["pruned"] = pruned
        result["pruned_count"] = len(pruned)

        if ret.vacuum:
            try:
                info = self._db.db_size_info()
                if info["free_bytes"] >= ret.vacuum_min_free_mb * 1024 * 1024:
                    before = info["total_bytes"]
                    self._db.vacuum()
                    after = self._db.db_size_info()["total_bytes"]
                    result["vacuumed"] = True
                    result["reclaimed_bytes"] = max(0, before - after)
            except Exception:
                # A locked DB (concurrent reader) just defers compaction to the
                # next sweep -- never fatal.
                log.warning("GC: VACUUM skipped/failed", exc_info=True)

        if pruned or result["vacuumed"]:
            log.info(
                "GC (%s): pruned %d session(s), reclaimed %.1f MB%s",
                reason,
                len(pruned),
                result["reclaimed_bytes"] / 1e6,
                " (vacuumed)" if result["vacuumed"] else "",
            )
        return result

    def _find_active_session(self, ws_key: tuple) -> Session | None:
        """Return an existing session that occupies the given workspace key.

        A session occupies a workspace when its status is in _ACTIVE_STATES.
        Used by the concurrency guard to enforce one session per CodeSpace.
        """
        for s in self._sessions.values():
            if s.status not in _ACTIVE_STATES:
                continue
            if _workspace_key(s.agent_name, s.target, s.caller_id) == ws_key:
                return s
        return None

    async def _connect_via_session_host(
        self,
        target: SpawnTarget,
        *,
        tracker: Any,
        session_id: str,
        on_acp_event: Any,
        permission_callback: Any | None,
        mcp_servers: list[dict[str, Any]] | None = None,
        spawner: Any | None = None,
        remote_child_argv: list[str] | None = None,
        remote_cwd: str | None = None,
        load_session_id: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        parity_fault_result: dict[str, Any] | None = None,
    ) -> tuple[AcpClient, str]:
        """Spawn a child inside a survivable Session Host and drive ACP over the
        reattachable loopback endpoint (Session-Host mode).

        ``spawner`` selects the boundary seam (default :class:`LocalSpawner`; a
        :class:`CodeSpaceSpawner` bootstraps the Host inside a CodeSpace and
        stands up the ``-L`` forward). Registers the durable host index -- with
        the remote-boundary ``endpoint`` descriptor -- so a restarted frontend
        can re-forward and reattach. Teardown DETACHES (host-mode
        ``AcpClient.shutdown``), never reaping the child inadvertently -- goal 1.
        """
        from . import __version__
        from .session_host.acp_adapter import open_acp_streams
        from .session_host.client import SessionHostClient
        from .session_host.host_index import HostRecord
        from .session_host.spawner import LocalSpawner
        from .transport import resolve_local_launch

        if spawner is None:
            spawner = LocalSpawner(
                unexpected_reap_seconds=self._session_host_unexpected_reap_seconds,
                active_reap_seconds=self._session_host_active_reap_seconds,
                ready_timeout=self._timeouts.session_host_ready,
            )

        if remote_child_argv is not None:
            # Remote boundary (CodeSpace/mesh): the child runs on the FAR side,
            # so there is no local worktree to resolve -- the Spawner is handed
            # the remote copilot argv + remote cwd directly, and the far-side
            # Session Host owns copilot's stdio as a clean local pipe there.
            args, work_dir, env = remote_child_argv, remote_cwd, {}
        else:
            args, work_dir, env = await resolve_local_launch(
                target, tracker=tracker, session_id=session_id,
            )
            if work_dir and not target.cwd:
                target.cwd = work_dir

        with tracker.stage(ConnectStage.LAUNCH_ACP):
            # Tag the child's environment with its own bridge session id so a
            # command the agent runs (e.g. an in-session `test-chamber services
            # agent-bridge update`) can tell the drain to spare THIS session --
            # cancelling the turn running the update would abort the update
            # (#1790). Any descendant process inherits it.
            child_env = dict(env or {})
            child_env["AGENT_BRIDGE_SESSION_ID"] = session_id
            # Bootstrap the Session Host through the boundary Spawner seam (P2a).
            # The seam is boundary-agnostic: LocalSpawner binds a loopback port
            # directly; CodeSpaceSpawner ships+launches the Host on the CS and
            # stands up an -L forward so the frontend below still dials
            # 127.0.0.1:<local_port>. spawn() blocks briefly on host readiness,
            # so it is already off-loop.
            spawned = await spawner.spawn(
                args, cwd=work_dir, env=child_env, session_id=session_id,
            )
            # Retain a remote-boundary forward so reattach can refresh it and
            # teardown can cancel it.
            if getattr(spawned, "forward", None) is not None:
                self._forwards[session_id] = spawned.forward
            relays = getattr(spawned, "relay", None)
            if relays is not None:
                if not isinstance(relays, (list, tuple, set)):
                    relays = [relays]
                relays = list(relays)
                if relays:
                    self._relays[session_id] = relays
            sock = await SessionHostClient.connect(port=spawned.local_port)
            await sock.attach(0, nonce=spawned.nonce.encode())
            streams = await open_acp_streams(sock)

            async def _closer() -> None:
                await streams.aclose()
                await sock.close()

            client = AcpClient(
                on_event=on_acp_event,
                on_permission=permission_callback,
                model_override=model,
                effort_override=effort,
            )
            # Surface a mid-session transport drop (loopback socket down, host +
            # child alive) as ``disconnected`` so the reattach driver fires (P1).
            streams.on_transport_lost = client.mark_transport_lost
            streams.on_child_exit = client.mark_host_child_exited
            # Retain the host control channel so the manager can push STATUS
            # (reapable) / DETACH (graceful) for host self-reap (#51).
            client.session_host_client = sock
            if permission_callback:
                client.auto_approve = False
            try:
                await asyncio.wait_for(
                    client.start_streams(
                        streams.reader, streams.writer,
                        child_pid=spawned.child_pid, closer=_closer,
                    ),
                    timeout=self._timeouts.session_start,
                )
                if streams.child_exit_code is not None:
                    client.mark_host_child_exited(streams.child_exit_code)
                    raise ConnectionError(
                        "Session Host child exited during ACP startup "
                        f"(code={streams.child_exit_code})"
                    )
                session_cwd = remote_cwd or target.cwd or _default_cwd(target)
                if load_session_id:
                    await asyncio.wait_for(
                        client.load_session(
                            cwd=session_cwd,
                            session_id=load_session_id,
                        ),
                        timeout=self._timeouts.session_new,
                    )
                    acp_sid = load_session_id
                else:
                    acp_sid = await asyncio.wait_for(
                        client.new_session(cwd=session_cwd, mcp_servers=mcp_servers),
                        timeout=self._timeouts.session_new,
                    )
            except (TimeoutError, asyncio.TimeoutError) as exc:
                # Leave a queryable marker so a stalled session-host resume/launch
                # is not a silent [starting]->[stopped] (#1468). The child's
                # stderr lives in the Session Host here (not this frontend), so
                # the tail is empty -- the always-logged child stderr carries it.
                with contextlib.suppress(Exception):
                    on_acp_event("acp_launch_timeout", {
                        "stage": "LAUNCH_ACP",
                        "mode": "session-host",
                        "handshake_timeout_s": self._timeouts.session_start,
                        "session_new_timeout_s": self._timeouts.session_new,
                    })
                cleanup_confirmed = await self._rollback_failed_host_launch(
                    spawner,
                    spawned,
                    sock,
                    streams,
                    session_id,
                    parity_fault_result,
                )
                if not cleanup_confirmed:
                    from .session_host.spawner import (
                        RemoteSpawnCleanupPendingError,
                    )

                    raise RemoteSpawnCleanupPendingError(
                        "remote Session Host ACP initialization failed and "
                        f"cleanup is inconclusive for {session_id}; retaining "
                        "target ownership"
                    ) from exc
                raise ConnectError(
                    ConnectStage.LAUNCH_ACP,
                    f"Copilot ACP launch (session host) timed out "
                    f"(handshake {self._timeouts.session_start}s / "
                    f"session/new {self._timeouts.session_new}s). A cold "
                    f"session/new on a large workspace may need a larger "
                    f"budget -- raise timeouts.session_new in "
                    f"~/.agent-bridge/config.yaml and restart the daemon.",
                    retryable=False,
                    cause=exc,
                ) from exc
            except Exception as exc:
                cleanup_confirmed = await self._rollback_failed_host_launch(
                    spawner,
                    spawned,
                    sock,
                    streams,
                    session_id,
                    parity_fault_result,
                )
                if not cleanup_confirmed:
                    from .session_host.spawner import (
                        RemoteSpawnCleanupPendingError,
                    )

                    raise RemoteSpawnCleanupPendingError(
                        "remote Session Host ACP initialization failed and "
                        f"cleanup is inconclusive for {session_id}; retaining "
                        "target ownership"
                    ) from exc
                raise

        if self._host_index is not None:
            self._host_index.register(HostRecord(
                session_id=session_id,
                port=spawned.local_port,
                host_pid=spawned.host_pid,
                child_pid=spawned.child_pid,
                host_version=__version__,
                protocol_version=spawned.protocol_version,
                state_file=spawned.state_file,
                created_at=time.time(),
                nonce=spawned.nonce,
                boundary=spawned.boundary,
                endpoint=getattr(spawned, "endpoint", {}) or {},
                extra={
                    "remote_authority_v2": spawned.boundary != "local",
                },
            ))
        # #4272 bridge-lock: mark this bridge-owned session's liveness as a
        # lattice file the picker reads cheaply, so a bare/bridge Copilot
        # (cwd=home) still shows ACTIVE (#1416). LOCAL boundary only -- a remote
        # child_pid is far-side, so its liveness isn't provable locally. Best-
        # effort: never breaks a launch.
        if getattr(spawned, "boundary", "local") == "local":
            from . import bridge_lock
            with contextlib.suppress(Exception):
                await bridge_lock.write(
                    session_id, target.worktree_id, spawned.child_pid)
        return client, acp_sid

    async def _rollback_failed_host_launch(
        self,
        spawner: Any,
        spawned: Any,
        sock: Any,
        streams: Any,
        session_id: str,
        result: dict[str, Any] | None,
    ) -> bool:
        """Reap a failed Host launch and remove every local holder."""
        with contextlib.suppress(Exception):
            await sock.terminate()
        with contextlib.suppress(Exception):
            await streams.aclose()
        with contextlib.suppress(Exception):
            await sock.close()

        remote = getattr(spawned, "boundary", "local") != "local"
        confirmed = not remote
        if remote:
            abort = getattr(spawner, "abort_spawned", None)
            if callable(abort):
                try:
                    confirmed = bool(await abort(spawned, session_id))
                except Exception:
                    confirmed = False
        with contextlib.suppress(Exception):
            await spawned.aclose()
        self._forwards.pop(session_id, None)
        self._relays.pop(session_id, None)
        if result is not None:
            result.update({
                "host_process_removed": confirmed,
                "child_process_removed": confirmed,
                "remote_authority_removed": confirmed,
                "forward_removed": session_id not in self._forwards,
                "relay_removed": session_id not in self._relays,
            })
        return confirmed

    # -- boundary-aware Session Host liveness ---------------------------------
    def _rec_host_alive(self, rec: Any) -> bool:
        """Is a Session Host still alive? Boundary-aware.

        A **local** host is a local process, so ``pid_alive`` is authoritative.
        A **remote** host's ``host_pid`` is a *far-side* pid -- checking it
        against local processes is meaningless (and would randomly match an
        unrelated local pid). A remote host is instead treated as *presumed
        alive* here and **verified** by the actual forward + ATTACH probe in
        ``_reattach_one`` (which prunes on failure), so a truly-dead remote host
        is dropped when the reattach fails rather than by a bogus local pid check.
        """
        from .session_host.osutil import pid_alive
        if getattr(rec, "boundary", "local") == "local":
            return pid_alive(rec.host_pid)
        return True

    def _rec_child_alive(self, rec: Any) -> bool:
        """Is the copilot child alive? Local: ``pid_alive``; remote: presumed
        (a dead remote child surfaces as the host closing on ATTACH)."""
        from .session_host.osutil import pid_alive
        if getattr(rec, "boundary", "local") == "local":
            return pid_alive(rec.child_pid)
        return True

    def _live_host_records(self) -> list[Any]:
        """Records whose host is (boundary-appropriately) alive."""
        if self._host_index is None:
            return []
        return [r for r in self._host_index.all() if self._rec_host_alive(r)]

    def _live_remote_host_sessions(self) -> set[str]:
        """Session ids backed by a live **remote** (ssh/codespace) Session Host.

        A remote host fronts a child whose turn / tool-call activity runs across
        the boundary and is **not** reflected by the local session status: a
        ``--reply-timeout`` detach, a resume-into-``[starting]``, a host reap, or
        a tunnel flap can leave the local status ``IDLE``/``STARTING`` while the
        far-side child is mid-work. So these sessions must not be judged
        idle/not-busy from local state alone (dotfiles#1633) -- the drain must
        count them and the idle reaper must not free their child. Local-boundary
        hosts are excluded (their pid + status are locally authoritative)."""
        out: set[str] = set()
        for rec in self._live_host_records():
            if getattr(rec, "boundary", "local") == "local":
                continue
            sid = getattr(rec, "session_id", None)
            if sid:
                out.add(sid)
        return out

    def _prune_dead_hosts(self) -> None:
        """Drop records whose host is dead (local only -- remote is verified by
        the reattach probe, never by a local pid check)."""
        if self._host_index is None:
            return
        for r in [r for r in self._host_index.all() if not self._rec_host_alive(r)]:
            with contextlib.suppress(Exception):
                self._host_index.remove(r.session_id)

    async def reattach_session_hosts(
        self,
        *,
        remote_recovery_timeout: float = 60.0,
    ) -> int:
        """Reconnect to every surviving Session Host on startup (goal 3).

        After an agent-bridge restart, ``_rehydrate`` has marked host-backed
        sessions STOPPED. This reads the durable host index and, for each host
        whose process is still alive, re-establishes the ACP connection over the
        reattached loopback endpoint and **adopts** the existing ACP session --
        no child respawn, no lost session. Dead hosts are pruned. Returns the
        count reattached. No-op when no host index exists.

        **Version-mux (Phase 4).** A host advertising a wire-envelope protocol
        this frontend no longer speaks (a rare breaking host-layer change) is
        *not* driven with incompatible client code. Per :func:`plan_host`:
        a compatible host is reattached; an incompatible host whose child is
        still alive is **left running** so it keeps its child until the child's
        own stop (goal 1 -- never reap mid-turn); an incompatible host whose
        child has already stopped is reaped so it stops pinning its old install.
        """
        if self._host_index is None:
            return 0
        from .session_host.version_mux import HostDisposition, plan_host

        loop = asyncio.get_running_loop()
        startup_budget = max(0.0, float(remote_recovery_timeout))
        deadline = loop.time() + startup_budget
        startup_session_ids = {
            session.session_id
            for session in self._sessions.values()
            if session.status not in {SessionStatus.ENDED, SessionStatus.FAILED}
        }
        recovery_budget = min(
            startup_budget / 2,
            max(0.0, deadline - loop.time()),
        )
        recovered = await self._recover_remote_host_records(
            session_ids=startup_session_ids,
            timeout_seconds=recovery_budget,
        )
        if recovered:
            log.info(
                "Recovered %d remote Session Host record(s) from far-side "
                "authority files",
                recovered,
            )
        self._prune_dead_hosts()
        reattached = 0
        now = time.time()
        for rec in self._live_host_records():
            session = self._sessions.get(rec.session_id)
            if (
                session is not None
                and session.status in {SessionStatus.ENDED, SessionStatus.FAILED}
            ):
                log.info(
                    "Skipping startup reattach for terminal session %s (%s)",
                    rec.session_id, session.status.value,
                )
                continue
            if (
                rec.session_id in self._remote_recovery_skipped
                or rec.session_id in self._remote_recovery_inconclusive
            ):
                log.info(
                    "Skipping startup reattach for %s because its remote venue "
                    "could not be authoritatively inspected",
                    rec.session_id,
                )
                continue
            if deadline - loop.time() <= 0:
                self._remote_recovery_inconclusive.add(rec.session_id)
                log.warning(
                    "Startup Session Host reattach budget exhausted before %s",
                    rec.session_id,
                )
                continue
            plan = plan_host(
                protocol_version=rec.protocol_version,
                child_alive=self._rec_child_alive(rec),
                age_seconds=(now - rec.created_at) if rec.created_at else None,
                stale_reap_seconds=self._session_host_stale_reap_seconds,
            )
            if plan.disposition is HostDisposition.STRAND:
                log.info(
                    "Session %s pinned to incompatible Session Host "
                    "(proto=%s, build=%s, pid=%s); %s",
                    rec.session_id, rec.protocol_version, rec.host_version,
                    rec.host_pid, plan.reason,
                )
                continue
            if plan.disposition in (HostDisposition.REAP_STOPPED,
                                    HostDisposition.FORCE_REAP):
                self._reap_host_record(rec, plan.reason)
                continue
            session = self._sessions.get(rec.session_id)
            if session is None or not session.acp_session_id:
                # A live host with no adoptable session -- ended out from under
                # it (its row was deleted) or a pre-#1786 orphan. Reap it rather
                # than leak the host + child forever.
                self._reap_host_record(rec, "no adoptable session on reattach")
                continue

            remaining = deadline - loop.time()
            if remaining <= 0:
                self._remote_recovery_inconclusive.add(rec.session_id)
                log.warning(
                    "Startup Session Host reattach budget exhausted before %s",
                    rec.session_id,
                )
                continue
            try:
                attached = await asyncio.wait_for(
                    self._reattach_one(
                        rec, session, new_status=SessionStatus.IDLE,
                        send_resume=(
                            getattr(rec, "resume_on_reattach", False)
                            or session.restart_status
                            == SessionStatus.STARTING.value
                        ),
                        # A failed remote attach may be a transient SSH/control-plane
                        # outage while the far-side host, child, and auth relay are
                        # still serving tools. Retain its record and relay ownership;
                        # only an authoritative far-side liveness probe may declare it
                        # dead. Local PIDs remain directly authoritative.
                        prune_on_fail=(
                            getattr(rec, "boundary", "local") == "local"
                            or not (getattr(rec, "extra", {}) or {}).get(
                                "remote_authority_v2"
                            )
                        ),
                    ),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                self._remote_recovery_inconclusive.add(rec.session_id)
                log.warning(
                    "Startup Session Host reattach timed out for %s after %.1fs",
                    rec.session_id, remaining,
                )
                continue
            if attached:
                reattached += 1
        if reattached:
            log.info("Reattached %d session(s) to surviving Session Hosts", reattached)
        return reattached

    async def _recover_remote_host_records(
        self,
        *,
        allow_wake: bool = False,
        session_ids: set[str] | None = None,
        timeout_seconds: float = 120.0,
    ) -> int:
        """Rebuild missing remote HostIndex rows from far-side records.

        The frontend DB identifies adoptable ACP sessions and their remote venue
        targets. The Session Host itself is authoritative for host/child PID,
        port, nonce, version, and relay-forward metadata.
        """
        if self._host_index is None:
            return 0
        from .relay_state import get_live_relay_port
        from .session_host.codespace_transport import (
            build_codespace_spawner,
            parse_codespace_target,
        )
        from .session_host.container_transport import build_container_spawner
        from .session_host.spawner import RemoteHostDeadError

        groups: dict[str, tuple[Any, list[tuple[Session, Any]]]] = {}
        for session in self._sessions.values():
            if session_ids is not None and session.session_id not in session_ids:
                continue
            target = session.target
            has_container_target = (
                isinstance(target.container, dict)
                and bool(target.container.get("name"))
            )
            if not session.acp_session_id and not has_container_target:
                continue
            existing = self._host_index.get(session.session_id)
            if (
                existing is not None
                and not (getattr(existing, "extra", {}) or {}).get(
                    "remote_authority_v2"
                )
            ):
                continue
            cs_target = None
            container_target = (
                target.container
                if isinstance(target.container, dict)
                and target.container.get("name")
                else None
            )
            if isinstance(target.codespace, dict) and target.codespace.get("name"):
                cs_target = target.codespace
            elif target.spawn_command:
                cs_target = parse_codespace_target(target.spawn_command)
            if not cs_target and not container_target:
                continue
            if container_target:
                name = f"container:{container_target['name']}"
                if name not in groups:
                    spawner = build_container_spawner(
                        container_target,
                        ready_timeout=self._timeouts.session_host_ready,
                        unexpected_reap_seconds=(
                            self._session_host_unexpected_reap_seconds
                        ),
                        active_reap_seconds=(
                            self._session_host_active_reap_seconds
                        ),
                    )
                    groups[name] = (spawner, [])
            else:
                codespace_name = cs_target["name"]
                name = f"codespace:{codespace_name}"
                if name not in groups:
                    spawner = build_codespace_spawner(
                        codespace_name,
                        cs_target.get("repo") or "",
                        relay_port=get_live_relay_port(),
                        unexpected_reap_seconds=(
                            self._session_host_unexpected_reap_seconds
                        ),
                        active_reap_seconds=(
                            self._session_host_active_reap_seconds
                        ),
                    )
                    groups[name] = (spawner, [])
            groups[name][1].append((session, existing))

        semaphore = asyncio.Semaphore(3)

        async def _inspect_group(spawner: Any, entries: list[tuple[Session, Any]]):
            async with semaphore:
                inspect_before_connect = (
                    not allow_wake
                    or getattr(spawner, "boundary", "") == "container"
                )
                if inspect_before_connect:
                    try:
                        can_inspect = await spawner.can_inspect_without_wake()
                    except Exception:
                        log.warning(
                            "Could not determine remote venue state without waking; "
                            "skipping startup authority inspection",
                            exc_info=True,
                        )
                        return [
                            (session, existing, "unknown", None)
                            for session, existing in entries
                        ]
                    if not can_inspect:
                        status = (
                            "dead"
                            if getattr(spawner, "boundary", "") == "container"
                            else "skipped"
                        )
                        return [
                            (session, existing, status, None)
                            for session, existing in entries
                        ]
                results = []
                for session, existing in entries:
                    try:
                        record = await spawner.recover_record(session.session_id)
                        status = "live" if record is not None else "missing"
                        results.append((session, existing, status, record))
                    except RemoteHostDeadError:
                        results.append((session, existing, "dead", None))
                    except Exception:
                        log.warning(
                            "Could not inspect remote Session Host authority for %s",
                            session.session_id,
                            exc_info=True,
                        )
                        results.append((session, existing, "unknown", None))
                return results

        task_entries = {
            asyncio.create_task(_inspect_group(spawner, entries)): entries
            for spawner, entries in groups.values()
        }
        tasks = list(task_entries)
        if not tasks:
            return 0
        done, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if pending:
            log.warning(
                "Remote Session Host authority recovery timed out with %d "
                "remote venue group(s) still pending after %.1fs",
                len(pending), timeout_seconds,
            )
            for task in pending:
                for session, _existing in task_entries[task]:
                    self._remote_recovery_inconclusive.add(session.session_id)

        recovered = 0
        for task in done:
            try:
                results = task.result()
            except Exception:
                log.warning(
                    "Remote Session Host authority recovery group failed",
                    exc_info=True,
                )
                for session, _existing in task_entries[task]:
                    self._remote_recovery_skipped.add(session.session_id)
                continue
            for session, existing, status, record in results:
                if status == "live" and record is not None:
                    container_target = (
                        session.target.container
                        if isinstance(session.target.container, dict)
                        and session.target.container.get("name")
                        else None
                    )
                    if container_target is not None:
                        try:
                            self._acquire_container_lock(
                                session.session_id,
                                container_target["name"],
                            )
                        except Exception:
                            self._remote_recovery_inconclusive.add(
                                session.session_id
                            )
                            log.warning(
                                "Live container Session Host authority for %s "
                                "could not reclaim target ownership",
                                session.session_id,
                                exc_info=True,
                            )
                            continue
                    self._remote_recovery_inconclusive.discard(session.session_id)
                    self._remote_recovery_skipped.discard(session.session_id)
                    if existing is not None:
                        record.resume_on_reattach = existing.resume_on_reattach
                    self._host_index.register(record)
                    if existing is None:
                        recovered += 1
                elif status == "dead":
                    self._release_container_lock(session.session_id)
                    self._set_container_launch_pending(
                        session.session_id,
                        False,
                    )
                    self._remote_recovery_inconclusive.discard(session.session_id)
                    self._remote_recovery_skipped.discard(session.session_id)
                    if existing is not None:
                        await self._drop_forward(session.session_id)
                        self._host_index.remove(session.session_id)
                        log.info(
                            "Pruned confirmed-dead remote Session Host for %s",
                            session.session_id,
                        )
                elif status == "unknown":
                    self._remote_recovery_skipped.discard(session.session_id)
                    self._remote_recovery_inconclusive.add(session.session_id)
                elif status == "missing":
                    self._release_container_lock(session.session_id)
                    self._set_container_launch_pending(
                        session.session_id,
                        False,
                    )
                    self._remote_recovery_inconclusive.discard(session.session_id)
                    self._remote_recovery_skipped.discard(session.session_id)
                elif status == "skipped":
                    self._remote_recovery_skipped.add(session.session_id)
        return recovered

    async def _ensure_forward(self, rec: Any) -> None:
        """Ensure a remote-boundary Host's frontend ``-L`` forward is up.

        No-op for a local Host (direct loopback, no forward). For a CodeSpace /
        mesh Host, (re-)establishes the forward so ``rec.port`` resolves before we
        dial it -- the ``refresh_endpoint()`` step of the reattach driver, driven
        from the durable ``rec.endpoint`` descriptor so it works even after a
        frontend restart with no live Spawner. Refreshes an existing ``-L``
        forward (cancel + re-establish) or rebuilds one from the endpoint.

        Credential relay ``-R`` specs in the same endpoint are supervised by
        dedicated relay handles owned separately from the frontend ``-L``. A
        normal front detach/reattach leaves an existing relay alone; a daemon
        restart (no in-memory relay) re-supervises it from the descriptor.
        """
        boundary = getattr(rec, "boundary", "local")
        endpoint = getattr(rec, "endpoint", None) or {}
        if boundary == "local" or not endpoint:
            return
        from .session_host.endpoints import forward_from_endpoint

        existing = self._forwards.get(rec.session_id)
        try:
            if existing is not None:
                local_port = await existing.refresh()
            else:
                fwd = forward_from_endpoint(endpoint)
                try:
                    local_port = await fwd.establish()
                except asyncio.CancelledError:
                    with contextlib.suppress(Exception):
                        await fwd.cancel()
                    raise
                except Exception:
                    with contextlib.suppress(Exception):
                        await fwd.cancel()
                    raise
                self._forwards[rec.session_id] = fwd
            if (
                getattr(rec, "port", None) != local_port
                or endpoint.get("local_port") != local_port
            ):
                rec.port = local_port
                endpoint["local_port"] = local_port
                rec.endpoint = endpoint
                if self._host_index is not None and is_dataclass(rec):
                    self._host_index.register(rec)
            await self._ensure_relays_from_endpoint(rec.session_id, endpoint)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning(
                "Failed to (re-)establish forward for session %s (boundary=%s)",
                rec.session_id, boundary, exc_info=True,
            )
            raise

    async def _ensure_relays_from_endpoint(self, session_id: str, endpoint: dict) -> None:
        """Start endpoint-declared relay supervisors if this daemon owns none.

        The relay is independent of frontend reattach. When a live daemon already
        owns a relay for the Session Host, leave it alone; after a daemon restart
        the in-memory owner set is empty, so this method reconstructs the relay
        from the durable endpoint. If a prior handle is present but no longer
        alive, stop/replace it to avoid double ``-R`` binds.
        """
        if not (endpoint.get("reverse_forwards") or []):
            return
        existing = self._relays.get(session_id) or []
        if existing and all(getattr(relay, "is_alive", True) for relay in existing):
            return
        await self._replace_relays_from_endpoint(session_id, endpoint)

    async def _replace_relays_from_endpoint(self, session_id: str, endpoint: dict) -> None:
        """Stop any prior relay owner and start supervisors from ``endpoint``."""
        from .relay_state import get_live_relay_port
        from .session_host.endpoints import (
            endpoint_serving_probe_factory,
            relay_forwards_from_endpoint,
        )

        await self._stop_relays(session_id)
        relays = relay_forwards_from_endpoint(
            endpoint,
            host_port_resolver=get_live_relay_port,
            serving_probe_for_port=endpoint_serving_probe_factory(endpoint),
        )
        started = []
        for relay in relays:
            try:
                await relay.start()
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await relay.stop()
                for prior in started:
                    with contextlib.suppress(Exception):
                        await prior.stop()
                raise
            except Exception:
                log.warning(
                    "Failed to start credential relay supervisor for session %s; "
                    "continuing without relay",
                    session_id, exc_info=True,
                )
                with contextlib.suppress(Exception):
                    await relay.stop()
                continue
            started.append(relay)
        if started:
            self._relays[session_id] = started

    async def _stop_relays(self, session_id: str) -> None:
        """Stop and forget a session's credential-relay supervisors."""
        relays = self._relays.pop(session_id, [])
        for relay in relays:
            with contextlib.suppress(Exception):
                await relay.stop()

    async def interrupt_relays_for_parity(
        self,
        session_id: str,
        *,
        timeout: float = 90.0,
    ) -> dict[str, Any]:
        """Interrupt one parity session's relay processes and await recovery."""
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        if not str(session.caller_id or "").startswith("venue-parity:"):
            raise PermissionError(
                "relay interruption is restricted to venue-parity sessions"
            )
        if session.status is not SessionStatus.IDLE:
            raise RuntimeError("relay interruption requires an idle session")
        active_states = {
            SessionStatus.CREATED,
            SessionStatus.STARTING,
            SessionStatus.RUNNING,
            SessionStatus.IDLE,
            SessionStatus.STOPPING,
        }
        active_others = [
            item.session_id
            for item in self._sessions.values()
            if item.session_id != session.session_id
            and item.status in active_states
        ]
        if active_others:
            raise RuntimeError(
                "relay interruption refuses another active managed session: "
                + ", ".join(active_others[:5])
            )

        relays = list(self._relays.get(session.session_id) or [])
        if not relays:
            raise RuntimeError("session has no supervised credential relay")
        handles_before = tuple(id(relay) for relay in relays)
        processes_before = [getattr(relay, "_proc", None) for relay in relays]
        pids_before = [
            int(getattr(process, "pid", 0) or 0)
            for process in processes_before
        ]
        if (
            not all(getattr(relay, "is_alive", False) for relay in relays)
            or not all(processes_before)
            or not all(pids_before)
        ):
            raise RuntimeError("session relay is not healthy before interruption")

        interrupted = 0
        for process in processes_before:
            try:
                process.kill()
            except ProcessLookupError as exc:
                raise RuntimeError(
                    "session relay exited before interruption"
                ) from exc
            interrupted += 1
        for process in processes_before:
            with contextlib.suppress(
                ProcessLookupError,
                TimeoutError,
                asyncio.TimeoutError,
            ):
                await asyncio.wait_for(process.wait(), timeout=5.0)

        deadline = time.monotonic() + max(1.0, timeout)
        current: list[Any] = []
        pids_after: list[int] = []
        while time.monotonic() < deadline:
            current = list(self._relays.get(session.session_id) or [])
            pids_after = [
                int(getattr(getattr(relay, "_proc", None), "pid", 0) or 0)
                for relay in current
            ]
            if (
                tuple(id(relay) for relay in current) == handles_before
                and len(current) == len(relays)
                and all(getattr(relay, "is_alive", False) for relay in current)
                and all(pids_after)
                and all(
                    before != after
                    for before, after in zip(pids_before, pids_after, strict=True)
                )
            ):
                return {
                    "owner_count_before": len(relays),
                    "owner_count_after": len(current),
                    "interrupted_count": interrupted,
                    "all_recovered": True,
                    "owner_identity_preserved": True,
                    "processes_replaced": True,
                }
            await asyncio.sleep(0.25)
        raise TimeoutError(
            f"session relay did not recover within {timeout:.0f}s"
        )

    async def recreate_container_for_parity(
        self,
        session_id: str,
        *,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """Recreate one parity container and replace its bridge session."""
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        if not str(session.caller_id or "").startswith("venue-parity:"):
            raise PermissionError(
                "container recreation is restricted to venue-parity sessions"
            )
        if session.status is not SessionStatus.IDLE:
            raise RuntimeError("container recreation requires an idle session")
        container_target = (
            session.target.container
            if isinstance(session.target.container, dict)
            else None
        )
        if (
            not container_target
            or container_target.get("security_profile") != "trusted"
        ):
            raise RuntimeError(
                "container recreation requires a trusted container target"
            )
        active_states = {
            SessionStatus.CREATED,
            SessionStatus.STARTING,
            SessionStatus.RUNNING,
            SessionStatus.IDLE,
            SessionStatus.STOPPING,
        }
        active_others = [
            item.session_id
            for item in self._sessions.values()
            if item.session_id != session.session_id
            and item.status in active_states
        ]
        if active_others:
            raise RuntimeError(
                "container recreation refuses another active managed session: "
                + ", ".join(active_others[:5])
            )
        if self._host_index is None:
            raise RuntimeError("container session has no HostIndex")
        old_host = self._host_index.get(session.session_id)
        if old_host is None or old_host.boundary != "container":
            raise RuntimeError(
                "container session has no authoritative container Host record"
            )
        replacement_target = SpawnTarget.from_json(session.target.to_json())
        old_session_id = session.session_id
        old_acp_session_id = session.acp_session_id
        old_child_pid = session.pid
        old_host_pid = old_host.host_pid

        from .session_host.container_transport import (
            ContainerRecreateAfterRemovalError,
            container_state,
            recreate_container_for_parity,
        )

        before = await container_state(container_target)
        old_container_id = str(before.get("container_id") or "")
        if (
            before.get("name") != container_target.get("name")
            or before.get("running") is not True
            or not old_container_id
        ):
            raise RuntimeError(
                "container lifecycle state is not authoritative before recreation"
            )
        try:
            replacement = await recreate_container_for_parity(
                container_target,
                expected_container_id=old_container_id,
                timeout=timeout,
            )
        except ContainerRecreateAfterRemovalError:
            container_target["authoritative_identity_removed"] = True
            self._db.update_session_target(
                old_session_id,
                session.target.to_json(),
                session.target.cwd,
            )
            self._mark_session_failed(
                session, trigger="container_recreate_failed"
            )
            if session.event_log:
                session.event_log.append("container_recreate_failed", {
                    "message": (
                        "The original container identity was removed before "
                        "replacement failed; target ownership is retained."
                    ),
                })
            raise
        container_target["authoritative_identity_removed"] = True
        self._db.update_session_target(
            old_session_id,
            session.target.to_json(),
            session.target.cwd,
        )
        self._mark_session_failed(session, trigger="container_recreated")
        if session.event_log:
            session.event_log.append("container_recreated", {
                "message": "The original container identity was replaced.",
            })

        new_session = await self.start_session(
            replacement_target,
            agent_name=session.agent_name,
            caller_id=session.caller_id,
            mcp_servers=[
                dict(server) for server in session.mcp_servers
            ],
            model=session.model_override,
            effort=session.effort_override,
            replace_session_id=old_session_id,
            retain_container_lock_on_failure=True,
        )
        if new_session.status is not SessionStatus.IDLE:
            new_container = (
                new_session.target.container
                if isinstance(new_session.target.container, dict)
                else {}
            )
            if (
                self._host_index.get(new_session.session_id) is None
                and new_container.get("launch_pending_session_id")
                != new_session.session_id
            ):
                new_container["recreate_failed_without_host"] = True
                self._db.update_session_target(
                    new_session.session_id,
                    new_session.target.to_json(),
                    new_session.target.cwd,
                )
            raise RuntimeError(
                f"replacement container session {new_session.session_id} "
                "failed to reach idle and retains target ownership"
            )
        new_host = self._host_index.get(new_session.session_id)
        if new_host is None or new_host.boundary != "container":
            raise RuntimeError(
                "replacement container session has no Host record"
            )

        await self._stop_relays(old_session_id)
        forward = self._forwards.pop(old_session_id, None)
        if forward is not None:
            with contextlib.suppress(Exception):
                await forward.cancel()
        self._host_index.remove(old_session_id)
        await self.end_session(old_session_id, force=True)

        name = str(container_target["name"])
        return {
            "old_session_id": old_session_id,
            "replacement_session_id": new_session.session_id,
            "old_acp_session_id": old_acp_session_id,
            "replacement_acp_session_id": new_session.acp_session_id,
            "old_container_id": old_container_id,
            "replacement_container_id": replacement["new_container_id"],
            "old_host_pid": old_host_pid,
            "replacement_host_pid": new_host.host_pid,
            "old_child_pid": old_child_pid,
            "replacement_child_pid": new_session.pid,
            "container_identity_changed": (
                replacement["new_container_id"] != old_container_id
            ),
            "old_session_removed": (
                old_session_id not in self._sessions
                and self._db.get_session(old_session_id) is None
            ),
            "old_host_index_removed": (
                self._host_index.get(old_session_id) is None
            ),
            "old_forward_removed": old_session_id not in self._forwards,
            "old_relay_removed": old_session_id not in self._relays,
            "target_lock_transferred": (
                self._container_lock_sessions.get(new_session.session_id)
                == name
                and old_session_id not in self._container_lock_sessions
            ),
        }

    def _acquire_container_lock(self, session_id: str, name: str) -> None:
        if session_id in self._container_lock_sessions:
            return
        existing = self._container_locks.get(name)
        if existing is not None:
            raise RuntimeError(
                f"Container '{name}' is already owned by bridge session "
                f"{existing[1]}"
            )
        from ssh_manager import TargetLock

        lock = TargetLock(f"container:{name}", op="session-host")
        lock.acquire()
        self._container_locks[name] = (lock, session_id)
        self._container_lock_sessions[session_id] = name

    def _transfer_container_lock(
        self,
        old_session_id: str,
        new_session_id: str,
        name: str,
    ) -> None:
        """Move one held container target lock without an unlocked window."""
        entry = self._container_locks.get(name)
        if (
            self._container_lock_sessions.get(old_session_id) != name
            or entry is None
            or entry[1] != old_session_id
        ):
            raise RuntimeError(
                f"Container '{name}' is not owned by bridge session "
                f"{old_session_id}"
            )
        lock, _owner = entry
        self._container_lock_sessions.pop(old_session_id, None)
        self._container_lock_sessions[new_session_id] = name
        self._container_locks[name] = (lock, new_session_id)

    def _release_container_lock(self, session_id: str) -> None:
        name = self._container_lock_sessions.pop(session_id, None)
        if name is None:
            return
        entry = self._container_locks.get(name)
        if entry is None:
            return
        lock, owner_session = entry
        if owner_session != session_id:
            return
        self._container_locks.pop(name, None)
        with contextlib.suppress(Exception):
            lock.release()

    def _set_container_launch_pending(
        self,
        session_id: str,
        pending: bool,
    ) -> None:
        """Persist whether a partially launched container Host needs reaping."""
        session = self._sessions.get(session_id)
        if session is None or not isinstance(session.target.container, dict):
            return
        target = session.target.container
        if pending:
            target["launch_pending_session_id"] = session_id
        elif target.get("launch_pending_session_id") == session_id:
            target.pop("launch_pending_session_id", None)
        else:
            return
        self._db.update_session_target(
            session_id,
            session.target.to_json(),
            session.target.cwd,
        )

    async def _drop_forward(self, session_id: str) -> None:
        """Cancel and forget a session's remote-boundary forwards (if any)."""
        await self._stop_relays(session_id)
        fwd = self._forwards.pop(session_id, None)
        if fwd is not None:
            with contextlib.suppress(Exception):
                await fwd.cancel()
        self._release_container_lock(session_id)

    async def _reattach_one(
        self,
        rec: Any,
        session: Session,
        *,
        new_status: SessionStatus,
        send_resume: bool = False,
        prune_on_fail: bool = False,
    ) -> bool:
        """(Re)connect to a live Session Host and adopt its session -- the shared
        core of both startup reattach and in-session liveness-driven recovery.

        Dials the host's endpoint, resumes by the host-retained seq cursor
        (buffered frames past the durable ack replay with no gap and no
        re-stream), re-initializes ACP over the fresh stream pair, and adopts the
        existing ACP session id -- no child respawn. Wires transport-loss
        detection so a *subsequent* drop re-arms the driver. Sets
        ``session.client`` and ``session.status = new_status``; returns True on
        success.

        ``send_resume`` nudges a graceful-cancelled turn back to work with a
        single "Resume". ``prune_on_fail`` drops the index record on failure
        (startup path); the in-session driver leaves it for a later retry.
        """
        from .session_host.acp_adapter import open_acp_streams
        from .session_host.client import SessionHostClient

        def _on_acp_event(event_type: str, data: dict[str, Any]) -> None:
            if session.event_log:
                session.event_log.append(event_type, data)
            self._capture_progress(session, event_type, data)
            if event_type == "usage_update":
                self._handle_usage_update(session, data)

        # Release any stale (dead-transport) client first so its socketpair +
        # relay tasks are freed; host-mode shutdown DETACHES (child survives).
        old = session.client
        if old is not None:
            with contextlib.suppress(Exception):
                await old.shutdown()

        sock = None
        streams = None
        client = None

        async def _close_partial_reattach() -> None:
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.shutdown()
            if streams is not None:
                with contextlib.suppress(Exception):
                    await streams.aclose()
            if sock is not None:
                with contextlib.suppress(Exception):
                    await sock.close()

        def _settle_dead_child(exit_code: int) -> None:
            client_obj = session.client
            if client_obj is not None:
                client_obj.mark_host_child_exited(exit_code)
            self._reap_host_record(
                rec, f"Session Host child exited (code={exit_code})"
            )
            session.client = None
            session.status = SessionStatus.STOPPED
            self._db.update_session_status(
                rec.session_id, SessionStatus.STOPPED.value, time.time()
            )
            if session.event_log:
                session.event_log.append("session_state_changed", {
                    "status": SessionStatus.STOPPED.value,
                    "host_child_exited": True,
                    "exit_code": exit_code,
                })

        try:
            await self._ensure_forward(rec)
            sock = await SessionHostClient.connect(port=rec.port)
            await sock.attach(0, nonce=getattr(rec, "nonce", "").encode())
            streams = await open_acp_streams(sock)

            async def _closer(_streams: Any = streams, _sock: Any = sock) -> None:
                await _streams.aclose()
                await _sock.close()

            client = AcpClient(
                on_event=_on_acp_event,
                model_override=session.model_override,
                effort_override=session.effort_override,
            )
            streams.on_transport_lost = client.mark_transport_lost
            streams.on_child_exit = client.mark_host_child_exited
            # Retain the host control channel so the manager can push STATUS
            # (reapable) / DETACH (graceful) for host self-reap (#51).
            client.session_host_client = sock
            await asyncio.wait_for(
                client.start_streams(
                    streams.reader, streams.writer,
                    child_pid=rec.child_pid, closer=_closer,
                ),
                timeout=self._timeouts.session_start,
            )
            if streams.child_exit_code is not None:
                client.mark_host_child_exited(streams.child_exit_code)
                await _close_partial_reattach()
                _settle_dead_child(streams.child_exit_code)
                return False
            client.adopt_session(session.acp_session_id)
            session.client = client
            session.status = new_status
            self._db.update_session_status(
                rec.session_id, new_status.value, time.time(), pid=session.pid,
            )
            log.info(
                "Reattached session %s to live Session Host (pid=%s, port=%s)",
                rec.session_id, rec.host_pid, rec.port,
            )
        except asyncio.CancelledError:
            await _close_partial_reattach()
            raise
        except Exception:
            child_exit_code = (
                streams.child_exit_code if streams is not None else None
            )
            await _close_partial_reattach()
            if child_exit_code is not None:
                session.client = client
                _settle_dead_child(child_exit_code)
                return False
            log.warning(
                "Failed to reattach session %s to host pid=%s%s",
                rec.session_id, rec.host_pid,
                "; pruning" if prune_on_fail else "",
                exc_info=True,
            )
            if prune_on_fail and self._host_index is not None:
                with contextlib.suppress(Exception):
                    await self._drop_forward(rec.session_id)
                with contextlib.suppress(Exception):
                    self._host_index.remove(rec.session_id)
            return False

        # If this session's in-flight turn was graceful-cancelled for a redeploy,
        # nudge it back to work with a single "Resume" now that the frontend is
        # reattached (a bare "Resume" re-orients a Copilot session well).
        if send_resume and self._host_index is not None:
            self._host_index.set_resume_flag(rec.session_id, False)
            try:
                await self.submit_prompt(rec.session_id, "Resume")
                log.info(
                    "Sent 'Resume' to reattached session %s "
                    "(turn was graceful-cancelled for redeploy)",
                    rec.session_id,
                )
            except Exception:
                log.warning(
                    "Failed to send 'Resume' to reattached session %s",
                    rec.session_id, exc_info=True,
                )
        return True

    async def _try_reattach_live_host(self, session: Session) -> bool:
        """Adopt a still-alive Session Host for ``session`` instead of respawning.

        For a host-backed session whose frontend transport dropped (laptop sleep,
        tunnel flap, SSH sever) but whose Session Host + copilot child survive on
        the far side, reattach and adopt the running child -- recovering the
        in-flight/just-finished turn -- rather than spawning a *fresh* child +
        ``load_session``, which abandons the running work (the #145 "each send
        does a fresh load_session, losing the mid-turn tool call" symptom).

        Returns True on a successful reattach. No-op (False) unless Session-Host
        mode is on and a live, protocol-compatible host record exists for this
        session. Passes ``send_resume=False``: a caller resuming to submit a new
        prompt drives the turn itself, and it avoids re-entering ``submit_prompt``
        from within a resume.
        """
        if self._host_index is None:
            return False
        if not session.acp_session_id:
            return False
        rec = self._host_index.get(session.session_id)
        authority_v2 = bool(
            rec is not None
            and getattr(rec, "boundary", "local") != "local"
            and (getattr(rec, "extra", {}) or {}).get("remote_authority_v2")
        )
        if rec is None or authority_v2:
            await self._recover_remote_host_records(
                allow_wake=True,
                session_ids={session.session_id},
            )
            rec = self._host_index.get(session.session_id)
            if session.session_id in self._remote_recovery_inconclusive:
                raise RemoteHostRecoveryPendingError(
                    f"Remote Session Host state is inconclusive for "
                    f"{session.session_id}; refusing to spawn a duplicate "
                    "Copilot process"
                )
            if rec is None:
                return False
        container_target = (
            session.target.container
            if isinstance(session.target.container, dict)
            else {}
        )
        if (
            container_target.get("launch_pending_session_id")
            == session.session_id
        ):
            if rec is not None:
                self._reap_host_record(
                    rec,
                    "partially launched container Session Host",
                )
            raise RemoteHostRecoveryPendingError(
                f"Container Session Host cleanup is pending for "
                f"{session.session_id}; refusing to attach or spawn a "
                "duplicate Copilot process"
            )
        if not self._rec_host_alive(rec) or not self._rec_child_alive(rec):
            return False
        from .session_host.version_mux import HostDisposition, plan_host

        plan = plan_host(
            protocol_version=rec.protocol_version,
            child_alive=True,
            age_seconds=(time.time() - rec.created_at) if rec.created_at else None,
            stale_reap_seconds=self._session_host_stale_reap_seconds,
        )
        authority_v2 = (getattr(rec, "extra", {}) or {}).get(
            "remote_authority_v2"
        )
        if plan.disposition is not HostDisposition.REATTACH:
            if (
                getattr(rec, "boundary", "local") != "local"
                and authority_v2
            ):
                raise RemoteHostRecoveryPendingError(
                    f"Remote Session Host for {session.session_id} is still "
                    f"authoritative but cannot be reattached: {plan.reason}"
                )
            return False
        attached = await self._reattach_one(
            rec, session, new_status=SessionStatus.IDLE, send_resume=False,
        )
        if (
            not attached
            and getattr(rec, "boundary", "local") != "local"
            and authority_v2
        ):
            raise RemoteHostRecoveryPendingError(
                f"Could not reattach remote Session Host for "
                f"{session.session_id}; retained its authority record and "
                "credential relay, refusing to spawn a duplicate Copilot process"
            )
        return attached

    async def _resume_via_new_remote_host(
        self,
        session: Session,
        *,
        on_acp_event: Any,
        permission_callback: Any | None,
        load_existing: bool = True,
    ) -> tuple[AcpClient, str] | None:
        """Replace a dead remote Host and load/create its ACP session."""
        target = session.target
        container_target = (
            target.container
            if isinstance(target.container, dict)
            and target.container.get("name")
            else None
        )
        if container_target is not None:
            from .relay_state import get_live_relay_port
            from .session_host.container_transport import (
                build_container_spawner,
                cleanup_container_session_host,
                ensure_container_ready,
                prepare_container_session_host,
            )
            from .session_host.spawner import RemoteSpawnCleanupPendingError

            already_held = (
                session.session_id in self._container_lock_sessions
            )
            self._acquire_container_lock(
                session.session_id, container_target["name"],
            )
            prepared = None
            try:
                await ensure_container_ready(container_target)
                prepared = await prepare_container_session_host(
                    container_target,
                    get_live_relay_port(),
                )
                container_target["ssh"] = prepared["ssh"]
                container_target["state_command"] = prepared["state_command"]
                self._db.update_session_target(
                    session.session_id,
                    target.to_json(),
                    target.cwd,
                )
                spawner = build_container_spawner(
                    container_target,
                    prepared=prepared,
                    ready_timeout=self._timeouts.session_host_ready,
                    unexpected_reap_seconds=(
                        self._session_host_unexpected_reap_seconds
                    ),
                    active_reap_seconds=self._session_host_active_reap_seconds,
                )
                remote_cwd = (
                    prepared.get("workspace_folder")
                    or container_target.get("workspace_folder")
                    or None
                )
                plugin_dirs = await _resolve_remote_ai_plugin_dirs(
                    spawner.transport,
                    f"container:{container_target['name']}",
                    remote_cwd,
                )
                tracker = ConnectTracker(
                    session.event_log.append,
                    session_id=session.session_id,
                )
                return await self._connect_via_session_host(
                    target,
                    tracker=tracker,
                    session_id=session.session_id,
                    on_acp_event=on_acp_event,
                    permission_callback=permission_callback,
                    mcp_servers=session.mcp_servers,
                    spawner=spawner,
                    remote_child_argv=_container_remote_child_argv(
                        container_target,
                        prepared,
                        plugin_dirs,
                    ),
                    remote_cwd=remote_cwd,
                    load_session_id=(
                        session.acp_session_id if load_existing else None
                    ),
                    model=session.model_override,
                    effort=session.effort_override,
                )
            except Exception as exc:
                if isinstance(exc, RemoteSpawnCleanupPendingError):
                    self._set_container_launch_pending(
                        session.session_id,
                        True,
                    )
                elif not already_held:
                    self._release_container_lock(session.session_id)
                raise
            finally:
                if prepared is not None:
                    with contextlib.suppress(Exception):
                        await cleanup_container_session_host(
                            container_target,
                            prepared,
                        )
        cs_target = (
            target.codespace
            if isinstance(target.codespace, dict)
            and target.codespace.get("name")
            else None
        )
        if cs_target is None and target.spawn_command:
            from .session_host.codespace_transport import parse_codespace_target

            cs_target = parse_codespace_target(target.spawn_command)
        if not cs_target:
            return None
        from .relay_state import get_live_relay_port
        from .session_host.codespace_transport import build_codespace_spawner

        relay_prelude, relay_port = _resolve_relay_launch_env(
            cs_target["name"],
            get_live_relay_port(),
        )
        acp_command = cs_target["acp_command"]
        spawner = build_codespace_spawner(
            cs_target["name"],
            cs_target.get("repo") or "",
            relay_port=relay_port,
            unexpected_reap_seconds=self._session_host_unexpected_reap_seconds,
            active_reap_seconds=self._session_host_active_reap_seconds,
        )
        ai_plugin_dirs = await _resolve_remote_ai_plugin_dirs(
            spawner.transport,
            f"codespace:{cs_target['name']}",
            cs_target.get("workspace_folder") or None,
        )
        acp_command = _append_plugin_dirs(acp_command, ai_plugin_dirs)
        tracker = ConnectTracker(
            session.event_log.append,
            session_id=session.session_id,
        )
        client, acp_sid = await self._connect_via_session_host(
            target,
            tracker=tracker,
            session_id=session.session_id,
            on_acp_event=on_acp_event,
            permission_callback=permission_callback,
            mcp_servers=session.mcp_servers,
            spawner=spawner,
            remote_child_argv=["bash", "-lc", relay_prelude + acp_command],
            remote_cwd=cs_target.get("workspace_folder") or None,
            load_session_id=(session.acp_session_id if load_existing else None),
            model=session.model_override,
            effort=session.effort_override,
        )
        return client, acp_sid

    async def recover_disconnected_hosts(self) -> int:
        """In-session liveness-driven reattach for host-backed sessions (P1).

        The P0 heartbeat only *stamps* liveness; this is the *actuator*. For each
        host-backed session whose transport to its (still-alive) Session Host has
        dropped -- ``liveness_state() == 'disconnected'`` on a RUNNING turn, or a
        non-RUNNING session whose host-mode client is no longer running -- while
        the host + child processes survive, redial the host and resume by cursor
        (no restart, no lost turn). A merely ``stalled`` session (channel up,
        agent silent) is surfaced but not reattached -- reconnecting cannot
        un-wedge a silent agent. Returns the count reattached.
        """
        if self._host_index is None:
            return 0
        from .session_host.version_mux import HostDisposition, plan_host

        recovered = 0
        now = time.time()
        for rec in list(self._live_host_records()):
            session = self._sessions.get(rec.session_id)
            if session is None or not session.acp_session_id:
                continue
            client = session.client
            if (
                client is not None
                and client.host_child_exit_code is not None
            ):
                with contextlib.suppress(Exception):
                    await client.shutdown()
                self._reap_host_record(
                    rec,
                    "Session Host child exit observed by attached client",
                )
                session.client = None
                session.status = SessionStatus.STOPPED
                self._db.update_session_status(
                    rec.session_id, SessionStatus.STOPPED.value, time.time()
                )
                if session.event_log:
                    session.event_log.append("session_state_changed", {
                        "status": SessionStatus.STOPPED.value,
                        "host_child_exited": True,
                        "exit_code": client.host_child_exit_code,
                    })
                continue
            # A live, running client needs nothing; surface a stall and move on.
            if client is not None and client.is_running:
                if session.liveness_state(now) == "stalled":
                    log.warning(
                        "Session %s stalled (channel up, no output) -- "
                        "surfaced, not reattached", rec.session_id,
                    )
                continue
            # Transport is down. Only resume if the child is still there; a dead
            # child is a real end, left to normal teardown/GC.
            if not self._rec_child_alive(rec):
                continue
            # Respect version-mux: never drive a host this build can't speak to.
            plan = plan_host(
                protocol_version=rec.protocol_version,
                child_alive=True,
                age_seconds=(now - rec.created_at) if rec.created_at else None,
                stale_reap_seconds=self._session_host_stale_reap_seconds,
            )
            if plan.disposition is not HostDisposition.REATTACH:
                continue
            # Preserve a RUNNING turn's status so its replayed buffered frames
            # keep flowing; otherwise land it IDLE and drivable.
            keep = (SessionStatus.RUNNING
                    if session.status == SessionStatus.RUNNING
                    else SessionStatus.IDLE)
            if await self._reattach_one(
                rec, session, new_status=keep,
                send_resume=getattr(rec, "resume_on_reattach", False),
            ):
                recovered += 1
        if recovered:
            log.info("Recovered %d disconnected host-backed session(s)", recovered)
        return recovered

    async def reconcile_wedged_running(self, now: float | None = None) -> int:
        """Heal sessions wedged in RUNNING (issues #22 / #2384 / #2427).

        Eventual-terminal reconciliation across two shapes of wedge:

        1. **No live turn** (#2384): a session persisted as RUNNING whose turn
           can no longer reach a terminal event -- output has stopped
           (``liveness_state`` ``stalled`` or ``disconnected``) and there is **no
           live prompt task** driving it in this daemon -- would otherwise mirror
           "Responding..." forever. Resync it (rebuild from the agent's
           authoritative replay, respawning the child if the transport is gone)
           so it lands IDLE with a terminal ``session_state_changed``. A
           ``disconnected`` (transport gone) session is rebuilt at once; a
           ``stalled`` (transport UP, silent) one is held until its silence
           passes ``live_stall_interrupt_after_s`` first, because a **reattached,
           still-thinking** turn (adopted by cursor after a restart, no
           ``send_prompt`` here) is indistinguishable from a wedge by output
           alone -- resyncing it early would land a live think IDLE (#1276).

        2. **Live-stalled turn** (#2427, Phase 5): a session that is liveness
           ``stalled`` (transport up, no ACP frame for ``_STALL_AFTER_S``) but
           **still has a live ``_prompt_task``** -- the child is alive and a
           ``send_prompt`` is awaiting output that has gone silent. Resync cannot
           touch it (a live turn); instead, once its silence exceeds the separate,
           conservative ``live_stall_interrupt_after_s`` threshold, gracefully
           ``interrupt_turn()`` it (ACP session/cancel, #899). The in-flight
           ``send_prompt`` returns/raises, the runner settles the session to IDLE
           with a terminal event, and consumers converge. Never a task-cancel or
           child kill.

        Guards keep a genuinely progressing turn untouched: a session still
        producing output (liveness ``active``) is always skipped; a live turn is
        interrupted only after real silence past the large, operator-tunable
        threshold (0 disables the live-stall interrupt entirely). Best-effort and
        per-session isolated; a single failure never stalls the sweep. Returns the
        count reconciled (resynced + interrupted).
        """
        now = now if now is not None else time.time()
        healed = 0
        for sid, session in list(self._sessions.items()):
            if session.status != SessionStatus.RUNNING:
                continue
            liveness = session.liveness_state(now)
            if liveness not in ("stalled", "disconnected"):
                continue
            task = session._prompt_task
            if task is not None and not task.done():
                # A live turn is being driven here. The only safe action is a
                # graceful interrupt, and only for a *live-stalled* turn (client
                # up, output silent) that has been silent past the separate,
                # conservative live-stall threshold -- never a 'disconnected'
                # transport (cancel needs the client) and never a merely-long
                # turn still producing output. Diagnose-before-remediating: err
                # toward leaving a live turn alone.
                threshold = self._live_stall_interrupt_after_s
                silent_for = (
                    now - session.last_output_at
                    if session.last_output_at is not None else 0.0
                )
                if (liveness == "stalled" and threshold > 0
                        and silent_for > threshold):
                    try:
                        await self.interrupt_turn(sid)
                        healed += 1
                        log.warning(
                            "Interrupted live-stalled RUNNING session %s "
                            "(live turn silent for %.0fs > %.0fs threshold)",
                            sid, silent_for, threshold,
                        )
                    except Exception:
                        log.warning(
                            "Failed to interrupt live-stalled session %s",
                            sid, exc_info=True,
                        )
                continue
            # No live turn in THIS daemon. Two shapes reach here:
            #  * ``disconnected`` -- the transport is gone; nothing live to
            #    preserve and the log may be truncated, so rebuild it now.
            #  * ``stalled`` -- the client is UP but no local prompt task drives
            #    it. That is ALSO exactly what a **reattached, still-thinking**
            #    turn looks like: after a daemon restart / tunnel flap the
            #    Session Host's child survives and is adopted by cursor with NO
            #    ``send_prompt`` in this daemon, and a deep-reasoning step emits
            #    no ACP frame for minutes. Output alone can't tell that from a
            #    genuine wedge, and ``resync_session`` tears the client down,
            #    respawns, and lands the session IDLE -- which would KILL a
            #    resumed think mid-turn (dotfiles#1276). So hold off on a
            #    client-up stall until its silence exceeds the same conservative
            #    threshold the live-stall interrupt uses; a real wedge still
            #    heals, just later. ``disconnected`` (no live transport) is not
            #    gated -- it must rebuild to recover.
            if liveness == "stalled":
                threshold = self._live_stall_interrupt_after_s
                silent_for = (
                    now - session.last_output_at
                    if session.last_output_at is not None else 0.0
                )
                if not (threshold > 0 and silent_for > threshold):
                    continue
            try:
                await self.resync_session(sid)
                healed += 1
                log.warning(
                    "Reconciled wedged RUNNING session %s to idle "
                    "(no live turn, output stopped)", sid,
                )
            except Exception:
                log.warning(
                    "Failed to reconcile wedged session %s", sid, exc_info=True,
                )
        if healed:
            log.info("Reconciled %d wedged RUNNING session(s)", healed)
        return healed

    def stranded_host_records(self) -> list[Any]:
        """Live Session Hosts this frontend can no longer speak to (version-mux).

        Returns the ``HostRecord``s for hosts whose process is alive but whose
        wire-envelope protocol is not one this build supports -- i.e. old-version
        hosts still keeping their children until each stops. Useful for
        observability and for a deploy layer to know which old on-disk installs
        are still pinned. Empty (the common case) unless a breaking host-layer
        change has left older hosts running.
        """
        if self._host_index is None:
            return []
        from .session_host.version_mux import is_compatible

        return [
            rec for rec in self._live_host_records()
            if not is_compatible(rec.protocol_version)
        ]

    def _reap_host_record(self, rec: Any, reason: str) -> None:
        """Reap a Session Host + its child and drop the durable index record.

        Kills the **child first** -- a POSIX SIGTERM to the host does not run its
        cleanup, so the child could otherwise orphan -- then the host, then
        removes the record. Cross-platform via ``osutil.kill_pid`` (on Windows
        ``taskkill /T`` collects the process tree, and the host's kill-on-close
        job also takes the child). Used for both the explicit-terminate reap
        (#1786) and the version-mux stranded/forced reap.
        """
        from .session_host.osutil import kill_pid, reap_zombie

        log.info(
            "Reaping Session Host for session %s (host pid=%s, child pid=%s): %s",
            rec.session_id, rec.host_pid, rec.child_pid, reason,
        )
        boundary = getattr(rec, "boundary", "local")
        if boundary == "local":
            kill_pid(rec.child_pid, force=True)
            kill_pid(rec.host_pid, force=True)
            # Clear the zombie a host we parented leaves behind (no-op for a
            # reattached host that init reaps, or on Windows).
            reap_zombie(rec.child_pid)
            reap_zombie(rec.host_pid)
        else:
            # Remote (CodeSpace / mesh) boundary: host_pid/child_pid live on the
            # FAR side -- killing those pid numbers locally would hit unrelated
            # local processes. Tear down the local forward and best-effort kill
            # the remote host (its PR_SET_PDEATHSIG takes the child with it).
            self._kill_forward_sync(
                rec.session_id,
                release_container_lock=False,
            )
            self._schedule_remote_reap(rec, reason)
        with contextlib.suppress(Exception):
            self._host_index.remove(rec.session_id)
        # #4272 bridge-lock: best-effort clear the lattice lock at teardown.
        # Fire-and-forget (this reap path is sync); a lingering lock is already
        # ignored by the picker's reader once the child pid dies.
        with contextlib.suppress(Exception):
            from . import bridge_lock
            bridge_lock.remove_sync(rec.session_id)

    def _kill_forward_sync(
        self,
        session_id: str,
        *,
        release_container_lock: bool = True,
    ) -> None:
        """Best-effort synchronous teardown of session forward processes."""
        self._kill_relays_sync(session_id)
        fwd = self._forwards.pop(session_id, None)
        if fwd is not None:
            proc = getattr(fwd, "_proc", None)
            if proc is not None and getattr(proc, "returncode", 0) is None:
                with contextlib.suppress(Exception):
                    proc.kill()
        if release_container_lock:
            self._release_container_lock(session_id)

    def _kill_relays_sync(self, session_id: str) -> None:
        """Best-effort synchronous teardown of relay supervisors."""
        for relay in self._relays.pop(session_id, []):
            task = getattr(relay, "_monitor_task", None)
            if task is not None and not getattr(task, "done", lambda: True)():
                with contextlib.suppress(Exception):
                    task.cancel()
            proc = getattr(relay, "_proc", None)
            if proc is not None and getattr(proc, "returncode", 0) is None:
                with contextlib.suppress(Exception):
                    proc.kill()

    def _schedule_remote_reap(self, rec: Any, reason: str) -> None:
        """Fire-and-forget a remote ``kill`` of a detached far-side Host.

        Uses the durable endpoint's SSH config (no live Spawner needed) to run a
        one-shot ``kill`` over the tunnel. Best-effort: if there is no running
        loop or the exec fails, the detached Host lingers until the CodeSpace
        stops -- never fatal, and never touches a local process.
        """
        endpoint = getattr(rec, "endpoint", None) or {}
        if not endpoint:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._remote_reap(rec, endpoint))
        self._remote_reap_tasks.add(task)
        task.add_done_callback(self._remote_reap_tasks.discard)

    async def _remote_reap(self, rec: Any, endpoint: dict) -> bool:
        from ssh_manager import ConnectionManager

        from .session_host.endpoints import ssh_config_from_endpoint

        class _StaticSource:
            def __init__(self, cfg):
                self._cfg = cfg

            def get_ssh_config(self):
                return self._cfg

            def refresh(self):
                return self._cfg

        cfg = ssh_config_from_endpoint(endpoint)
        host = cfg.host_alias
        mgr = None
        confirmed_dead = False
        try:
            mgr = ConnectionManager()
            await mgr.ensure_connected(host, _StaticSource(cfg), [])
            # Kill the host's whole PROCESS GROUP, not just the host pid. The
            # host was launched via ``setsid`` (so it leads its own group,
            # pgid == host_pid) and the ``bash -lc`` wrapper + the copilot
            # grandchild inherit that group -- so ``kill -- -<pgid>`` takes the
            # host AND copilot in one shot, with nothing orphaned (killing only
            # host_pid would leave copilot reparented to init). Fall back to the
            # bare pid if the group send is rejected. SIGTERM first (lets copilot
            # flush), then SIGKILL as a backstop.
            pid = int(rec.host_pid)
            child_pid = int(rec.child_pid)
            result = await mgr.exec_command(
                host,
                f"kill -TERM -{pid} 2>/dev/null || kill -TERM {pid} 2>/dev/null; "
                f"sleep 1; kill -KILL -{pid} 2>/dev/null || kill -KILL {pid} "
                "2>/dev/null || true; "
                "alive() { test -r /proc/$1/stat && "
                "test \"$(awk '{print $3}' /proc/$1/stat 2>/dev/null)\" != Z; }; "
                "i=0; while { alive "
                f"{pid} || alive {child_pid}; "
                "} && test $i -lt 20; do sleep 0.1; i=$((i+1)); done; "
                f"if alive {pid} || alive {child_pid}; then exit 42; fi; "
                "printf __REAPED__",
                timeout=20.0,
            )
            confirmed_dead = (
                result.exit_code == 0
                and "__REAPED__" in (result.stdout or "")
            )
            if not confirmed_dead:
                raise RuntimeError(
                    f"remote reap could not verify process death "
                    f"(rc={result.exit_code}, output={result.stdout!r})"
                )
            log.info("Remote-reaped Session Host group for session %s (far pid=%s)",
                     rec.session_id, rec.host_pid)
        except Exception:
            log.warning(
                "Best-effort remote reap failed for session %s (far pid=%s); "
                "the detached Host will exit when the CodeSpace stops",
                rec.session_id, rec.host_pid, exc_info=True,
            )
        finally:
            if mgr is not None:
                with contextlib.suppress(Exception):
                    await mgr.disconnect(host)
            if confirmed_dead:
                self._set_container_launch_pending(rec.session_id, False)
                self._release_container_lock(rec.session_id)
        return confirmed_dead

    def sweep_stranded_hosts(self) -> int:
        """Reap stranded incompatible Session Hosts that are now reapable.

        A periodic counterpart to the startup-time gate in
        ``reattach_session_hosts``: during a single long frontend lifetime an
        incompatible host's child may finally reach its own stop, or an immortal
        one may outlive the configured ``session_host_stale_reap_seconds`` sprawl
        bound. This re-evaluates every live host and reaps those whose disposition
        is REAP_STOPPED or FORCE_REAP -- never touching a compatible host or a
        stranded host still within the bound. Returns the count reaped.
        """
        if self._host_index is None:
            return 0
        from .session_host.version_mux import HostDisposition, plan_host

        self._prune_dead_hosts()
        now = time.time()
        reaped = 0
        for rec in self._live_host_records():
            plan = plan_host(
                protocol_version=rec.protocol_version,
                child_alive=self._rec_child_alive(rec),
                age_seconds=(now - rec.created_at) if rec.created_at else None,
                stale_reap_seconds=self._session_host_stale_reap_seconds,
            )
            if plan.disposition in (HostDisposition.REAP_STOPPED,
                                    HostDisposition.FORCE_REAP):
                self._reap_host_record(rec, plan.reason)
                reaped += 1
        return reaped

    # -- Subscriber tracking + idle reaper (#1826) ----------------------------

    def add_subscriber(self, session_id: str) -> None:
        """Register an active event subscriber (an SSE stream / attached front).

        Increments the session's live-subscriber count so the idle reaper knows
        the session is being watched. Paired with ``remove_subscriber`` in the
        SSE stream's teardown. No-op for an unknown session.
        """
        sid = self._resolve_ref(session_id) or session_id
        s = self._sessions.get(sid)
        if s is not None:
            s.subscriber_count += 1

    def remove_subscriber(self, session_id: str) -> None:
        """Deregister an event subscriber; clamp at zero.

        When the last subscriber leaves, ``touch()`` the session so the
        idle-reaper TTL clock starts from the moment it became unwatched (not
        from the last turn).
        """
        sid = self._resolve_ref(session_id) or session_id
        s = self._sessions.get(sid)
        if s is not None:
            s.subscriber_count = max(0, s.subscriber_count - 1)
            if s.subscriber_count == 0:
                s.touch()

    async def sweep_idle_sessions(self, *, now: float | None = None) -> int:
        """Stop idle, unwatched sessions past the reap TTL (#1826).

        The bridge owns session process lifetime: a session that is IDLE (agent
        at its own stop -- never mid-turn), has ZERO active subscribers, holds no
        active background sub-agents, **has run at least one turn** (so it has a
        persisted ACP conversation a fresh child can ``load_session``), is **not
        backed by a live remote Session Host** (whose far-side child's activity
        the frontend cannot see -- dotfiles#1633), and has been idle+unwatched at
        least ``idle_reap_ttl_seconds`` is **stopped with its host child reaped**
        -- freeing the Copilot process while leaving the session resumable (fresh
        child + ``load_session`` replay). This is what lets a front (Neuron
        Forge) merely connect/disconnect and never reap for resource reasons.
        Returns the count reaped. No-op unless enabled + Session-Host mode.
        """
        ttl = self._idle_reap_ttl_seconds
        if not ttl or ttl <= 0:
            return 0
        now = now if now is not None else time.time()
        # Sessions fronted by a live REMOTE host must not be idle-reaped on LOCAL
        # idle/unwatched signals: a codespace/ssh child can be mid remote
        # tool-call while the session looks idle here, so "freeing" it decapitates
        # live remote work (observed: a 15-30min remote build poll whose child was
        # freed while the session read idle+unwatched -- dotfiles#1633). Local
        # hosts keep the reaper (their pid + status are locally authoritative).
        remote_host_sids = self._live_remote_host_sessions()
        reaped = 0
        for sid, s in list(self._sessions.items()):
            if s.status != SessionStatus.IDLE:
                continue
            if s.subscriber_count > 0:
                continue
            if s.has_active_background_tasks:
                continue
            if sid in remote_host_sids:
                continue
            if s.turn_count <= 0:
                # A 0-turn session has no persisted ACP conversation, so a fresh
                # child cannot load_session it -- reaping it to STOPPED would
                # leave it unresumable (validated live: resume -> "session not
                # found"). Only reap sessions with resumable state; leave empties
                # to the existing 0-turn worktree cleanup.
                continue
            idle_for = now - s.updated_at
            if idle_for < ttl:
                continue
            try:
                await self.stop_session(sid, reap_host=True)
            except SessionBusyError:
                continue
            except Exception:
                log.warning(
                    "Idle reap of session %s failed", sid, exc_info=True
                )
                continue
            reaped += 1
            log.info(
                "Idle-reaped session %s (%s): idle+unwatched %.0fs >= %.0fs TTL "
                "-- child freed, session resumable",
                sid, s.name, idle_for, ttl,
            )
        return reaped

    def note_heartbeats(self, now: float | None = None) -> int:
        """Periodic transport-liveness beat for RUNNING sessions (#145).

        Stamps ``last_heartbeat_at`` on every RUNNING session whose ACP client
        subprocess is still alive. A frozen heartbeat then means the transport
        died (tunnel drop / host sleep); a fresh heartbeat with a stale
        ``last_output_at`` means the agent stalled while the channel is up. In
        memory only; cheap (a process poll per session). Returns the count beat.
        """
        now = now if now is not None else time.time()
        beat = 0
        for s in list(self._sessions.values()):
            if s.status != SessionStatus.RUNNING:
                continue
            if s.client and s.client.is_running:
                s.note_heartbeat(now)
                beat += 1
        return beat

    async def start_session(
        self,
        target: SpawnTarget,
        agent_name: str | None = None,
        caller_id: str | None = None,
        permission_callback: Any | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        copilot_args: list[str] | None = None,
        env_overrides: dict[str, str] | None = None,
        caller_owner_ref: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        parity_fault: str | None = None,
        replace_session_id: str | None = None,
        retain_container_lock_on_failure: bool = False,
    ) -> Session:
        """Create and start a new agent session.

        Spawns a copilot --acp --stdio subprocess, initializes the ACP
        protocol, and creates a new ACP session. The session is ready
        to receive prompts when this returns.

        Args:
            target: Where/how to spawn the agent.
            agent_name: Optional display name for the agent.
            caller_id: Optional caller identity (e.g. worktree ID) for
                session affinity.  Sessions with matching (agent_name,
                caller_id) are reused instead of creating new ones.
            permission_callback: Optional async callback for permission
                requests. Signature: (session_id, options, tool_call) ->
                RequestPermissionResponse. If set, auto_approve is disabled.
            mcp_servers: Optional per-session MCP toolset (ACP server specs)
                mounted into the ACP session at session/new. None preserves
                the historic empty toolset.
            copilot_args: Optional extra ``copilot`` CLI args appended to
                ``target.copilot_args`` for this session only (e.g. a per-run
                ``--additional-mcp-config``). None preserves the agent's args.
            env_overrides: Request-owned environment overrides already merged
                into ``target.env`` by the route.
        """
        if self._draining:
            raise DaemonDrainingError("session")
        from .protocol import FAILED_ACP_HANDSHAKE_FAULT

        if parity_fault not in {None, FAILED_ACP_HANDSHAKE_FAULT}:
            raise ValueError(f"unsupported parity fault: {parity_fault}")
        # Per-session copilot args: append to the resolved target's args for
        # THIS spawn only (a fresh target copy so a shared/cached AgentConfig
        # target is never mutated). Every spawn path appends target.copilot_args,
        # so this reaches local, SSH, and command launches uniformly.
        existing_venue = target.venue if isinstance(target.venue, dict) else {}
        existing_overrides = existing_venue.get(_REQUEST_OVERRIDES_KEY)
        existing_request_env = (
            existing_overrides.get("env")
            if isinstance(existing_overrides, dict)
            else None
        )
        existing_request_args = (
            existing_overrides.get("copilot_args")
            if isinstance(existing_overrides, dict)
            else None
        )
        request_copilot_args = (
            list(existing_request_args)
            if copilot_args is None and isinstance(existing_request_args, list)
            else list(copilot_args or [])
        )
        request_env = (
            dict(existing_request_env)
            if env_overrides is None and isinstance(existing_request_env, dict)
            else dict(env_overrides or {})
        )
        if copilot_args:
            target = replace(
                target,
                copilot_args=[*target.copilot_args, *copilot_args],
            )
        if self._provider_backed_target(target):
            target = replace(
                target,
                venue={
                    **(target.venue or {}),
                    _REQUEST_OVERRIDES_KEY: {
                        "env": request_env,
                        "copilot_args": request_copilot_args,
                    },
                },
            )
        # #2178: bind the caller worktree onto the target so the worktree-resolve
        # step records it on the spawned (bridge) worktree, enabling the Picker's
        # "Jump to caller". caller_id is the caller's WORKTREE_ID (agent-bridge
        # convention); a non-worktree caller simply won't resolve in the Picker.
        if caller_id and not target.caller_worktree:
            target = replace(target, caller_worktree=caller_id)
        # resource-obligation-settlement Ph3c: carry the caller's qualified
        # ClaimRef onto the target so the worktree-resolve stamps it as the
        # bridge worktree's owner_ref (via `resolve --new --owner-ref`). The
        # carve then journals the reciprocal `worktree` claim on the caller, so
        # the caller's finalize gate sees the bridge session as an obligation and
        # the bridge worktree's finalize settles it (Phase 3a). Best-effort:
        # None (a non-worktree caller / stale runtime) simply skips it.
        if caller_owner_ref and not target.caller_owner_ref:
            target = replace(target, caller_owner_ref=caller_owner_ref)
        session_id = str(uuid.uuid4())[:12]
        name = _generate_name()
        now = time.time()

        # Concurrency guard: command-type (CodeSpace) agents allow only one
        # active session at a time, since they share a single checkout. This
        # check and the self._sessions registration below run synchronously
        # (no await in between), so concurrent start_session calls cannot
        # race past the guard.
        ws_key = _workspace_key(agent_name, target, caller_id)
        if ws_key is not None:
            existing = self._find_active_session(ws_key)
            if (
                existing is not None
                and existing.status == SessionStatus.STOPPED
                and not existing.acp_session_id
            ):
                # A zero-turn/failed-start incumbent has no ACP identity to
                # preserve and may carry stale provider metadata. Remove it
                # before the fresh target is registered, so the caller's new
                # provider resolution (workspace, Session Host transport,
                # launch policy) is not discarded by the workspace guard.
                log.info(
                    "Replacing stopped session %s with no ACP identity for %s",
                    existing.session_id,
                    agent_name,
                )
                await self.end_session(existing.session_id, force=True)
                existing = self._find_active_session(ws_key)
            if (
                existing is not None
                and existing.session_id != replace_session_id
            ):
                raise SessionConflictError(
                    agent_name=agent_name or "",
                    existing_session_id=existing.session_id,
                )

        session = Session(session_id, name, target, agent_name, caller_id=caller_id)
        # Per-session model / reasoning-effort override (agent-bridge create
        # --model/--effort). Retained on the Session so within-daemon resume /
        # reattach re-applies it (copilot ignores --model under --acp; the model
        # is set per-session via session/set_config_option -- see
        # AcpClient._apply_model_config).
        session.model_override = model
        session.effort_override = effort
        session.mcp_servers = [
            dict(server) for server in (mcp_servers or [])
        ]
        session.event_log = EventLog(
            db=self._db,
            session_id=session_id,
            worktree_id=target.worktree_id,
        )

        # Wire ACP events into the session's event log
        def on_acp_event(event_type: str, data: dict[str, Any]) -> None:
            if session.event_log:
                session.event_log.append(event_type, data)
            self._capture_progress(session, event_type, data)
            if event_type == "usage_update":
                self._handle_usage_update(session, data)

        # Persist to DB
        self._db.create_session(
            session_id=session_id,
            name=name,
            agent_name=agent_name,
            caller_id=caller_id,
            target_dir=target.cwd,
            target_type=target.type,
            status=SessionStatus.STARTING.value,
            now=now,
            target_json=target.to_json(),
        )

        session.status = SessionStatus.STARTING
        self._sessions[session_id] = session

        tracker = ConnectTracker(session.event_log.append, session_id=session_id)
        # Stage 3 (SSH connect) is patient for codespace boot, else the
        # general ssh_connect budget.
        connect_timeout = (
            self._timeouts.codespace_boot
            if target.type == "command" or target.spawn_command
            else self._timeouts.ssh_connect
        )
        client: AcpClient | None = None
        agent_proc: AgentProcess | None = None
        acp_sid: str | None = None

        async def _cleanup_failed_process_launch() -> None:
            """Reap a process-owned launch before recording terminal failure."""
            if agent_proc is None:
                return
            pid = agent_proc.pid
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.shutdown()
            # AcpClient.shutdown owns this same process, but retain the
            # AgentProcess whole-tree kill as a fallback if shutdown failed or
            # returned before a wrapper/SSH descendant exited.
            if agent_proc.alive:
                with contextlib.suppress(Exception):
                    await agent_proc.kill()
            session.client = None
            session.event_log.append("failed_launch_cleanup", {
                "pid": pid,
                "reaped": not agent_proc.alive,
            })
            log.info(
                "Reaped failed process-owned launch for session %s (pid=%s, alive=%s)",
                session_id,
                pid,
                agent_proc.alive,
            )

        try:
            cs_target = None
            # Prefer the structured provider metadata (#177); fall back to
            # shape-detecting the spawn_command for agents registered before
            # the metadata seam existed (back-compat).
            if isinstance(target.codespace, dict) and target.codespace.get("name"):
                cs_target = target.codespace
            elif target.spawn_command:
                from .session_host.codespace_transport import parse_codespace_target
                cs_target = parse_codespace_target(target.spawn_command)
            container_target = (
                target.container
                if isinstance(target.container, dict)
                and target.container.get("name")
                else None
            )
            if parity_fault:
                session.parity_fault_result = {
                    "fault": parity_fault,
                    "provider_cleanup": container_target is None,
                }

            if target.type == "local":
                # Session-Host mode: the child lives in a survivable host that
                # outlives this frontend (goal 1/3). resolve->launch host->
                # reattach over loopback->drive ACP.
                client, acp_sid = await self._connect_via_session_host(
                    target,
                    tracker=tracker,
                    session_id=session_id,
                    on_acp_event=on_acp_event,
                    permission_callback=permission_callback,
                    mcp_servers=mcp_servers,
                    model=model,
                    effort=effort,
                )
            elif container_target is not None:
                # Trusted containers use the same durable far-side Session Host
                # model as CodeSpaces.  agent-containers prepares only the SSH
                # endpoint and one launch's secret-backed env command; all Host,
                # forwarding, authority, and recovery ownership stays here.
                from .relay_state import get_live_relay_port
                from .session_host.container_transport import (
                    build_container_spawner,
                    cleanup_container_session_host,
                    ensure_container_ready,
                    prepare_container_session_host,
                )
                from .session_host.spawner import RemoteSpawnCleanupPendingError

                if replace_session_id:
                    self._transfer_container_lock(
                        replace_session_id,
                        session_id,
                        container_target["name"],
                    )
                else:
                    self._acquire_container_lock(
                        session_id, container_target["name"],
                    )
                prepared = None
                try:
                    await ensure_container_ready(container_target)
                    prepared = await prepare_container_session_host(
                        container_target,
                        get_live_relay_port(),
                    )
                    container_target["ssh"] = prepared["ssh"]
                    container_target["state_command"] = prepared["state_command"]
                    self._db.update_session_target(
                        session_id,
                        target.to_json(),
                        target.cwd,
                    )
                    container_spawner = build_container_spawner(
                        container_target,
                        prepared=prepared,
                        ready_timeout=self._timeouts.session_host_ready,
                        unexpected_reap_seconds=(
                            self._session_host_unexpected_reap_seconds
                        ),
                        active_reap_seconds=self._session_host_active_reap_seconds,
                    )
                    remote_cwd = (
                        prepared.get("workspace_folder")
                        or container_target.get("workspace_folder")
                        or None
                    )
                    plugin_dirs = (
                        []
                        if parity_fault
                        else await _resolve_remote_ai_plugin_dirs(
                            container_spawner.transport,
                            f"container:{container_target['name']}",
                            remote_cwd,
                        )
                    )
                    client, acp_sid = await self._connect_via_session_host(
                        target,
                        tracker=tracker,
                        session_id=session_id,
                        on_acp_event=on_acp_event,
                        permission_callback=permission_callback,
                        mcp_servers=mcp_servers,
                        spawner=container_spawner,
                        remote_child_argv=_container_remote_child_argv(
                            container_target,
                            prepared,
                            plugin_dirs,
                            acp_command_override=(
                                _failed_acp_handshake_command()
                                if parity_fault
                                else None
                            ),
                        ),
                        remote_cwd=remote_cwd,
                        model=model,
                        effort=effort,
                        parity_fault_result=session.parity_fault_result,
                    )
                except Exception as exc:
                    if isinstance(exc, RemoteSpawnCleanupPendingError):
                        self._set_container_launch_pending(session_id, True)
                        with contextlib.suppress(Exception):
                            await self._recover_remote_host_records(
                                allow_wake=True,
                                session_ids={session_id},
                            )
                            provisional = self._host_index.get(session_id)
                            if provisional is not None:
                                self._reap_host_record(
                                    provisional,
                                    "incomplete container Session Host launch",
                                )
                    elif (
                        not parity_fault
                        and not retain_container_lock_on_failure
                    ):
                        self._release_container_lock(session_id)
                    raise
                finally:
                    if prepared is not None:
                        try:
                            cleaned = await cleanup_container_session_host(
                                container_target,
                                prepared,
                            )
                        except Exception:
                            cleaned = False
                            log.warning(
                                "Container launch-env cleanup failed for %s",
                                container_target["name"],
                                exc_info=True,
                            )
                        if session.parity_fault_result is not None:
                            session.parity_fault_result[
                                "provider_cleanup"
                            ] = cleaned
                            if (
                                cleaned
                                and session.parity_fault_result.get(
                                    "remote_authority_removed"
                                ) is True
                            ):
                                self._release_container_lock(session_id)
            elif cs_target is not None:
                # CodeSpace Session-Host mode (#177): bootstrap the Host inside
                # the CodeSpace, forward its loopback endpoint, and drive ACP over
                # it -- so a host sleep/tunnel flap disconnects the front while
                # copilot keeps running on the CS and the front reattaches by
                # cursor. The relay port rides the persistent forward's -R for
                # ADO/git during a build.
                from .session_host.codespace_transport import build_codespace_spawner

                # #897 Increment B step 2: acquire the exclusive, worktree-keyed
                # claim BEFORE establishing the Session-Host transport. A
                # CodeSpace is fronted by exactly one bridge, and Session-Host
                # dispatch never runs ``agent-codespaces ssh`` (the direct-path
                # enforcement point), so this is where a second worktree
                # dispatching to an already-claimed CodeSpace is bounced instead
                # of clobbering the incumbent. Degrade-safe: no owner / disabled
                # / binstub absent -> proceed unclaimed, today's behavior.
                claim_owner = getattr(target, "caller_worktree", None) or caller_id
                _claim_status, _claim_detail = _claim_codespace(
                    cs_target["name"],
                    claim_owner or "",
                    holder_ref=getattr(target, "caller_owner_ref", None),
                )
                if _claim_status == "conflict":
                    raise CodespaceClaimConflictError(
                        cs_target["name"], claim_owner or "", _claim_detail
                    )
                if _claim_status == "coordination-rejected":
                    raise CodespaceCoordinationRejectedError(
                        cs_target["name"], claim_owner or "", _claim_detail
                    )


                # path injects, so a detached copilot on the CS has working
                # ADO/git auth over the credential relay (the daemon owns the
                # relay; the per-codespace token is minted by agent-codespaces).
                # Guarded: if agent-codespaces isn't importable, the Host runs
                # auth-light (fine for ACP + non-ADO turns).
                #
                # Source the port from the daemon's *actually-bound* relay
                # (get_live_relay_port) rather than agent-codespaces' static
                # config port, so the CS env + the persistent forward's ``-R``
                # follow the live relay -- mirroring the mesh path (commit
                # 8a8bd8f8) and fixing CodeSpace ADO auth when the relay isn't on
                # the declared 9857 (dotfiles #489/#540 pt3). None -> the callee
                # falls back to the config port.
                relay_prelude = ""
                relay_port = None
                from .relay_state import get_live_relay_port
                relay_prelude, relay_port = _resolve_relay_launch_env(
                    cs_target["name"], get_live_relay_port()
                )
                cs_spawner = build_codespace_spawner(
                    cs_target["name"], cs_target["repo"], relay_port=relay_port,
                    unexpected_reap_seconds=self._session_host_unexpected_reap_seconds,
                    active_reap_seconds=self._session_host_active_reap_seconds,
                )
                # The acp_command is a far-side SHELL string (e.g.
                # ``cd /workspaces/repo && copilot --acp --stdio``), not an argv,
                # so the Session Host execs it through a login shell (with the
                # relay prelude prepended); copilot inherits the host's stdio pipe
                # as fd 0/1 and its exit ends the shell (child-liveness tracks it).
                #
                # Model/effort are NOT passed as ``--model`` launch flags here:
                # copilot ignores them in ``--acp`` mode. The dispatched agent's
                # model is set by the ACP client after the session exists, via
                # ``session/set_config_option`` (see AcpClient._apply_model_config,
                # dotfiles#790) -- the single, uniform mechanism for every
                # dispatch path.
                acp_command = cs_target["acp_command"]
                # Fold the CodeSpace repo's OWN enabled ``.ai`` plugin dirs into
                # the launch as ``--plugin-dir`` so the dispatched ACP agent
                # loads the product repo's own in-repo skills/MCP. ``copilot
                # --acp`` ignores ``enabledPlugins`` -- only ``--plugin-dir``
                # surfaces plugin skills -- and this Session-Host path (never the
                # front-owns-stdio ``agent-codespaces ssh`` path) is where a real
                # CodeSpace dispatch launches, so the fold MUST happen here
                # (dotfiles#1274 WS1-skills). Resolved by shelling agent-codespaces'
                # own venv (process boundary), mirroring the relay-launch-env
                # seam. Best-effort: [] on any failure -> unchanged launch. The
                # payloads already live in the checkout, so ``--plugin-dir`` at
                # ``/workspaces/<repo>/.ai/<name>`` needs no install/egress. The
                # flags append to the tail of ``cd <repo> && copilot --acp …``, so
                # they land on the ``copilot`` invocation.
                ai_plugin_dirs = (
                    []
                    if parity_fault
                    else await _resolve_remote_ai_plugin_dirs(
                        cs_spawner.transport,
                        f"codespace:{cs_target['name']}",
                        cs_target.get("workspace_folder") or None,
                    )
                )
                acp_command = (
                    _failed_acp_handshake_command()
                    if parity_fault
                    else _append_plugin_dirs(acp_command, ai_plugin_dirs)
                )
                if ai_plugin_dirs:
                    log.info(
                        "Folded %d repo-own .ai --plugin-dir(s) into %s launch: %s",
                        len(ai_plugin_dirs), cs_target["name"], ai_plugin_dirs,
                    )
                remote_argv = [
                    "bash", "-lc", relay_prelude + acp_command,
                ]
                # Copilot runs its tools from the ACP session cwd, so it must be
                # the CodeSpace workspace checkout (e.g. /workspaces/example-web) --
                # NOT the _default_cwd() /home/<user> fallback, or the agent works
                # blind with no repo in view. Prefer the structured provider
                # workspace_folder; else the cd-target parsed from acp_command.
                remote_cwd = cs_target.get("workspace_folder") or None
                from .session_host.spawner import RemoteSpawnCleanupPendingError

                try:
                    client, acp_sid = await self._connect_via_session_host(
                        target,
                        tracker=tracker,
                        session_id=session_id,
                        on_acp_event=on_acp_event,
                        permission_callback=permission_callback,
                        mcp_servers=mcp_servers,
                        spawner=cs_spawner,
                        remote_child_argv=remote_argv,
                        remote_cwd=remote_cwd,
                        model=model,
                        effort=effort,
                        parity_fault_result=session.parity_fault_result,
                    )
                except RemoteSpawnCleanupPendingError:
                    raise
                except Exception:
                    if not parity_fault:
                        claim_key = _codespace_claim_key(session.target)
                        if claim_key is not None:
                            _release_codespace_claim(*claim_key)
                    raise
            elif self._is_codespace_target(target):
                # A CodeSpace target MUST run under a Session Host: only then does
                # copilot's stdio belong to a survivable host on the far side, so
                # a transport drop (tunnel flap, daemon restart, credential-relay
                # TTL) merely detaches the front instead of closing the child's
                # stdio and self-cancelling its in-flight turn ("Operation
                # cancelled by user"). Session Hosts are always on (dotfiles#1478),
                # so reaching this branch means the codespace target could not be
                # resolved to a spawner -- fail loud rather than silently degrade
                # to a non-survivable process-owned session that loses in-flight
                # work on any hiccup.
                raise RuntimeError(
                    f"CodeSpace target {getattr(target, 'agent_name', None)!r} "
                    f"was detected but could not be resolved to a CodeSpace "
                    f"spawner/transport (cs_target=unresolved). Session Hosts are "
                    f"always on, so this is a resolution/configuration failure, "
                    f"not a disabled mode. Refusing to fall back to the "
                    f"process-owned (non-survivable) path."
                )
            else:
                # Process-owned (front-owns-stdio) transport for ssh/command
                # targets (mesh, elevated, spawn_command providers). Local +
                # CodeSpace always run under a survivable Session Host above;
                # ssh/command have no host-boundary spawner yet (SshSpawner /
                # ElevatedSpawner are the remaining gap -- see
                # ThomasMichon/copilot-extensions#566), so this is their only
                # path. It is NOT reachable for a local target. Emits per-stage
                # checkpoints (auth-env, ssh-connect, worktree) into the event log.
                agent_proc = await spawn(
                    target,
                    tracker=tracker,
                    connect_timeout=connect_timeout,
                    session_id=session_id,
                )

                # Stage 7: launch + initialize Copilot in ACP mode. Should be
                # fast; bound it so a hung launch fails fast.
                with tracker.stage(ConnectStage.LAUNCH_ACP):
                    client = AcpClient(
                        on_event=on_acp_event,
                        on_permission=permission_callback,
                        model_override=model,
                        effort_override=effort,
                    )
                    if permission_callback:
                        client.auto_approve = False
                    try:
                        await asyncio.wait_for(
                            client.start(agent_proc.proc),
                            timeout=self._timeouts.session_start,
                        )
                        # Create ACP session -- binstub agents resolve CWD
                        # remotely, so target.cwd may be None.  The ACP spec
                        # requires an absolute path.  Prefer the venue's concrete
                        # workspace folder (container fleets surface it) so the
                        # agent runs from the repo checkout, not the home default;
                        # else derive a home-dir default.
                        session_cwd = (
                            _venue_workspace_cwd(target)
                            or target.cwd
                            or _default_cwd(target)
                        )
                        acp_sid = await asyncio.wait_for(
                            client.new_session(
                                cwd=session_cwd, mcp_servers=mcp_servers,
                            ),
                            timeout=self._timeouts.session_new,
                        )
                    except (TimeoutError, asyncio.TimeoutError) as exc:
                        # Leave a queryable marker so a stalled launch is not a
                        # silent [starting]->[stopped] (#1468). The process-owned
                        # client captured the child's startup stderr -- include
                        # the tail.
                        with contextlib.suppress(Exception):
                            on_acp_event("acp_launch_timeout", {
                                "stage": "LAUNCH_ACP",
                                "mode": "process",
                                "handshake_timeout_s": self._timeouts.session_start,
                                "session_new_timeout_s": self._timeouts.session_new,
                                "stderr_tail": client.stderr_tail(),
                            })
                        raise ConnectError(
                            ConnectStage.LAUNCH_ACP,
                            f"Copilot ACP launch timed out "
                            f"(handshake {self._timeouts.session_start}s / "
                            f"session/new {self._timeouts.session_new}s). A cold "
                            f"session/new on a large workspace may need a larger "
                            f"budget -- raise timeouts.session_new in "
                            f"~/.agent-bridge/config.yaml and restart the daemon.",
                            retryable=False,
                            cause=exc,
                        ) from exc

            session.client = client
            session.acp_session_id = acp_sid
            session.status = SessionStatus.IDLE
            if session.event_log:
                session.event_log.set_telemetry_identity(
                    acp_session_id=acp_sid,
                    worktree_id=target.worktree_id,
                )
            self._db.update_session_acp_id(session_id, acp_sid)
            # Persist target with resolved values (worktree_id, cwd from plan)
            self._db.update_session_target(
                session_id, target.to_json(), target.cwd
            )
            self._db.update_session_status(
                session_id, SessionStatus.IDLE.value, time.time(), pid=session.pid
            )
            session.event_log.append("session_state_changed", {
                "status": SessionStatus.IDLE.value,
                "acp_session_id": acp_sid,
            })
            log.info(
                "Session %s (%s) started, pid=%s, acp=%s",
                session_id, name, session.pid, acp_sid,
            )
            # Phase 4a (worktree-self-knowledge): register this ACP session into
            # the agent-worktrees ground layer so the derived head is correct and
            # the worktree is not left "looking unowned" (the vision's *explicit
            # session binding* names "a spawned successor, a headless launch").
            # LOCAL worktrees only -- a remote worktree's ground layer lives on
            # its own machine. Best-effort / fail-open: never breaks a launch.
            if target.type == "local" and target.worktree_id and acp_sid:
                from . import worktree_lineage
                with contextlib.suppress(Exception):
                    worktree_lineage.register_session(
                        target.worktree_id, acp_sid, pid=session.pid,
                        worktree_dir=target.cwd,
                    )
        except ConnectError as exc:
            # Structured failure: we know exactly which stage failed and
            # whether a retry could help -- never an opaque "agent died".
            await _cleanup_failed_process_launch()
            self._mark_session_failed(session, trigger="connect_failed")
            session.event_log.append("connect_failed", {
                "stage": int(exc.stage),
                "stage_name": exc.stage.name,
                "retryable": exc.retryable,
                "message": exc.detail,
            })
            session.event_log.append("error", {"message": str(exc)})
            log.error(
                "Session %s failed at stage %d/%s: %s",
                session_id, int(exc.stage), exc.stage.name, exc.detail,
                exc_info=True,
            )
        except CodespaceClaimConflictError as exc:
            # #897: the CodeSpace is exclusively held by another live worktree.
            # This is a deliberate BOUNCE, not a transport failure -- mark the
            # session FAILED with an actionable, distinguishable event so a
            # caller (or the Picker) can tell "someone else owns this box" apart
            # from an infra failure, and re-dispatch elsewhere or take over with
            # --force-claim. No claim was acquired, so there is nothing to
            # release here.
            await _cleanup_failed_process_launch()
            self._mark_session_failed(
                session, trigger="codespace_claim_conflict"
            )
            session.event_log.append("codespace_claim_conflict", {
                "codespace": exc.codespace,
                "owner": exc.owner,
                "message": exc.detail or str(exc),
            })
            session.event_log.append("error", {"message": str(exc)})
            log.warning(
                "Session %s bounced: CodeSpace '%s' is claimed by another "
                "worktree (dispatcher '%s')",
                session_id, exc.codespace, exc.owner,
            )
        except CodespaceCoordinationRejectedError as exc:
            await _cleanup_failed_process_launch()
            self._mark_session_failed(
                session, trigger="codespace_coordination_rejected"
            )
            session.event_log.append("codespace_coordination_rejected", {
                "codespace": exc.codespace,
                "owner": exc.owner,
                "message": exc.detail or str(exc),
            })
            session.event_log.append("error", {"message": str(exc)})
            log.warning(
                "Session %s blocked: CodeSpace '%s' lacks durable coordination "
                "for dispatcher '%s'",
                session_id, exc.codespace, exc.owner,
            )
        except Exception as exc:
            await _cleanup_failed_process_launch()
            self._mark_session_failed(session, trigger="start_exception")
            session.event_log.append("error", {"message": str(exc)})
            log.error("Failed to start session %s: %s", session_id, exc, exc_info=True)

        session.touch()
        return session

    async def finalize_parity_fault_start(
        self,
        session: Session,
        fault: str,
    ) -> dict[str, Any]:
        """Remove a confirmed failed-start transaction and report booleans."""
        result = dict(session.parity_fault_result or {})
        result["fault"] = fault
        if session.status != SessionStatus.FAILED:
            with contextlib.suppress(Exception):
                await self.end_session(session.session_id, force=True)
            raise RuntimeError(
                "failed ACP handshake parity injection unexpectedly started "
                "a usable session"
            )
        cleanup_confirmed = all(
            result.get(name) is True
            for name in (
                "host_process_removed",
                "child_process_removed",
                "remote_authority_removed",
                "forward_removed",
                "relay_removed",
                "provider_cleanup",
            )
        )
        if not cleanup_confirmed:
            claim_key = _codespace_claim_key(session.target)
            ownership_retained = (
                session.session_id in self._container_lock_sessions
                or claim_key is not None
            )
            result.update({
                "cleanup_confirmed": False,
                "ownership_retained": ownership_retained,
                "durable_session_retained": (
                    self._db.get_session(session.session_id) is not None
                ),
            })
            return result

        session_id = session.session_id
        claim_key = _codespace_claim_key(session.target)
        claim_removed = (
            True
            if claim_key is None
            else _release_codespace_claim(*claim_key)
        )
        result["codespace_claim_removed"] = claim_removed
        if not claim_removed:
            result.update({
                "cleanup_confirmed": False,
                "ownership_retained": True,
                "durable_session_retained": (
                    self._db.get_session(session_id) is not None
                ),
            })
            return result
        self._release_container_lock(session_id)
        await self.end_session(session_id, force=True)
        host_index_removed = (
            self._host_index is None
            or self._host_index.get(session_id) is None
        )
        target_lock_removed = session_id not in self._container_lock_sessions
        result.update({
            "session_row_removed": self._db.get_session(session_id) is None,
            "session_memory_removed": session_id not in self._sessions,
            "host_index_removed": host_index_removed,
            "forward_removed": session_id not in self._forwards,
            "relay_removed": session_id not in self._relays,
            "target_lock_removed": target_lock_removed,
            "ownership_retained": False,
        })
        result["cleanup_confirmed"] = all(
            result.get(name) is True
            for name in (
                "host_process_removed",
                "child_process_removed",
                "remote_authority_removed",
                "provider_cleanup",
                "session_row_removed",
                "session_memory_removed",
                "host_index_removed",
                "forward_removed",
                "relay_removed",
                "target_lock_removed",
                "codespace_claim_removed",
            )
        )
        return result

    async def resume_session(
        self,
        session_id: str,
        permission_callback: Any | None = None,
        *,
        drain: bool = True,
        allow_recreate: bool = False,
    ) -> Session:
        """Resume a stopped session by spawning a new process.

        Uses AcpClient.load_session() to reattach to the persisted ACP
        session. The session is ready to receive prompts when this returns.

        ``drain`` (default True): once the session lands IDLE, deliver any
        durable ``pending_prompts`` queued for it -- this is how a queue that
        outlived a bridge/host restart is delivered when the session comes back.
        ``submit_prompt``'s auto-resume path passes ``drain=False`` because it
        runs its own prompt next and that turn's settle drains the rest, so the
        resume never starts a second concurrent turn.

        ``allow_recreate`` (default False): the **end+create last resort** of the
        resume recovery ladder (#1468). When the ``_MAX_RESUME_ROUNDS`` stop->
        resume rounds all fail (they preserve context by re-``load_session``-ing
        the SAME ACP session), a truthy ``allow_recreate`` falls back to a FRESH
        ACP session (``new_session``) under the **same bridge session id** --
        recovering a working session at the cost of prior-turn context. The
        ``send``/auto-resume path opts in; an explicit ``resume`` leaves it False
        so a resume never silently drops context.
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        async with session._lifecycle_lock:
            if session.status != SessionStatus.STOPPED:
                raise ValueError(
                    f"Session {session_id} is {session.status.value}, not stopped"
                )
            if not session.acp_session_id and not allow_recreate:
                raise RuntimeError(
                    f"Session {session_id} has no ACP session ID -- cannot resume"
                )

            # Prefer reattaching to a surviving Session Host (adopt the running
            # child + its in-flight turn) over a fresh child + load_session, so a
            # resume after a transport drop (laptop sleep / tunnel flap / SSH
            # sever) recovers the SAME work instead of abandoning a mid-turn tool
            # call (#145). Falls through to the fresh-child path below when no
            # live host survives (a genuinely dead child).
            if await self._try_reattach_live_host(session):
                log.info(
                    "Session %s (%s) resumed by reattaching to its live "
                    "Session Host (no respawn)", session_id, session.name,
                )
                session.touch()
                return session

            await self._refresh_provider_target(session)

            session.status = SessionStatus.STARTING
            self._db.update_session_status(
                session_id, SessionStatus.STARTING.value, time.time()
            )

            def on_acp_event(event_type: str, data: dict[str, Any]) -> None:
                if session.event_log:
                    session.event_log.append(event_type, data)
                self._capture_progress(session, event_type, data)
                if event_type == "usage_update":
                    self._handle_usage_update(session, data)

            client: AcpClient | None = None
            from .session_host.spawner import RemoteSpawnCleanupPendingError

            for attempt in range(1, _MAX_RESUME_ROUNDS + 1):
                client = None
                try:
                    load_existing = bool(session.acp_session_id)
                    host_result = await self._resume_via_new_remote_host(
                        session,
                        on_acp_event=on_acp_event,
                        permission_callback=permission_callback,
                        load_existing=load_existing,
                    )
                    resumed_acp_id = (
                        host_result[1] if host_result is not None else None
                    )
                    client = host_result[0] if host_result is not None else None
                    if client is None:
                        agent_proc = await spawn(session.target)
                        client = AcpClient(
                            on_event=on_acp_event,
                            on_permission=permission_callback,
                            model_override=session.model_override,
                            effort_override=session.effort_override,
                        )
                        if permission_callback:
                            client.auto_approve = False
                        # Bound each round so a stalled Copilot ACP launch (the
                        # "Resuming…"/extension-reload race, #1468) fails fast and
                        # we re-roll, rather than hanging in STARTING.
                        await asyncio.wait_for(
                            client.start(agent_proc.proc),
                            timeout=self._timeouts.session_start,
                        )
                        if session.acp_session_id:
                            await asyncio.wait_for(
                                client.load_session(
                                    cwd=(
                                        session.target.cwd
                                        or _default_cwd(session.target)
                                    ),
                                    session_id=session.acp_session_id,
                                ),
                                timeout=self._timeouts.session_new,
                            )
                            resumed_acp_id = session.acp_session_id
                        else:
                            resumed_acp_id = await asyncio.wait_for(
                                client.new_session(
                                    cwd=(
                                        session.target.cwd
                                        or _default_cwd(session.target)
                                    ),
                                    mcp_servers=session.mcp_servers,
                                ),
                                timeout=self._timeouts.session_new,
                            )

                    if resumed_acp_id and (
                        resumed_acp_id != session.acp_session_id
                    ):
                        session.acp_session_id = resumed_acp_id
                        self._db.update_session_acp_id(
                            session_id,
                            resumed_acp_id,
                        )

                    session.client = client
                    session.status = SessionStatus.IDLE
                    self._db.update_session_status(
                        session_id, SessionStatus.IDLE.value, time.time(),
                        pid=session.pid,
                    )
                    if session.event_log:
                        session.event_log.append("session_state_changed", {
                            "status": SessionStatus.IDLE.value,
                            "resumed": True,
                            "acp_session_id": session.acp_session_id,
                            "resume_attempt": attempt,
                        })
                    log.info(
                        "Session %s (%s) resumed, pid=%s (attempt %d/%d)",
                        session_id, session.name, session.pid,
                        attempt, _MAX_RESUME_ROUNDS,
                    )
                    break  # success -- leave the ladder
                except Exception as exc:
                    if isinstance(exc, RemoteSpawnCleanupPendingError):
                        session.status = SessionStatus.STOPPED
                        self._db.update_session_status(
                            session_id,
                            SessionStatus.STOPPED.value,
                            time.time(),
                        )
                        if session.event_log:
                            session.event_log.append(
                                "remote_launch_cleanup_pending",
                                {"message": str(exc)},
                            )
                        raise
                    # Capture the child's startup stderr tail BEFORE tearing the
                    # client down, so the retry marker records why it stalled.
                    stderr_tail = client.stderr_tail() if client else ""
                    # Many stall exceptions (notably ``asyncio.TimeoutError()``)
                    # have an empty ``str()`` -- keep the type so markers/logs are
                    # interpretable.
                    exc_desc = f"{type(exc).__name__}: {exc}".rstrip(": ")
                    # Stop the wedged child before the next round (the "stop" in
                    # stop->resume); re-rolls the launch against the SAME ACP
                    # session, preserving prior-turn context.
                    if client:
                        with contextlib.suppress(Exception):
                            await client.shutdown()
                    session.client = None
                    if session.event_log:
                        session.event_log.append("acp_resume_retry", {
                            "attempt": attempt,
                            "of": _MAX_RESUME_ROUNDS,
                            "error": exc_desc,
                            "stderr_tail": stderr_tail,
                            "will_retry": attempt < _MAX_RESUME_ROUNDS,
                        })
                    if attempt < _MAX_RESUME_ROUNDS:
                        log.warning(
                            "Resume attempt %d/%d for session %s failed (%s); "
                            "stopping the wedged child and re-rolling",
                            attempt, _MAX_RESUME_ROUNDS, session_id, exc_desc,
                        )
                        continue
                    # Ladder exhausted.
                    if not allow_recreate:
                        # No fresh-session fallback (e.g. an explicit `resume`):
                        # surface the failure rather than silently drop context.
                        session.status = SessionStatus.STOPPED
                        self._db.update_session_status(
                            session_id, SessionStatus.STOPPED.value, time.time()
                        )
                        if session.event_log:
                            session.event_log.append("error", {
                                "message": f"Resume failed after "
                                           f"{_MAX_RESUME_ROUNDS} attempts: "
                                           f"{exc_desc}",
                            })
                        log.error(
                            "Failed to resume session %s after %d attempts: %s",
                            session_id, _MAX_RESUME_ROUNDS, exc_desc,
                        )
                        raise
                    # allow_recreate: fall through to the in-place end+create
                    # (fresh ACP session, prior-turn context dropped) below.
                    log.warning(
                        "Resume ladder exhausted for session %s after %d "
                        "attempts (%s); falling back to a fresh ACP session "
                        "(end+create, prior-turn context dropped)",
                        session_id, _MAX_RESUME_ROUNDS, exc_desc,
                    )
                    break

            # End+create last resort (opt-in, ladder exhausted). The stop->resume
            # rounds above all failed to reattach the persisted ACP session; as a
            # final recovery, end it and create a FRESH ACP session in place --
            # SAME bridge session id (delivery cursor / affinity intact), new
            # (empty) ACP session -- so a wedged CodeSpace resume still yields a
            # working session, trading prior-turn context for availability
            # (#1468). Only reached when allow_recreate and no round succeeded.
            if session.status != SessionStatus.IDLE:
                recreate_client: AcpClient | None = None
                try:
                    host_result = await self._resume_via_new_remote_host(
                        session,
                        on_acp_event=on_acp_event,
                        permission_callback=permission_callback,
                        load_existing=False,
                    )
                    if host_result is not None:
                        recreate_client, new_acp = host_result
                    else:
                        agent_proc = await spawn(session.target)
                        recreate_client = AcpClient(
                            on_event=on_acp_event,
                            on_permission=permission_callback,
                            model_override=session.model_override,
                            effort_override=session.effort_override,
                        )
                        if permission_callback:
                            recreate_client.auto_approve = False
                        await asyncio.wait_for(
                            recreate_client.start(agent_proc.proc),
                            timeout=self._timeouts.session_start,
                        )
                        new_acp = await asyncio.wait_for(
                            recreate_client.new_session(
                                cwd=(
                                    _venue_workspace_cwd(session.target)
                                    or session.target.cwd
                                    or _default_cwd(session.target)
                                ),
                                mcp_servers=session.mcp_servers,
                            ),
                            timeout=self._timeouts.session_new,
                        )
                    old_acp = session.acp_session_id
                    session.client = recreate_client
                    session.acp_session_id = new_acp
                    session.status = SessionStatus.IDLE
                    if session.event_log:
                        session.event_log.set_telemetry_identity(
                            acp_session_id=new_acp,
                            worktree_id=session.target.worktree_id,
                        )
                    # The fresh ACP session starts EMPTY -- reset context-usage /
                    # handoff state so stale "critical" usage or a pending
                    # context-pressure handoff from the dropped session can't
                    # misfire against the new (empty) session (review on #1468).
                    session.context_size = None
                    session.context_used = None
                    session._crossed_thresholds = set()
                    session._handoff_pending = False
                    self._db.update_session_acp_id(session_id, new_acp)
                    self._db.update_session_status(
                        session_id, SessionStatus.IDLE.value, time.time(),
                        pid=session.pid,
                    )
                    if session.event_log:
                        # Durable lifecycle transition for SSE/telemetry consumers
                        # (the recreate is still a resume-to-IDLE, just onto a
                        # fresh ACP session).
                        session.event_log.append("session_state_changed", {
                            "status": SessionStatus.IDLE.value,
                            "resumed": True,
                            "recreated": True,
                            "acp_session_id": new_acp,
                        })
                        session.event_log.append("acp_resume_recreated", {
                            "old_acp_session_id": old_acp,
                            "new_acp_session_id": new_acp,
                            "context_dropped": True,
                        })
                    log.warning(
                        "Session %s (%s) recreated with a fresh ACP session %s "
                        "(was %s) after the resume ladder was exhausted -- "
                        "prior-turn context dropped",
                        session_id, session.name, new_acp, old_acp,
                    )
                except Exception as exc:
                    if recreate_client:
                        with contextlib.suppress(Exception):
                            await recreate_client.shutdown()
                    session.client = None
                    session.status = SessionStatus.STOPPED
                    self._db.update_session_status(
                        session_id, SessionStatus.STOPPED.value, time.time()
                    )
                    exc_desc = f"{type(exc).__name__}: {exc}".rstrip(": ")
                    if session.event_log:
                        session.event_log.append("error", {
                            "message": f"Resume + recreate failed: {exc_desc}",
                        })
                    log.error(
                        "Resume + recreate failed for session %s: %s",
                        session_id, exc_desc,
                    )
                    raise

        session.touch()
        # Queue outlived the restart? Deliver it now that the session is IDLE.
        # Outside the lifecycle lock (drain submits a fresh turn). Skipped by the
        # auto-resume-from-submit path (drain=False) to avoid a double turn.
        if drain and session.status == SessionStatus.IDLE:
            await self._drain_pending_prompts(session)
        return session

    async def resync_session(self, session_id: str) -> int:
        """Rebuild a session's event log from the agent's authoritative replay.

        Reattaches to the persisted ACP session and captures the full
        conversation history the agent streams back during load (per the ACP
        spec), then replaces the event log with it. This heals logs that were
        truncated by a mid-session disconnect (e.g. an oversized ACP frame
        that crashed the read loop): the agent always holds the complete
        history, so its replay is the source of truth.

        Idempotent: resyncing an already-complete session rebuilds the same
        log. Leaves the session IDLE with a live client, ready for prompts.
        Returns the number of events in the rebuilt log.

        A ``RUNNING`` status only blocks resync while a turn is *actually* live
        in this daemon. A **wedged** session -- status left at ``RUNNING`` with
        no live prompt task (a turn whose runner already exited without a
        terminal event, or a session rehydrated after a daemon restart) -- is
        exactly what needs healing, so it is allowed through; only a genuinely
        live turn is refused (issue #22 / #2385).
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        if not session.acp_session_id:
            raise RuntimeError(
                f"Session {session_id} has no ACP session ID -- cannot resync"
            )

        async with session._lifecycle_lock:
            if session.status == SessionStatus.RUNNING:
                turn_live = (
                    session._prompt_task is not None
                    and not session._prompt_task.done()
                )
                if turn_live:
                    raise ValueError(
                        f"Session {session_id} is running a live turn "
                        "-- cannot resync"
                    )
                # Wedged RUNNING: no live turn to protect. Cancel any lingering
                # (already-finished) task handle and heal the stuck state.
                log.warning(
                    "Resyncing wedged RUNNING session %s (no live turn)",
                    session_id,
                )
                if session._prompt_task is not None:
                    with contextlib.suppress(Exception):
                        session._prompt_task.cancel()

            # Tear down any live client so we can reattach cleanly.
            if session.client:
                with contextlib.suppress(Exception):
                    await session.client.shutdown()
                session.client = None

            session.status = SessionStatus.STARTING
            self._db.update_session_status(
                session_id, SessionStatus.STARTING.value, time.time()
            )

            captured: list[tuple[str, dict[str, Any]]] = []

            def on_capture(event_type: str, data: dict[str, Any]) -> None:
                captured.append((event_type, data))
                if event_type == "usage_update":
                    self._handle_usage_update(session, data)

            client: AcpClient | None = None
            try:
                agent_proc = await spawn(session.target)
                client = AcpClient(
                    on_event=on_capture,
                    model_override=session.model_override,
                    effort_override=session.effort_override,
                )
                await client.start(agent_proc.proc)
                # suppress_replay=False -> the replayed history is captured.
                await client.load_session(
                    cwd=session.target.cwd or _default_cwd(session.target),
                    session_id=session.acp_session_id,
                    suppress_replay=False,
                )

                count = 0
                if session.event_log:
                    count = session.event_log.rebuild(captured)
                    session.event_log.append("session_state_changed", {
                        "status": SessionStatus.IDLE.value,
                        "resynced": True,
                        "acp_session_id": session.acp_session_id,
                    })

                session.client = client
                session.status = SessionStatus.IDLE
                self._db.update_session_status(
                    session_id, SessionStatus.IDLE.value, time.time(),
                    pid=session.pid,
                )
                log.info(
                    "Session %s (%s) resynced: rebuilt %d events",
                    session_id, session.name, count,
                )
            except Exception as exc:
                if client:
                    with contextlib.suppress(Exception):
                        await client.shutdown()
                session.client = None
                session.status = SessionStatus.STOPPED
                self._db.update_session_status(
                    session_id, SessionStatus.STOPPED.value, time.time()
                )
                log.error("Failed to resync session %s: %s", session_id, exc)
                raise

        session.touch()
        return count

    async def submit_prompt(self, session_id: str, prompt: str) -> int:
        """Atomically start a turn against conditional idle teardown."""
        if self._draining:
            raise DaemonDrainingError("turn")
        resolved = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(resolved)
        if not session:
            raise KeyError(f"Session {resolved} not found")
        async with session._turn_start_lock:
            return await self._submit_prompt_locked(resolved, prompt)

    async def _submit_prompt_locked(self, session_id: str, prompt: str) -> int:
        """Submit a prompt to a session, returning the turn index.

        The prompt is sent to the ACP subprocess. Streaming events
        (agent_message, tool_call_start, etc.) flow to the EventLog in
        real time. The prompt runs as a background task so the HTTP
        request can return immediately -- callers consume output via SSE.

        If the session process has died (e.g. after a server restart)
        but the ACP session ID is available, the process is
        automatically re-spawned and the session resumed before
        delivering the prompt.
        """
        if self._draining:
            raise DaemonDrainingError("turn")
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        if session.status not in (SessionStatus.IDLE, SessionStatus.STOPPED):
            raise ValueError(
                f"Session {session_id} is {session.status.value}, not idle"
            )

        # Auto-resume if the process is dead but session is recoverable
        if not session.client or not session.client.is_running:
            log.info(
                "Session %s (%s) process is dead -- auto-%s",
                session_id,
                session.name,
                (
                    "resuming"
                    if session.acp_session_id
                    else "creating a fresh ACP session"
                ),
            )
            # Mark as STOPPED so resume_session accepts it
            session.status = SessionStatus.STOPPED
            # A `send`/prompt must land a working session, so opt into the
            # end+create last resort: if the stop->resume ladder is exhausted,
            # recreate a fresh ACP session in place rather than failing the send
            # (#1468).
            await self.resume_session(
                session_id, drain=False, allow_recreate=True
            )
            # resume_session sets status to IDLE and attaches a new client

        turn_index = session.turn_count
        session.turn_count += 1
        now = time.time()

        # Persist turn skeleton
        self._db.create_turn(session_id, turn_index, prompt, now)

        # Cancel any pending out-of-turn content bracket (#2835) synchronously,
        # BEFORE this new turn's `running` is written and the prompt task is
        # created. A due settle timer would otherwise fire between here and the
        # background task starting, injecting a spurious `idle` into the new
        # turn. Doing it here (on the loop, with no await before the task is
        # scheduled) guarantees the cancel wins that race.
        cancel_ooo = getattr(session.client, "_cancel_out_of_turn", None)
        if callable(cancel_ooo):
            # Real clients expose a sync canceller; tolerate a test double that
            # returns a coroutine by closing it (never a coroutine in prod).
            _maybe = cancel_ooo()
            if inspect.iscoroutine(_maybe):
                _maybe.close()

        # Update status
        session.status = SessionStatus.RUNNING
        # Reset the live-stall clock at turn start. `last_output_at` only
        # advances on an ACP frame, so after a long idle gap it still points at
        # the *previous* turn's last frame. Without this reset, the live-stall
        # watchdog (reconcile_wedged_running) sees the brand-new turn as
        # "silent for <entire idle gap>", judges it stalled, and interrupts it
        # (ACP session/cancel) before it can emit its first frame -- surfacing
        # as a phantom "Operation cancelled by user" on the first resend after
        # an idle gap > live_stall_interrupt_after_s (#4122). Silence must be
        # measured within the current turn; a genuine stall is still caught,
        # measured from turn-start. Synchronous (no await before the prompt task
        # is scheduled), so it wins the race against the watchdog.
        session.last_output_at = now
        self._db.update_session_status(session_id, SessionStatus.RUNNING.value, now)

        if session.event_log:
            # Persist the user's prompt as a durable, replayable event -- not
            # just a row in the turns table -- so every consumer (other tabs,
            # other relay instances, and history replayed on resume/open) sees
            # the prompt bubble, not only the agent's response. This mirrors
            # what the agent's load-time replay emits during a resync, keeping
            # live and replayed histories consistent.
            session.event_log.append("user_message", {"content": prompt})
            session.event_log.append("session_state_changed", {
                "status": SessionStatus.RUNNING.value,
                "turn_index": turn_index,
            })

        # Run the prompt as a background task
        session._prompt_task = asyncio.create_task(
            self._run_prompt(session, turn_index, prompt)
        )

        # The child is now busy: tell its session host it is NOT reapable so a
        # front lost mid-turn never self-reaps a running turn (#51).
        await self._notify_host_reapable(session)

        session.touch()
        return turn_index

    async def submit_or_queue_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        caller_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a prompt now, or durably queue it if the session is busy.

        The durable counterpart to :meth:`submit_prompt`. Where ``submit_prompt``
        rejects a busy session (409), this persists the follow-up to the
        ``pending_prompts`` table so it survives a caller remount, an NF crash,
        and a bridge/host restart, then drains it -- FIFO, exactly once -- on the
        next turn-settle (or on resume). This is the send-or-queue seam host CLI
        agents and Neuron Forge both submit through.

        Returns a dict describing the outcome:
          - ran now:   ``{"queued": False, "turn_index": int, "status": str}``
          - enqueued:  ``{"queued": True, "queue_id": int, "position": int,
                          "status": str}``

        A prompt is enqueued (rather than run) when the session is actively
        running a turn, when the daemon is draining for redeploy, or when a
        queue already exists (a new submit joins the back of the line -- never
        jumps ahead of already-queued follow-ups). Otherwise it runs immediately
        via ``submit_prompt`` (which auto-resumes a recoverable STOPPED session).
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        # Context-pressure handoff (opt-in, prompt-triggered): a prompt into an
        # already-saturated session that is idle rolls the worktree to a fresh
        # successor FIRST, then delivers this prompt to it. This is the only way
        # forward for a minimal consumer -- a phone -- that can *only* send the
        # next message and has no manual session-creation affordance. Unlike the
        # proactive path this ignores `unwatched_only`: the sender is explicitly
        # asking for the next turn. A live turn is left to the proactive
        # turn-settle path (it would be handed off mid-turn otherwise); the
        # successor is fresh, so this never recurses.
        if (session.status in (SessionStatus.IDLE, SessionStatus.STOPPED)
                and self._is_over_critical(session)
                and self._auto_handoff_eligible(session)):
            session._handoff_pending = False
            successor = await self.handoff_session(
                session_id, reason="context-pressure-prompt"
            )
            return await self.submit_or_queue_prompt(
                successor.session_id, prompt, caller_id=caller_id
            )

        turn_live = (
            session.status == SessionStatus.RUNNING
            or (session._prompt_task is not None
                and not session._prompt_task.done())
        )
        queue_nonempty = self._db.count_pending_prompts(session_id) > 0
        # During a redeploy drain we must not start a new turn, but we can still
        # persist the follow-up -- it delivers after the restart resumes.
        must_queue = turn_live or queue_nonempty or self._draining

        if not must_queue:
            turn_index = await self.submit_prompt(session_id, prompt)
            return {
                "queued": False,
                "turn_index": turn_index,
                "status": session.status.value,
            }

        now = time.time()
        queue_id = self._db.enqueue_prompt(
            session_id, prompt, now, caller_id=caller_id
        )
        position = self._db.count_pending_prompts(session_id)
        if session.event_log:
            session.event_log.append("prompt_enqueued", {
                "queue_id": queue_id,
                "caller_id": caller_id,
                "position": position,
            })
        log.info(
            "Queued prompt %d for session %s (position %d, status=%s)",
            queue_id, session_id, position, session.status.value,
        )
        # If no turn is live to drain the queue on settle -- and we are not
        # mid-drain -- kick delivery now, resuming a recoverable STOPPED session
        # if needed. A live turn's settle tail (or the post-restart resume) will
        # drain otherwise.
        if not turn_live and not self._draining:
            await self._kick_pending_drain(session)
        return {
            "queued": True,
            "queue_id": queue_id,
            "position": position,
            "status": session.status.value,
        }

    async def _kick_pending_drain(self, session: Session) -> None:
        """Start draining a queue when no turn-settle will do it for us.

        For an IDLE session with a live client, drain directly. For a
        recoverable STOPPED session (queue outlived a restart, or a fresh submit
        landed on a dormant session), resume it -- ``resume_session`` drains as
        it lands IDLE. Best-effort: a failure here leaves the rows durably
        queued for the next resume/settle, never lost.
        """
        try:
            if (session.status == SessionStatus.IDLE
                    and session.client and session.client.is_running):
                await self._drain_pending_prompts(session)
            elif (session.status == SessionStatus.STOPPED
                    and session.acp_session_id):
                await self.resume_session(session.session_id)
        except Exception as exc:
            log.warning(
                "Kick-drain for session %s failed (queue preserved): %s",
                session.session_id, exc,
            )

    async def _drain_pending_prompts(self, session: Session) -> None:
        """Deliver the next durable queued prompt, if the session can run it.

        Called from the turn-settle tail and the resume idle-tail. Atomically
        pops one row and submits it; the follow-up's own settle re-invokes this,
        so the queue drains one-per-turn in FIFO order. Only drains an IDLE
        session with a live client -- if the process is dead, a later resume
        drains instead, so a queued message is never lost to a dead process, and
        pop-then-submit stays exactly-once (no loss, no dup).
        """
        if session.status != SessionStatus.IDLE:
            return
        if not (session.client and session.client.is_running):
            return
        row = self._db.pop_pending_prompt(session.session_id)
        if row is None:
            return
        prompt = row["prompt"]
        if session.event_log:
            session.event_log.append("prompt_dequeued", {
                "queue_id": row["id"],
                "caller_id": row.get("caller_id"),
            })
        try:
            await self.submit_prompt(session.session_id, prompt)
        except Exception as exc:
            # Defensive: an unexpected submit failure must not drop the message.
            # Re-enqueue (at the tail -- a rare reorder is better than a loss);
            # a later settle/resume retries delivery.
            log.error(
                "Dequeued prompt for session %s failed to submit: %s "
                "-- re-enqueuing",
                session.session_id, exc,
            )
            self._db.enqueue_prompt(
                session.session_id, prompt, time.time(),
                caller_id=row.get("caller_id"),
            )

    def _clear_pending_queue(self, session: Session, *, reason: str) -> int:
        """Drop a session's whole durable queue; emit ``queue_cleared`` if any.

        The shared teardown path for interrupt/end (and any future rollback):
        mirrors NF's "queue cleared if cancelled, ended, or rolled" so queued
        follow-ups never resurface against a session the operator tore down.
        Returns how many rows were removed.
        """
        removed = self._db.clear_pending_prompts(session.session_id)
        if removed and session.event_log:
            session.event_log.append("queue_cleared", {
                "removed": removed,
                "reason": reason,
            })
        return removed

    def list_pending_queue(self, session_id: str) -> list[dict[str, Any]]:
        """Snapshot a session's durable queue in FIFO order (route/CLI read)."""
        session_id = self._resolve_ref(session_id) or session_id
        if session_id not in self._sessions:
            raise KeyError(f"Session {session_id} not found")
        return self._db.list_pending_prompts(session_id)

    def remove_pending_prompt(self, session_id: str, queue_id: int) -> bool:
        """Drop one queued prompt by id (operator drops a chip). True if hit."""
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        removed = self._db.remove_pending_prompt(session_id, queue_id)
        if removed and session.event_log:
            session.event_log.append("prompt_removed", {"queue_id": queue_id})
        return removed

    def clear_pending_queue(self, session_id: str) -> int:
        """Clear a session's whole durable queue on operator request."""
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        return self._clear_pending_queue(session, reason="cleared")

    async def _run_prompt(
        self, session: Session, turn_index: int, prompt: str
    ) -> None:
        """Background task: send prompt via ACP and persist the result."""
        try:
            result = await session.client.send_prompt(prompt)

            # Persist completed turn
            self._db.update_turn(
                session.session_id,
                turn_index,
                response_text=result.get("response_text", ""),
                thought_text=result.get("thought_text", ""),
                stop_reason=result.get("stop_reason"),
                tool_calls_json=json.dumps(result.get("tool_calls", [])),
                completed_at=time.time(),
            )

            session.status = SessionStatus.IDLE
            self._db.update_session_status(
                session.session_id, SessionStatus.IDLE.value, time.time()
            )

        except Exception as exc:
            log.error(
                "Prompt failed for session %s turn %d: %s",
                session.session_id, turn_index, exc,
            )
            self._db.update_turn(
                session.session_id,
                turn_index,
                stop_reason=f"error: {exc}",
                completed_at=time.time(),
            )
            session.status = SessionStatus.IDLE
            self._db.update_session_status(
                session.session_id, SessionStatus.IDLE.value, time.time()
            )

        # Always drive the event log to a terminal state so no consumer is left
        # mirroring a turn that never ends. On the happy path this trails the
        # client's turn_complete; on failure it is paired with the client's
        # (now non-hanging) error -- either way the stream reaches idle, matching
        # the synthetic idle a resync would emit (issue #22).
        if session.event_log:
            session.event_log.append("session_state_changed", {
                "status": SessionStatus.IDLE.value,
            })

        # Turn done: refresh the session host's reapable state (idle + no active
        # background tasks), so a subsequently-lost front can self-reap the idle
        # child (#51).
        await self._notify_host_reapable(session)

        session.touch()

        # Deliver the next durable queued follow-up now that the turn settled.
        # One-per-turn FIFO: this drains a single row and submits it; that turn's
        # own settle re-enters here for the next. Exactly-once (atomic pop), and
        # a no-op when the queue is empty or the process has since died.
        await self._drain_pending_prompts(session)

        # If a context-pressure handoff came due mid-turn and the queue is now
        # drained (session still idle), roll the worktree to a fresh successor.
        # A pending drained-prompt above leaves the session RUNNING, so this
        # no-ops until the queue empties -- the handoff then carries no work.
        self._schedule_auto_handoff_if_pending(session)

    def _handle_usage_update(
        self, session: Session, data: dict[str, Any]
    ) -> None:
        """Persist context usage and emit threshold warnings.

        Merge semantics: only the fields actually present in ``data`` are
        advanced. In particular ``usage_model`` is preserved unless a *real*
        model is reported -- copilot's ACP ``UsageUpdate`` carries ``model=None``
        every turn, so a naive overwrite kept wiping the model the client applied
        via ``session/set_config_option`` (dotfiles#790), leaving ``usage_model``
        perpetually NULL so ``status`` could never show the dispatched agent's
        model. The client re-emits the *applied* model through this same path
        (a model-only ``usage_update``), so the last-known model sticks.
        """
        now = time.time()
        if "context_size" in data:
            session.context_size = data.get("context_size")
        if "context_used" in data:
            session.context_used = data.get("context_used")
        model = data.get("model")
        if model:
            session.usage_model = model
        session.last_usage_at = now

        ctx_size = session.context_size
        ctx_used = session.context_used
        self._db.update_session_usage(
            session.session_id,
            context_size=ctx_size,
            context_used=ctx_used,
            usage_model=session.usage_model,
            now=now,
        )

        # Check thresholds and emit warnings
        if ctx_size and ctx_used is not None and ctx_size > 0:
            pct = ctx_used / ctx_size * 100
            thresholds = self._thresholds

            if pct >= thresholds.critical and "critical" not in session._crossed_thresholds:
                session._crossed_thresholds.add("critical")
                if session.event_log:
                    session.event_log.append("context_critical", {
                        "context_size": ctx_size,
                        "context_used": ctx_used,
                        "context_pct": round(pct, 1),
                        "threshold": thresholds.critical,
                        "message": "Context window usage critical -- consider handoff",
                    })
                # Context-pressure handoff (opt-in): mark a handoff owed, then
                # fire it once the session is idle. Usage typically crosses
                # critical mid-turn, so the turn-settle path (_run_prompt) drives
                # the deferred cutover; if we are already idle (usage arrived out
                # of turn), _schedule_auto_handoff_if_pending fires it now.
                if self._auto_handoff_eligible(session):
                    session._handoff_pending = True
                    self._schedule_auto_handoff_if_pending(session)

            elif pct >= thresholds.warning and "warning" not in session._crossed_thresholds:
                session._crossed_thresholds.add("warning")
                if session.event_log:
                    session.event_log.append("context_warning", {
                        "context_size": ctx_size,
                        "context_used": ctx_used,
                        "context_pct": round(pct, 1),
                        "threshold": thresholds.warning,
                        "message": "Context window usage elevated -- prepare for handoff",
                    })

    def _host_reapable(self, session: Session) -> bool:
        """Is the child safe to free? True only when its turn has completed and
        no background sub-agents are still running (#51)."""
        return (session.status == SessionStatus.IDLE
                and not session.has_active_background_tasks)

    def _is_codespace_target(self, target: "SpawnTarget") -> bool:
        """True if this target is a CodeSpace boundary agent -- structured
        ``codespace`` metadata, or a codespace-shaped ``spawn_command``. Such a
        target must run under a Session Host (never the process-owned path), so
        ``connect`` refuses to fall through to that path for one. A CodeSpace
        target that cannot be resolved to a spawner is a misconfiguration to
        surface, not to silently honor."""
        cs = getattr(target, "codespace", None)
        if isinstance(cs, dict) and cs.get("name"):
            return True
        sc = getattr(target, "spawn_command", None)
        if sc:
            try:
                from .session_host.codespace_transport import parse_codespace_target
                if parse_codespace_target(sc) is not None:
                    return True
            except Exception:
                pass
        return False

    def _session_host_client(self, session: Session) -> Any:
        """The session's host control channel, or None if not host-backed."""
        if session.client is None:
            return None
        return getattr(session.client, "session_host_client", None)

    async def _notify_host_reapable(self, session: Session) -> None:
        """Push the child's current reapable state to its session host, so the
        host can self-reap it if the front is later lost (#51). No-op for a
        non-host-backed session; best-effort so it never disturbs a turn."""
        hc = self._session_host_client(session)
        if hc is None:
            return
        with contextlib.suppress(Exception):
            await hc.send_status(self._host_reapable(session))

    async def _detach_host(self, session: Session) -> None:
        """Signal a GRACEFUL detach (+ current reapable state) to the session
        host before teardown, so an idle host reaps promptly instead of after
        the unexpected-grace window (#51). No-op / best-effort."""
        hc = self._session_host_client(session)
        if hc is None:
            return
        with contextlib.suppress(Exception):
            await hc.detach(self._host_reapable(session))

    async def refresh_host_reapable(self) -> None:
        """Reconcile every host-backed session's reapable state to its host.

        A periodic backstop (called from the heartbeat) beneath the precise
        turn-boundary STATUS pushes: it catches an initial idle session that has
        run no turn yet, and background-sub-agent transitions that occur outside
        a turn boundary -- so the host's ``_last_reapable`` never drifts stale
        enough to reap a session that is actually busy, or to miss reaping a
        genuinely idle one (#51)."""
        for session in list(self._sessions.values()):
            await self._notify_host_reapable(session)

    async def _quiesce_session(
        self, session: Session, *, cancel_turn: bool = True
    ) -> None:
        """Best-effort teardown of a session's in-flight prompt + ACP client.

        Must be resilient to a *mid-turn* session: cancelling an in-flight
        prompt or shutting down a busy ACP client must never raise out of
        stop/end. (A raising shutdown here surfaced as HTTP 500 when ending a
        mid-turn session -- see the credential-hang showcase report.) Errors
        are logged and swallowed so teardown always completes.

        ``cancel_turn`` (default True): send an ACP ``session/cancel`` to the
        remote agent's in-flight turn. A redeploy/shutdown passes ``False``
        (dotfiles#1661): the frontend detaches (host + child + turn survive for
        reattach) WITHOUT telling the remote agent to cancel -- only the local
        prompt task + ACP client are torn down. Explicit stop/end keep the
        default so an operator stop still cancels.
        """
        # Signal a GRACEFUL detach to the session host BEFORE tearing down the
        # transport, carrying the child's current reapable state, so an idle
        # host self-reaps promptly instead of waiting out the unexpected-grace
        # window (#51). Best-effort and pre-cancel: computed from the *current*
        # status before the in-flight prompt below is cancelled.
        await self._detach_host(session)
        task = session._prompt_task
        if task and not task.done():
            if session.client and cancel_turn:
                with contextlib.suppress(Exception):
                    await session.client.cancel_prompt()
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        if session.client:
            try:
                await session.client.shutdown()
            except Exception:
                log.warning(
                    "ACP client shutdown failed while tearing down session %s",
                    session.session_id, exc_info=True,
                )
            session.client = None
        # Clean up unused worktrees (0-turn sessions from crash-loops)
        try:
            await _cleanup_worktree(session.target, session.turn_count)
        except Exception:
            log.warning(
                "worktree cleanup failed while tearing down session %s",
                session.session_id, exc_info=True,
            )

    async def interrupt_turn(self, session_id: str) -> "Session":
        """Interrupt the in-flight turn, leaving the session alive and idle.

        Sends an ACP cancel to the active prompt so the current turn stops and
        the session returns to IDLE, ready for the next turn. Unlike
        ``stop_session``/``end_session`` this preserves the ACP client and the
        session itself -- it cancels the *turn*, not the session. A no-op that
        returns the session unchanged if nothing is in flight.

        The in-flight ``_run_prompt`` observes the cancel (``send_prompt``
        returns with a ``cancelled`` stop reason, or raises) and lands the
        session IDLE with a terminal ``session_state_changed`` (the Phase-1
        guarantee), which flows to every consumer over the event stream.
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        task = session._prompt_task
        if (session.status != SessionStatus.RUNNING
                or task is None or task.done()):
            # Nothing live to interrupt -- return the session as-is.
            return session

        # Operator explicitly cancelled this turn: retire its durable queue too,
        # BEFORE the runner settles IDLE (whose tail would otherwise drain it).
        # Auto-firing a batch of queued follow-ups right after a manual
        # interrupt would surprise the operator -- mirror NF's "queue cleared if
        # cancelled" (#4114). Cleared before the ACP cancel so the drain that
        # runs on settle finds nothing.
        self._clear_pending_queue(session, reason="interrupted")

        # Ask the agent to cancel the active turn (ACP session/cancel).
        if session.client is not None:
            with contextlib.suppress(Exception):
                await session.client.cancel_prompt()

        # Give the runner a bounded moment to settle to a terminal state so the
        # caller sees idle promptly. `shield` so this wait never cancels the
        # runner itself; if it does not settle in time the terminal still flows
        # over the event stream (and the wedged-session watchdog is the backstop).
        # Never force-kill the task here -- that would end the session.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(task), timeout=10.0)

        log.info("Interrupted in-flight turn for session %s", session_id)
        return session

    async def answer_ask_user(
        self,
        session_id: str,
        tool_call_id: str,
        content: dict[str, Any] | None,
        *,
        action: str = "accept",
    ) -> bool:
        """Answer a parked ``ask_user`` elicitation on a live session.

        Resolves the ACP client's pending ``elicitation/create`` for the given
        tool call so the agent's ``ask_user`` completes and the turn continues.
        ``action`` is ``accept`` (with ``content``), ``decline``, or ``cancel``.
        Returns ``True`` when a matching request was outstanding, ``False`` when
        none was (already answered/withdrawn). Raises ``KeyError`` if the
        session is unknown and ``ValueError`` if it has no live ACP client.
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        if session.client is None:
            raise ValueError(f"Session {session_id} has no live ACP client")
        return session.client.resolve_elicitation(
            tool_call_id, content, action=action,
        )

    async def answer_permission(
        self, session_id: str, request_id: str, option_id: str
    ) -> bool:
        """Resolve a correlated permission request on a live session."""
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        if session.client is None:
            raise ValueError(f"Session {session_id} has no live ACP client")
        return session.client.resolve_permission(request_id, option_id)

    async def stop_session(
        self, session_id: str, *, force: bool = False, reap_host: bool = False,
        cancel_turn: bool = True,
    ) -> None:
        """Stop a session -- shut down ACP client, preserve state for resume.

        Refuses with SessionBusyError when the session is hosting active
        background sub-agents unless ``force`` is set, so a routine stop does
        not kill in-flight background work (e.g. the PR daemon).

        Teardown is **never gated by the drain flag** (#1755): stopping a
        session is exactly what lets the busy sessions ``drain()`` waits on
        settle, so gating it here would self-deadlock a redeploy.

        ``reap_host`` (idle-reaper path, #1826): a plain stop in Session-Host
        mode only *detaches* the client, leaving the child **reattachable**; the
        idle reaper instead wants the child **freed** for resource reclamation,
        so it reaps the host record too. The session still ends STOPPED and is
        resumable via ``load_session`` replay (a *fresh* child) -- allowed
        because the reaper only ever stops an IDLE session, never mid-turn
        (goal 1).

        ``cancel_turn`` (default True): tell the remote agent to cancel its
        in-flight turn (ACP ``session/cancel``). A redeploy/shutdown passes
        ``False`` (dotfiles#1661) so the frontend detaches without cancelling --
        the host + child + turn survive for reattach. An explicit operator stop
        keeps the default (a stop IS an explicit host cancel).
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        if not force and session.has_active_background_tasks:
            raise SessionBusyError(session_id, session.active_background_tasks)

        await self._quiesce_session(session, cancel_turn=cancel_turn)

        # Idle-reaper only: free the Session Host child (a plain stop detaches
        # to keep it reattachable). Safe here because the session is idle.
        if reap_host and self._host_index is not None:
            rec = self._host_index.get(session_id)
            if rec is not None:
                self._reap_host_record(rec, "idle reap (#1826)")

        session.status = SessionStatus.STOPPED
        now = time.time()
        self._db.update_session_status(session_id, SessionStatus.STOPPED.value, now)
        # Release the per-worktree ownership reservation (#2912): a stopped
        # owned session is no longer actively controlling the worktree, so free
        # it for a live CLI (or a later fresh owner) to claim.
        self._db.release_worktree_ownership(session_id=session_id)
        if session.event_log:
            session.event_log.append("session_state_changed", {
                "status": SessionStatus.STOPPED.value,
            })
        session.touch()
        log.info("Session %s (%s) stopped", session_id, session.name)

    async def end_session_if_idle(
        self,
        session_id: str,
        *,
        force: bool = False,
    ) -> None:
        """End only if no turn can start between the idle check and teardown."""
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        async with session._turn_start_lock:
            if not session.is_at_rest():
                raise ValueError(f"Session {session_id} is not idle")
            pending = self._db.list_pending_prompts(session_id)
            if pending:
                raise ValueError(
                    f"Session {session_id} has queued prompts and is not idle"
                )
            await self.end_session(session_id, force=force)

    async def end_session(self, session_id: str, *, force: bool = False) -> None:
        """End a session -- shut down client and clean up all state.

        Always removes the session (even mid-turn): teardown is best-effort so
        ending never fails with a server error on a busy/hung session (#48).
        Both the persisted-status update and the row delete are suppressed so a
        transient DB error (e.g. a locked SQLite file) can't surface as HTTP
        500. The ENDED status is written *before* the delete so that even if the
        row is not removed, a later restart rehydrate cleans it up rather than
        resurrecting the session as STOPPED/active.

        Refuses with SessionBusyError when the session is hosting active
        background sub-agents unless ``force`` is set -- ending kills the
        process and every in-process sub-agent with it.

        Teardown is **never gated by the drain flag** (#1755): ending a session
        is exactly what lets the busy sessions ``drain()`` waits on settle, so
        gating it would self-deadlock a redeploy (the operator could not clear
        the very sessions blocking the drain).
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        if not force and session.has_active_background_tasks:
            raise SessionBusyError(session_id, session.active_background_tasks)

        container = (
            session.target.container
            if isinstance(session.target.container, dict)
            else {}
        )
        explicit_absence = (
            container.get("authoritative_identity_removed") is True
            or container.get("recreate_failed_without_host") is True
        )
        cleanup_pending = (
            session_id in self._container_lock_sessions
            or container.get("launch_pending_session_id") == session_id
        )
        recovery_inconclusive = session_id in self._remote_recovery_inconclusive
        if (
            (cleanup_pending or recovery_inconclusive)
            and self._host_index is not None
            and self._host_index.get(session_id) is None
            and not explicit_absence
        ):
            self._remote_recovery_inconclusive.discard(session_id)
            try:
                await self._recover_remote_host_records(
                    allow_wake=True,
                    session_ids={session_id},
                )
            except Exception as exc:
                self._remote_recovery_inconclusive.add(session_id)
                raise RemoteHostRecoveryPendingError(
                    "Remote Session Host cleanup could not inspect authority "
                    f"for {session_id}; retained session and remote ownership"
                ) from exc
            if (
                self._host_index.get(session_id) is None
                and session_id in self._remote_recovery_inconclusive
            ):
                raise RemoteHostRecoveryPendingError(
                    "Remote Session Host cleanup is inconclusive; retained "
                    f"session {session_id} and remote ownership"
                )
            if (
                self._host_index.get(session_id) is None
                and session_id not in self._remote_recovery_inconclusive
            ):
                self._set_container_launch_pending(session_id, False)
                container = (
                    session.target.container
                    if isinstance(session.target.container, dict)
                    else {}
                )

        await self._quiesce_session(session)

        # Session-Host mode: an explicit end is a *sanctioned terminate*, so it
        # must REAP the child -- unlike stop, whose host-mode shutdown only
        # detaches to keep the child reattachable. Without this the host + child
        # survive with a dangling index record and are never collected (#1786;
        # goal 1: termination is intentional, not inadvertent).
        rec = None
        if self._host_index is not None:
            rec = self._host_index.get(session_id)
            if rec is not None:
                container = (
                    session.target.container
                    if isinstance(session.target.container, dict)
                    else {}
                )
                if (
                    rec.boundary == "container"
                    and container.get("authoritative_identity_removed") is True
                ):
                    self._kill_forward_sync(session_id)
                    with contextlib.suppress(Exception):
                        self._host_index.remove(session_id)
                elif rec.boundary == "container":
                    self._kill_forward_sync(
                        session_id,
                        release_container_lock=False,
                    )
                    confirmed_dead = await self._remote_reap(
                        rec,
                        getattr(rec, "endpoint", None) or {},
                    )
                    if not confirmed_dead:
                        self._mark_session_failed(
                            session, trigger="remote_reap_inconclusive"
                        )
                        raise RemoteHostRecoveryPendingError(
                            "Container Session Host reap is inconclusive; "
                            f"retained session {session_id} and target ownership"
                        )
                    with contextlib.suppress(Exception):
                        self._host_index.remove(session_id)
                    with contextlib.suppress(Exception):
                        from . import bridge_lock
                        bridge_lock.remove_sync(session_id)
                    self._set_container_launch_pending(session_id, False)
                else:
                    self._reap_host_record(rec, "session ended")

        if (
            container
            and container.get("launch_pending_session_id") != session_id
            and (
                (self._host_index is not None and rec is None)
                or
                container.get("authoritative_identity_removed") is True
                or container.get("recreate_failed_without_host") is True
            )
        ):
            self._release_container_lock(session_id)

        session.status = SessionStatus.ENDED
        with contextlib.suppress(Exception):
            self._clear_pending_queue(session, reason="ended")
        # #897: release the exclusive CodeSpace claim this session held, so the
        # box is immediately re-dispatchable by another worktree instead of
        # waiting for the liveness/TTL sweep. Best-effort and idempotent (the
        # CLI only releases a claim this owner actually holds). Keyed off the
        # persisted target, so it is correct even after a daemon restart.
        with contextlib.suppress(Exception):
            claim_key = _codespace_claim_key(session.target)
            if claim_key is not None:
                _release_codespace_claim(*claim_key)
        with contextlib.suppress(Exception):
            self._db.update_session_status(
                session_id, SessionStatus.ENDED.value, time.time()
            )
        with contextlib.suppress(Exception):
            self._db.release_worktree_ownership(session_id=session_id)
        with contextlib.suppress(Exception):
            self._db.delete_session(session_id)
        self._sessions.pop(session_id, None)
        log.info("Session %s (%s) ended and cleaned up", session_id, session.name)

    # -- Context-aware in-place handoff --------------------------------------

    def _auto_handoff_eligible(self, session: Session) -> bool:
        """Is this session a candidate for policy-driven in-place handoff?

        False unless the operator opted in (`auto_handoff.enabled`), and never
        for a single-checkout (command/CodeSpace) agent -- those cannot host
        predecessor and successor at once, so the spawn-then-retire cutover does
        not apply (their retire-before-spawn variant is deferred, mirroring the
        guard in ``handoff_session``)."""
        if not self._auto_handoff.enabled:
            return False
        if _workspace_key(session.agent_name, session.target, session.caller_id):
            return False
        return True

    @staticmethod
    def _is_over_critical(session: Session) -> bool:
        """True once the session has crossed the critical context threshold."""
        return "critical" in session._crossed_thresholds

    def _schedule_auto_handoff_if_pending(self, session: Session) -> None:
        """Fire a deferred context-pressure handoff if one is owed and the
        session has settled to idle. Called at turn-settle (and after an
        out-of-turn usage update). No-op unless a handoff is pending, the
        session is idle, and the policy still permits it.

        The ``unwatched_only`` preference is honored here (proactive path): a
        session a human is streaming is left pending -- not rolled out from
        under -- and re-evaluated on its next settle. The prompt-triggered path
        (`submit_or_queue_prompt`) bypasses this and hands off directly, because
        the sender is explicitly asking for the next turn."""
        if not session._handoff_pending:
            return
        if session.status != SessionStatus.IDLE:
            return
        if not self._auto_handoff_eligible(session):
            session._handoff_pending = False
            return
        if self._auto_handoff.unwatched_only and session.subscriber_count > 0:
            return  # a human is attached; defer until unwatched
        session._handoff_pending = False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._run_auto_handoff(session.session_id))
        self._auto_handoff_tasks.add(task)
        task.add_done_callback(self._auto_handoff_tasks.discard)

    async def _run_auto_handoff(self, session_id: str) -> None:
        """Perform a policy-driven handoff and migrate any durably-queued
        follow-ups onto the successor, so no prompt is stranded on the retired
        predecessor. Best-effort: a failed handoff logs and leaves the
        predecessor current (``handoff_session`` never orphans the worktree)."""
        try:
            pending = self._db.list_pending_prompts(session_id)
        except Exception:
            pending = []
        try:
            successor = await self.handoff_session(
                session_id, reason="context-pressure"
            )
        except Exception as exc:
            log.warning("Auto-handoff of %s failed: %s", session_id, exc)
            return
        for row in pending:
            with contextlib.suppress(Exception):
                await self.submit_or_queue_prompt(
                    successor.session_id,
                    row["prompt"],
                    caller_id=row.get("caller_id"),
                )
        if pending:
            with contextlib.suppress(Exception):
                self._db.clear_pending_prompts(session_id)

    @staticmethod
    def _build_handoff_prompt(session: Session) -> str:
        """The self-contained instruction that asks a retiring child to author
        its own continuation brief. Deliberately does NOT depend on any handoff
        skill being installed in the child -- the structure is inlined here so a
        plain ``copilot --acp`` child produces a usable brief."""
        return (
            "SYSTEM: Your context window is nearly full and this session is about "
            "to be handed off to a fresh successor session in the SAME worktree. "
            "Author a CONTINUATION BRIEF that lets the successor resume your work "
            "with no access to this conversation. Respond with the brief ONLY -- "
            "no preamble, no questions, no offer to continue.\n\n"
            "Write the brief in Markdown with these sections:\n"
            "## Objective -- the goal you are pursuing (1-3 sentences).\n"
            "## State -- what is done, what is in progress, and key decisions or "
            "findings so far.\n"
            "## Files -- files created or modified and their purpose.\n"
            "## Next steps -- the concrete next actions, in order.\n"
            "## Gotchas -- pitfalls, constraints, or context the successor must "
            "know.\n\n"
            "Be specific and self-contained: name paths, commands, ids, issue and "
            "PR numbers, and branch names explicitly rather than referring to "
            "'the above'."
        )

    @staticmethod
    def _build_seed_prompt(brief: str) -> str:
        """Wrap a continuation brief as the successor's opening turn."""
        return (
            "You are the successor session continuing work in this worktree after "
            "an automatic context handoff. The previous session has been retired "
            "because its context window filled. Below is its continuation brief -- "
            "pick the work up from it and keep going.\n\n"
            "----- CONTINUATION BRIEF -----\n"
            f"{brief}\n"
            "----- END BRIEF -----"
        )

    def _synthesize_handoff_brief(self, session: Session) -> str:
        """Fallback brief built from session-side state, used when the child
        cannot author one (dead client, errored turn, empty reply)."""
        rows = self._db.execute_read(
            "SELECT prompt FROM turns WHERE session_id=? ORDER BY turn_index ASC "
            "LIMIT 1",
            (session.session_id,),
        )
        first_prompt = rows[0]["prompt"] if rows else "(original request unknown)"
        return (
            "## Objective\n"
            f"Continue the work started in the predecessor session for worktree "
            f"`{session.caller_id or 'unknown'}` (agent "
            f"`{session.agent_name or 'unknown'}`).\n\n"
            "## State\n"
            f"The predecessor ran {session.turn_count} turn(s) before its context "
            "window filled. An agent-authored brief could not be produced, so this "
            "is a minimal synthesized handoff.\n\n"
            "## Original request\n"
            f"{first_prompt}\n\n"
            "## Next steps\n"
            "Re-establish context from the worktree itself -- recent git log, "
            "modified files, and any effort/plan/README docs -- then resume the "
            "original request above."
        )

    async def _run_turn_sync(self, session: Session, prompt: str) -> str:
        """Run one prompt to completion synchronously and return the agent's
        reply text. Unlike :meth:`submit_prompt` (which backgrounds the turn and
        returns only a turn index), this awaits ``turn_complete`` so the caller
        gets the response text -- exactly what handoff-brief authoring needs.

        The session must be IDLE with a live client. Status is driven RUNNING for
        the duration and restored to IDLE afterward so watchdogs and the
        concurrency guard see a coherent state.
        """
        turn_index = session.turn_count
        session.turn_count += 1
        now = time.time()
        self._db.create_turn(session.session_id, turn_index, prompt, now)
        session.status = SessionStatus.RUNNING
        session.last_output_at = now
        self._db.update_session_status(
            session.session_id, SessionStatus.RUNNING.value, now
        )
        try:
            result = await session.client.send_prompt(prompt)
        finally:
            session.status = SessionStatus.IDLE
            self._db.update_session_status(
                session.session_id, SessionStatus.IDLE.value, time.time()
            )
        text = ""
        if isinstance(result, dict):
            text = result.get("response_text", "") or ""
        self._db.update_turn(
            session.session_id,
            turn_index,
            response_text=text,
            stop_reason=(result or {}).get("stop_reason")
            if isinstance(result, dict) else None,
            completed_at=time.time(),
        )
        return text

    async def handoff_session(
        self, session_id: str, *, reason: str | None = None, seed: bool = True,
    ) -> Session:
        """Hand a hosted session off to a fresh successor in the same worktree.

        The in-place, bridge-native analogue of the interactive context handoff:

        1. Ask the retiring child to author a continuation brief (synthesize one
           from session state if it cannot).
        2. Spawn a successor with the SAME target (same worktree, agent, caller).
        3. Record a durable two-way predecessor/successor link.
        4. Announce the changeover with a ``session_handoff`` event on BOTH the
           predecessor's and the successor's event streams, so every caller --
           a UI following the worktree, a bridge-as-agent host, a CLI reader --
           follows the baton in place rather than staring at a retired session.
        5. Seed the successor's opening turn with the brief so it resumes warm.
        6. Retire the predecessor (``stop_session`` -> STOPPED/resumable, its
           transcript and succession link preserved).

        Ordering is spawn-then-retire so a failed successor spawn leaves the
        predecessor untouched and current -- a handoff never orphans the
        worktree. Returns the successor session.
        """
        if self._draining:
            raise DaemonDrainingError("handoff")
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        # Single-checkout (CodeSpace/command) agents cannot host predecessor and
        # successor at once, so the spawn-then-retire ordering below does not
        # apply; that path needs retire-before-spawn and is deferred.
        if _workspace_key(session.agent_name, session.target, session.caller_id):
            raise ValueError(
                f"Session {session_id} is a single-checkout (command) agent; "
                "in-place handoff for those is not yet supported"
            )
        if session.status not in (SessionStatus.IDLE, SessionStatus.STOPPED):
            raise ValueError(
                f"Session {session_id} is {session.status.value}, not idle -- "
                "cannot hand off mid-turn"
            )

        # A live child is needed to author the brief; auto-resume a recoverable
        # STOPPED session, mirroring submit_prompt.
        if not session.client or not session.client.is_running:
            if not session.acp_session_id:
                raise RuntimeError(
                    f"Session {session_id} has no live child and no ACP id -- "
                    "cannot hand off"
                )
            session.status = SessionStatus.STOPPED
            await self.resume_session(session_id, drain=False)

        # 1. Author the continuation brief (agent-authored; synthesize on fail).
        brief = ""
        try:
            brief = (
                await self._run_turn_sync(session, self._build_handoff_prompt(session))
            ).strip()
        except Exception as exc:
            log.warning(
                "Handoff brief authoring failed for %s: %s", session_id, exc
            )
        if not brief:
            brief = self._synthesize_handoff_brief(session)
        if session.event_log:
            session.event_log.append(
                "handoff_brief", {"brief": brief, "reason": reason}
            )

        # 2. Spawn the successor in the SAME worktree. Local worktree agents are
        #    unguarded, so predecessor + successor briefly coexist; the
        #    predecessor is retired only after the successor is confirmed up.
        successor = await self.start_session(
            session.target,
            agent_name=session.agent_name,
            caller_id=session.caller_id,
        )
        if successor.status != SessionStatus.IDLE:
            # Spawn failed -- retain the predecessor and surface the failure so
            # the worktree is never left headless.
            if session.event_log:
                session.event_log.append("handoff_failed", {
                    "reason": reason,
                    "successor_id": successor.session_id,
                    "successor_status": successor.status.value,
                    "message": "successor failed to start; predecessor retained",
                })
            raise RuntimeError(
                f"Handoff of {session_id} aborted: successor "
                f"{successor.session_id} failed to start "
                f"({successor.status.value}); predecessor retained"
            )

        # 3. Persist the two-way succession link.
        now = time.time()
        self._db.link_succession(session_id, successor.session_id, now)

        # 3b. Phase 4b (worktree-self-knowledge): write the succession into the
        #     agent-worktrees GROUND LAYER -- the authoritative, single-owner
        #     lineage the facility derives the head from. The `_db` link above is
        #     the bridge's *private* event bookkeeping; the source of truth is the
        #     ground layer (the vision's *derive-dont-duplicate*). One atomic
        #     write marks the predecessor `handed-off` + makes the successor the
        #     derived head; a companion note mirrors the handoff into the worktree
        #     record's history. LOCAL worktrees only (a remote worktree's ground
        #     layer lives on its own machine); best-effort / fail-open.
        gl_worktree = getattr(session.target, "worktree_id", None)
        gl_local = getattr(session.target, "type", None) == "local"
        gl_dir = getattr(session.target, "cwd", None)
        pred_acp = session.acp_session_id
        succ_acp = successor.acp_session_id
        if gl_local and gl_worktree and pred_acp and succ_acp:
            from . import worktree_lineage
            # The successor's own start_session already registered it into the
            # ground layer (4a, synchronous), so link-succession finds it tracked.
            with contextlib.suppress(Exception):
                worktree_lineage.link_succession(
                    gl_worktree, pred_acp, succ_acp, worktree_dir=gl_dir,
                )
            with contextlib.suppress(Exception):
                worktree_lineage.note_handoff(
                    gl_worktree, pred_acp, title=(reason or "context-pressure"),
                    worktree_dir=gl_dir,
                )

        # 4. Announce the changeover on BOTH event streams.
        payload = {
            "rolled_from": session_id,
            "rolled_to": successor.session_id,
            "predecessor_acp": session.acp_session_id,
            "successor_acp": successor.acp_session_id,
            "worktree_id": session.caller_id,
            "reason": reason or "context-pressure",
            "summary": brief[:1000],
        }
        if session.event_log:
            session.event_log.append("session_handoff", payload)
        if successor.event_log:
            successor.event_log.append("session_handoff", payload)

        # 5. Seed the successor's opening turn with the brief (fire-and-forget;
        #    the successor processes it as its first turn). Phase 4c: prepend the
        #    successor's ground-layer role + the worktree history digest, because
        #    the `sessionStart` role/digest hook CANNOT fire under `copilot --acp`
        #    -- so the ACP successor learns its lineage from its opening turn
        #    instead. Fail-open: a missing digest never blocks the warm seed.
        if seed:
            seed_prompt = self._build_seed_prompt(brief)
            if gl_local and gl_worktree and succ_acp:
                from . import worktree_lineage
                with contextlib.suppress(Exception):
                    role = worktree_lineage.session_role(
                        gl_worktree, succ_acp, worktree_dir=gl_dir,
                    )
                    digest = worktree_lineage.history_digest(
                        gl_worktree, succ_acp, worktree_dir=gl_dir,
                    )
                    header = worktree_lineage.build_succession_seed_header(
                        role, digest, predecessor=pred_acp,
                    )
                    if header:
                        seed_prompt = header + seed_prompt
            with contextlib.suppress(Exception):
                await self.submit_prompt(successor.session_id, seed_prompt)

        # 6. Retire the predecessor: STOPPED keeps it resumable and preserves its
        #    transcript + succession link (end_session would delete both).
        with contextlib.suppress(Exception):
            await self.stop_session(session_id, force=True)

        log.info(
            "Handoff: session %s -> %s (worktree %s)",
            session_id, successor.session_id, session.caller_id,
        )
        return successor

    def _resolve_ref(self, ref: str) -> str | None:
        """Resolve a session reference to the canonical bridge session_id.

        Accepts either the bridge session_id (the internal uuid) or the
        ACP-sourced session id (``acp_session_id``).  Returns the bridge
        session_id, or None if no session matches.  This lets HTTP/CLI
        callers address sessions by the durable ACP id without knowing the
        bridge's internal handle.
        """
        if ref in self._sessions:
            return ref
        for sid, session in self._sessions.items():
            if session.acp_session_id == ref:
                return sid
        return None

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(self._resolve_ref(session_id) or session_id)

    def list_sessions(self, status: str | None = None) -> list[Session]:
        sessions = list(self._sessions.values())
        if status:
            sessions = [s for s in sessions if s.status.value == status]
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)


def session_manager_from_config(db: Database, cfg: ServiceConfig) -> SessionManager:
    """Build a :class:`SessionManager` wired from service config.

    The **single** construction site for a config-driven manager, so every
    entrypoint -- the HTTP daemon (``app.py``) and ACP-agent mode
    (``__main__._cmd_agent``) -- wires the same session-host settings. Session
    Hosts are always on (dotfiles#1478); this factory forwards the operator's
    host tunables (reap/idle/stall budgets). Route every config-driven
    construction through here.
    """
    return SessionManager(
        db,
        context_thresholds=cfg.context_thresholds,
        auto_handoff=cfg.auto_handoff,
        timeouts=cfg.timeouts,
        retention=cfg.retention,
        session_host_stale_reap_seconds=cfg.session_host_stale_reap_seconds,
        graceful_cancel_settle_seconds=cfg.graceful_cancel_settle_seconds,
        cancel_turns_on_redeploy=cfg.cancel_turns_on_redeploy,
        idle_reap_ttl_seconds=cfg.idle_reap_ttl_seconds,
        live_stall_interrupt_after_s=cfg.live_stall_interrupt_after_s,
        session_host_unexpected_reap_seconds=cfg.session_host_unexpected_reap_seconds,
        session_host_active_reap_seconds=cfg.session_host_active_reap_seconds,
    )
