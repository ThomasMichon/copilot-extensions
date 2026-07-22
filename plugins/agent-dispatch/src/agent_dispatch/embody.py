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

import json
import shlex
import shutil
import subprocess

DEFAULT_DRIVER = "agent-dispatch"


def project_for_task(task: dict) -> str | None:
    """Resolve a task's lane to a local **project name** for embody's ``--project``.

    A supervisor / fleet spawner runs CWD-neutral (its working directory is a
    service runtime dir or an SSH login CWD, not the repo), so it must name the
    project explicitly rather than rely on git-like CWD discovery -- see the
    ``project-scoped-invocation`` pattern. Preference: the registry's
    authoritative reverse-mapping of the canonical lane
    (``identity.name_for_repo``); failing that, the lane's final path segment
    (``…/tmichon/aperture-labs`` -> ``aperture-labs``) as a best effort. Returns
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
    handle["worktree"] = (
        data.get("worktree_id") or data.get("worktree") or launch.get("worktree_id")
    )
    handle["session"] = (
        data.get("session_id") or data.get("session") or launch.get("session")
    )
    return handle


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
        f"session. This is a **contract-net evaluation**: you win an exclusive, "
        f"tight-lease EVALUATION window first, decide whether the task is really "
        f"yours to do, and only THEN commit to running it. Steps: "
        f"(1) read it with `agent-dispatch show {task_id}`; "
        f"(2) claim it for evaluation with "
        f"`agent-dispatch claim --task {task_id} --evaluation` "
        f"(add `--capability <cap>` for each capability the task requires) -- "
        f"this takes a SHORT evaluation lease, not the full work lease; "
        f"(3) **EVALUATE before committing** -- while you hold the evaluation "
        f"window, assess: (a) DUPLICATE check -- sweep open tasks "
        f"(`agent-dispatch list`) and any active worktree charters for an "
        f"equivalent already queued, claimed, or in progress; (b) FEASIBILITY -- "
        f"is the task well-formed and doable from here; (c) IS-THIS-FOR-ME -- do "
        f"your machine/worktree/capabilities actually fit it; "
        f"(4a) on ACCEPT, `agent-dispatch start {task_id}` (this extends the "
        f"lease from the tight evaluation window to the full work lease), then "
        f"carry out the work described in the task's prompt/payload to "
        f"completion; "
        f"(4b) if the task is NOT FOR YOU or you hit a transient blocker, decline "
        f"WITHOUT abandoning it: `agent-dispatch yield {task_id} --exclude-self "
        f"worktree --note <why>` returns it to the queue and appends a narrow "
        f"'not me' exclusion so you are not re-offered it (widen to "
        f"`--exclude-self machine` only when the mismatch is machine-wide); "
        f"(4c) if it is a DUPLICATE or obsolete, retire it terminally with "
        f"`agent-dispatch abandon {task_id} --duplicate-of <ref>` (cite the "
        f"existing task/PR/issue) so the dedup is recorded, never a silent drop; "
        f"(5) ONLY once you judge an accepted task's goal genuinely reached, run "
        f"`agent-dispatch complete {task_id} --result-ref <ref>`. "
        f"Do NOT mark it complete before the goal is met -- completing the task "
        f"is your explicit signal that the work is done. "
        f"**Report progress as you go** so the operator can watch the fleet at a "
        f"glance: at each phase boundary (plan settled, implementation done, a PR "
        f"opened, a blocker hit) run "
        f"`agent-dispatch progress {task_id} --phase <phase> --summary "
        f"\"<one line toward the goal>\"` (add `--pr <ref>` or `--blocker <why>` "
        f"when relevant). Keep each summary to a single line -- it is a status "
        f"beat, not a transcript; emit one only at real transitions, never on a "
        f"timer."
    )


def spawn_embodied_worker(
    task_id: str,
    *,
    coordinator_url: str,
    worker_id: str,
    driver: str = DEFAULT_DRIVER,
    project: str | None = None,
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
    exe = shutil.which("agent-worktrees")
    if exe is None:
        raise EmbodyUnavailable("agent-worktrees CLI not found on PATH")
    seed = autopilot_worker_prompt(
        task_id, coordinator_url=coordinator_url, worker_id=worker_id
    )
    cmd = [exe]
    if project:
        # `--project` is an agent-worktrees GLOBAL option -- it precedes the
        # `embody` subcommand. It lets a CWD-neutral caller name the target
        # project instead of relying on git-like CWD discovery.
        cmd += ["--project", project]
    cmd += ["embody", "--new", "--seed", seed, "--driver", driver, "--json"]
    if verify_timeout:
        cmd += ["--verify-timeout", str(verify_timeout)]
    return subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
        cmd, check=False, capture_output=True, text=True, timeout=timeout
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
      ``ssh <origin> agent-dispatch <verb> ...`` (the origin is a facility SSH
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
        f"{task_id} {owner} --evaluation` (add `--capability <cap>` for each "
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
        f"work lease), then carry out the work described in the task's "
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
    seed>" ...`` **on** ``host`` (its facility SSH alias) -- creating a fresh
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
    # `host` is the facility SSH alias (never a raw IP). BatchMode so a missing key
    # fails fast instead of hanging on a password prompt.
    cmd = [exe, "-o", "BatchMode=yes", host.strip().lower(), remote_cmd]
    return subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
        cmd, check=False, capture_output=True, text=True, timeout=timeout
    )
