"""agent-worktrees integration: dispatch a task to a CLI-backed autopilot session.

Unlike :mod:`agent_dispatch.bridge` (which spawns a *headless* agent-bridge ACP
worker), this spawns a durable, **CLI-backed autopilot** session in a fresh
parallel worktree on the same machine via ``agent-worktrees embody``. The
embodied Copilot launches with ``--allow-all-tools`` (tools auto-approved -- no
per-tool confirmation prompts), claims and starts the task, works it
autonomously, and marks the task ``completed`` **explicitly** only when it judges
the goal reached -- *deferred completion*, never stamped at spawn or pickup.

agent-dispatch stays decoupled: it shells out to the ``agent-worktrees`` binstub
when present and degrades gracefully (the caller falls back to the bridge
backend, or leaves the task queued) when it is not -- so the plugin remains
standalone on a host without agent-worktrees.
"""

from __future__ import annotations

import shutil
import subprocess

DEFAULT_DRIVER = "agent-dispatch"


class EmbodyUnavailable(RuntimeError):
    """Raised when the ``agent-worktrees`` CLI is not available on this host."""


def embody_available() -> bool:
    """True if the ``agent-worktrees`` CLI is on PATH."""
    return shutil.which("agent-worktrees") is not None


def autopilot_worker_prompt(
    task_id: str, *, coordinator_url: str, worker_id: str
) -> str:
    """Build the autopilot seed handed to a dispatched, embodied CLI session.

    A dispatch-flavored variant of :func:`agent_dispatch.bridge.worker_prompt`:
    it frames the session as an autonomous autopilot worker and makes explicit
    that **completing the task is its own deliberate signal that the work is
    done** -- it must not complete before the goal is met.

    The worker drives its whole lifecycle under its **worktree identity**
    (owner-less ``claim``/``start``/``complete``/``yield``, which the coordinator
    resolves to ``<machine>/<worktree>``). That keeps the task's owner equal to
    its worktree, so agent-bridge live-session tracking can join the task to the
    embodied session (see :mod:`agent_dispatch.tracking`) -- a dispatched CLI
    body is then as trackable as a headless worker. ``worker_id`` names the
    session in the seed for legibility only.
    """
    return (
        f"You are a dispatched agent-dispatch **autopilot** worker (worker id: "
        f"{worker_id}), running in a fresh parallel worktree with tools "
        f"auto-approved (--allow-all-tools). A task has been queued for you on "
        f"the coordinator at {coordinator_url}. Work it end-to-end, "
        f"autonomously, without waiting for a human. Claim it under this "
        f"worktree's own identity (no owner argument -- the coordinator resolves "
        f"machine/worktree), which keeps the task trackable as your live "
        f"session. Steps: "
        f"(1) read it with `agent-dispatch show {task_id}`; "
        f"(2) claim it with `agent-dispatch claim --task {task_id}` "
        f"(add `--capability <cap>` for each capability the task requires); "
        f"(3) `agent-dispatch start {task_id}`, then carry out the "
        f"work described in the task's prompt/payload to completion; "
        f"(4) ONLY once you judge the task's goal genuinely reached, run "
        f"`agent-dispatch complete {task_id} --result-ref <ref>`. "
        f"Do NOT mark it complete before the goal is met -- completing the task "
        f"is your explicit signal that the work is done. On a real blocker, "
        f"`agent-dispatch yield {task_id} --note <why>` returns it "
        f"to the queue for a later cycle."
    )


def spawn_embodied_worker(
    task_id: str,
    *,
    coordinator_url: str,
    worker_id: str,
    driver: str = DEFAULT_DRIVER,
    verify_timeout: int = 0,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Spawn a CLI-backed autopilot worker via ``agent-worktrees embody``.

    Runs ``agent-worktrees embody --new --seed "<autopilot seed>" --driver
    <driver> --json`` -- creating a fresh parallel worktree and a detached
    mux+Copilot session seeded to claim + execute ``task_id`` autonomously. The
    ``--driver`` label stamps the "driven by <agent>" banner so the session is
    legible in Neuron Forge. Raises :class:`EmbodyUnavailable` if the
    ``agent-worktrees`` CLI is not on PATH; the caller degrades from there.

    ``verify_timeout`` (seconds) optionally makes embody wait for the mux
    session to come up before returning (0 = don't wait).
    """
    exe = shutil.which("agent-worktrees")
    if exe is None:
        raise EmbodyUnavailable("agent-worktrees CLI not found on PATH")
    seed = autopilot_worker_prompt(
        task_id, coordinator_url=coordinator_url, worker_id=worker_id
    )
    cmd = [exe, "embody", "--new", "--seed", seed, "--driver", driver, "--json"]
    if verify_timeout:
        cmd += ["--verify-timeout", str(verify_timeout)]
    return subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
        cmd, check=False, capture_output=True, text=True, timeout=timeout
    )
