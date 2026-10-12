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

from .worker_charter import (
    AUTOPILOT_CHARTER_NAME,
    OPERATING_PROCEDURES_CHARTER_NAME,
)


def _charter_read_step(
    ad: str,
    *,
    task_charter: str | None = None,
    concise: bool,
) -> str:
    universal = f"`{ad} charter show {OPERATING_PROCEDURES_CHARTER_NAME}`"
    if task_charter:
        task_cmd = f"`{ad} charter show {task_charter}`"
        commands = f"{universal}, then {task_cmd}"
    else:
        commands = universal
    if concise:
        return (
            f"If this session has not already read the worker charters, do that "
            f"now: {commands}. Otherwise skip this step. "
        )
    return (
        "Start by reading this session's worker charters now: "
        f"{commands}. They carry the behavior contract for this task; the "
        "steps below are only the per-invocation mechanics. "
    )


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

    ``concise`` keeps the same task-specific mechanics but changes the charter
    instruction from "read them now" to "read them now only if this session
    has not already done so" -- used for re-drive nudges to an already-embodied
    worker that may have read the charters earlier in the same session.
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
    charter_step = _charter_read_step(
        ad, task_charter=AUTOPILOT_CHARTER_NAME, concise=concise
    )
    return (
        f"You are a dispatched agent-dispatch **autopilot** worker (worker id: "
        f"{worker_id}) with auto-approved access and SDK extensions enabled "
        f"(--allow-all --experimental). "
        f"Task {task_id} is queued for you. {route_note}{identity_note}"
        f"{charter_step}"
        f"Then: (1) read the task with `{ad} show {task_id}`; "
        f"(2) claim it for evaluation with "
        f"`{ad} claim --task {task_id} --evaluation{claim_owner}{lane}` "
        f"(add `--capability <cap>` for each capability the task requires); "
        f"(3) while you hold the evaluation lease, follow the autopilot charter's "
        f"contract-net checks, using `{ad} list{lane}` for this task's lane-aware "
        f"duplicate sweep; "
        f"(4) on ACCEPT per the charter, `{ad} start {task_id}{owner_arg}`, then "
        f"`{ad} steer take {task_id}{owner_arg} --all` before continuing; "
        f"(5) record the status beats the charters require with `{ad} progress "
        f"{task_id}{owner_arg} --phase <phase> --summary "
        f'"<one line>"` (add `--pr <ref>` or `--blocker <why>` when relevant); '
        f"(6) if the task is not for you or you hit a transient blocker, decline "
        f"per the charter with {decline}; "
        f"(7) if it is a duplicate or obsolete, retire it terminally with "
        f"`{ad} abandon {task_id}{abandon_owner} --duplicate-of <ref>` (cite the "
        f"existing task/PR/issue) so the dedup is recorded, never a silent drop; "
        f"(8) only once the charters say the goal is genuinely met, `{ad} "
        f"complete {task_id}{owner_arg} --result-ref <ref>`."
    )


