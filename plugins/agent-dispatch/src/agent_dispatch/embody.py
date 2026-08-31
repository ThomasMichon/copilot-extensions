"""agent-worktrees integration: dispatch a task to a CLI-backed autopilot session.

Unlike :mod:`agent_dispatch.bridge` (which spawns a *headless* agent-bridge ACP
worker), this spawns a durable, **CLI-backed autopilot** session in a fresh
parallel worktree on the same machine via ``agent-worktrees embody``. The
embodied Copilot launches with ``--allow-all-tools`` (tools auto-approved -- no
per-tool confirmation prompts), claims and starts the task, works it
autonomously, and marks the task ``completed`` **explicitly** only when it judges
the goal reached -- *deferred completion*, never stamped at spawn or pickup.

agent-dispatch stays decoupled: it shells out to the ``agent-worktrees`` runtime
(its venv interpreter via ``-m agent_worktrees`` when present, else the binstub
on PATH) and degrades gracefully (the caller falls back to the bridge backend,
or leaves the task queued) when it is not -- so the plugin remains standalone on
a host without agent-worktrees.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess

from .procutil import agent_worktrees_launch_prefix, no_window_kwargs

DEFAULT_DRIVER = "agent-dispatch"


def project_for_task(task: dict) -> str | None:
    """Resolve a task's lane to a local **project name** for embody's ``--project``.

    A supervisor / fleet spawner runs CWD-neutral (its working directory is a
    service runtime dir or an SSH login CWD, not the repo), so it must name the
    project explicitly rather than rely on git-like CWD discovery -- see the
    ``project-scoped-invocation`` pattern. Preference: the registry's
    authoritative reverse-mapping of the canonical lane
    (``identity.name_for_repo``); failing that, the lane's final path segment
    (``…/example-user/test-chamber`` -> ``test-chamber``) as a best effort. Returns
    ``None`` only when the task has no lane at all -- the spawn then falls back to
    CWD discovery, which surfaces the misconfiguration loudly for a CWD-neutral
    caller rather than silently embodying the wrong project.
    """
    repo = task.get("repo")
    if not repo:
        return None
    try:
        from .identity import name_for_repo

        name = name_for_repo(repo)
    except Exception:  # identity resolution is best-effort -- never fatal here
        name = None
    if name:
        return name
    tail = repo.rstrip("/").rsplit("/", 1)[-1]
    return tail or None


def parse_handle(result: subprocess.CompletedProcess) -> dict[str, str | None]:
    """Best-effort extract the session/worktree handle from ``embody --json``.

    Returns ``{"session": ..., "worktree": ...}`` (values may be ``None``). Used
    to record a spawn reservation's handle so a supervisor restart can reconcile.
    """
    handle: dict[str, str | None] = {"session": None, "worktree": None}
    try:
        data = json.loads(result.stdout or "{}")
    except (ValueError, TypeError):
        return handle
    if not isinstance(data, dict):
        return handle
    launch = data.get("launch") if isinstance(data.get("launch"), dict) else {}
    worktree_obj = data.get("worktree") if isinstance(data.get("worktree"), dict) else {}
    handle["worktree"] = (
        data.get("worktree_id")
        or worktree_obj.get("id")
        or (data.get("worktree") if isinstance(data.get("worktree"), str) else None)
        or launch.get("worktree_id")
    )
    handle["session"] = (
        data.get("session_id") or data.get("session") or launch.get("session")
    )
    return handle


class EmbodyUnavailable(RuntimeError):
    """Raised when the ``agent-worktrees`` CLI is not available on this host."""


def _agent_worktrees_launch_prefix() -> list[str] | None:
    """Resolve an argv prefix that runs the ``agent-worktrees`` CLI **without**
    routing through a Windows ``.cmd``/``.bat`` shim.

    The autopilot seed handed to ``embody --seed`` contains shell
    metacharacters (``&``, ``(``, ``)``, ``<``, ``>``, backtick). On Windows a
    ``subprocess`` launch of the ``agent-worktrees.cmd`` binstub runs it through
    ``cmd.exe``, whose ``%*`` re-parse treats those characters as command
    operators and corrupts the arguments -- the shim then fails with WinError 2
    ("The system cannot find the file specified"). This is the BatBadBut class
    of bug. Invoking the interpreter directly (``python -m agent_worktrees``)
    bypasses ``cmd.exe`` entirely, so the seed is delivered verbatim.

    Resolve the agent-worktrees runtime interpreter via the **standardized spawn
    flow** (:func:`~agent_dispatch.procutil.resolve_runtime_python` -- the
    canonical versioned-runtime resolver the binstubs use), **not** a hard-coded
    ``.venv`` path (which misses the ``versions/<ver>`` slot layout and then falls
    back to a ``.ps1`` ``subprocess`` cannot exec on Windows). Fall back to the
    ``agent-worktrees`` binstub on PATH only on POSIX (its shims are plain exec
    scripts and do not re-parse). Returns ``None`` when neither is resolvable."""
    return agent_worktrees_launch_prefix()


def embody_available() -> bool:
    """True if the ``agent-worktrees`` CLI can be launched on this host."""
    return _agent_worktrees_launch_prefix() is not None


def autopilot_worker_prompt(task_id: str, *, worker_id: str, route: str = "") -> str:
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

    ``route`` is the coordinator **routing intent** to bake into the worker's
    ``agent-dispatch`` commands, as a leading flag fragment (``""`` for the
    default local coordinator, ``" --shared"``, or ``" --url <endpoint>"``).
    The default (``""``) deliberately carries **no** endpoint so each command
    rediscovers the live local coordinator -- that is what makes a zero-downtime
    coordinator port cutover transparent to a long-running dispatcher. A stable
    explicit target (``--url``) or the env-configured ``--shared`` endpoint is
    preserved so a task created on a non-default coordinator is still reachable.
    """
    ad = f"agent-dispatch{route}"
    if route:
        route_note = (
            f"Use the `{ad}` CLI commands exactly as shown below so every command "
            f"targets the same coordinator this task lives on. "
        )
    else:
        route_note = (
            "Use the payload-local `agent-dispatch` CLI commands exactly as shown "
            "below, without `--url`; the CLI resolves the live local coordinator "
            "endpoint for each command (transparent to a coordinator port change). "
        )
    return (
        f"You are a dispatched agent-dispatch **autopilot** worker (worker id: "
        f"{worker_id}), running in a fresh parallel worktree with tools "
        f"auto-approved (--allow-all-tools). A task has been queued for you. "
        f"{route_note}Work the task end-to-end, "
        f"autonomously, without waiting for a human. Claim it under this "
        f"worktree's own identity (no owner argument -- the coordinator resolves "
        f"machine/worktree), which keeps the task trackable as your live "
        f"session. This is a **contract-net evaluation**: you win an exclusive, "
        f"tight-lease EVALUATION window first, decide whether the task is really "
        f"yours to do, and only THEN commit to running it. Steps: "
        f"(1) read it with `{ad} show {task_id}`; "
        f"(2) claim it for evaluation with "
        f"`{ad} claim --task {task_id} --evaluation` "
        f"(add `--capability <cap>` for each capability the task requires) -- "
        f"this takes a SHORT evaluation lease, not the full work lease; "
        f"(3) **EVALUATE before committing** -- while you hold the evaluation "
        f"window, assess: (a) DUPLICATE check -- sweep open tasks "
        f"(`{ad} list`) and any active worktree charters for an "
        f"equivalent already queued, claimed, or in progress; (b) FEASIBILITY -- "
        f"is the task well-formed and doable from here; (c) IS-THIS-FOR-ME -- do "
        f"your machine/worktree/capabilities actually fit it; "
        f"(4a) on ACCEPT, `{ad} start {task_id}` (this extends the "
        f"lease from the tight evaluation window to the full work lease), run "
        f"`{ad} steer take {task_id} --all` and incorporate any pending "
        f"operator guidance, then carry out the work as follows. FIRST re-read the task with "
        f"`{ad} show {task_id}` and check whether it carries a durable "
        f"**goal** and **done-criteria** (the `goal` / `done_criteria` fields) "
        f"plus an accumulated **progress log** (the `progress_log` array). "
        f"If it DOES, treat the task as a goal to PURSUE, and RESUME rather than "
        f"restart: read the prior progress log to see what earlier passes already "
        f"accomplished, then continue from there. LOOP: do one unit of work "
        f"toward the goal -> record a progress beat with "
        f"`{ad} progress {task_id} --phase <phase> --summary "
        f"\"<one line>\"` (this now APPENDS to the durable progress log, so a "
        f"replacement worker can resume) -> re-check the done-criteria -> repeat "
        f"until they are genuinely met. If the task carries NO goal/done-criteria "
        f"(a plain one-shot task), just carry out the work described in its "
        f"prompt/payload to completion as usual; "
        f"(4b) if the task is NOT FOR YOU or you hit a transient blocker, decline "
        f"WITHOUT abandoning it: `{ad} yield {task_id} --exclude-self "
        f"worktree --note <why>` returns it to the queue and appends a narrow "
        f"'not me' exclusion so you are not re-offered it (widen to "
        f"`--exclude-self machine` only when the mismatch is machine-wide); "
        f"(4c) if it is a DUPLICATE or obsolete, retire it terminally with "
        f"`{ad} abandon {task_id} --duplicate-of <ref>` (cite the "
        f"existing task/PR/issue) so the dedup is recorded, never a silent drop; "
        f"(5) ONLY once you judge an accepted task's goal genuinely reached (its "
        f"done-criteria met, when it carries them), run "
        f"`{ad} complete {task_id} --result-ref <ref>`. "
        f"Do NOT mark it complete before the goal is met -- completing the task "
        f"is your explicit signal that the work is done. "
        f"**Report progress as you go** so the operator can watch the fleet at a "
        f"glance and so a replacement worker can resume from your recorded "
        f"progress: at each phase boundary (plan settled, implementation done, a "
        f"PR opened, a blocker hit) and at each pass of a goal loop run "
        f"`{ad} progress {task_id} --phase <phase> --summary "
        f"\"<one line toward the goal>\"` (add `--pr <ref>` or `--blocker <why>` "
        f"when relevant). Keep each summary to a single line -- it is a status "
        f"beat, not a transcript; emit one at real transitions, never on a "
        f"timer."
    )


