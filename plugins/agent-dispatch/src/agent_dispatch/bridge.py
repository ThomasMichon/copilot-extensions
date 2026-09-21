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

from . import bridge_remote, remote_dispatch
from .procutil import (
    agent_bridge_launch_prefix,
    no_window_kwargs,
    run_ssh_command,
)

DEFAULT_WORKER_AGENT = "task-worker"


class BridgeUnavailable(RuntimeError):
    """Raised when the agent-bridge CLI is not available on this host."""


class BridgeCarriedSessionBusy(BridgeUnavailable):
    """Raised when a carried session (same exclusive key, prior attempt) is
    confirmed still live/busy -- not gone -- and declines a resume.

    This is a **legitimate deferral**, not a spawn failure: the prior body is
    genuinely still doing work. Callers must not treat it like an ordinary
    :class:`BridgeUnavailable` (which the supervisor fails the reservation
    for, burning a dead-letter-counted attempt); they should instead defer the
    reservation (see :meth:`agent_dispatch.queue.TaskQueue.defer_spawn`) so a
    fresh attempt re-checks liveness next cycle without being counted as a
    failure.
    """


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


def _record_is_direct_spawn_target(record: dict | None) -> bool:
    """Whether an agent-bridge metadata record is a first-class direct target."""
    if not isinstance(record, dict):
        return True
    return bool(record.get("spawnable_as_target", True))


def _spawn_worker_via_worktree(
    *,
    exe: list[str],
    task_id: str,
    agent: str,
    worker_id: str,
    prompt: str,
    route: str,
    project: str | None,
    target_dir: str | None,
    worktree_id: str | None,
    reclaim: bool,
    wait: bool,
    json_output: bool,
    timeout: float | None,
) -> subprocess.CompletedProcess:
    """Embody a bound-charter worker by worktree handle, not by charter target.

    Phase 4 of agent-bridge-worktree-native-agents: a charter is a spawn
    *profile*, not a direct target. The worktree is created/resolved first
    (binding ``--agent <charter>`` at the ground layer), then agent-bridge is
    addressed by the **worktree handle** so the bound charter's spawn shape is
    auto-applied by Phase 3's ``_apply_bound_charter()`` path.
    """
    from . import embody

    if worktree_id:
        ensured_worktree_id = worktree_id
    else:
        if not project:
            raise BridgeUnavailable(
                f"headless agent profile {agent!r} requires a project/worktree "
                "context so agent-dispatch can create a bound worktree first"
            )
        created = embody.create_worktree(
            project=project,
            interface="bridge",
            task_id=task_id,
            reservation_key=f"bridge:{worker_id}",
            attempt=1,
            driver="agent-dispatch",
            supervisor="bridge",
            timeout=timeout,
            agent=agent,
        )
        ensured_worktree_id = str(created["worktree"])

    resume_cmd = [*exe, "resume", ensured_worktree_id]
    if reclaim:
        resume_cmd.append("--force")
    resumed = subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
        resume_cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        **no_window_kwargs(),
    )
    if resumed.returncode != 0:
        return resumed

    send_cmd = [*exe, "send", ensured_worktree_id, "--prompt-file", "-", "--caller"]
    send_cmd.append(f"agent-dispatch:{worker_id}")
    if not wait:
        send_cmd.append("--no-wait")
    sent = subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
        send_cmd,
        input=prompt,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        **no_window_kwargs(),
    )
    if sent.returncode != 0 or not json_output:
        return sent

    resolved = subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
        [
            *exe,
            "--json",
            "live-sessions",
            "resolve",
            "--handle",
            ensured_worktree_id,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        **no_window_kwargs(),
    )
    if resolved.returncode == 0:
        try:
            live = json.loads((resolved.stdout or "").strip() or "{}")
        except (ValueError, TypeError):
            live = {}
        if isinstance(live, dict):
            session_id = live.get("session_id")
            if session_id:
                return subprocess.CompletedProcess(
                    args=sent.args,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "session_id": session_id,
                            "worktree": ensured_worktree_id,
                            "reused": False,
                        }
                    ),
                    stderr=sent.stderr,
                )
    return subprocess.CompletedProcess(
        args=sent.args,
        returncode=0,
        stdout=json.dumps({"worktree": ensured_worktree_id}),
        stderr=sent.stderr,
    )


