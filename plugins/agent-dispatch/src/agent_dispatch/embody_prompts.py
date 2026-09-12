"""Autopilot seed prompts for embodied (CLI-backed) dispatch workers.

Split out of :mod:`agent_dispatch.embody` (componentization: that module was
over this repo's module-size cap, see `tools/check-module-size.py`) --
these two functions are pure string builders with no shared state, no
subprocess/network I/O, and nothing any test needs to monkeypatch, making
them the lowest-risk possible extraction. :mod:`agent_dispatch.embody`
re-exports both under their original names, so every existing call site
(``embody.autopilot_worker_prompt``, ``embody.fleet_autopilot_worker_prompt``)
is unaffected.
"""

from __future__ import annotations


def autopilot_worker_prompt(
    task_id: str,
    *,
    worker_id: str,
    route: str = "",
    repo: str | None = None,
    all_repos: bool = False,
    explicit_worker_identity: bool = False,
    concise: bool = False,
) -> str:
    """Build the autopilot seed handed to a dispatched, embodied CLI session.

    A dispatch-flavored variant of :func:`agent_dispatch.bridge.worker_prompt`:
    it frames the session as an autonomous autopilot worker and makes explicit
    that **completing the task is its own deliberate signal that the work is
    done** -- it must not complete before the goal is met.

    A CLI-backed worker drives its whole lifecycle under its **worktree identity**
    (owner-less ``claim``/``start``/``complete``/``yield``, which the coordinator
    resolves to ``<machine>/<worktree>``). That keeps the task's owner equal to
    its worktree, so agent-bridge live-session tracking can join the task to the
    embodied session (see :mod:`agent_dispatch.tracking`) -- a dispatched CLI
    body is then as trackable as a headless worker. ``worker_id`` names the
    session in the seed for legibility only. A headless body has no worktree
    identity, so ``explicit_worker_identity`` makes every owner-gated command use
    the generated worker id directly.

    ``route`` is the coordinator **routing intent** to bake into the worker's
    ``agent-dispatch`` commands, as a leading flag fragment (``""`` for the
    default local coordinator, ``" --shared"``, or ``" --url <endpoint>"``).
    The default (``""``) deliberately carries **no** endpoint so each command
    rediscovers the live local coordinator -- that is what makes a zero-downtime
    coordinator port cutover transparent to a long-running dispatcher. A stable
    explicit target (``--url``) or the env-configured ``--shared`` endpoint is
    preserved so a task created on a non-default coordinator is still reachable.

    ``concise`` (default ``False``, every existing call site unaffected) swaps
    the always-inlined behavioral essay for a short seed that instead points
    the worker at ``agent-dispatch charter show autopilot`` (see
    :mod:`agent_dispatch.worker_charter`) to pull the same policy prose only
    when it needs it -- cheaper per embodiment when a worker already learned
    the charter earlier in the same session (e.g. a repository-issue-loop body
    that claims several tasks in one embodied lifetime).
    """
    ad = f"agent-dispatch{route}"
    if repo and all_repos:
        raise ValueError("autopilot claim scope cannot set repo and all_repos")
    lane = " --all-repos" if all_repos else (f" --repo {repo}" if repo else "")
    claim_owner = f" --worker {worker_id}" if explicit_worker_identity else ""
    owner_arg = f" {worker_id}" if explicit_worker_identity else ""
    abandon_owner = f" --worker-id {worker_id}" if explicit_worker_identity else ""
    identity_note = (
        f"Claim and drive it under the explicit worker id `{worker_id}` shown in "
        f"each owner-gated command; this headless body has no worktree identity. "
        if explicit_worker_identity
        else (
            "Claim it under this worktree's own identity (no owner argument -- "
            "the coordinator resolves machine/worktree), which keeps the task "
            "trackable as your live session. "
        )
    )
    decline = (
        f"`{ad} yield {task_id}{owner_arg} --note <why>` returns it to the queue "
        f"without inventing a worktree exclusion"
        if explicit_worker_identity
        else (
            f"`{ad} yield {task_id} --exclude-self worktree --note <why>` returns "
            f"it to the queue and appends a narrow 'not me' exclusion so you are "
            f"not re-offered it (widen to `--exclude-self machine` only when the "
            f"mismatch is machine-wide)"
        )
    )
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
    if concise:
        from .worker_charter import AUTOPILOT_CHARTER_NAME

        return (
            f"You are a dispatched agent-dispatch **autopilot** worker (worker id: "
            f"{worker_id}), running in a fresh parallel worktree with tools "
            f"auto-approved (--allow-all-tools). Task {task_id} is queued for you. "
            f"{route_note}{identity_note}"
            f"If you do not already have this session's agent-dispatch worker "
            f"charter, read it now: `{ad} charter show {AUTOPILOT_CHARTER_NAME}` "
            f"(contract-net evaluation, the goal/progress loop, and "
            f"decline/duplicate/complete conventions -- required before you claim; "
            f"skip only if a prior turn this session already read it). "
            f"Then: (1) read the task with `{ad} show {task_id}`; "
            f"(2) claim it for evaluation with "
            f"`{ad} claim --task {task_id} --evaluation{claim_owner}{lane}` "
            f"(add `--capability <cap>` for each capability the task requires); "
            f"(3) on ACCEPT per the charter, `{ad} start {task_id}{owner_arg}`; "
            f"(4) decline per the charter with {decline}; "
            f"(5) once genuinely done, `{ad} complete {task_id}{owner_arg} "
            f"--result-ref <ref>`."
        )
    return (
        f"You are a dispatched agent-dispatch **autopilot** worker (worker id: "
        f"{worker_id}), running in a fresh parallel worktree with tools "
        f"auto-approved (--allow-all-tools). A task has been queued for you. "
        f"{route_note}Work the task end-to-end, "
        f"autonomously, without waiting for a human. {identity_note}"
        f"This is a **contract-net evaluation**: you win an exclusive, "
        f"tight-lease EVALUATION window first, decide whether the task is really "
        f"yours to do, and only THEN commit to running it. Steps: "
        f"(1) read it with `{ad} show {task_id}`; "
        f"(2) claim it for evaluation with "
        f"`{ad} claim --task {task_id} --evaluation{claim_owner}{lane}` "
        f"(add `--capability <cap>` for each capability the task requires) -- "
        f"this takes a SHORT evaluation lease, not the full work lease; "
        f"(3) **EVALUATE before committing** -- while you hold the evaluation "
        f"window, assess: (a) DUPLICATE check -- sweep open tasks "
        f"(`{ad} list{lane}`) and any active worktree charters for an "
        f"equivalent already queued, claimed, or in progress; (b) FEASIBILITY -- "
        f"is the task well-formed and doable from here; (c) IS-THIS-FOR-ME -- do "
        f"your machine/worktree/capabilities actually fit it; "
        f"(4a) on ACCEPT, `{ad} start {task_id}{owner_arg}` (this extends the "
        f"lease from the tight evaluation window to the full work lease), run "
        f"`{ad} steer take {task_id}{owner_arg} --all` and incorporate any pending "
        f"operator guidance, then carry out the work as follows. FIRST re-read the task with "
        f"`{ad} show {task_id}` and check whether it carries a durable "
        f"**goal** and **done-criteria** (the `goal` / `done_criteria` fields) "
        f"plus an accumulated **progress log** (the `progress_log` array). "
        f"If it DOES, treat the task as a goal to PURSUE, and RESUME rather than "
        f"restart: read the prior progress log to see what earlier passes already "
        f"accomplished, then continue from there. LOOP: do one unit of work "
        f"toward the goal -> record a progress beat with "
        f"`{ad} progress {task_id}{owner_arg} --phase <phase> --summary "
        f'"<one line>"` (this now APPENDS to the durable progress log, so a '
        f"replacement worker can resume) -> re-check the done-criteria -> repeat "
        f"until they are genuinely met. If the task carries NO goal/done-criteria "
        f"(a plain one-shot task), just carry out the work described in its "
        f"prompt/payload to completion as usual; "
        f"(4b) if the task is NOT FOR YOU or you hit a transient blocker, decline "
        f"WITHOUT abandoning it: {decline}; "
        f"(4c) if it is a DUPLICATE or obsolete, retire it terminally with "
        f"`{ad} abandon {task_id}{abandon_owner} --duplicate-of <ref>` (cite the "
        f"existing task/PR/issue) so the dedup is recorded, never a silent drop; "
        f"(5) ONLY once you judge an accepted task's goal genuinely reached (its "
        f"done-criteria met, when it carries them), run "
        f"`{ad} complete {task_id}{owner_arg} --result-ref <ref>`. "
        f"Do NOT mark it complete before the goal is met -- completing the task "
        f"is your explicit signal that the work is done. "
        f"**Report progress as you go** so the operator can watch the fleet at a "
        f"glance and so a replacement worker can resume from your recorded "
        f"progress: at each phase boundary (plan settled, implementation done, a "
        f"PR opened, a blocker hit) and at each pass of a goal loop run "
        f"`{ad} progress {task_id}{owner_arg} --phase <phase> --summary "
        f'"<one line toward the goal>"` (add `--pr <ref>` or `--blocker <why>` '
        f"when relevant). Keep each summary to a single line -- it is a status "
        f"beat, not a transcript; emit one at real transitions, never on a "
        f"timer."
    )


def fleet_autopilot_worker_prompt(
    task_id: str,
    *,
    origin: str,
    owner: str,
    worker_id: str,
    repo: str | None = None,
    all_repos: bool = False,
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
    if repo and all_repos:
        raise ValueError("fleet claim scope cannot set repo and all_repos")
    lane = " --all-repos" if all_repos else (f" --repo {repo}" if repo else "")
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
        f"{task_id} --worker {owner} --evaluation{lane}` (add `--capability <cap>` for each "
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
        f'--summary "<one line toward the goal>"` (add `--pr <ref>` or '
        f"`--blocker <why>` when relevant). Keep each summary to a single line -- "
        f"it is a status beat, not a transcript; emit one only at real "
        f"transitions, never on a timer."
    )
