# Handoff Cutover Lifecycle Journal

- **Slug:** `handoff-cutover-lifecycle-journal`
- **Repo:** copilot-extensions (plugins/agent-worktrees, plugins/context-handoff)
- **Branch(es):** `worktree/<local-agent-worktree-branch>` (private branch name; not public-safe to record verbatim)
- **Created:** 2026-09-11
- **Status:** Active
- **Umbrella issue:** [#2457](https://github.com/ThomasMichon/copilot-extensions/issues/2457)
- **Sub-issues:** TBD

## Guiding Intent

A worktree's handoff cutover — replacing the current Copilot session with a
successor in a new mux pane, without losing the operator's place — must be
**observable and re-traceable at any time**, end to end, across every
component that participates. Today the mechanics exist (tracking records,
`activity.jsonl`, cutover claim files, context-handoff's request/delivery
files) but they are scattered, inconsistent about which of the 13 real
lifecycle stages they cover, and several stages emit **no** durable evidence at
all. The operator-visible symptom this produces: `trigger_handoff` acknowledges
successfully, no new pane appears, and a retry reports the handoff "already
taken" with still no replacement pane — with no single place to answer "what
actually happened, in order, for this worktree?"

This effort makes the full cutover sequence a first-class, always-on journal:
one event per stage, emitted durably by whichever component is actually
responsible for that stage, on both sides of the cutover (predecessor and
successor sessions), cross-linked by session id like a linked list, readable
for active diagnosis right now, and persisted durably in session-state so the
trail survives into the archive for later auditing.

**Explicit scope decision (2026-09-11):** this effort's mandate is the
**logging/observability infrastructure** — durable per-stage events, an
accessible diagnosis surface, and durable session-state persistence for
archive auditing (Phases 1-3, 5). It is **not** a mandate to remediate any
live worktree found stuck mid-handoff, and Phase 4's *fixes* are follow-on
work once the trace makes root cause provable rather than inferred — the
operator explicitly declined remediation of the live case study worktree.
Phase 4 stays in this doc as the root-cause *evidence* (already gathered) and
as forward-looking fix items, but execution priority is Phases 1-3 first.

**Explicit scope decision (2026-09-11, round 2):** the 13-stage trace in this
effort covers the **mux/CLI resident-status-monitor cutover path** —
`sessions.mux_new_session()`/`mux_new_window()`, `cmd_register_session`'s
`sessionStart`/`sessionEnd` binding, and `_monitor_pending_handoff_request()`'s
claim/spawn/retire flow. **agent-bridge's independent spawn path**
(`agent-bridge handoff-request` → `SessionManager.handoff_session()`, which
can create an ACP successor that bypasses the mux `sessionStart`/`sessionEnd`
hooks entirely and has no participation in the resident monitor's
exclusive-create claim file) is real, documented in Context below as a thing
`trigger_handoff` can also wake, but instrumenting *it* to the same 13-stage
model — including a cross-runner atomic claim between two independently
owned processes — is **out of scope for this effort** and left as a named,
deferred follow-on (see Phase 2's closing note). This keeps the schema,
ordering, and validation plan coherent for one well-understood runner instead
of speculatively designing for both at once.

## Participants

Single local agent, no dispatch/coordination topology needed.

## Context

Prior investigation (this effort's kickoff) mapped the current state across
both plugins:

- **agent-worktrees** (`plugins/agent-worktrees/src/agent_worktrees/`):
  - `activity.py` — append-only JSONL lifecycle log at
    `~/.agent-worktrees/logs/activity.jsonl`. Already defines
    `worktree_created`, `launcher_started`, `session_started`, `mux_attached`,
    `mux_failed`, `pane_exited`, `copilot_exited`, `handoff_cutover_claim`,
    `handoff_cutover_spawn`, `handoff_predecessor_retire`,
    `handoff_retire_guard`. **`log_event()` swallows every exception** — a
    missing record is not proof an operation didn't happen.
  - `tracking.py` — `WorktreeRecord` / `SessionEntry` hold pending handoffs,
    `register_session()`, `associate_handoff_candidate()`, `link_handoff()`,
    `claim_handoff()`.
  - `sessions.py` — `mux_new_window()` / `mux_new_session()` do the actual
    tmux/psmux pane creation for cutover; `mux_seed_pane()` for the
    interactive path.
  - `__main__.py` — `cmd_register_session()` (sessionStart), the status
    monitor's `_monitor_pending_handoff_request()` /
    `_monitor_claim_handoff_cutover()` (claim, using an exclusive-create file
    under `~/.agent-worktrees/status-monitor-handoffs.d/<worktree>/<token>.json`),
    `_handoff_cutover_spawn_result()` (spawn), `_wait_for_handoff_candidate()`
    (waits for the successor's sessionStart to associate the token).
  - `health.py` — `find_orphaned_handoffs()` already detects "claimed but no
    live successor pane" as a class of bug — this is the exact reported
    symptom.
- **context-handoff** (`plugins/context-handoff/extensions/context-handoff/`):
  - `handoff-core.mjs` — `triggerHandoff()`, `storeHandoff()`,
    `consumeDispatchHandoffTask()`, `consumeFileHandoffOnce()`. Writes
    `~/.copilot/session-state/<sid>/handoff-request.json` (predecessor side)
    and `<worktree-state-dir>/handoff/handoff-<sid>.json` /
    `delivery-<task-id>.json`. Best-effort calls out to
    `agent-worktrees note-handoff` and `agent-worktrees activity-log
    handoff_requested` — both swallow failures.
  - `trigger_handoff` **does not itself spawn anything** — by design it is
    process-manager agnostic. There are **two independent runners** that can
    actually spawn the successor: the resident agent-worktrees status monitor
    (polls for pending handoffs, per `_monitor_pending_handoff_request()`),
    and **agent-bridge**, which `trigger_handoff` best-effort pings via
    `agent-bridge handoff-request` — that route awaits
    `SessionManager.handoff_session()` and can itself create the successor
    before returning
    (`plugins/agent-bridge/src/agent_bridge/routes/worktrees.py:939-1000`),
    independently of the resident monitor. Both runners can observe and act
    on the same stored baton. If neither runner is present/reachable, or both
    race on the same baton, `trigger_handoff` still reports success.
  - No context-handoff sessionStart hook inspects or claims a pending handoff;
    that is left to `agent-worktrees` (a session becomes `successor-elect` via
    role calculation) and finally to the session explicitly calling
    `consume_handoff`.

**Net finding:** the 13 stages the operator described map roughly onto real
code, but only ~5 of them (`handoff_requested`, `handoff_cutover_claim`,
`handoff_cutover_spawn`, `handoff_predecessor_retire`, `session_started`) are
logged today, none are cross-linked into a single per-worktree trace, and none
are split/mirrored into **both** the predecessor's and successor's own
session-state folders the way the operator wants (a literal linked list
readable from either side without needing the other session's home directory).

## Request

> Handoff cutover is not working, in general. As agents are nudged to perform
> a handoff, they are running the skill, saving the handoff, and then calling
> the trigger tool, and they even get an acknowledgement, but nothing seems to
> happen. However, when I try the attempt again, the agent claims the handoff
> has been taken, yet I see no replacement mux pane. For each worktree, each
> part of the system needs to contribute to logging, so we can audit what
> happens, in sequence, each time.
>
> For a worktree, we need an event when:
> 1. agent-worktrees creates the worktree, before a session is started
> 2. agent-worktrees assigns a mux session and a pane
> 3. agent-worktrees invokes copilot
> 4. Copilot sessionStart hook runs, binding the session to the worktree
> 5. Copilot invokes the status report tools
> 6. Copilot invokes trigger_handoff
> 7. Handoff-compatible worktree host (worktree-manager + mux, agent-bridge,
>    etc) acknowledges handoff
> 8. Handoff runner starts new Copilot session
> 9. New Copilot sessionStart hook runs, slotting new session id as handoff
>    candidate
> 10. New Copilot session claims handoff, declaring itself new head
> 11. Handoff runner confirms pickup of handoff, starts closing old session
> 12. Copilot sessionEnd hook runs from old session
> 13. Handoff runner declares handoff complete
>
> It should be possible, for a worktree, to re-trace these steps at any time.
> I also want to see these journaled in the session-state folder for the
> session, split across the predecessor and successor sessions, with id
> pointers to create a "linked list".

(The operator's own list numbers a doubled "8" — "Handoff runner starts new
Copilot session" and the next item are both implicitly step 8/9 in the
original message. This effort treats them as 13 distinct, ordered stages,
renumbering from the "acknowledges handoff" step onward.)

## Plan

### Phase 1 — Unified event schema + shared emitter
- [x] Define one canonical stage vocabulary covering all 13 stages, layered
      **on top of** existing wire event names rather than replacing them —
      `handoff_requested` (the resident monitor's pending scan reads this
      exact event name; renaming it without an atomic producer+consumer
      update would stop automatic cutovers from being discovered) keeps its
      name and gains a `stage: 6` / `stage_name: handoff_triggered` pair of
      fields alongside it. New stages with no existing event
      (`mux_session_assigned`, `copilot_invoked`, `session_start_bound`,
      `status_reported`, `handoff_host_acknowledged`,
      `handoff_successor_spawn_started` + a paired terminal spawn-result
      event, `handoff_successor_session_start_bound`,
      `handoff_successor_claimed`,
      `handoff_pickup_confirmed_predecessor_closing`, `session_end_bound`,
      `handoff_complete`) are added as new wire names. Do not rename any
      existing event relied on elsewhere without an audit of readers
      (`health.py`, `disposition_history.py`, any docs) and an atomic
      producer+consumer update.
      **Landed:** `activity.py`'s `HANDOFF_STAGE_MAP` + auto-stamping in
      `log_event()` (PR #2472) — covers the 8 existing wire events; the
      not-yet-existing stage names are pre-declared in the map's docstring so
      Phase 2's new emitters slot in without a second schema change.
- [x] Every event carries: `worktree_id`, `stage` (ordinal + name),
      `session_id` — **nullable**, since Stage 1 (`worktree_created`) fires
      before any Copilot session exists (the existing event already has no
      session id; pre-session correlation uses `worktree_id` + `launch_id`
      alone) — `launch_id` (or an equivalent attempt id; **required and
      nullable**: stages 1-3 all occur before `sessionStart` binding, and the
      launcher already carries `launch_id` end to end, so it is the only
      correlation key that can distinguish concurrent/retried launch attempts
      on the same worktree before a session id exists), `handoff_token` (when
      applicable), `predecessor_session_id` / `successor_session_id` (the
      linked-list pointers — null until known), `ts`, `source` (which
      component emitted it: agent-worktrees / context-handoff / hook).
      **Landed:** all base fields already existed as `log_event()` params/
      `**fields`; `stage`/`stage_name` land via PR #2472's auto-stamping.
      `predecessor_session_id`/`successor_session_id` are Phase 3's job (the
      cross-linking work), not stamped yet.
- [ ] Give the spawn stage (8) an explicit **start** event
      (`handoff_successor_spawn_started`, emitted before success is known)
      *and* a terminal **result** event/field
      (`handoff_successor_spawn_result: succeeded|failed`, or reuse
      `handoff_cutover_spawn`'s existing success-only shape plus a new
      failure-path sibling) — a single one-shot event cannot distinguish "the
      spawn is still in flight" from "the spawn failed," which the Phase 3/4
      validation plan (stages 8+ absent on a killed spawn) depends on.
- [ ] Make `activity.log_event()` (or a new sibling) **not** swallow errors
      silently in a way that's invisible — keep best-effort delivery (never
      block the caller) but surface a debug-level warning/counter so a
      missing event is itself detectable, not just theorized.

### Phase 2 — Instrument all 13 stages
- [ ] Stage 1 (`worktree_created`) — confirm `cmd_create` already emits this
      with no gaps; add `predecessor_session_id: null` framing.
- [ ] Stage 2 (`mux_session_assigned`) — **ordinary launches don't go through
      the Python `sessions.py` helpers at all**: `bin/launch-session.sh` /
      `.ps1` invoke `tmux`/`psmux new-session` directly and already emit
      `mux_attached` after success. Emit stage 2 from that existing
      launcher creation/join boundary for the ordinary-launch path, and
      additionally from `mux_new_session()` / `mux_new_window()` (after the
      mux subprocess call succeeds, not from the pure argv-builder
      `build_mux_new_window_argv()`) for the programmatic cutover path — both
      emitters, not one instead of the other.
- [ ] Stage 3 (`copilot_invoked`) — the pane wrapper only **forwards** an
      already-resolved command; it is not where Copilot is actually resolved
      and invoked, so logging there can report `copilot_invoked` even when
      setup fails before Copilot ever starts. Emit from the true final
      resolution/exec point — `default-setup.sh` / `default-setup.ps1` (and
      any configured/legacy launch template) right before the `copilot`
      binary is actually exec'd — or, if the pane wrapper is kept as the
      emitter for implementation convenience, name its event an explicit
      *invocation attempt* (e.g. `copilot_invocation_attempted`) distinct from
      a confirmed `copilot_invoked`, so a setup failure before Copilot starts
      is visibly distinguishable in the trace.
- [ ] Stage 4 (`session_start_bound`) — emit from `cmd_register_session` once
      a session id + worktree are actually resolved (already close to
      `session_started`; make sure the *binding* moment, not just tool entry,
      is what's logged).
- [ ] Stage 5 (`status_reported`) — emit from the `status`/status-report tool
      path when it first runs in a session (marks "Copilot did something in
      this worktree").
- [ ] Stage 6 (`handoff_triggered`) — covered by context-handoff's existing
      `handoff_requested` wire event; add the `stage`/`stage_name` fields to
      it in place rather than introducing a second event name.
- [ ] Stage 7 (`handoff_host_acknowledged`) — new: emit when the resident
      status monitor's `_monitor_pending_handoff_request()` first observes and
      accepts the pending handoff, distinct from the later claim. (Per the
      scope decision above, this covers the resident-monitor runner only —
      agent-bridge's independent `handoff-request` route is documented in
      Context as a real alternate path but is explicitly out of scope for
      this effort's instrumentation; see the deferred follow-on note below.)
- [ ] Stage 8 (`handoff_successor_spawn_started`) — emit at the start of
      `_handoff_cutover_spawn_result` / `mux_new_window`, before success is
      known, so a spawn that later fails still leaves a trace.
- [ ] Stage 9 (`handoff_successor_session_start_bound`) — emit from the
      successor's own `cmd_register_session` when it recognizes the
      `--handoff-candidate-token` and calls `associate_handoff_candidate`.
- [ ] Stage 10 (`handoff_successor_claimed`) — **must be emitted at the point
      the tracking record's head is authoritatively transferred**, not from
      `associate_handoff_candidate()` (which only records a candidate without
      takeover) and not from `consume_handoff` (which never calls
      `tracking.link_handoff()` and can run after the predecessor is already
      retired). The actual head-transfer point is wherever
      `tracking.link_handoff()` is invoked in the monitor's flow — **locate
      that exact call site as a Phase 2 task** (it is not
      `associate_handoff_candidate()`, and today's retire timing suggests it
      happens close to, but must be verified against, the monitor's
      claim/retire sequence) and instrument stage 10 there, not at candidate
      association. Log the successor's later `consume_handoff` call as a
      *separate* "baton delivery consumed" event, not conflated with stage 10.
- [ ] Stage 11 (`handoff_pickup_confirmed_predecessor_closing`) — emit
      immediately after the same `tracking.link_handoff()` call site used for
      stage 10 (today's monitor retires the predecessor right after the head
      transfers, not after a separate later successor acknowledgement) — keep
      10 and 11 adjacent in the same code path until/unless a future change
      makes retirement wait for an explicit `consume_handoff` acknowledgement.
- [ ] Stage 12 (`session_end_bound`) — a `sessionEnd` hook and its
      `session_ended` deregistration event **already exist**; extend that
      existing path to also emit `session_end_bound` with the stage/linkage
      fields rather than treating stage 12 as possibly missing or adding a
      second, duplicate hook path.
- [ ] Stage 13 (`handoff_complete`) — emit once the runner has confirmed the
      predecessor pane is gone and the successor is head.

> **Deferred follow-on (explicitly out of scope here):** instrumenting
> agent-bridge's `SessionManager.handoff_session()` spawn path to the same
> 13-stage model. That path can produce an ACP successor that bypasses the
> mux `sessionStart`/`sessionEnd` hooks entirely (so stages 4, 9, 12, 13 as
> defined above don't apply to it as written) and does not participate in the
> resident monitor's exclusive-create claim file (so stage 7 has no built-in
> cross-runner arbitration against it). A follow-on effort should define
> runner-specific emitters and completion predicates for the bridge/ACP path,
> or explicitly scope the 13-stage trace to mux/CLI handoffs in the
> user-facing docs (Phase 5) until that follow-on lands.

### Phase 3 — Cross-linking (the "linked list") + durable persistence
- [ ] When the successor's `session_start_bound` (stage 9) fires, stamp its
      own session-state handoff record with `predecessor_session_id`, **and
      backfill stages 1-8** (which necessarily happened before the
      successor's session-state directory existed) into the successor's
      trace file at that moment, sourced from the durable per-worktree store
      below — not from the predecessor's directory, which may already be
      gone by the time anyone reads the successor's copy.
- [ ] When the predecessor retires (stage 11/12), stamp its own session-state
      record with `successor_session_id` (it may not have known this at
      trigger time), and append any later stage-9-through-13 events it can
      still observe before it exits.
- [ ] `~/.agent-worktrees/logs/activity.jsonl` is a **rolling log with a
      retention window shorter than "at any time"** (days, not indefinite),
      and Stage 1 happens before either session-state directory exists — so
      neither `activity.jsonl` alone nor the two session-state files alone
      can satisfy "re-trace at any time" / "persisted... for future auditing
      from the archive" once the log rotates past a stage-1 event. Add a
      **durable, per-worktree trace store** (e.g.
      `~/.agent-worktrees/logs/handoff-traces/<project>/<worktree-id>.jsonl` —
      **namespaced by project, not worktree id alone**: worktree ids are only
      project-scoped (`_find_tracking_file_exact` explicitly raises when the
      same id exists under multiple projects' tracking dirs), so a
      machine-global path keyed on worktree id alone would let two projects'
      same-named worktrees append into one file and let
      `handoff-trace --token` render unrelated attempts together. Derive
      `<project>` from the same resolved tracking record the rest of the
      command uses, and require the CLI lookup (Phase 3's `handoff-trace`
      command) to resolve and pass that identical discriminator — exempt
      from the rolling-window rotation, or an equivalent unrotated sink) that
      every stage's emitter writes to in addition to `activity.jsonl` and the
      session-state files, and treat *that* store — not `activity.jsonl` — as
      the archival source of truth. **This sink is written concurrently by
      multiple independent processes on both Linux/WSL and Windows** (the
      Python CLI, the hook client, the status monitor, context-handoff's Node
      process) — `O_APPEND` + single-`write()` is a **POSIX-specific**
      guarantee and does not by itself establish atomicity on Windows. Define
      the append/locking primitive **per platform**
      (POSIX: `O_APPEND` + a single bounded `write()` per line; Windows: an
      equivalent atomic-append primitive, e.g. Node's/Python's append-mode
      handle combined with a short-lived advisory lock, or routing all writes
      for a given worktree through one lock-holding writer) and run the
      concurrent-writer race test (below) on **each** supported OS, not just
      a POSIX-side Node stand-in — or explicitly document a platform-specific
      fallback if true lock-free atomicity isn't achievable on one platform.
- [ ] Write/append the full per-stage trace into **both**:
      `~/.copilot/session-state/<predecessor-sid>/handoff-trace.jsonl` and
      `~/.copilot/session-state/<successor-sid>/handoff-trace.jsonl` — each
      side gets every event it can see (plus the Phase-3 backfill above), plus
      the other side's session id, so either session-state folder alone lets
      you walk the chain for as long as that session-state folder itself is
      retained.
- [ ] Add a `agent-worktrees handoff-trace <worktree-id|session-id>
      [--project <name>] [--token <handoff-token>]` CLI command. Tracking
      lookup is **project-scoped**, and neither a bare worktree id nor a
      session id identifies the active project when the command runs from a
      neutral CWD (e.g. a daemon), so `--project` is required in that case (or
      the command implements and documents an all-project resolver, with a
      neutral-CWD test proving it actually resolves). A worktree can have
      **multiple handoff attempts over its lifetime** (the case study has
      three) — accepting only a worktree/session id is ambiguous about which
      attempt to render, so require `--token` to select one specific attempt,
      or define and document an explicit "most recent attempt" default rather
      than merging/nondeterministically picking among unrelated attempts. The
      command reads the durable per-worktree store (falling back to
      `activity.jsonl` + both session-state trace files for stages recent
      enough to still be in the rolling log) and renders the ordered
      13-stage sequence for the selected worktree/token, with gaps visibly
      marked ("stage 8 logged, stage 9 never observed").

### Phase 4 — Fix the reported failure class (deferred; evidence only for now)

> **Scope note:** the operator explicitly declined remediation of the live
> case-study worktree and asked this effort to focus on durable logging,
> active-diagnosis accessibility, and archive-durable session-state
> persistence first (Phases 1, 2, 3, 5). The items below stay as forward
> tracking for when fix work is greenlit — do not start them as part of this
> effort's first execution pass.
- [x] Reproduce the "ack but no pane, retry says already-claimed" symptom —
      **found live**, not synthetic: a real worktree observed mid-cutover
      (identifiers withheld; see Proposal § Case study). Two distinct bugs identified from real
      `activity.jsonl` data:
      1. a second `trigger_handoff` from a session id reused across mux
         resume collides with its own already-consumed prior handoff
         record/token and silently no-ops (no claim attempt, no log line);
      2. `handoff_predecessor_retire` reports `outcome: "left-running"` /
         `copilot_reaped: 0` on **every** observed cutover in this worktree,
         even ones with `successor_verified: true` — the predecessor pane may
         never actually be retired even on a "successful" cutover.
- [ ] Fix bug 1: the file-backed record keying is only half the problem —
      `dispatchHandoff()` also creates agent-dispatch tasks with
      `--dedup-key handoff-${sid}`, so a task-backed retrigger from the same
      resumed session id would keep colliding with the old task even after
      the file token is fixed. Mint a **fresh per-trigger ID** for both the
      file token and the dispatch dedup key, propagate that same fresh ID
      into the tracking record and the monitor's pending scan, and preserve
      the *existing* behavior only for an explicit recovery-token reuse
      (e.g. `trigger_handoff --handoff-token <token>` resuming a known
      in-flight token), not for an ordinary fresh trigger.
- [ ] Investigate bug 2 rather than assume it: `left-running` +
      `copilot_reaped: 0` is what the retirement code **intentionally**
      reports for guard paths (last-window, identity mismatch, unavailable
      identity), which also emit a separate `handoff_retire_guard` event —
      confirm via the guard/method fields and actual pane liveness whether
      this worktree's retirements are a real bug or working-as-designed guard
      behavior before treating it as a confirmed root cause and before
      changing `_monitor_claim_handoff_cutover`'s retire branch.
- [ ] With the new trace command (Phase 3), confirm both fixes against a fresh
      handoff cycle: full 13-stage trace, predecessor pane actually gone,
      reciprocal_relation no longer left `"ambiguous"`.
- [ ] Feed `health.find_orphaned_handoffs()` from the new trace so it can name
      the exact stage where an orphaned handoff stalled.

### Phase 5 — Docs
- [ ] Document the 13-stage lifecycle + trace command in
      `plugins/agent-worktrees/docs/architecture.md` and the context-handoff
      README's handoff-lifecycle section.
- [ ] Update the `context-handoff` skill and `worktree` skill with a pointer
      to `handoff-trace` for diagnosing a stuck cutover.

## Validation Plan

- [ ] Unit/integration tests per plugin (existing test layout — see
      `plugins/agent-worktrees/tests/`, `plugins/context-handoff/tests/`)
      asserting each of the 13 events fires with the right fields on a
      successful cutover, scoped to the mux/CLI resident-monitor path (see
      Phase 2's deferred-follow-on note for agent-bridge coverage).
- [ ] A test that intentionally kills the spawn step and asserts
      `handoff-trace` shows stages 1-7 present and 8+ absent (proves the tool
      surfaces partial traces usefully, not just full ones).
- [ ] A concurrent-writer race test against the durable per-worktree trace
      store (Phase 3): multiple simulated emitters (Python + a stand-in for
      the Node context-handoff process) appending in parallel produce no
      interleaved/corrupted lines and no dropped events — run **on both
      Linux/WSL and Windows** (this repo supports both), since the append
      contract is platform-specific per Phase 3.
- [ ] A live reproduction on this machine: trigger a real handoff on a
      disposable worktree, run `agent-worktrees handoff-trace`, confirm a
      complete 13-stage trace exists in both the predecessor's and successor's
      `~/.copilot/session-state/<sid>/handoff-trace.jsonl`.
- [ ] `find_orphaned_handoffs()` correctly names the stalled stage against a
      forced-partial-failure fixture.

## Proposal

_Phase 1 schema is the next concrete design decision (exact field names,
whether to extend `activity.py`'s existing event dataclass or add a sibling
`handoff_trace.py` module)._

### Case study: a live worktree observed mid-cutover (identifiers redacted)

Pulled directly from `agent-worktrees list --json` + the local
`activity.jsonl` lifecycle log + the local status-monitor handoff-claim
directory on 2026-09-11 (a private, local worktree/session identifier is
withheld per this repo's public-safe identity policy — see
`efforts/README.md` § Local conventions). This worktree's `reciprocal_relation`
is currently `"state": "ambiguous"`, `binding.handoff_state: "pending"`,
`binding.session_id: <session A>` (a session that is **not** the live one),
while `live_session_ids` names a completely different, fourth session id
(`<session D>`). Timeline reconstructed from `activity.jsonl` (all times UTC,
relative day 1/day 2; session labels are local aliases for this write-up, not
literal ids):

| time | event | session | notes |
|---|---|---|---|
| day1 08:23 | `session_started` | `A` | original head |
| day1 16:31 | `handoff_requested` (context-handoff) | `A` | token `T1`, stored via agent-dispatch |
| day1 16:31 | `handoff_cutover_claim` | `A` | outcome **acquired** — monitor claim works |
| day1 16:31 | `handoff_cutover_spawn` | `A` | new pane, `candidate_status: awaiting-session-association` — spawn works |
| day1 16:32 | `session_started` | `B` | successor's sessionStart fires |
| day1 16:32 | `handoff_predecessor_retire` | `A` | `successor_verified: true` but `outcome: "left-running"`, `copilot_reaped: 0` |
| day1 17:21–17:22 | (repeat cycle) | `B`→`C` | same claim→spawn→retire pattern, same "left-running" outcome |
| day1 19:10 – day2 09:42 (six `worktree_resumed` cycles over ~14h) | `session_started` for `A` / `B` **recur verbatim** across unrelated launch ids | — | expected: ordinary mux resume reattaches the *same* Copilot session id |
| day2 08:35 | `handoff_requested` | `B` | **same `handoff_id`** (`T2`) as the day1 17:21 cycle, reused verbatim |
| — | *(nothing)* | — | **no `handoff_cutover_claim`, no `handoff_cutover_spawn`, no "already-claimed" log entry follows** — the second trigger is silently absorbed |
| day2 09:38 | `session_ended` | `B` | session just ends normally, unaware its handoff never re-armed |
| day2 21:56 | `session_started` | `D` | a **brand-new** session id, never seen before, becomes the live head after the next resume |

**Root cause candidate (stronger than the original hypothesis in Context):**
context-handoff's file-backed handoff record is keyed by **session id**
(`<worktree-state-dir>/handoff/handoff-<sid>.json`). Because ordinary mux
resume reattaches the **same** Copilot session id across `worktree_resumed`
cycles (this is expected/correct behavior for resume), a *second*
`trigger_handoff` call from that same, already-once-handed-off session id
collides with the **already-consumed** record/token from its *first* handoff
cycle hours earlier. `storeHandoff()`/`noteHandoffInRecord()` and the status
monitor's pending-handoff scan apparently treat the already-resolved token as
nothing-to-do (no new pending entry, or the monitor's pending scan does not
re-arm a resolved token) — **silently**: no claim attempt, no "already-claimed"
log line, nothing. `trigger_handoff` still reports its normal ack (file write
+ best-effort pings all succeed) because none of those steps depend on the
monitor actually re-arming. The operator sees exactly the reported symptom:
ack with no pane, and by the time they notice and look again, an unrelated
resume has produced an entirely new, unaffiliated session id that the
tracking record has no linkage to at all — hence `"ambiguous"`.

Second observation from the same trace, **not yet confirmed as a bug**: every
`handoff_predecessor_retire` in this worktree's history reports
`outcome: "left-running"` with `copilot_reaped: 0` despite
`successor_verified: true`. The retirement code is known to report exactly
this outcome deliberately for guard paths (last-window, identity mismatch,
unavailable identity), alongside a separate `handoff_retire_guard` event — so
this observation needs the guard/method fields and actual pane-liveness
evidence checked (Phase 4, deferred) before concluding the predecessor is
incorrectly left alive rather than intentionally guarded.

This case study should become the primary Phase 4 validation fixture — it
already has the exact "claimed-but-no-log, then orphaned" shape once we
instrument stage 7 (host ack)/8 (spawn-started) distinctly from stage
10/11 (claim/retire) per the new schema, instead of relying on eyeballing
`activity.jsonl` by hand as done here.

## Journal

### 2026-09-11 — Kickoff
- Effort created from a live operator report: handoff cutover silently fails
  (ack with no pane; retry reports already-claimed with still no pane).
- Investigated current state across `plugins/agent-worktrees` and
  `plugins/context-handoff` via two parallel explore passes; findings folded
  into Context above. Confirmed the reported symptom matches an already-known
  gap: `health.find_orphaned_handoffs()` exists specifically because "claimed"
  and "live successor pane exists" can diverge, but there is no per-worktree
  replayable trace to show *where* that divergence happened.
- Drafted the 13-stage event vocabulary and a phased plan: schema →
  instrument all stages → cross-link predecessor/successor session-state
  journals as a linked list → root-cause + fix the reported failure → docs.
- Operator flagged a **live** instance of the exact bug on a local worktree
  (identifiers withheld per this repo's public-safe policy). Traced it in full from
  `activity.jsonl` + tracking record — see Proposal § Case study. Found two
  concrete, distinct root causes (handoff-token reuse across a resumed
  session id silently no-ops re-triggering; predecessor retire logs
  `left-running` on every cutover generation seen). Phase 4's reproduction
  step is now done against real data; the plan's fix items were rewritten to
  target these two specific causes instead of the original generic
  hypothesis.
- Operator explicitly scoped this effort to logging/observability only —
  no remediation of any live stuck worktree; Phase 4's fixes stay as
  forward-looking evidence, not this pass's execution.
- Submitted the plan as PR #2458 and drove it through **six review rounds**
  (Copilot's Lite-tier reviewer) before merge: public-safe identity redaction
  (PR body *and* file), preserving `handoff_requested` as the wire-compatible
  name instead of renaming, nullable `session_id` + required `launch_id`
  correlation, splitting the spawn stage into start+result, reusing the
  existing `sessionEnd`/`session_ended` path instead of implying a new hook,
  an explicit successor-side backfill step, a durable per-worktree trace
  store exempt from `activity.jsonl`'s rolling retention (with a
  **per-platform** atomic-append contract after the reviewer caught that
  `O_APPEND` is POSIX-only and this repo also ships Windows), scoping the
  13-stage model to the mux/CLI resident-monitor path with agent-bridge's
  independent `SessionManager.handoff_session()` spawn path explicitly
  named and deferred, correcting Stage 10's basis away from
  `associate_handoff_candidate()` (which the reviewer showed doesn't move the
  tracking head) toward the actual `tracking.link_handoff()` call site,
  moving Stage 3 off the pane wrapper (which only forwards an
  already-resolved command) onto the real Copilot resolution/exec point, and
  namespacing the durable trace store by project (worktree ids are only
  project-scoped). PR #2458 merged 2026-09-11.
- **Second live corroboration**, in real time, on a different worktree
  (a different worktree; identifier omitted per public-safe policy): the
  operator reported "handoff triggered, no
  replacement mux pane" while this PR was still in review. Traced via
  `activity.jsonl`: an earlier handoff cycle completed a full
  claim→spawn→retire sequence hours prior; a fresh `trigger_handoff` then
  reused that exact same `handoff_id` (its session id had been reattached
  across an intervening mux resume, same mechanism as the first case study),
  and no claim/spawn/log followed — the request was silently swallowed
  again. No remediation performed, per standing instruction. This is the
  same already-documented root cause recurring independently, not a new
  bug class — it further substantiates Phase 4's bug 1 without changing this
  effort's Phase 1-3/5 scope.
- **Phase 1 executed**: landed `HANDOFF_STAGE_MAP` + `log_event()`
  auto-stamping in `activity.py` (PR #2472, plugins/agent-worktrees test
  suite: 4149 passed / 20 skipped / 3 pre-existing unrelated failures
  noted in the PR description). Next slice: Phase 1's remaining schema items
  (spawn start/result event split as dedicated wire events) and Phase 2's
  per-stage instrumentation across `sessions.py`, `__main__.py`, the launcher
  scripts, and context-handoff's `handoff-core.mjs`.
- **Phase 1 fully merged** (PR #2472, six review rounds). Beyond the schema
  landing itself, review caught two real correctness bugs the auto-stamping
  design hadn't accounted for, both now fixed and tested: (1)
  `handoff_cutover_claim` fires for `outcome="already-claimed"` and
  `outcome="error"` as well as `"acquired"` — only `"acquired"` is stamped as
  a Stage 7 success now (`_HANDOFF_STAGE_GATE`); (2) `handoff_predecessor_retire`
  fires for `outcome="identity-mismatch"` and `outcome="left-running"` as well
  as `"gone"` — only `"gone"` (the actual case study's every observed
  retirement was `"left-running"`, never `"gone"` — this independently
  confirms the Proposal § Case study's flagged-but-unconfirmed observation was
  onto something real, though whether it's a bug or an intentional guard per
  `handoff_retire_guard` is still Phase 4's open question) is stamped as a
  Stage 11 success. A caller-supplied `stage=`/`stage_name=` on a *mapped*
  event is now reserved (can't override the canonical stamp); an *unmapped*
  custom event's own fields pass through untouched (an earlier fix attempt
  over-corrected and accidentally stripped those too — caught in the same
  review). `agent-worktrees` bumped 1.5.5-dev65 → dev69 across the fix
  rounds.
- **Next slice for a fresh session:** Phase 1's still-open item (split Stage
  8's spawn event into an explicit start + a terminal success/failure result —
  currently only `handoff_cutover_spawn`'s single success-only shape exists;
  make `log_event`/`activity.log_event` non-silently-swallowing per Phase 1's
  last checklist item), then Phase 2's remaining per-stage instrumentation
  (stages 2-13's actual emitter call sites: `sessions.py`'s `mux_new_session`/
  `mux_new_window`, the `bin/launch-session.sh`/`.ps1` launchers' existing
  `mux_attached` boundary, `default-setup.sh`/`.ps1`'s real Copilot exec point
  for Stage 3, `cmd_register_session` for stages 4/9, the status-report tool
  path for stage 5, locating the actual `tracking.link_handoff()` call site
  for stage 10 per the effort's own corrected basis, and extending the
  existing `sessionEnd`/`session_ended` path for stage 12), landing each as
  its own small reviewed PR the same way Phase 1 did. Phase 3 (durable
  per-worktree/per-project trace store with the per-platform atomic-append
  contract, successor backfill, `handoff-trace` CLI) and Phase 5 (docs) follow.
  Phase 4 stays deferred per the operator's explicit scope decision.