def spawn_worker(
    task_id: str,
    *,
    agent: str = DEFAULT_WORKER_AGENT,
    worker_id: str,
    prompt: str | None = None,
    route: str = "",
    project: str | None = None,
    target_dir: str | None = None,
    worktree_id: str | None = None,
    reclaim: bool = False,
    wait: bool = True,
    json_output: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Spawn a worker agent via agent-bridge to claim + execute ``task_id``.

    Runs either:

    - ``agent-bridge [--json] create <agent> "<prompt>" --caller <id>
      [--no-wait]`` for a **venue/direct target**; or
    - for a hidden worktree-bound charter profile, creates/resolves the
      target worktree first and then routes through ``resume <worktree>`` +
      ``send <worktree>`` so agent-bridge applies the bound charter's spawn
      shape from the worktree.

    Raises :class:`BridgeUnavailable` if the needed CLI/runtime is unavailable;
    the caller degrades by leaving the task queued.

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

    ``reclaim`` is agent-dispatch's own judgment that ``worktree_id`` is stale
    and safe to take over (e.g. an unclaimed handoff task past a bounded
    reconciliation window) -- it never implements the in-place replacement
    itself; that is agent-bridge's job. Since ``create --reclaim`` was removed
    (agent-bridge-cold-resume Phase 3: a create into an occupied worktree has
    no bypass of its own), this delegates to
    :func:`bridge_reclaim.resume_worktree_and_send`: resume ``worktree_id``
    (creating a fresh owned session if none exists, or reusing a live one),
    then ``send`` it the seed -- the two-call equivalent of the old single
    ``create --reclaim`` invocation. Deliberately never forces past a genuine
    live interactive CLI holder (see that module's docstring) -- agent-dispatch
    judged the *task* stale, never that a human's own attached session should
    be torn out from under them.

    ``--caller`` (copilot-extensions#2202): without an explicit caller, `create`
    derives one from the *current process's own* worktree context -- meaningless
    for a long-running supervisor daemon that isn't itself running inside any
    single worktree. That leaves ``caller_worktree`` unset on the spawned target,
    so agent-worktrees' ``resolved_origin`` falls through to ``"user"`` (Picker-
    visible, freely drivable) instead of ``"delegate"`` (Picker-hidden) -- every
    autopilot worker this function spawns looked exactly like a worktree the
    operator created themselves, inviting accidental manual takeover of a live,
    task-owning worker. A stable, task-attempt-scoped synthetic identity here
    (it need not resolve to a real worktree; the origin check is presence-only)
    fixes that regardless of the daemon's own execution context.
    """
    exe = _agent_bridge_launch_prefix()
    if exe is None:
        raise BridgeUnavailable("agent-bridge CLI not found on PATH")
    if prompt is None:
        prompt = worker_prompt(task_id, worker_id=worker_id, route=route)
    record = _resolve_agent_record(
        agent,
        timeout=min(timeout, 8.0) if timeout is not None else 8.0,
        include_unaddressable=True,
    )
    if (
        record not in (None, _AGENT_NOT_FOUND)
        and isinstance(record, dict)
        and not _record_is_direct_spawn_target(record)
    ):
        return _spawn_worker_via_worktree(
            exe=exe,
            task_id=task_id,
            agent=agent,
            worker_id=worker_id,
            prompt=prompt,
            route=route,
            project=project,
            target_dir=target_dir,
            worktree_id=worktree_id,
            reclaim=reclaim,
            wait=wait,
            json_output=json_output,
            timeout=timeout,
        )
    if reclaim:
        if not worktree_id:
            raise ValueError("reclaim=True requires worktree_id")
        from . import bridge_reclaim

        return bridge_reclaim.resume_worktree_and_send(
            worktree_id, prompt, exe=exe, agent=agent,
            caller=f"agent-dispatch:{worker_id}", wait=wait,
            json_output=json_output, timeout=timeout,
        )
    cmd = [*exe]
    if json_output:
        cmd.append("--json")
    cmd += ["create"]
    if target_dir:
        cmd += ["--target-dir", target_dir]
    if worktree_id:
        cmd += ["--worktree-id", worktree_id]
    cmd += [agent, prompt]
    cmd += ["--caller", f"agent-dispatch:{worker_id}"]
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
    project: str | None = None,
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
            raise BridgeCarriedSessionBusy(
                f"carried session remains live and cannot accept work: "
                f"{prior_session_id}"
            )
    return spawn_worker(
        task_id,
        agent=agent,
        worker_id=worker_id,
        prompt=prompt,
        project=project,
        target_dir=target_dir,
        worktree_id=worktree_id,
        wait=wait,
        json_output=json_output,
        timeout=timeout,
    )


def stop_worker(session_id: str, *, timeout: float | None = 20.0) -> bool:
    """Stop a local body and reap its host when the peer supports that option.

    Independently installed older Agent Bridge builds do not know
    ``--reap-host``. Only argparse's exact unsupported-option response permits
    a compatibility retry with ordinary stop; every operational failure remains
    a hard failure.
    """
    exe = _agent_bridge_launch_prefix()
    if exe is None:
        return False
    completed = subprocess.run(  # noqa: S603 -- fixed argv + validated id
        [*exe, "stop", session_id, "--reap-host"],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        **no_window_kwargs(),
    )
    output = f"{completed.stderr or ''}\n{completed.stdout or ''}".lower()
    if (
        completed.returncode == 2
        and (
            "unrecognized arguments: --reap-host" in output
            or "unrecognized argument: --reap-host" in output
        )
    ):
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


def force_end_session(session_id: str, *, timeout: float | None = 20.0) -> bool:
    """Unconditionally end ANY local session (headless ACP or interactive
    CLI) via ``agent-bridge end <id> --force`` -- torn down even with active
    background work, unlike :func:`end_worker`'s idle-only ``--if-idle``.

    The execution primitive behind Phase 2's ``force-stop`` CLI verb: the
    operator's deliberate "stop it now" action, distinct from the durable
    :meth:`agent_dispatch.queue.TaskQueue.set_hold` pause (which does not
    itself terminate a live session). Returns ``False`` (never raises) on any
    failure -- no bridge on PATH, an unknown session id, a transport error --
    so the caller can still fence the task's own state transition even when
    the live session could not be confirmed torn down.
    """
    exe = _agent_bridge_launch_prefix()
    if exe is None:
        return False
    try:
        completed = subprocess.run(  # noqa: S603 -- fixed argv + validated id
            [*exe, "end", session_id, "--force"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        # The docstring promises this never raises -- an unlaunchable binary
        # or a stuck/timed-out process is exactly the "transport error"
        # case callers (force-stop's fenced suspend) already expect to
        # degrade to `False` (PR #2913 review).
        return False
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


def resume_session(
    session_id: str,
    prompt: str,
    *,
    host: str | None = None,
    wait: bool = False,
    timeout: float | None = 20.0,
) -> bool:
    """Resume a stopped ACP session by exact session id, local or fleet-hosted.

    ``host`` is the pool host a fleet body's session lives on (parsed from a
    ``fleet-body:<host>:<session>`` reservation handle -- see
    :mod:`agent_dispatch.spawn_factories`); omit it for a local body, which
    delegates straight to :func:`resume_worker`. A fleet resume mirrors that
    same ``agent-bridge send`` shape over SSH -- the same mesh
    :func:`redrive_embodied_worker` uses -- rather than adding a second bridge
    transport. Used by :mod:`agent_dispatch.reattach` to deliver the resume
    prompt once a reattached task is claimed/started/bound, regardless of
    which host the recovered session actually lives on.
    """
    if host is None:
        try:
            return resume_worker(session_id, prompt, wait=wait, timeout=timeout)
        except (subprocess.SubprocessError, OSError):
            return False
    normalized_host = bridge_remote.normalize_host(host)
    ssh = shutil.which("ssh")
    if ssh is None:
        return False
    cmd = ["agent-bridge", "send", session_id, "--prompt-file", "-"]
    if not wait:
        cmd.append("--no-wait")
    remote_cmd = " ".join(shlex.quote(arg) for arg in cmd)
    try:
        proc = run_ssh_command(
            [ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", normalized_host, remote_cmd],
            input=prompt,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return proc.returncode == 0


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
    normalized_machine = (
        bridge_remote.normalize_host(machine) if machine is not None else None
    )
    local_machine = remote_dispatch.local_machine()
    normalized_local_machine = (
        bridge_remote.normalize_host(local_machine)
        if local_machine is not None
        else None
    )
    is_remote = (
        normalized_machine is not None
        and normalized_local_machine is not None
        and normalized_machine != normalized_local_machine
    )
    if is_remote:
        try:
            bridge_remote.LocalBridgeRemoteClient().send_live_message(
                normalized_machine,
                worktree,
                sender=sender,
                message=prompt,
                kind="prompt",
                expected_session_id=expected_session_id,
                idempotency_key=idempotency_key,
                timeout=timeout if timeout is not None else 20.0,
            )
            return True
        except bridge_remote.RemoteBridgeUnavailable:
            pass
        except bridge_remote.RemoteBridgeOperationError:
            return False
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
            normalized_machine,
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
    machine = bridge_remote.normalize_host(machine)
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
        and machine != bridge_remote.normalize_host(local_machine)
    )
    if is_remote:
        try:
            bridge_remote.LocalBridgeRemoteClient().send_live_message(
                machine,
                worktree,
                sender="agent-dispatch-steer",
                message=prompt,
                kind="prompt",
                expected_session_id=owner_session_id,
                idempotency_key=idempotency_key,
                timeout=timeout if timeout is not None else 20.0,
            )
            return True
        except bridge_remote.RemoteBridgeUnavailable:
            pass
        except bridge_remote.RemoteBridgeOperationError:
            return False
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


def registered_agents(*, timeout: float = 20.0) -> list[dict] | None:
    """Best-effort agent records from the **local** agent-bridge.

    Returns ``None`` (indeterminate) whenever the registry can't be read -- the
    bridge CLI is absent, the command exits non-zero, times out, or emits
    unparseable output. Never raises.

    ``timeout`` defaults generously (20s, was 8s): ``agent-bridge agents``
    enumerates every registered namespace provider (e.g. CodeSpaces across
    mapped GitHub accounts, or Docker containers), and even with agent-bridge's
    own short-TTL namespace-list cache warm, a cold cache or a provider having
    a genuinely slow moment can still take several seconds. A too-tight
    timeout here silently degrades a real registry read to "indeterminate",
    which upstream callers may then dead-letter a spawn reservation on -- this
    is defense-in-depth headroom, not a substitute for the bridge-side caching
    fix.

    Prefer :func:`registered_agent` when only a single, known agent name needs
    checking (the common case: spawn preflight, headless-lane resolution) --
    it never enumerates namespace resolvers at all, so it is not subject to
    CodeSpace/container latency in the first place. Use this full listing only
    when the actual set of *all* registered agents is needed.
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


class _NotFound:
    """Sentinel: ``agent-show`` positively confirmed no such agent -- distinct
    from ``None`` (indeterminate: bridge absent, timeout, crash, unparseable).
    A caller that raises on indeterminate must not also raise on a
    legitimately-absent agent; keeping these two outcomes distinguishable is
    the whole point of this sentinel."""


_AGENT_NOT_FOUND = _NotFound()


class _Unsupported:
    """Sentinel: the installed agent-bridge CLI does not recognize
    ``agent-show`` (argparse rejects it, exit 2) -- version skew, since
    agent-bridge and agent-dispatch are independently-updated plugins and an
    agent-dispatch update can land before its paired agent-bridge one.
    Distinct from a genuine indeterminate failure: callers must fall back to
    the full listing here, not treat every local allocation as unreadable."""


_AGENT_SHOW_UNSUPPORTED = _Unsupported()


def registered_agent(
    name: str,
    *,
    timeout: float = 8.0,
    include_unaddressable: bool = False,
) -> dict | _NotFound | _Unsupported | None:
    """Best-effort single-agent record via agent-bridge's fast ``agent-show``
    lookup -- a static/topology-only lookup that never enumerates namespace
    resolvers (CodeSpaces, containers), unlike :func:`registered_agents`.

    Returns:
    - a ``dict`` record when ``name`` is a registered (non-namespace-prefixed)
      agent;
    - :data:`_AGENT_NOT_FOUND` when the registry was read successfully and
      confirmed no such agent (``agent-show`` exits 1) -- a legitimate,
      non-error outcome;
    - :data:`_AGENT_SHOW_UNSUPPORTED` when the installed agent-bridge CLI
      doesn't recognize ``agent-show`` yet (argparse exit 2) -- version skew;
      callers should fall back to :func:`registered_agents`, not treat this
      as indeterminate;
    - ``None`` (indeterminate) when the registry could not be read at all
      (bridge CLI absent, spawn error, timeout, crash, unparseable output).

    Cannot resolve a namespace-prefixed name (``codespace:foo``,
    ``container:bar``) -- ``agent-show`` deliberately does not enumerate
    namespace resolvers, which is exactly the latency this fast path exists
    to avoid. Callers needing a namespace-resolved agent, or one this
    returned :data:`_AGENT_SHOW_UNSUPPORTED` for, should use
    :func:`registered_agents` (or the ``_resolve_agent_record`` wrapper below,
    which does this automatically).
    """
    exe = _agent_bridge_launch_prefix()
    if exe is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe resolved above
            [
                *exe,
                "--json",
                "agent-show",
                name,
                *(["--include-unaddressable"] if include_unaddressable else []),
            ],
            check=False, capture_output=True, text=True, timeout=timeout,
            **no_window_kwargs(),
        )
    except (subprocess.SubprocessError, OSError):
        return None
    stderr = proc.stderr or ""
    if proc.returncode == 2 and (
        "agent-show" in stderr or "--include-unaddressable" in stderr
    ):
        return _AGENT_SHOW_UNSUPPORTED
    if proc.returncode == 1:
        return _AGENT_NOT_FOUND
    if proc.returncode != 0:
        return None
    text = (proc.stdout or "").strip()
    if not text or text == "null":
        return _AGENT_NOT_FOUND
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _resolve_agent_record(
    name: str,
    *,
    timeout: float,
    fallback_timeout: float = 20.0,
    include_unaddressable: bool = False,
) -> dict | _NotFound | None:
    """Resolve one agent's record, preferring the fast single-agent lookup
    and transparently falling back to the full listing when the fast path
    can't answer for this ``name``:

    - a namespace-prefixed name (``codespace:foo``) -- ``agent-show`` never
      enumerates namespace resolvers, so it can never resolve one;
    - :data:`_AGENT_SHOW_UNSUPPORTED` -- the installed agent-bridge CLI
      predates ``agent-show`` (version skew across independently-updated
      plugins).

    ``fallback_timeout`` is deliberately more generous than ``timeout``: the
    full listing legitimately waits on namespace-resolver enumeration, which
    the fast path exists specifically to avoid.
    """
    if ":" not in name:
        row = registered_agent(
            name,
            timeout=timeout,
            include_unaddressable=include_unaddressable,
        )
        if row is not _AGENT_SHOW_UNSUPPORTED:
            return row
    rows = registered_agents(timeout=max(timeout, fallback_timeout))
    if rows is None:
        return None
    for row in rows:
        if row.get("name") == name:
            return row
    return _AGENT_NOT_FOUND


