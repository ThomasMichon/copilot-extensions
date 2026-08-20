"""Transport-exec seam: run a bash command on a session's *target*.

agent-bridge owns transport-agnostic session logic (``peek`` now; the repo-own
``.ai`` plugin-dir resolve in a follow-up). The target-specific "how do I run a
command *over there*" is this small seam, so that logic stays in agent-bridge and
only the transport differs:

  - **local** target  -> run under ``bash`` on this host.
  - **codespace** target -> shell out to the ``agent-codespaces`` binstub
    (``agent-codespaces ssh <name> --remote-cmd``), reusing its own venv, relay,
    and ssh-manager state (the same process-boundary pattern session_manager uses
    for ``relay-launch-env``).

A session dict (as returned by ``BridgeClient.get_session`` /
``list_sessions``) is the input: ``agent_name`` == ``codespace:<name>`` selects
the codespace transport; otherwise the target is treated as local. ssh/container
transports are future work (raise ``TargetExecError`` for now).
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from agent_procutil import no_window_flags

log = logging.getLogger("agent-bridge")

_CODESPACE_PREFIX = "codespace:"

# Suppress a flashing console window when spawning the transport subprocess on a
# Windows host (parity with the retired session_manager shell-out, which set this
# on its ``agent-codespaces`` call).
_CREATE_NO_WINDOW = no_window_flags()


class TargetExecError(RuntimeError):
    """The target's transport is unavailable or the remote exec failed."""


def target_kind(session: dict) -> str:
    """``codespace`` | ``local`` | ``<target_type>`` for a session dict."""
    agent = str(session.get("agent_name") or "")
    if agent.startswith(_CODESPACE_PREFIX):
        return "codespace"
    tt = str(session.get("target_type") or "local")
    return "local" if tt in ("local", "command") else tt


def codespace_name(session: dict) -> str:
    """The CodeSpace name from a ``codespace:<name>`` agent, or ``""``."""
    agent = str(session.get("agent_name") or "")
    return agent[len(_CODESPACE_PREFIX):] if agent.startswith(_CODESPACE_PREFIX) else ""


def exec_bash_on_target(session: dict, command: str, *, timeout: float) -> str:
    """Run ``command`` (a bash string) on ``session``'s target; return stdout.

    ``timeout`` bounds the *remote* command; a transport-specific slack is added
    for connect/teardown. Raises ``TargetExecError`` when the transport is
    missing/unsupported; a non-zero remote exit is NOT itself an error (callers
    parse a marker line and degrade), so stdout is returned regardless.
    """
    kind = target_kind(session)
    if kind == "codespace":
        binstub = shutil.which("agent-codespaces")
        if not binstub:
            raise TargetExecError("agent-codespaces not on PATH (codespace transport)")
        name = codespace_name(session)
        if not name:
            raise TargetExecError("codespace target has no name")
        argv = [
            binstub, "ssh", name,
            "--timeout", str(int(timeout)),
            "--remote-cmd", command,
        ]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout + 60,
                creationflags=_CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise TargetExecError(f"codespace exec timed out after {timeout}s") from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise TargetExecError(f"codespace exec failed: {exc}") from exc
        return proc.stdout

    if kind == "local":
        bash = shutil.which("bash")
        if not bash:
            raise TargetExecError("no bash on PATH for local target")
        try:
            proc = subprocess.run(
                [bash, "-lc", command], capture_output=True, text=True,
                timeout=timeout + 10, creationflags=_CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise TargetExecError(f"local exec timed out after {timeout}s") from exc
        return proc.stdout

    raise TargetExecError(f"unsupported target transport: {kind!r}")
