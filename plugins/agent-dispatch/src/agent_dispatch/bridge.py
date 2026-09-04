"""agent-bridge integration: spawn a worker agent to execute a task.

agent-dispatch stays decoupled from agent-bridge -- it shells out to the
``agent-bridge`` CLI binstub when present, and degrades gracefully (leaving the
task queued for any worker to claim) when it is not. agent-bridge is an
*optional* producer of workers, never a hard dependency, so agent-dispatch
remains a standalone plugin usable where no bridge exists.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from collections.abc import Callable, Sequence

from . import remote_dispatch
from .procutil import (
    agent_bridge_launch_prefix,
    no_window_kwargs,
    run_ssh_command,
)

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

    Resolve the agent-bridge runtime interpreter via the **standardized spawn
    flow** (:func:`~agent_dispatch.procutil.resolve_runtime_python` -- the
    canonical versioned-runtime resolver the binstubs use), **not** a hard-coded
    ``venv`` path (which misses the ``versions/<ver>`` slot layout and then falls
    back to a ``.ps1`` ``subprocess`` cannot exec on Windows). Fall back to the
    ``agent-bridge`` binstub on PATH only on POSIX (its shims are plain exec
    scripts and do not re-parse). Returns ``None`` when neither is resolvable."""
    return agent_bridge_launch_prefix()


def bridge_available() -> bool:
    """True if the ``agent-bridge`` CLI can be launched on this host."""
    return _agent_bridge_launch_prefix() is not None


def worker_prompt(task_id: str, *, worker_id: str, route: str = "") -> str:
    """Build the instruction prompt handed to a spawned worker agent.

    ``route`` is the coordinator **routing intent** (a leading ``agent-dispatch``
    flag fragment: ``""`` for the default local coordinator, or ``" --shared"``
    for the env-configured shared moniker). The default carries **no** endpoint,
    so each command rediscovers the live local coordinator -- transparent to a
    zero-downtime port cutover. A raw ``--url`` endpoint is never baked into a
    worker (the caller rejects that combination); routing is by discovery or a
    stable moniker only.
    """
    ad = f"agent-dispatch{route}"
    if route:
        discover = f"Use the payload-local `{ad}` CLI (targets the shared coordinator moniker) "
    else:
        discover = (
            "Use the payload-local `agent-dispatch` CLI without `--url` so each "
            "command resolves the live local coordinator "
        )
    return (
        f"You are an agent-dispatch task worker (worker id: {worker_id}). "
        f"A task has been queued for you. {discover}for each command. "
        f"Steps: (1) read it with `{ad} show {task_id}`; "
        f"(2) claim it with `{ad} claim {task_id} --worker {worker_id}` "
        f"(add `--capability <cap>` for each capability the task requires); "
        f"(3) `{ad} start {task_id} {worker_id}`, then run "
        f"`{ad} steer take {task_id} {worker_id} --all` and incorporate any "
        f"pending operator guidance before doing the work described in the "
        f"task's prompt/payload; "
        f"(4) `{ad} complete {task_id} {worker_id} --result-ref <ref>`. "
        f"On a recoverable snag, `{ad} yield {task_id} {worker_id} "
        f"--note <why>` returns it to the queue."
    )


def spawn_worker(
    task_id: str,
    *,
    agent: str = DEFAULT_WORKER_AGENT,
    worker_id: str,
    prompt: str | None = None,
    route: str = "",
    target_dir: str | None = None,
    worktree_id: str | None = None,
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
    the seed it wants delivered verbatim. ``route`` is threaded into the default
    seed only (ignored when ``prompt`` is supplied).

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
        prompt = worker_prompt(task_id, worker_id=worker_id, route=route)
    cmd = [*exe]
    if json_output:
        cmd.append("--json")
    cmd += ["create"]
    if target_dir:
        cmd += ["--target-dir", target_dir]
    if worktree_id:
        cmd += ["--worktree-id", worktree_id]
    cmd += [agent, prompt]
    if not wait:
        cmd.append("--no-wait")
    return subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
        cmd, check=False, capture_output=True, text=True, timeout=timeout,
        **no_window_kwargs(),
    )