def agent_is_registered(
    name: str, *, timeout: float = 8.0, include_unaddressable: bool = False
) -> bool | None:
    """Best-effort "is ``name`` a registered agent?" -- prefers the fast
    single-agent path, falling back to the full listing for namespace-
    prefixed names or an agent-bridge CLI that predates ``agent-show`` (see
    :func:`_resolve_agent_record`). Returns ``None`` (indeterminate) when the
    registry could not be read at all."""
    row = _resolve_agent_record(
        name, timeout=timeout, include_unaddressable=include_unaddressable
    )
    if row is None:
        return None
    return row is not _AGENT_NOT_FOUND


def registered_agent_names(*, timeout: float = 20.0) -> set[str] | None:
    """Best-effort set of names registered with the local agent-bridge."""
    rows = registered_agents(timeout=timeout)
    if rows is None:
        return None
    return {
        name
        for row in rows
        if isinstance((name := row.get("name")), str) and name
    }


def registered_agent_project(
    agent: str,
    *,
    timeout: float = 8.0,
    strict: bool = False,
) -> str | None:
    """Return a registered agent's explicit project, when available.

    Prefers the fast single-agent lookup, which resolves purely from static/
    topology config and never enumerates namespace resolvers -- but
    transparently falls back to the full listing for a namespace-prefixed
    name or an agent-bridge CLI predating ``agent-show`` (see
    :func:`_resolve_agent_record`), so this remains correct for every caller,
    not just the common plain-local-agent case. ``timeout`` defaults to 8s
    (was 20s): the fast path has no namespace-enumeration latency to size
    for; a fallback to the full listing uses a more generous timeout of its
    own regardless.
    """
    row = _resolve_agent_record(
        agent, timeout=timeout, include_unaddressable=True
    )
    if row is None:
        if strict:
            raise BridgeUnavailable(
                f"could not read the local agent registry while resolving {agent!r}"
            )
        return None
    if row is _AGENT_NOT_FOUND:
        return None
    project = row.get("project")
    return project if isinstance(project, str) and project else None


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
    host, so each is probed over SSH; otherwise the local registry is probed via
    the fast single-agent path (:func:`agent_is_registered`), which skips
    namespace/CodeSpace/container enumeration for the common case (a plain
    local/SSH-topology name) and transparently falls back to the full listing
    for a namespace-prefixed name or version-skewed agent-bridge CLI -- see
    :func:`_resolve_agent_record`. ``local_timeout`` defaults to 8s
    accordingly (was 20s, sized for the full listing this no longer calls in
    the common case).
    """
    checks: list[tuple[str, dict | _NotFound | None]] = []
    if pool:
        from . import embody

        for host in pool:
            h = host.strip()
            if not h:
                continue
            checks.append(
                (
                    h,
                    embody.remote_registered_agent_record(
                        h, agent, timeout=remote_timeout
                    ),
                )
            )
    else:
        row = _resolve_agent_record(
            agent,
            timeout=local_timeout,
            include_unaddressable=True,
        )
        checks.append(("this host", row))
    warnings: list[str] = []
    for where, record in checks:
        if record is _AGENT_NOT_FOUND:
            warnings.append(
                f"agent-dispatch supervise: WARNING -- headless embody agent "
                f"{agent!r} is not registered with agent-bridge on {where}; "
                f"headless spawns for this lane will fail ({agent!r} is not a known "
                f"agent name) and dead-letter. Register it (e.g. add a body to "
                f"acp-agents.json) or set --headless-agent to a registered agent."
            )
        elif isinstance(record, dict) and bool(record.get("managed")):
            warnings.append(
                f"agent-dispatch supervise: WARNING -- headless embody agent "
                f"{agent!r} resolves on {where} but is managed (non-spawnable as a "
                "bound charter); headless spawns for this lane will fail. Use a "
                "non-managed charter profile or a venue target instead."
            )
    return warnings
