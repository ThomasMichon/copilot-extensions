"""The host's launch policy, asked before agent-bridge launches a worker on a CodeSpace.

agent-codespaces owns the policy (``agent-codespaces launch-policy`` registers a
host command; see its ``launch_policy`` module). The bridge never imports that
plugin (#796), so it shells the same ``launch-check`` seam its CodeSpace claim
already uses, right before a Session Host is spawned -- a fresh start, or a
respawn on resume (including the implicit resume a later ``send`` triggers).

Allowed without asking only when no policy can be registered: ``agent-codespaces``
absent, or too old to have ``launch-check``, *and* no registration file on this
machine. Otherwise it fails closed: a check that can't run, fails for any other
reason, or a registration that can't be checked refuses the launch.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from agent_procutil import no_window_flags

#: ``agent_codespaces.launch_policy.LAUNCH_REFUSED_EXIT``.
LAUNCH_REFUSED_EXIT = 79
#: ``launch_policy.MAX_TIMEOUT`` (45s, the most a registered policy may take)
#: plus room for agent-codespaces' own start-up and tree-kill cleanup, so a slow
#: policy times out inside the check, the same way for every launch path.
_CHECK_TIMEOUT = 60.0
#: The check is told to answer this long before ``_CHECK_TIMEOUT`` kills it, so
#: the policy's timeout shrinks to fit a slow start-up and its own cleanup
#: always runs (killing only the check would orphan the policy's process group).
_CHECK_DEADLINE_MARGIN = 5.0


class LaunchRefusedError(RuntimeError):
    """The host's launch policy refused a worker launch on a venue."""

    def __init__(self, codespace: str, reason: str) -> None:
        self.codespace = codespace
        self.reason = reason
        super().__init__(f"launch on CodeSpace '{codespace}' refused: {reason}")


def _detail(result: subprocess.CompletedProcess) -> str:
    return " ".join(((result.stderr or "") + " " + (result.stdout or "")).split())[:200]


def _unchecked_refusal(why: str) -> str | None:
    """No ``launch-check`` to ask: allowed only when nothing is registered. A
    registration persists across runtime fallbacks, so one that exists but can't
    be checked refuses the launch rather than silently skipping it."""
    if not _registration_present():
        return None
    return f"a launch policy is registered but can't be checked ({why})"


def _registration_present() -> bool:
    """Whether a launch policy is registered on this machine, judged by its file
    alone (agent-codespaces' ``RUNTIME_DIR/launch-policy.json``, with the same
    ``AGENT_CODESPACES_HOME`` / ``AGENT_HOME`` overrides), so it holds whatever
    agent-codespaces version -- or none -- is currently installed."""
    override = os.environ.get("AGENT_CODESPACES_HOME", "").strip()
    if override:
        root = Path(override).expanduser()
    else:
        home = os.environ.get("AGENT_HOME", "").strip()
        root = (Path(home) if home else Path.home()) / ".agent-codespaces"
    path = root / "launch-policy.json"
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:  # can't tell (e.g. no traverse permission): assume registered
        return True
    return True


def _check_command() -> list[str] | None:
    """How to reach agent-codespaces: the active ``codespace`` provider's absolute
    command from ``providers.d`` (the daemon's service ``PATH`` normally lacks
    sibling binstubs; that command also runs inside the provider's own
    installation context), with a ``PATH`` lookup only as a legacy fallback."""
    try:
        from .provider_sources import discover_provider_manifests

        manifest = discover_provider_manifests().get("codespace")
    except Exception:  # noqa: BLE001 -- discovery trouble falls back to PATH, never crashes a launch
        manifest = None
    if manifest is not None and manifest.command:
        return list(manifest.command)
    binstub = shutil.which("agent-codespaces")  # marketplace-isolation: allow provider-management
    return [binstub] if binstub else None


def codespace_launch_refusal(codespace: str) -> str | None:
    """Why the host refuses a worker launch on ``codespace`` now, or ``None``."""
    command = _check_command()
    if not command:
        return _unchecked_refusal("agent-codespaces is not installed")
    try:
        result = subprocess.run(
            [*command, "launch-check", codespace, "--json",
             "--deadline", f"{time.time() + _CHECK_TIMEOUT - _CHECK_DEADLINE_MARGIN:.3f}"],
            # Decode leniently: malformed bytes must reach the answer validation
            # below (and refuse), not escape it as a UnicodeDecodeError.
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=_CHECK_TIMEOUT,
            creationflags=no_window_flags(),
        )
    except subprocess.TimeoutExpired:
        return f"launch policy check timed out after {_CHECK_TIMEOUT:g}s"
    except OSError as exc:
        return f"launch policy check could not run: {exc}"
    if result.returncode == 0:
        # Fail closed on anything but the explicit allow answer: truncated or
        # stale output from an exit-0 check is not permission to launch.
        try:
            answer = json.loads(result.stdout)
        except json.JSONDecodeError:
            answer = None
        if isinstance(answer, dict) and "refuse" in answer and answer["refuse"] is None:
            return None
        return f"launch policy check answered without an explicit allow: {_detail(result) or 'no output'}"
    if result.returncode == 2 and "invalid choice" in (result.stderr or ""):
        return _unchecked_refusal("this agent-codespaces predates launch-check")
    if result.returncode == LAUNCH_REFUSED_EXIT:
        try:
            reason = json.loads(result.stdout).get("refuse")
        except (json.JSONDecodeError, AttributeError):
            reason = None
        return reason or _detail(result) or "refused by the launch policy"
    return f"launch policy check failed (exit {result.returncode}): {_detail(result)}"


def ensure_codespace_launch_allowed(codespace: str) -> None:
    """Raise :class:`LaunchRefusedError` when the host refuses the launch."""
    reason = codespace_launch_refusal(codespace)
    if reason is not None:
        raise LaunchRefusedError(codespace, reason)


async def gate_raw_codespace_spawn(target: object) -> None:
    """Ask the policy before a raw-transport spawn whose target is a CodeSpace (a
    resync, or a resume without a Session Host), as the Session Host spawner does
    for its own; any other target passes. Raises :class:`LaunchRefusedError`."""
    import asyncio

    name = (getattr(target, "codespace", None) or {}).get("name")
    if name:
        await asyncio.to_thread(ensure_codespace_launch_allowed, name)
