"""agent-bridge integration: spawn a worker agent to execute a task.

agent-dispatch stays decoupled from agent-bridge -- it shells out to the
``agent-bridge`` CLI binstub when present, and degrades gracefully (leaving the
task queued for any worker to claim) when it is not. agent-bridge is an
*optional* producer of workers, never a hard dependency, so agent-dispatch
remains a standalone plugin usable where no bridge exists.
"""

from __future__ import annotations

import shutil
import subprocess

DEFAULT_WORKER_AGENT = "task-worker"


class BridgeUnavailable(RuntimeError):
    """Raised when the agent-bridge CLI is not available on this host."""


def bridge_available() -> bool:
    """True if the ``agent-bridge`` CLI is on PATH."""
    return shutil.which("agent-bridge") is not None


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
    wait: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Spawn a worker agent via agent-bridge to claim + execute ``task_id``.

    Runs ``agent-bridge create <agent> "<prompt>" [--no-wait]``. Raises
    :class:`BridgeUnavailable` if the agent-bridge CLI is not on PATH; the caller
    degrades by leaving the task queued.
    """
    exe = shutil.which("agent-bridge")
    if exe is None:
        raise BridgeUnavailable("agent-bridge CLI not found on PATH")
    prompt = worker_prompt(task_id, coordinator_url=coordinator_url, worker_id=worker_id)
    cmd = [exe, "create", agent, prompt]
    if not wait:
        cmd.append("--no-wait")
    return subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
        cmd, check=False, capture_output=True, text=True, timeout=timeout
    )