def spawn_or_resume_worker(
    task_id: str,
    *,
    agent: str = DEFAULT_WORKER_AGENT,
    worker_id: str,
    prompt: str,
    prior_session_id: str | None = None,
    liveness_fn: Callable[[str], str] | None = None,
    target_dir: str | None = None,
    worktree_id: str | None = None,
    wait: bool = True,
    json_output: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Resume a carried bridge session when safe, otherwise create one.

    Unknown liveness and a live session that rejects the prompt both fail
    closed. A confirmed-gone session is offered one resume attempt first because
    a stopped ACP session remains reusable; only a failed resume after a
    confirmed-gone verdict permits creating a replacement.
    """
    if prior_session_id:
        verdict = liveness_fn(prior_session_id) if liveness_fn else "unknown"
        if verdict == "unknown":
            raise BridgeUnavailable(
                f"could not determine carried session liveness: {prior_session_id}"
            )
        if resume_worker(
            prior_session_id,
            prompt,
            wait=wait,
            timeout=timeout,
        ):
            return subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(
                    {"session_id": prior_session_id, "reused": True}
                ),
                stderr="",
            )
        if verdict != "gone":
            raise BridgeUnavailable(
                f"carried session remains live and cannot accept work: "
                f"{prior_session_id}"
            )
    return spawn_worker(
        task_id,
        agent=agent,
        worker_id=worker_id,
        prompt=prompt,
        target_dir=target_dir,
        worktree_id=worktree_id,
        wait=wait,
        json_output=json_output,
        timeout=timeout,
    )


def stop_worker(session_id: str, *, timeout: float | None = 20.0) -> bool:
    """Stop one local headless body while preserving its resumable ACP session."""
    exe = _agent_bridge_launch_prefix()
    if exe is None:
        return False
    completed = subprocess.run(  # noqa: S603 -- fixed argv + validated id
        [*exe, "stop", session_id],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        **no_window_kwargs(),
    )
    return completed.returncode == 0


def end_worker(session_id: str, *, timeout: float | None = 20.0) -> bool:
    """End one local headless body only while it remains idle or stopped."""
    exe = _agent_bridge_launch_prefix()
    if exe is None:
        return False
    completed = subprocess.run(  # noqa: S603 -- fixed argv + validated id
        [*exe, "end", session_id, "--if-idle"],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        **no_window_kwargs(),
    )
    return completed.returncode == 0


def resume_worker(
    session_id: str,
    prompt: str,
    *,
    wait: bool = False,
    timeout: float | None = 20.0,
) -> bool:
    """Resume an existing stopped ACP session and enqueue its next task turn."""
    exe = _agent_bridge_launch_prefix()
    if exe is None:
        return False
    cmd = [*exe, "send", session_id, "--prompt-file", "-"]
    if not wait:
        cmd.append("--no-wait")
    completed = subprocess.run(  # noqa: S603 -- fixed argv + validated id
        cmd,
        input=prompt,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        **no_window_kwargs(),
    )
    return completed.returncode == 0


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


def redrive_embodied_worker(
    worktree: str,
    prompt: str,
    *,
    machine: str | None = None,
    expected_session_id: str | None = None,
    sender: str = "agent-dispatch-supervisor",
    idempotency_key: str | None = None,
    timeout: float | None = 20.0,
) -> bool:
    """Deliver a work-bearing prompt to an already-live embodied worker.

    Used by the supervisor after a restart/cutover when the spawn reservation says
    an embody worker exists, the live-session registry confirms a worker is still
    there, but the task remains queued/unclaimed because the startup seed was
    lost or never resumed. The prompt is delivered by worktree handle, guarded by
    the resolved live session id when available, and queued if the worker is busy.
    """
    bridge_argv = [
        "agent-bridge",
        "send",
        "--no-wait",
        "--queue",
        "--kind",
        "prompt",
        "--sender",
        sender,
        *(
            ["--idempotency-key", idempotency_key]
            if idempotency_key
            else []
        ),
        *(
            ["--expected-session-id", expected_session_id]
            if expected_session_id
            else []
        ),
        worktree,
        "--prompt-file",
        "-",
    ]
    local_machine = remote_dispatch.local_machine()
    is_remote = (
        machine is not None
        and local_machine is not None
        and machine.casefold() != local_machine.casefold()
    )
    if is_remote:
        ssh = shutil.which("ssh")
        if ssh is None:
            return False
        remote_cmd = " ".join(shlex.quote(arg) for arg in bridge_argv)
        cmd = [
            ssh,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=3",
            machine.lower(),
            remote_cmd,
        ]
    else:
        exe = _agent_bridge_launch_prefix()
        if exe is None:
            return False
        cmd = [*exe, *bridge_argv[1:]]
    try:
        if is_remote:
            proc = run_ssh_command(cmd, input=prompt, timeout=timeout)
        else:
            proc = subprocess.run(  # noqa: S603 -- fixed local argv
                cmd,
                input=prompt,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
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
    owner_session_id: str | None = None,
    idempotency_key: str | None = None,
    timeout: float | None = 20.0,
) -> bool:
    """Resume a task owner immediately after an operator submits steering.

    Unlike :func:`send_nudge`, this is a work-bearing ``prompt`` delivery. The
    bridge queues it when the owner is busy so the current turn is preserved and
    the steering prompt runs next. The canonical ``machine/worktree`` owner is
    split at this boundary: the bare worktree is the bridge address, and a
    remote machine is reached over SSH. Delivery is best-effort: the steer
    itself is already durable in agent-dispatch, so an unavailable bridge or
    failed send returns ``False`` without consuming or losing the operator's
    answer.
    """
    if not owner_session_id:
        return False
    machine, separator, worktree = owner.partition("/")
    prompt = message or (
        f"The operator answered your card on task {task_id}. Resume, run "
        f"`agent-dispatch steer take {task_id} --all` to read every pending "
        f"answer, and continue "
        f"toward your goal."
    )
    if not separator or not machine or not worktree:
        return resume_worker(owner_session_id, prompt, timeout=timeout)
    bridge_argv = [
        "agent-bridge",
        "send",
        "--no-wait",
        "--queue",
        "--kind",
        "prompt",
        "--sender",
        "agent-dispatch-steer",
        *(
            ["--idempotency-key", idempotency_key]
            if idempotency_key
            else []
        ),
        "--expected-session-id",
        owner_session_id,
        worktree,
        "--prompt-file",
        "-",
    ]
    local_machine = remote_dispatch.local_machine()
    is_remote = (
        local_machine is not None
        and machine.casefold() != local_machine.casefold()
    )
    if is_remote:
        ssh = shutil.which("ssh")
        if ssh is None:
            return False
        remote_cmd = " ".join(shlex.quote(arg) for arg in bridge_argv)
        cmd = [
            ssh,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=3",
            machine.lower(),
            remote_cmd,
        ]
    else:
        exe = _agent_bridge_launch_prefix()
        if exe is None:
            return False
        cmd = [*exe, *bridge_argv[1:]]
    try:
        if is_remote:
            proc = run_ssh_command(cmd, input=prompt, timeout=timeout)
        else:
            proc = subprocess.run(  # noqa: S603 -- fixed local argv
                cmd,
                input=prompt,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                **no_window_kwargs(),
            )
    except (subprocess.SubprocessError, OSError):
        return False
    return proc.returncode == 0


def parse_agents(out: str | None) -> list[dict] | None:
    """Extract agent records from ``agent-bridge --json agents`` stdout.

    ``agent-bridge`` may print a human preamble line before the JSON array, so we
    locate the first ``[`` and ``raw_decode`` from there. Returns ``None`` --
    meaning *indeterminate*, not *empty* -- when the payload is missing or
    unparseable.
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
    return [entry for entry in data if isinstance(entry, dict)]


def parse_agent_names(out: str | None) -> set[str] | None:
    """Extract agent ``name`` values from ``agent-bridge --json agents`` stdout."""
    rows = parse_agents(out)
    if rows is None:
        return None
    return {
        name
        for row in rows
        if isinstance((name := row.get("name")), str) and name
    }


def registered_agents(*, timeout: float = 8.0) -> list[dict] | None:
    """Best-effort agent records from the **local** agent-bridge.

    Returns ``None`` (indeterminate) whenever the registry can't be read -- the
    bridge CLI is absent, the command exits non-zero, times out, or emits
    unparseable output. Never raises.
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
    return parse_agents(proc.stdout)


def registered_agent_names(*, timeout: float = 8.0) -> set[str] | None:
    """Best-effort set of names registered with the local agent-bridge."""
    rows = registered_agents(timeout=timeout)
    if rows is None:
        return None
    return {
        name
        for row in rows
        if isinstance((name := row.get("name")), str) and name
    }


def registered_agent_project(agent: str, *, timeout: float = 8.0) -> str | None:
    """Return a registered local agent's explicit project, when available."""
    rows = registered_agents(timeout=timeout)
    if rows is None:
        return None
    for row in rows:
        if row.get("name") != agent:
            continue
        project = row.get("project")
        return project if isinstance(project, str) and project else None
    return None


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
