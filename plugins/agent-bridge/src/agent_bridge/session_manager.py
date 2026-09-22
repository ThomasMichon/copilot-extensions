"""Session manager -- lifecycle, persistence, and event routing.

Manages all active sessions. Each session wraps one ACP client (which
owns the subprocess) and an EventLog for SSE streaming. State is
persisted to SQLite so sessions survive service restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import shlex
import shutil
import subprocess
import time
from typing import Any

from agent_procutil import no_window_flags

from .acp_client import AcpClient
from .db import Database
from .events import EventLog
from .models import ServiceConfig, SessionStatus
from .session_host_liveness import _HostLivenessMixin
from .session_recovery_dormancy import (
    _BACKGROUND_RECOVERY_IDLE_DORMANCY_AFTER,  # noqa: F401 -- re-exported for tests
    _DISCONNECTED_REATTACH_ESCALATE_AFTER,  # noqa: F401 -- re-exported for tests
    _RecoveryDormancyMixin,
    _background_recovery_backoff_seconds,  # noqa: F401 -- re-exported for tests
)
from .transport import SpawnTarget, _agent_worktrees_python, _agent_worktrees_root, spawn

log = logging.getLogger("agent-bridge")

_REQUEST_OVERRIDES_KEY = "_agent_bridge_request_overrides"

# The resume recovery ladder (#1468): a resume stall is Copilot CLI's ACP
# startup race, not a broken session -- so on a failed/stalled resume attempt we
# stop the wedged child and re-resume (a fresh Copilot launch re-rolls the race)
# against the SAME persisted ACP session, preserving prior-turn context. Only
# after this many rounds all fail is the resume surfaced as a failure (the
# caller's end+create is then the last resort).
_MAX_RESUME_ROUNDS = 3

class _AcpLaunchTiming:
    """Collect low-noise ACP launch sub-step timings for one session launch."""

    def __init__(self, *, session_id: str, mode: str, boundary: str) -> None:
        self.session_id = session_id
        self.mode = mode
        self.boundary = boundary
        self._started = time.monotonic()
        self._steps: list[tuple[str, float]] = []

    def add(self, label: str, elapsed_s: float) -> None:
        self._steps.append((label, max(0.0, float(elapsed_s))))

    def total_s(self) -> float:
        return max(0.0, time.monotonic() - self._started)

    def step_map_ms(self) -> dict[str, int]:
        return {
            label: int(round(elapsed_s * 1000.0))
            for label, elapsed_s in self._steps
        }

    def summary(self) -> str:
        parts = [
            f"session={self.session_id}",
            f"mode={self.mode}",
            f"boundary={self.boundary}",
        ]
        parts.extend(
            f"{label}={int(round(elapsed_s * 1000.0))}ms"
            for label, elapsed_s in self._steps
        )
        parts.append(f"total={int(round(self.total_s() * 1000.0))}ms")
        return " ".join(parts)


def _format_optional_hours(value_s: float | None) -> str:
    """Render an optional age in hours for compact GC observability."""
    if value_s is None:
        return "-"
    return f"{value_s / 3600.0:.1f}h"


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
    aw_lib = os.path.join(_agent_worktrees_root(), "lib")
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
        # Gates startup/heartbeat auto-recovery; stop clears it, resume arms it.
        self.background_recovery_enabled = True
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


from .session_core import _SessionCoreMixin
from .session_handoff import _SessionHandoffMixin
from .session_host_connection import _SessionHostConnectionMixin
from .session_host_recovery import _SessionHostRecoveryMixin
from .session_lifecycle import _SessionLifecycleMixin
from .session_monitoring import _SessionMonitoringMixin
from .session_parity import _SessionParityMixin
from .session_prompts import _SessionPromptMixin
from .session_resume import _SessionResumeMixin
from .session_start import _SessionStartMixin

__all__ = [
    "AcpClient",
    "CodespaceClaimConflictError",
    "CodespaceCoordinationRejectedError",
    "DaemonDrainingError",
    "ProviderTargetRefreshError",
    "RemoteHostRecoveryPendingError",
    "Session",
    "SessionBusyError",
    "SessionConflictError",
    "SessionManager",
    "SessionStatus",
    "_BACKGROUND_RECOVERY_IDLE_DORMANCY_AFTER",
    "_DISCONNECTED_REATTACH_ESCALATE_AFTER",
    "_MAX_RESUME_ROUNDS",
    "_background_recovery_backoff_seconds",
    "_claim_codespace",
    "_release_codespace_claim",
    "_resolve_relay_launch_env",
    "_resolve_remote_ai_plugin_dirs",
    "session_manager_from_config",
    "spawn",
]


class SessionManager(
    _SessionCoreMixin,
    _RecoveryDormancyMixin,
    _HostLivenessMixin,
    _SessionHostConnectionMixin,
    _SessionParityMixin,
    _SessionHostRecoveryMixin,
    _SessionMonitoringMixin,
    _SessionStartMixin,
    _SessionResumeMixin,
    _SessionPromptMixin,
    _SessionLifecycleMixin,
    _SessionHandoffMixin,
):
    """Manages all agent-bridge sessions with SQLite persistence."""

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