def spawn_embodied_worker(
    task_id: str,
    *,
    worker_id: str,
    driver: str = DEFAULT_DRIVER,
    project: str | None = None,
    route: str = "",
    verify_timeout: int = 0,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Spawn a CLI-backed autopilot worker via ``agent-worktrees embody``.

    Runs ``agent-worktrees [--project <project>] embody --new --seed "<autopilot
    seed>" --driver <driver> --json`` -- creating a fresh parallel worktree and a
    detached mux+Copilot session seeded to claim + execute ``task_id``
    autonomously. The ``--driver`` label stamps the "driven by <agent>" banner so
    the session is legible in Neuron Forge. Raises :class:`EmbodyUnavailable` if
    the ``agent-worktrees`` CLI is not on PATH; the caller degrades from there.

    ``project`` names the target project explicitly (the agent-worktrees
    ``--project`` global). It is **required in practice for a CWD-neutral caller**
    (a service/daemon whose working directory is not inside the repo): without it,
    embody falls back to git-like discovery from CWD and fails with "Could not
    resolve a project for 'embody'". See the ``project-scoped-invocation`` pattern.

    ``verify_timeout`` (seconds) optionally makes embody wait for the mux
    session to come up before returning (0 = don't wait).
    """
    exe_prefix = _agent_worktrees_launch_prefix()
    if exe_prefix is None:
        raise EmbodyUnavailable("agent-worktrees CLI not found on PATH")
    seed = autopilot_worker_prompt(task_id, worker_id=worker_id, route=route)
    cmd = list(exe_prefix)
    if project:
        # `--project` is an agent-worktrees GLOBAL option -- it precedes the
        # `embody` subcommand. It lets a CWD-neutral caller name the target
        # project instead of relying on git-like CWD discovery.
        cmd += ["--project", project]
    cmd += ["embody", "--new", "--seed", seed, "--driver", driver, "--json"]
    if verify_timeout:
        cmd += ["--verify-timeout", str(verify_timeout)]
    return subprocess.run(  # noqa: S603 -- fixed argv, launcher resolved locally
        cmd, check=False, capture_output=True, text=True, timeout=timeout,
        **no_window_kwargs(),
    )


# -- Fleet dispatch (Model C): a remote body that drives the ORIGIN task -------


def fleet_autopilot_worker_prompt(
    task_id: str, *, origin: str, owner: str, worker_id: str
) -> str:
    """Build the autopilot seed for a **fleet-dispatched, remote** embody body.

    Model C: the reservation and the task lease live on the **origin**
    coordinator (fleet-wide at-most-once), and this body -- running on a *pool*
    host, not the origin -- drives the origin task's whole lifecycle back over the
    existing bidirectional SSH mesh, by prefixing every ``agent-dispatch`` verb
    with ``ssh <origin>``. That runs the verb **on** the origin against its own
    local coordinator, so there is **no new network bind** on the origin (its
    control API never leaves loopback).

    Two differences from the local :func:`autopilot_worker_prompt`:

    - **Reach the origin over SSH.** Lifecycle verbs run as
      ``ssh <origin> agent-dispatch <verb> ...`` (the origin is an SSH
      alias, never a raw IP).
    - **Carry an explicit owner.** The CWD-based owner resolution can't work over
      ``ssh <origin>`` (that shell lands in the origin's home dir, not this body's
      worktree), so the body passes the supervisor-assigned **synthetic owner**
      (``{owner}``) on every lease-holding verb. It is an opaque lease-holder id,
      stable for this attempt.
    """
    return (
        f"You are a fleet-dispatched agent-dispatch **autopilot** worker (worker "
        f"id: {worker_id}), running detached in a fresh parallel worktree on this "
        f"pool host with tools auto-approved (--allow-all-tools). Your task was "
        f"scheduled on a DIFFERENT machine -- the origin coordinator on host "
        f"'{origin}'. Drive the task there by running EVERY agent-dispatch "
        f"lifecycle verb over SSH against the origin, ALWAYS passing your explicit "
        f"owner id '{owner}' (your working directory here cannot identify you to "
        f"the origin, so the owner is not optional). Work the task end-to-end, "
        f"autonomously, without waiting for a human. This is a **contract-net "
        f"evaluation**: you win an exclusive, tight-lease EVALUATION window "
        f"first, decide whether the task is really yours to do, and only THEN "
        f"commit to running it. Steps: "
        f"(1) read it: `ssh {origin} agent-dispatch show {task_id}`; "
        f"(2) claim it for evaluation: `ssh {origin} agent-dispatch claim --task "
        f"{task_id} --worker {owner} --evaluation` (add `--capability <cap>` for each "
        f"capability the task requires) -- this takes a SHORT evaluation lease, "
        f"not the full work lease; "
        f"(3) **EVALUATE before committing** -- while you hold the evaluation "
        f"window, assess: (a) DUPLICATE check -- sweep the origin's open tasks "
        f"(`ssh {origin} agent-dispatch list`) for an equivalent already queued, "
        f"claimed, or in progress; (b) FEASIBILITY -- is the task well-formed and "
        f"doable from this pool host; (c) IS-THIS-FOR-ME -- do this host's "
        f"resources/capabilities actually fit it; "
        f"(4a) on ACCEPT, `ssh {origin} agent-dispatch start {task_id} {owner}` "
        f"(this extends the lease from the tight evaluation window to the full "
        f"work lease), run `ssh {origin} agent-dispatch steer take {task_id} "
        f"{owner} --all` and incorporate any pending operator guidance, then carry "
        f"out the work described in the task's "
        f"prompt/payload to completion; "
        f"(4b) if the task is NOT FOR YOU or you hit a transient blocker, decline "
        f"WITHOUT abandoning it: `ssh {origin} agent-dispatch yield {task_id} "
        f"{owner} --exclude-self machine --note <why>` returns it to the origin's queue "
        f"and appends a 'not me' exclusion so this host is not re-offered it; "
        f"(4c) if it is a DUPLICATE or obsolete, retire it terminally with "
        f"`ssh {origin} agent-dispatch abandon {task_id} --worker-id {owner} "
        f"--duplicate-of <ref>` (cite the existing task/PR/issue) so the dedup is "
        f"recorded, never a silent drop; "
        f"(5) ONLY once you judge an accepted task's goal genuinely reached, run "
        f"`ssh {origin} agent-dispatch complete {task_id} {owner} --result-ref "
        f"<ref>`. Do NOT mark it complete before the goal is met -- completing the "
        f"task is your explicit signal that the work is done. "
        f"**Report progress as you go** so the operator can watch the fleet at a "
        f"glance: at each phase boundary (plan settled, implementation done, a PR "
        f"opened, a blocker hit) run "
        f"`ssh {origin} agent-dispatch progress {task_id} {owner} --phase <phase> "
        f"--summary \"<one line toward the goal>\"` (add `--pr <ref>` or "
        f"`--blocker <why>` when relevant). Keep each summary to a single line -- "
        f"it is a status beat, not a transcript; emit one only at real "
        f"transitions, never on a timer."
    )


def spawn_fleet_embodied_worker(
    host: str,
    task_id: str,
    *,
    origin: str,
    owner: str,
    worker_id: str,
    driver: str = DEFAULT_DRIVER,
    project: str | None = None,
    verify_timeout: int = 0,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Spawn a CLI-backed autopilot body on a **remote pool ``host``** via SSH.

    Runs ``agent-worktrees [--project <project>] embody --new --seed "<fleet
    seed>" ...`` **on** ``host`` (its SSH alias) -- creating a fresh
    detached worktree + Copilot session there, seeded
    (:func:`fleet_autopilot_worker_prompt`) to drive the ``task_id`` lease back to
    the ``origin`` coordinator over SSH (Model C). The remote ``embody --json``
    handle rides the SSH stdout, so :func:`parse_handle` recovers the
    worktree/session for the reservation record.

    ``project`` names the target project explicitly (the ``--project`` global) --
    required in practice because the remote SSH command runs in the login CWD, not
    inside the repo, so git-like discovery would fail. See the
    ``project-scoped-invocation`` pattern.

    Raises :class:`EmbodyUnavailable` if ``ssh`` is not on PATH here; a remote
    host that lacks ``agent-worktrees`` surfaces as a non-zero exit (the caller
    fails the reservation). The body runs **detached** on ``host``, so an SSH blip
    after launch never kills a running job.
    """
    exe = shutil.which("ssh")
    if exe is None:
        raise EmbodyUnavailable("ssh CLI not found on PATH (needed for fleet dispatch)")
    seed = fleet_autopilot_worker_prompt(
        task_id, origin=origin, owner=owner, worker_id=worker_id
    )
    remote_argv = ["agent-worktrees"]
    if project:
        remote_argv += ["--project", project]
    remote_argv += [
        "embody", "--new",
        "--seed", seed, "--driver", driver, "--json",
    ]
    if verify_timeout:
        remote_argv += ["--verify-timeout", str(verify_timeout)]
    remote_cmd = " ".join(shlex.quote(a) for a in remote_argv)
    # `host` is the SSH alias (never a raw IP). BatchMode so a missing key
    # fails fast instead of hanging on a password prompt.
    cmd = [exe, "-o", "BatchMode=yes", host.strip().lower(), remote_cmd]
    return subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
        cmd, check=False, capture_output=True, text=True, timeout=timeout,
        **no_window_kwargs(),
    )


DEFAULT_HEADLESS_AGENT = "task-worker"


def spawn_fleet_headless_worker(
    host: str,
    task_id: str,
    *,
    origin: str,
    owner: str,
    worker_id: str,
    agent: str = DEFAULT_HEADLESS_AGENT,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Spawn a **headless agent-bridge ACP** body on a remote pool ``host`` via SSH.

    The headless-fleet embodiment (Model C, headless variant). Runs
    ``agent-bridge create <agent> "<fleet seed>" --no-wait`` **on** ``host`` (its
    SSH alias) over the SSH mesh -- spawning a headless ACP session in
    that host's own persistent agent-bridge service, seeded
    (:func:`fleet_autopilot_worker_prompt`) to drive the ``task_id`` lease back to
    the ``origin`` coordinator over SSH under the supervisor-assigned synthetic
    ``owner``. The seed is **identical** to the CLI fleet body's
    (:func:`spawn_fleet_embodied_worker`); only the *body* differs -- so a
    headless-fleet task is driven exactly like a CLI-fleet one.

    Why headless for the fleet: a seeded CLI/mux session can race the input caret
    and never deliver its startup seed (the documented "Loading..." hang), so a
    kicked CLI body may never claim its task. A headless ACP body sidesteps the
    CLI-start-prompt path entirely, so a fleet body embodies reliably on the pool
    host without a human attach -- the right body for bounded, self-contained
    sweeps.

    Unlike the CLI body, a headless body is **not a parallel worktree**, so no
    worktree handle is recovered (the caller records ``worktree=None``); the
    ``--no-wait`` create returns once the ACP session is spawned into the host's
    bridge daemon, which owns it independently of this SSH invocation.

    Raises :class:`EmbodyUnavailable` if ``ssh`` is not on PATH here; a remote host
    lacking ``agent-bridge`` surfaces as a non-zero exit (the caller fails the
    reservation).
    """
    exe = shutil.which("ssh")
    if exe is None:
        raise EmbodyUnavailable("ssh CLI not found on PATH (needed for fleet dispatch)")
    seed = fleet_autopilot_worker_prompt(
        task_id, origin=origin, owner=owner, worker_id=worker_id
    )
    # `--json` (a global flag, before the subcommand) makes `create --no-wait`
    # emit the created session_id as JSON, so the caller can record a recovery
    # handle (the pool host's agent-bridge session id) for liveness-gated
    # re-embody -- see parse_fleet_body_session / fleet_body_verdict.
    remote_argv = ["agent-bridge", "--json", "create", agent, seed, "--no-wait"]
    remote_cmd = " ".join(shlex.quote(a) for a in remote_argv)
    # `host` is the SSH alias (never a raw IP). BatchMode so a missing key
    # fails fast instead of hanging on a password prompt.
    cmd = [exe, "-o", "BatchMode=yes", host.strip().lower(), remote_cmd]
    return subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
        cmd, check=False, capture_output=True, text=True, timeout=timeout,
        **no_window_kwargs(),
    )


def remote_registered_agent_names(host: str, *, timeout: float = 15.0) -> set[str] | None:
    """Best-effort set of agent names registered with agent-bridge on remote ``host``.

    A fleet/pool headless body spawns on the pool *host*, so its agent must be
    registered **there**, not on the supervisor's host. Runs
    ``agent-bridge --json agents`` on ``host`` (its SSH alias, never a raw IP) over
    the mesh with ``BatchMode`` so a missing key fails fast. Returns ``None``
    (indeterminate) whenever ssh is absent, the probe errors/times out, or the
    output is unparseable -- never raises, never blocks. Used by
    :func:`agent_dispatch.bridge.preflight_headless_agent` to warn before a fleet
    lane silently dead-letters against an unregistered pool-host agent.
    """
    exe = shutil.which("ssh")
    if exe is None:
        return None
    remote_argv = ["agent-bridge", "--json", "agents"]
    remote_cmd = " ".join(shlex.quote(a) for a in remote_argv)
    # `host` is the SSH alias (never a raw IP). BatchMode so a missing key fails
    # fast instead of hanging on a password prompt.
    cmd = [exe, "-o", "BatchMode=yes", host.strip().lower(), remote_cmd]
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
            cmd, check=False, capture_output=True, text=True, timeout=timeout,
            **no_window_kwargs(),
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    from .bridge import parse_agent_names

    return parse_agent_names(proc.stdout)


def parse_fleet_body_session(result: subprocess.CompletedProcess) -> str | None:
    """Extract the agent-bridge **session id** from ``create --no-wait --json``.

    The headless-fleet body is a bridge-hosted ACP session on the pool host; its
    session id (rides the SSH stdout as JSON) is the correlator a later liveness
    probe (:func:`fleet_body_verdict`) uses to decide whether the body is still
    alive. ``agent-bridge create`` prints a couple of human preamble lines
    (``[>] Starting session…``) *before* the JSON object, so we locate the first
    ``{`` and ``raw_decode`` from there (ignoring any trailing output). Returns
    ``None`` on any parse miss (the caller then records no recovery handle and the
    body simply isn't auto-recovered -- degrade safe, never fatal).
    """
    out = result.stdout or ""
    start = out.find("{")
    if start == -1:
        return None
    try:
        data, _end = json.JSONDecoder().raw_decode(out[start:])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    sid = data.get("session_id") or data.get("session")
    return str(sid) if sid else None


#: agent-bridge session statuses that mean the body's ACP session has ended --
#: a **positive** "the body is gone" signal (the vision's
#: eventual-terminal-reconciliation lands a killed/finished child here).
_FLEET_BODY_TERMINAL = frozenset({
    "stopped", "completed", "failed", "ended", "error",
    "cancelled", "canceled", "closed", "gone", "dead",
})
#: statuses that mean the body's session is still alive (working or idle between
#: turns). An idle body is ALIVE -- never recovered.
_FLEET_BODY_ALIVE = frozenset({
    "running", "starting", "connecting", "idle", "active", "ready",
    "live", "working", "busy",
})


def _classify_body_status(proc: subprocess.CompletedProcess) -> str:
    """Classify an ``agent-bridge --json status <session>`` result to a tri-state
    verdict, shared by the fleet (SSH) and local body probes.

    Mirrors :func:`agent_dispatch.tracking.liveness_verdict`'s safety contract:
    only a *positive* answer yields GONE; anything ambiguous is UNKNOWN, so
    recovery never fires on ignorance and cannot double-spawn a live body.
    """
    from . import tracking

    if proc.returncode != 0:
        # A missing session exits non-zero ("[FAIL] Session <id> not found") --
        # but so could a transport failure. Distinguish: a genuine not-found is
        # GONE; any other non-zero (unreachable, auth) is UNKNOWN.
        err = (proc.stderr or "") + (proc.stdout or "")
        return tracking.GONE if "not found" in err.lower() else tracking.UNKNOWN
    out = (proc.stdout or "").strip()
    if not out:
        return tracking.UNKNOWN
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return tracking.UNKNOWN
    if not isinstance(data, dict):
        return tracking.UNKNOWN
    liveness = str(data.get("liveness") or "").strip().lower()
    if liveness in {"dead", "gone"}:
        return tracking.GONE
    status = str(data.get("status") or "").strip().lower()
    if status in _FLEET_BODY_TERMINAL:
        return tracking.GONE
    if status in _FLEET_BODY_ALIVE:
        return tracking.LIVE
    return tracking.UNKNOWN  # unrecognized/lagging -> never recover on ignorance


def fleet_body_verdict(
    host: str, session_id: str, *, timeout: float | None = None
) -> str:
    """Tri-state liveness of a **headless fleet body** via the pool host's bridge.

    Runs ``ssh <host> agent-bridge --json status <session_id>`` and classifies
    (see :func:`_classify_body_status`):

    - **GONE** -- the bridge answers that the session is **absent** (not found,
      non-zero exit) or in a **terminal** status (:data:`_FLEET_BODY_TERMINAL`),
      or reports ``liveness`` dead/gone. The body's ACP session has ended, so a
      non-terminal origin task means it died before completing -> re-embody.
    - **LIVE** -- the session is present in a known-alive status
      (:data:`_FLEET_BODY_ALIVE`).
    - **UNKNOWN** -- ssh/bridge unreachable, timeout, unparseable output, or an
      unrecognized status (a possibly-lagging reconcile). Left alone.

    Returns the string verdict (values match ``tracking.LIVE/GONE/UNKNOWN``).
    Never raises.
    """
    from . import tracking

    ssh = shutil.which("ssh")
    if ssh is None or not host or not session_id:
        return tracking.UNKNOWN
    remote = f"agent-bridge --json status {shlex.quote(session_id)}"
    cmd = [
        ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=3",
        host.strip().lower(), remote,
    ]
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            cmd, check=False, capture_output=True, text=True,
            timeout=timeout if timeout is not None else 8.0,
            **no_window_kwargs(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return tracking.UNKNOWN
    return _classify_body_status(proc)


def fleet_body_activity(
    host: str, session_id: str, *, timeout: float | None = None
) -> str | None:
    """Exact ACTIVE/STALLED state for a remote headless fleet body."""
    from . import tracking

    ssh = shutil.which("ssh")
    if ssh is None or not host or not session_id:
        return None
    remote = f"agent-bridge --json status {shlex.quote(session_id)}"
    cmd = [
        ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=3",
        host.strip().lower(), remote,
    ]
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout if timeout is not None else 8.0,
            **no_window_kwargs(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        session = json.loads((proc.stdout or "").strip())
    except json.JSONDecodeError:
        return None
    return tracking.session_activity(session if isinstance(session, dict) else None)


def local_body_verdict(session_id: str, *, timeout: float | None = None) -> str:
    """Tri-state liveness of a **local headless body** via *this* host's bridge.

    The local analog of :func:`fleet_body_verdict`: a headless body embodied on
    this machine (:func:`agent_dispatch.supervisor.make_headless_spawn`) is an
    agent-bridge ACP session on the *local* daemon, so its liveness is probed by
    running ``agent-bridge --json status <session_id>`` directly (no SSH). Same
    tri-state safety contract as the fleet probe -- only a positive not-found /
    terminal answer yields GONE; any transport/parse failure is UNKNOWN, so
    recovery never fires on ignorance.

    Returns the string verdict (values match ``tracking.LIVE/GONE/UNKNOWN``).
    Never raises.
    """
    from . import bridge, tracking

    exe = bridge._agent_bridge_launch_prefix()
    if exe is None or not session_id:
        return tracking.UNKNOWN
    cmd = [*exe, "--json", "status", session_id]
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe resolved above
            cmd, check=False, capture_output=True, text=True,
            timeout=timeout if timeout is not None else 8.0,
            **no_window_kwargs(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return tracking.UNKNOWN
    return _classify_body_status(proc)