def interactive_worker_prompt(
    task_id: str,
    *,
    status: str | None = None,
) -> str:
    """Build the lightweight seed for Phase 1 item 3's interactive-embodiment
    transaction -- a CLI-backed session an OPERATOR watches/drives, not an
    unattended autopilot worker. Valid specifically for a **non-headless**
    worker: one launched wrapped with mux, without ``--acp`` -- a live pane an
    operator can attach to directly, not a background/programmatic session.

    Deliberately different from :func:`autopilot_worker_prompt`: it never
    injects a worker identity or pool/recipe framing (this transaction never
    resolves a pool or a named worker identity -- see
    :mod:`agent_dispatch.interactive_embody`), and it is explicitly
    non-railroaded: the agent works the task's next phase but may stop and ask
    the operator directly at any point, and the operator -- not a done-criteria
    check -- decides when the session wraps up. Per the operator's own framing
    (Phase 0 feedback round 1): whenever the session ends, the agent's job is
    to leave the task's recorded state honest (complete / abandon / reset to
    proposed / suspended-for-resume), never to keep working unattended after
    the session ends.

    **Blocking still goes through a tool call first** (per
    *status-through-tool-calls-not-prose*, extended to this mux/non-ACP case
    in the ``agent-dispatch-worker-operating-procedures`` effort): an operator
    watching a live pane is not guaranteed to be attached at the moment the
    agent actually needs input, so a bare in-pane question alone leaves the
    task's own status silent about the block. The agent records the block
    with a durable card/progress call *first* -- so the task's status itself
    tells the operator to come open this session -- and only then pauses in
    the pane for whenever the operator does attach.

    The task is already claimed and started under this worktree's own
    identity by the transaction itself (the same ownership/generation fencing
    a normal claim uses) -- unlike the autopilot seed, this prompt does not ask
    the agent to claim/start; it opens already inside an owned, in-progress
    task.
    """
    status_note = f" (current status: `{status}`)" if status else ""
    return (
        f"You are driving agent-dispatch task {task_id}{status_note} in this "
        f"worktree, with auto-approved access and SDK extensions enabled "
        f"(--allow-all --experimental). This session "
        f"is interactive -- an operator may be watching or will check in -- "
        f"not an unattended autopilot worker: you are NOT a named worker "
        f"identity and this task was NOT assigned to you through a pool. "
        f"Start by reading the task with `agent-dispatch show {task_id}` to see "
        f"its prompt/payload, phase, and any durable **goal**/**done_criteria** "
        f"plus accumulated **progress log**. If it carries a goal, RESUME from "
        f"the recorded progress rather than restarting. Work toward the task's "
        f"next phase, reporting progress as you go with `agent-dispatch progress "
        f"{task_id} --phase <phase> --summary \"<one line>\"` at real "
        f"transitions (plan settled, implementation done, a PR opened, a "
        f"blocker hit). If you need the operator's input, do not just pause "
        f"and ask in this pane and leave it at that -- the operator may not "
        f"be attached to see it. First record the block with a tool call: "
        f"`agent-dispatch card set {task_id} --title \"<question>\" "
        f"--request-input <field>:<kind>` for a real decision, or at minimum "
        f"`agent-dispatch progress {task_id} --blocker \"<question>\"`, so the "
        f"task's own status tells the operator to come open this session "
        f"rather than relying on them noticing the pane. Only THEN pause and "
        f"wait for the operator's reply; that is expected, not an escape "
        f"hatch. The OPERATOR decides when this session wraps up, not a "
        f"done-criteria check alone. Whenever this session ends -- whether "
        f"you judge the goal met or the operator says to stop -- leave the "
        f"task's recorded state honest before finishing: `agent-dispatch "
        f"complete {task_id} --result-ref <ref>` only if the goal is "
        f"genuinely met; `agent-dispatch suspend {task_id} --reason \"<why>\"` "
        f"to pause and preserve this worktree/session identity for a later "
        f"resume (the default if you are simply pausing mid-work); or, only "
        f"if the task itself is a duplicate/obsolete/permanently blocked, "
        f"`agent-dispatch abandon {task_id} --permit --reason \"<why>\"` "
        f"(`--duplicate-of <ref>` when citing an existing task/PR/issue) to "
        f"retire it terminally rather than leaving it silently stalled. "
        f"Never keep working unattended after the session ends -- that is the "
        f"one hard rule this prompt carries."
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
    ad = f"ssh {origin} agent-dispatch"
    charter_step = _charter_read_step(
        ad, task_charter=AUTOPILOT_CHARTER_NAME, concise=False
    )
    return (
        f"You are a fleet-dispatched agent-dispatch **autopilot** worker (worker "
        f"id: {worker_id}) on this pool host with auto-approved access and SDK "
        f"extensions enabled (--allow-all --experimental). Task {task_id} lives on the origin coordinator at "
        f"'{origin}'. Drive it there by running every agent-dispatch lifecycle "
        f"verb over SSH against the origin, ALWAYS passing your explicit owner id "
        f"'{owner}' (your working directory here cannot identify you to the "
        f"origin, so the owner is not optional). {charter_step}"
        f"Then: (1) read the task: `{ad} show {task_id}`; "
        f"(2) claim it for evaluation: `{ad} claim --task {task_id} --worker "
        f"{owner} --evaluation{lane}` (add `--capability <cap>` for each "
        f"capability the task requires); "
        f"(3) while you hold the evaluation lease, follow the autopilot "
        f"charter's contract-net checks, using `{ad} list{lane}` for this "
        f"task's lane-aware duplicate sweep; "
        f"(4) on ACCEPT per the charter, `{ad} start {task_id} {owner}`, then "
        f"`{ad} steer take {task_id} {owner} --all` before continuing; "
        f"(5) record the status beats the charters require with `{ad} progress "
        f"{task_id} {owner} --phase <phase> --summary "
        f'"<one line>"` (add `--pr <ref>` or `--blocker <why>` when relevant); '
        f"(6) if the task is not for you or you hit a transient blocker, decline "
        f"per the charter with `{ad} yield {task_id} {owner} --exclude-self "
        f"machine --note <why>`; "
        f"(7) if it is a duplicate or obsolete, retire it terminally with `{ad} "
        f"abandon {task_id} --worker-id {owner} --duplicate-of <ref>` (cite the "
        f"existing task/PR/issue) so the dedup is recorded, never a silent drop; "
        f"(8) only once the charters say the goal is genuinely met, `{ad} "
        f"complete {task_id} {owner} --result-ref <ref>`."
    )
