"""agent-bridge integration: spawn a worker agent to execute a task.

agent-dispatch stays decoupled from agent-bridge -- it shells out to the
``agent-bridge`` CLI binstub when present, and degrades gracefully (leaving the
task queued for any worker to claim) when it is not. agent-bridge is an
*optional* producer of workers, never a hard dependency, so agent-dispatch
remains a standalone plugin usable where no bridge exists.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Sequence
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
        f"(2) claim it with `agent-dispatch claim {task_id} --worker {worker_id}` "
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
    json_output: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Spawn a worker agent via agent-bridge to claim + execute ``task_id``.

    Runs ``agent-bridge [--json] create <agent> "<prompt>" [--no-wait]``. Raises
    :class:`BridgeUnavailable` if the agent-bridge CLI is not on PATH; the caller
    degrades by leaving the task queued.

    ``prompt`` overrides the default worker seed (:func:`worker_prompt`). A caller
    embodying a task headlessly with richer semantics -- e.g. the supervisor's
    headless embody backend, which reuses the CLI autopilot seed so a
    headless-embodied task is driven identically to a CLI-embodied one -- passes
    the seed it wants delivered verbatim.

    ``json_output`` inserts the ``--json`` global flag before ``create`` so the
    created session id rides stdout as JSON -- the caller then records a recovery
    handle (the local agent-bridge session id) for liveness-gated auto-recovery
    of an orphaned reservation (see
    :func:`agent_dispatch.embody.parse_fleet_body_session` /
    :func:`agent_dispatch.embody.local_body_verdict`).
    """
    exe = _agent_bridge_launch_prefix()
    if exe is None:
        raise BridgeUnavailable("agent-bridge CLI not found on PATH")
    if prompt is None:
        prompt = worker_prompt(
            task_id, coordinator_url=coordinator_url, worker_id=worker_id
        )
    cmd = [*exe]
    if json_output:
        cmd.append("--json")
    cmd += ["create", agent, prompt]
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


def resume_steered_owner(
    owner: str,
    task_id: str,
    message: str | None = None,
    *,
    timeout: float | None = 20.0,
) -> bool:
    """Resume a task owner immediately after an operator submits steering.

    Unlike :func:`send_nudge`, this is a work-bearing ``prompt`` delivery. The
    bridge queues it when the owner is busy so the current turn is preserved and
    the steering prompt runs next. Delivery is best-effort: the steer itself is
    already durable in agent-dispatch, so an unavailable bridge or failed send
    returns ``False`` without consuming or losing the operator's answer.
    """
    exe = _agent_bridge_launch_prefix()
    if exe is None:
        return False
    prompt = message or (
        f"The operator answered your card on task {task_id}. Resume, run "
        f"`agent-dispatch steer take {task_id}` to read the answer, and continue "
        f"toward your goal."
    )
    cmd = [
        *exe,
        "send",
        "--no-wait",
        "--queue",
        "--kind",
        "prompt",
        "--sender",
        "agent-dispatch-steer",
        owner,
        prompt,
    ]
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            **no_window_kwargs(),
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return proc.returncode == 0


def parse_agent_names(out: str | None) -> set[str] | None:
    """Extract agent ``name`` values from ``agent-bridge --json agents`` stdout.

    ``agent-bridge`` may print a human preamble line before the JSON array, so we
    locate the first ``[`` and ``raw_decode`` from there. Returns the set of names,
    or ``None`` -- meaning *indeterminate*, not *empty* -- when the payload is
    missing or unparseable, so a caller can distinguish "provably absent" (a real
    set that omits a name) from "couldn't tell".
    """
    text = (out or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        # Tolerate a stray human preamble line before the JSON array: decode from
        # the first '[' (best-effort; a preamble that itself contains '[' simply
        # reads as indeterminate rather than crashing).
        start = text.find("[")
        if start == -1:
            return None
        try:
            data, _end = json.JSONDecoder().raw_decode(text[start:])
        except (ValueError, TypeError):
            return None
    if not isinstance(data, list):
        return None
    names: set[str] = set()
    for entry in data:
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str) and name:
                names.add(name)
    return names


def registered_agent_names(*, timeout: float = 8.0) -> set[str] | None:
    """Best-effort set of agent names registered with the **local** agent-bridge.

    Runs ``agent-bridge --json agents`` and collects each entry's ``name``.
    Returns ``None`` (indeterminate) whenever the registry can't be read -- the
    bridge CLI is absent, the command exits non-zero, times out, or emits
    unparseable output -- so a caller never mistakes "couldn't check" for "no such
    agent". Never raises.
    """
    exe = _agent_bridge_launch_prefix()
    if exe is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe resolved above
            [*exe, "--json", "agents"],
            check=False, capture_output=True, text=True, timeout=timeout,
            **no_window_kwargs(),
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return parse_agent_names(proc.stdout)


def preflight_headless_agent(
    agent: str,
    *,
    pool: Sequence[str] | None = None,
    local_timeout: float = 8.0,
    remote_timeout: float = 15.0,
) -> list[str]:
    """Best-effort check that ``agent`` is a registered agent-bridge agent on the
    host(s) where a headless embody body will actually spawn.

    A headless supervise lane hands ``agent`` to ``agent-bridge create <agent>``;
    if no such agent is registered the spawn fails ("'<agent>' is not a known
    agent name"), retries, and dead-letters -- silently, from the operator's seat.
    This preflight turns that latent misconfiguration (classically the bogus
    ``task-worker`` code default naming an agent nobody registered) into a loud,
    diagnosable startup WARNING, returning one human-readable line per host where
    ``agent`` is *provably* absent.

    It is deliberately **advisory and best-effort** (``degrade-gracefully`` +
    ``fail-loud-on-endpoint-error``): it warns only when the registry is readable
    AND the agent is confirmed missing. If the registry can't be read (bridge
    absent, host unreachable, timeout, unparseable) that host is INDETERMINATE and
    yields no warning -- the preflight never blocks a lane and never cries wolf on
    ignorance. For a fleet lane (``pool`` set) the body spawns on each remote pool
    host, so each is probed over SSH; otherwise the local registry is probed.
    """
    checks: list[tuple[str, set[str] | None]] = []
    if pool:
        from . import embody

        for host in pool:
            h = host.strip()
            if not h:
                continue
            checks.append(
                (h, embody.remote_registered_agent_names(h, timeout=remote_timeout))
            )
    else:
        checks.append(("this host", registered_agent_names(timeout=local_timeout)))
    warnings: list[str] = []
    for where, names in checks:
        if names is not None and agent not in names:
            warnings.append(
                f"agent-dispatch supervise: WARNING -- headless embody agent "
                f"{agent!r} is not registered with agent-bridge on {where}; "
                f"headless spawns for this lane will fail ({agent!r} is not a known "
                f"agent name) and dead-letter. Register it (e.g. add a body to "
                f"acp-agents.json) or set --headless-agent to a registered agent."
            )
    return warnings
