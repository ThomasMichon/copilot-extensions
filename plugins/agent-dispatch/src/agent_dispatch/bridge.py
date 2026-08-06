"""agent-bridge integration: spawn a worker agent to execute a task.

agent-dispatch stays decoupled from agent-bridge -- it shells out to the
``agent-bridge`` CLI binstub when present, and degrades gracefully (leaving the
task queued for any worker to claim) when it is not. agent-bridge is an
*optional* producer of workers, never a hard dependency, so agent-dispatch
remains a standalone plugin usable where no bridge exists.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .procutil import no_window_kwargs

DEFAULT_WORKER_AGENT = "task-worker"


class BridgeUnavailable(RuntimeError):
    """Raised when the agent-bridge CLI is not available on this host."""


def _agent_bridge_launch_prefix() -> list[str] | None:
    """Resolve an argv prefix that runs the ``agent-bridge`` CLI **without**
    routing through a Windows ``.cmd``/``.bat`` shim.

    ``spawn_worker`` hands the autopilot seed to ``agent-bridge create`` and
    ``send_nudge`` hands an arbitrary message to ``agent-bridge send``; both can
    contain shell metacharacters (``&``, ``(``, ``)``, ``<``, ``>``, backtick).
    On Windows a ``subprocess`` launch of the ``agent-bridge.cmd`` binstub runs
    it through ``cmd.exe``, whose ``%*`` re-parse treats those characters as
    command operators and corrupts the arguments -- the shim then fails with
    WinError 2 ("The system cannot find the file specified"). This is the
    BatBadBut class of bug (the same one ``embody._agent_worktrees_launch_prefix``
    fixes for the ``agent-worktrees`` binstub). Invoking the interpreter directly
    (``python -m agent_bridge``) bypasses ``cmd.exe`` entirely, so the argument is
    delivered verbatim.

    Prefer the agent-bridge runtime venv interpreter; fall back to the
    ``agent-bridge`` binstub on PATH when that venv isn't present (POSIX shims are
    plain exec scripts and do not re-parse, so they are unaffected). Returns
    ``None`` when neither is resolvable."""
    venv = Path.home() / ".agent-bridge" / "venv"
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if py.is_file():
        return [str(py), "-m", "agent_bridge"]
    exe = shutil.which("agent-bridge")
    return [exe] if exe else None


def bridge_available() -> bool:
    """True if the ``agent-bridge`` CLI can be launched on this host."""
    return _agent_bridge_launch_prefix() is not None


def worker_prompt(task_id: str, *, coordinator_url: str, worker_id: str) -> str:
    """Build the instruction prompt handed to a spawned worker agent."""
    return (
        f"You are an agent-dispatch task worker (worker id: {worker_id}). "
        f"A task has been queued for you on the coordinator at {coordinator_url}. "
        f"Steps: (1) read it with `agent-dispatch show {task_id}`; "
        f"(2) claim it with `agent-dispatch claim {worker_id} --task {task_id}` "
        f"(add `--capability <cap>` for each capability the task requires); "
        f"(3) `agent-dispatch start {task_id} {worker_id}`, do the work described "
        f"in the task's prompt/payload, then "
        f"(4) `agent-dispatch complete {task_id} {worker_id} --result-ref <ref>`. "
        f"On a recoverable snag, `agent-dispatch yield {task_id} {worker_id} "
        f"--note <why>` returns it to the queue."
    )


def spawn_worker(
    task_id: str,
    *,
    agent: str = DEFAULT_WORKER_AGENT,
    coordinator_url: str,
    worker_id: str,
    prompt: str | None = None,
    wait: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Spawn a worker agent via agent-bridge to claim + execute ``task_id``.

    Runs ``agent-bridge create <agent> "<prompt>" [--no-wait]``. Raises
    :class:`BridgeUnavailable` if the agent-bridge CLI is not on PATH; the caller
    degrades by leaving the task queued.

    ``prompt`` overrides the default worker seed (:func:`worker_prompt`). A caller
    embodying a task headlessly with richer semantics -- e.g. the supervisor's
    headless embody backend, which reuses the CLI autopilot seed so a
    headless-embodied task is driven identically to a CLI-embodied one -- passes
    the seed it wants delivered verbatim.
    """
    exe = _agent_bridge_launch_prefix()
    if exe is None:
        raise BridgeUnavailable("agent-bridge CLI not found on PATH")
    if prompt is None:
        prompt = worker_prompt(
            task_id, coordinator_url=coordinator_url, worker_id=worker_id
        )
    cmd = [*exe, "create", agent, prompt]
    if not wait:
        cmd.append("--no-wait")
    return subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
        cmd, check=False, capture_output=True, text=True, timeout=timeout,
        **no_window_kwargs(),
    )


def send_nudge(
    worktree: str,
    message: str,
    *,
    sender: str = "agent-dispatch-supervisor",
    timeout: float | None = 20.0,
) -> bool:
    """Send a non-blocking **nudge** to a live embodied session via agent-bridge.

    Shells ``agent-bridge send --no-wait --kind notify --sender <sender>
    <worktree> <message>`` -- the ``worktree`` handle resolves to whichever
    session is live now (and routes cross-machine through the bridge mesh). The
    nudge is *notify*-kind: an out-of-band prod, never treated as new work.
    Best-effort: returns ``True`` on a clean send, ``False`` if the bridge CLI is
    absent or the send fails -- a failed nudge is never fatal (a genuinely-gone
    worker is handled by liveness recovery, not the nudge).
    """
    exe = _agent_bridge_launch_prefix()
    if exe is None:
        return False
    cmd = [
        *exe, "send", "--no-wait", "--kind", "notify", "--sender", sender,
        worktree, message,
    ]
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            cmd, check=False, capture_output=True, text=True, timeout=timeout,
            **no_window_kwargs(),
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return proc.returncode == 0
