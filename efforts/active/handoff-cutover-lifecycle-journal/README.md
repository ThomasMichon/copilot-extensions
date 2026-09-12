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
- [ ] Every event carries: `worktree_id`, `stage` (ordinal + name),
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
      **Partially landed (schema supports this, population is not yet
      complete):** all base fields already existed as `log_event()` params/
      `**fields`; `stage`/`stage_name` are auto-stamped for a *mapped,
      ungated* event (PR #2472) — a gated claim/retire outcome (e.g.
      `outcome="already-claimed"`) correctly omits `stage`/`stage_name`
      rather than carrying them, so "every event carries `stage`" is not
      literally true by design. `predecessor_session_id`/
      `successor_session_id` are never initialized yet — that's Phase 3's
      cross-linking work. Leave this item open until Phase 3 populates the
      linkage fields; re-close it then rather than now.
- [x] Give the spawn stage (8) an explicit **start** event
      (`handoff_successor_spawn_started`, emitted before success is known)
      *and* a terminal **result** event/field
      (`handoff_successor_spawn_result: succeeded|failed`, or reuse
      `handoff_cutover_spawn`'s existing success-only shape plus a new
      failure-path sibling) — a single one-shot event cannot distinguish "the
      spawn is still in flight" from "the spawn failed," which the Phase 3/4
      validation plan (stages 8+ absent on a killed spawn) depends on.
      **Landed:** `handoff_successor_spawn_started` now fires in
      `_handoff_cutover_spawn_result` immediately before
      `sessions.mux_new_window()` is called; the existing
      `handoff_cutover_spawn` remains the terminal success event, and a new
      `handoff_successor_spawn_failed` sibling fires on the failure path.
      Both new event names are mapped to stage 8 in `HANDOFF_STAGE_MAP`.
- [x] Make `activity.log_event()` (or a new sibling) **not** swallow errors
      silently in a way that's invisible — keep best-effort delivery (never
      block the caller) but surface a debug-level warning/counter so a
      missing event is itself detectable, not just theorized.
      **Landed:** `log_event()` still never raises, but a write failure now
      increments a process-local `log_event_failure_count()` and emits a
      `logging.getLogger("agent-worktrees").debug(...)` line, so a dropped
      event is detectable rather than only inferable from a trace gap.

### Phase 2 — Instrument all 13 stages
- [ ] Stage 1 (`worktree_created`) — confirm `cmd_create` already emits this
      with no gaps; add `predecessor_session_id: null` framing.
      **Partially landed (confirmation only):** `cmd_create` already emits
      `worktree_created` unconditionally with `worktree_id`/`branch` — no gap
      in the event itself. The `predecessor_session_id: null` framing is
      *not yet* added: `log_event()`'s `**fields` silently drop a `None`
      value (only the named `session_id`/`launch_id` params survive as
      explicit nulls), so this needs `predecessor_session_id` promoted to a
      first-class field the same way Phase 1's schema item already defers —
      leave this item open until Phase 3's cross-linking work adds that
      field generally, then re-close it here alongside that.
- [x] Stage 2 (`mux_session_assigned`) — **ordinary launches don't go through
      the Python `sessions.py` helpers at all**: `bin/launch-session.sh` /
      `.ps1` invoke `tmux`/`psmux new-session` directly and already emit
      `mux_attached` after success. Emit stage 2 from that existing
      launcher creation/join boundary for the ordinary-launch path, and
      additionally from `mux_new_session()` / `mux_new_window()` (after the
      mux subprocess call succeeds, not from the pure argv-builder
      `build_mux_new_window_argv()`) for the programmatic cutover path — both
      emitters, not one instead of the other.
      **Landed:** `mux_new_session()` (the embody/programmatic path) and
      `mux_new_window()` (the handoff-cutover spawn path) each now emit
      `mux_session_assigned` right after their mux subprocess call succeeds
      — `mux_new_window()`'s emission sits before the seed/prompt-receipt
      wait, since pane creation and seed-readiness are distinct concerns.
      The ordinary-launch path's pre-existing `mux_attached` already covered
      the other emitter (both already mapped to stage 2 in
      `HANDOFF_STAGE_MAP`).
- [x] Stage 3 (`copilot_invoked`) — the pane wrapper only **forwards** an
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
      **Landed:** both `default-setup.sh` and `default-setup.ps1` now emit
      `copilot_invoked` (best-effort, detached) right before each of their
      `exec`/launch branches (override path, ambient `copilot`, `gh copilot`
      fallback). Added a new `get worktree-id` CLI key so the launcher can
      resolve the worktree id from CWD without a session id in hand. The
      config-driven launch-template and legacy `tools/setup/setup.{sh,ps1}`
      paths (which never reach default-setup's own emitter) are covered too:
      `launch-command.{sh,ps1}` -- the one wrapper seam every resolved
      command passes through -- emits the coarser `copilot_invocation_attempted`
      event for them, gated on the wrapper's existing default-setup
      detection so the two paths don't double-emit.
- [x] Stage 4 (`session_start_bound`) — emit from `cmd_register_session` once
      a session id + worktree are actually resolved (already close to
      `session_started`; make sure the *binding* moment, not just tool entry,
      is what's logged).
      **Confirmed landed:** `cmd_register_session` already emits
      `session_started` immediately after `tracking.register_session()`
      succeeds — the actual binding moment, not mere tool entry; no code
      change needed, ticked off as a Phase 2 confirmation.
- [x] Stage 5 (`status_reported`) — emit from the `status`/status-report tool
      path when it first runs in a session (marks "Copilot did something in
      this worktree").
      **Landed:** `_cmd_status_write` (the `status --summary/--title/
      --follow-up/--resolved` disposition write) now emits `status_reported`
      once per `COPILOT_AGENT_SESSION_ID`, gated by checking prior events for
      that session id so a chatty session's repeated disposition edits don't
      spam the trace.
- [x] Stage 6 (`handoff_triggered`) — covered by context-handoff's existing
      `handoff_requested` wire event; add the `stage`/`stage_name` fields to
      it in place rather than introducing a second event name.
      **Confirmed landed (no code change):** Phase 1's `HANDOFF_STAGE_MAP`
      already maps `handoff_requested` → stage 6/`handoff_triggered`, and
      `log_event()` auto-stamps it regardless of caller (the context-handoff
      extension calls this via the `activity-log` CLI, which is a thin
      wrapper over `log_event()`) — already covered by
      `test_log_event_stamps_known_handoff_stage`. Ticked off as a Phase 2
      confirmation, same pattern as stage 4.
- [x] Stage 7 (`handoff_host_acknowledged`) — new: emit when the resident
      status monitor's `_monitor_pending_handoff_request()` first observes and
      accepts the pending handoff, distinct from the later claim. (Per the
      scope decision above, this covers the resident-monitor runner only —
      agent-bridge's independent `handoff-request` route is documented in
      Context as a real alternate path but is explicitly out of scope for
      this effort's instrumentation; see the deferred follow-on note below.)
      **Confirmed already landed (no code change):** `handoff_cutover_claim`
      (emitted by `_monitor_claim_handoff_cutover`, called from
      `_monitor_pending_handoff_request`) already fires with
      `outcome="acquired"` for exactly the first monitor pass that accepts a
      pending handoff, and Phase 1's `HANDOFF_STAGE_MAP` + `_HANDOFF_STAGE_GATE`
      already stamp only that outcome as Stage 7 (`already-claimed`/`error`
      outcomes are gated out) — covered by
      `test_log_event_maps_existing_events_to_their_stage` and
      `test_log_event_does_not_stamp_a_failed_or_duplicate_claim`. Ticked off
      as a Phase 2 confirmation, same pattern as stages 4/6.
- [x] Stage 8 (`handoff_successor_spawn_started`) — emit at the start of
      `_handoff_cutover_spawn_result` / `mux_new_window`, before success is
      known, so a spawn that later fails still leaves a trace.
      **Landed as part of Phase 1's spawn-event split** (PR #2479):
      `_handoff_cutover_spawn_result` emits `handoff_successor_spawn_started`
      immediately before `sessions.mux_new_window()`, with
      `handoff_successor_spawn_failed` on the failure path and the existing
      `handoff_cutover_spawn` retained as the terminal success event.
- [x] Stage 9 (`handoff_successor_session_start_bound`) — emit from the
      successor's own `cmd_register_session` when it recognizes the
      `--handoff-candidate-token` and calls `associate_handoff_candidate`.
      **Landed:** `cmd_register_session` now emits
      `handoff_successor_session_start_bound` immediately after
      `tracking.associate_handoff_candidate()` succeeds (guarded by the same
      `candidate_token` check), carrying `worktree_id`/`session_id`/
      `handoff_token`.
- [x] Stage 10 (`handoff_successor_claimed`) — **must be emitted at the point
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
      **Landed:** the exact call site is `tracking.register_session()`'s
      internal `link_handoff()` invocation (reached via the `--handoff-token`
      CLI arg on both `register-session` and `bind-session` — NOT the separate
      `--handoff-candidate-token` path that only feeds stage 9). `register_session()`
      now returns the freshly-linked `SessionHandoff` (or `None` for no fresh
      transfer, including an idempotent re-link of an already-linked token), and
      `cmd_register_session`/`cmd_bind_session` emit `handoff_successor_claimed`
      via a shared `_emit_handoff_claim_stages()` helper whenever a fresh link
      is reported.
- [x] Stage 11 (`handoff_pickup_confirmed_predecessor_closing`) — emit
      immediately after the same `tracking.link_handoff()` call site used for
      stage 10 (today's monitor retires the predecessor right after the head
      transfers, not after a separate later successor acknowledgement) — keep
      10 and 11 adjacent in the same code path until/unless a future change
      makes retirement wait for an explicit `consume_handoff` acknowledgement.
      **Landed together with Stage 10** (same `_emit_handoff_claim_stages()`
      call): `handoff_pickup_confirmed_predecessor_closing` is emitted keyed by
      the *predecessor's* session id (`linked_handoff.predecessor`), with
      `successor_session_id` carrying the new head.
- [x] Stage 12 (`session_end_bound`) — a `sessionEnd` hook and its
      `session_ended` deregistration event **already exist**; extend that
      existing path to also emit `session_end_bound` with the stage/linkage
      fields rather than treating stage 12 as possibly missing or adding a
      second, duplicate hook path.
      **Confirmed already landed (no code change):** `cmd_deregister_session`
      already emits `session_ended` unconditionally on every sessionEnd hook
      invocation, and Phase 1's `HANDOFF_STAGE_MAP` already maps it to Stage
      12/`session_end_bound` with no gate. Confirmed end-to-end (not just the
      map entry in isolation) via a new test,
      `test_session_end_emits_stage_12_via_the_existing_session_ended_event`,
      that drives the real `cmd_deregister_session` hook path and asserts the
      logged event carries `stage=12`/`stage_name="session_end_bound"`. Ticked
      off as a Phase 2 confirmation, same pattern as stages 4/6/7.
- [x] Stage 13 (`handoff_complete`) — emit once the runner has confirmed the
      predecessor pane is gone and the successor is head.
      **Landed:** a new `_maybe_emit_stage_13()` helper checks BOTH
      confirmations (`handoff_predecessor_retire` with `outcome="gone"` for
      the exact token, AND the tracking record's handoff `state == "linked"`,
      not merely a candidate) and emits `handoff_complete` exactly once,
      deduped via an **atomic exclusive-create claim file** keyed by a
      collision-resistant digest of the exact (worktree, token) pair --
      `read_events()` is used only for the retire-confirmation check above,
      never as the dedup gate itself (a plain read-before-write check is a
      real cross-process race here). The claim is rolled back on a detected
      logger write failure (`activity.log_event_failure_count()`), so a
      transient I/O error stays retryable. Called from both sides of the
      ordering race the case study exposed: `_handoff_cutover_retire_result()`
      (right after it logs the retire outcome) and
      `_emit_handoff_claim_stages()` (right after Stage 10/11) -- whichever
      confirmation completes second is the one that actually emits Stage 13.

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
      **Implementation landed in PR #2496** (`handoff_trace.py`): namespaced
      per-project store, `fcntl`/`msvcrt` cross-process advisory lock,
      `activity.log_event()` write-through wired for every stage-mapped
      event, path-traversal validation on both `project`/`worktree_id`, a
      `remove_trace()` reap hook wired into all three tracking-record
      removal paths, and a subprocess-based (real separate OS processes, not
      just threads) concurrent-append race test proving the lock actually
      serializes independent processes on this machine's OS. **Left open:**
      a real run of that same test on a Windows CI runner (the code path is
      OS-selecting via `_append_lock`, but it has not yet been *observed*
      passing on Windows), and the `handoff-trace` CLI itself.
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
- **Phase 1's first slice merged** (PR #2472, six review rounds; Phase 1
  itself still has open items — see the Plan checklist). Beyond the schema
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
- **Phase 1 fully closed.** Landed the two remaining checklist items in a
  fresh worktree: `_handoff_cutover_spawn_result` now emits
  `handoff_successor_spawn_started` immediately before
  `sessions.mux_new_window()` (before success/failure is known), and a new
  `handoff_successor_spawn_failed` sibling fires on the failure path — both
  map to stage 8 in `HANDOFF_STAGE_MAP`, alongside the pre-existing terminal
  success event `handoff_cutover_spawn`. Separately, `activity.log_event()`
  no longer swallows a write failure invisibly: it still never raises into
  the caller, but a failure now increments a new
  `log_event_failure_count()` and logs a `logging.getLogger(
  "agent-worktrees").debug(...)` line. Submitted as PR #2479; review caught
  two real gaps beyond the initial landing, both fixed: (1) no command-level
  test asserted the new events' emission/ordering — added
  `test_spawn_success_emits_started_then_success_event` and
  `test_spawn_failure_emits_started_then_failed_event`; (2) an exception
  `sessions.mux_new_window()` doesn't itself catch (it only guards
  `OSError`/`RuntimeError`/`TimeoutExpired`) would have left the trace stuck
  at "started" forever with no terminal event — wrapped the call in
  `try`/`except Exception`, log `handoff_successor_spawn_failed` with the
  exception message, then re-raise unchanged (`test_spawn_exception_from_mux_still_emits_failed_event`
  covers this). Also fixed a version-count error a review caught in this
  same journal entry. Five new tests total (two in `test_activity.py`,
  three in `test_handoff_cutover.py`); full plugin suite: 4158 passed /
  20 skipped / 3 pre-existing unrelated installer/binstub failures (same
  three noted against PR #2472). `agent-worktrees` bumped 1.5.5-dev69 →
  dev72 across the fix rounds. Stage 8's Phase 2 checklist item is also now
  done as a side effect (ticked off above). Phase 2 (instrumenting the
  remaining 10 stages' actual emitter call sites) is next.
- **Phase 2 started.** PR #2479 merged. Confirmed Stage 4 (`session_start_bound`)
  already satisfies its acceptance criteria in existing code —
  `cmd_register_session` already emits `session_started` right after
  `tracking.register_session()` succeeds, the actual binding moment — no code
  change needed, ticked off as a confirmation. Stage 1 (`worktree_created`)
  is only *partially* confirmed: `cmd_create` already emits the event with no
  gaps, but the `predecessor_session_id: null` framing the checklist also
  asks for isn't landed yet (`log_event()`'s `**fields` silently drop a
  `None` value; only the named `session_id`/`launch_id` params survive as
  explicit nulls) — left open pending Phase 3's field promotion, per review
  (PR #2488's "Do not mark Stage 1 complete without predecessor framing").
  Landed Stage 9 (`handoff_successor_session_start_bound`):
  `cmd_register_session` now emits it immediately after stage 4's
  `session_started` (moved out of the `try` block that originally fired it
  *before* stage 4 — review caught the backwards ordering), carrying the
  same `launch_id` as `session_started` (review caught the missing
  correlation field too), guarded by the same `candidate_token`/
  `candidate_associated` check. Updated test
  `test_session_start_emits_stage_9_on_candidate_association` in
  `tests/test_register_session.py` now asserts the stage-4-then-stage-9
  ordering and the `launch_id` field, in addition to the event's other
  fields. `agent-worktrees` bumped 1.5.5-dev72 → dev73 in this PR (#2488) —
  PR #2479's own merge ended at dev72. No further bump needed for the
  review-response commit within this PR (module-size-only compaction, no
  further plugin content growth). Remaining Phase 2 stages: 2, 3, 5, 6, 7,
  10, 11, 12, 13.
- **Phase 2 continuation: stages 2, 3, 5, 6 landed.** `sessions.py`'s
  `mux_new_session()` (embody/programmatic path) and `mux_new_window()`
  (handoff-cutover spawn path) now each emit `mux_session_assigned` (stage
  2) right after their mux subprocess call succeeds — `mux_new_window()`'s
  emission is placed before the seed/prompt-receipt wait so it reflects
  pane creation, not Copilot readiness. `default-setup.sh`/`.ps1` now emit
  `copilot_invoked` (stage 3) right before each of their `exec`/launch
  branches, resolving the worktree id via a new `get worktree-id` CLI key
  (added since the launcher has no session id in hand at that point).
  `_cmd_status_write` now emits `status_reported` (stage 5) once per
  `COPILOT_AGENT_SESSION_ID`, gated against prior events for that session.
  Stage 6 (`handoff_triggered`) needed **no code change** — Phase 1's
  `HANDOFF_STAGE_MAP` already stamps `handoff_requested` as stage 6
  regardless of caller, already covered by
  `test_log_event_stamps_known_handoff_stage` — ticked off as a
  confirmation, same as stage 4. Both `sessions.py` and `__main__.py` sit at
  their exact module-size-baseline ceilings, so each addition was offset by
  an equal-or-greater compaction elsewhere in the same file (collapsing
  multi-line dict/set literals and call args that already fit the 99-column
  limit on one line) — `tools/check-module-size.py` passes at zero slack in
  both files. New/updated tests: `test_handoff_cutover.py` (stage-2 emission
  + non-emission-on-failure for `mux_new_window`), `test_embody.py`
  (stage-2 emission for `mux_new_session`), `test_status_write.py`
  (stage-5 emission, once-per-session dedup, no-session-id no-op),
  `test_context_resolution.py` (`get worktree-id`, both inside-worktree and
  at-anchor cases). Full plugin suite: 4167 passed / 20 skipped / 3
  pre-existing unrelated installer/binstub failures (same three noted
  against PR #2472/#2479 — confirmed still present on `origin/main` before
  this change, unaffected by it). `agent-worktrees` bumped 1.5.5-dev73 →
  dev74. Remaining Phase 2 stages: 7, 10, 11, 12, 13.
- **PR #2491 review response (same slice).** Copilot's review caught three
  real gaps. (1) Stage 3 coverage was incomplete: `_build_launch_cmd` also
  supports config-driven `launch`/`launch_recovery` templates and legacy
  `tools/setup/setup.{sh,ps1}` paths that never reach default-setup's own
  emitter. Fixed by instrumenting `launch-command.{sh,ps1}` -- the one
  wrapper seam every resolved launch command passes through -- with a new,
  coarser `copilot_invocation_attempted` event (mapped to stage 3 in
  `HANDOFF_STAGE_MAP` alongside `copilot_invoked`, same convention as stage
  8's started/failed pair), gated on the wrapper's own existing
  default-setup detection so the two normalized paths don't double-emit.
  (2) The stage-5 status dedup (once-per-session-id) was racy: the
  check-then-append happened *after* releasing the tracking record's
  cross-process lock, so two concurrent `status` processes sharing a
  session id could both observe "no prior event". Fixed by moving the
  check+emit inside the same `tracking._RecordLock` critical section as the
  disposition write, so it's serialized per worktree. (3) The Windows
  `default-setup.ps1` emitter had no test coverage. Added a cross-platform
  integration test that runs the actual `.ps1` under PowerShell Core (pwsh
  is cross-platform, so this doesn't need a native Windows box) covering
  the no-runtime-available early-return path; full event-content coverage
  (via the `conhost.exe`-spawned writer) remains untested here, matching the
  existing, accepted gap for `launch-session.ps1`'s own `Write-ActivityLog`
  helper -- neither has direct test coverage of the actual Windows-only
  spawn, both share the same `-WindowStyle Hidden`/`conhost.exe` mechanism.
  New/updated tests: `test_machine_settings_reconcile.py` (two new tests for
  the wrapper's stage-3 emission and its default-setup skip),
  `test_launch_cmd.py` (one new pwsh-based no-runtime-path test). Full
  plugin suite: 4170 passed / 20 skipped / same 3 pre-existing unrelated
  failures. No version bump needed for this same-PR review-response commit
  (still dev74; only the final shipped version needs to differ from the
  PR's base per convention).
- **PR #2491, unrelated-merge collision + a second review round.** Rebasing
  onto `origin/main` (to satisfy CI's version-bump check, which diffs
  against the PR's live base, not its fork point) pulled in two already-
  merged, unrelated PRs: one added +43 lines to `__main__.py` without its
  own compaction (using up module-size headroom another PR had opened),
  and another independently bumped `agent-worktrees` to the same
  `1.5.5-dev74` this PR had already claimed -- a version-number collision.
  Fixed by compacting `__main__.py` (~45 more lines, same multi-line-
  literal technique) and `repos.py` (1 line, an unrelated pre-existing
  1-over-cap violation surfaced by the rebase, not caused by this PR) back
  under their ceilings, and bumping to `1.5.5-dev75`. The next review round
  caught one more real bug plus two process gaps: (1) the stage-5 dedup
  query's `limit=500` was applied *after* filtering by `read_events`, so a
  worktree with 500+ newer `status_reported` events could silently drop an
  older matching one and re-emit -- removed the limit for this specific
  query (the log is already retention-pruned, so unbounded is cheap and
  correct). (2) The new Windows no-runtime-path test used a POSIX `#!/bin/sh`
  fixture unconditionally, which a real Windows `pwsh` runner cannot execute
  -- fixed to branch on `os.name == "nt"` for a `.cmd` fixture, matching the
  adjacent test's own pattern (this repo's CI runs both an `ubuntu-latest`
  and a `windows-latest` runner, so the mismatch would have failed there
  even though it passed locally on Linux). (3) The marketplace catalog's
  top-level `metadata.version` was left stale at `1.7.7-dev68` while the
  `agent-worktrees` plugin entry advanced to `dev75` -- bumped the catalog
  version to `1.7.7-dev69` too, per `CONTRIBUTING.md`'s two-version
  requirement. The remaining flagged items (config/legacy launch-path stage
  3 coverage, and the RecordLock-based dedup race) are the SAME threads from
  the prior round, already fixed there -- the bot's diff view doesn't always
  re-evaluate a carried-over thread against a fix landed in an earlier
  commit of the same PR, a known quirk from prior PRs in this effort.
  Verified via `git show HEAD:<file>` that both are still fixed as landed.
  Full plugin suite: 4184 passed / 20 skipped / same 3 pre-existing
  unrelated failures.
- **PR #2491, review round 4 (a real bug + reply/resolve hygiene).**
  Replied to and explicitly resolved the three still-open review threads
  (the stale config/legacy-launch-path thread, the Windows-coverage thread,
  and the PR-description-version thread) via the GraphQL
  `resolveReviewThread` mutation, updated the PR description to `dev75` with
  an `Additional fixes` section, and requested a fresh review. That review
  caught one genuinely new, real bug: `_log_copilot_invoked` (both
  `default-setup.{sh,ps1}`) and the `launch-command.{sh,ps1}` wrapper's
  Stage 3 emitter only ever resolved the runtime from the legacy
  `$HOME/.agent-worktrees` (or `%USERPROFILE%`) path -- but a
  contextual/cell launch validates and exports its own runtime root as
  `AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT` (`bin/launch-session.{sh,ps1}`),
  which may have no install at the legacy path at all, silently dropping
  Stage 3 for exactly those launches. Fixed all four emitters (plus
  `default-setup.ps1`'s pre-existing identical gap in its own setup-hook
  config-root guard resolution, since it's the same one-line fix in the
  same file) to prefer `AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT` before the
  legacy fallback, matching `bin/launch-session.{sh,ps1}`'s own precedence.
  This also surfaced a pre-existing test-isolation gap: `cmd_launch`
  (production code, not test scaffolding) sets
  `AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT` directly on the real `os.environ`
  (not via `monkeypatch`), so it leaks across tests in the same pytest
  process -- an earlier test's stale value made two of my own new
  `launch-command.sh` tests flaky (only visible running the full suite,
  not in isolation). Fixed by explicitly popping the var in the three
  affected tests before building each subprocess's env. Full plugin suite:
  4184 passed / 20 skipped / same 3 pre-existing unrelated failures.
- **Phase 2 continuation: stages 7, 10, 11 landed.** Stage 7
  (`handoff_host_acknowledged`) needed **no code change** — the pre-existing
  `handoff_cutover_claim` event (emitted by `_monitor_claim_handoff_cutover`,
  called from `_monitor_pending_handoff_request`) already fires with
  `outcome="acquired"` at exactly the resident monitor's first
  observe-and-accept of a pending handoff, and Phase 1's `HANDOFF_STAGE_MAP`
  already stamps only that outcome as Stage 7 — confirmed via
  `test_log_event_maps_existing_events_to_their_stage` and
  `test_log_event_does_not_stamp_a_failed_or_duplicate_claim`, ticked off as a
  confirmation same as stages 4/6. Stages 10 (`handoff_successor_claimed`) and
  11 (`handoff_pickup_confirmed_predecessor_closing`) needed a real new
  emitter: located the exact head-transfer call site as
  `tracking.register_session()`'s internal `link_handoff()` invocation
  (reached via the `--handoff-token` CLI arg on both `register-session` and
  `bind-session` -- distinct from the separate `--handoff-candidate-token`
  path that only feeds Stage 9). `register_session()` now returns the
  freshly-linked `SessionHandoff` (or `None` when no fresh transfer happened,
  including an idempotent re-link of an already-linked token, so a resumed
  session or duplicate hook call never double-emits), via a new
  `_link_if_fresh()` closure that compares the handoff's state *before*
  calling `link_handoff()` against its state after. `cmd_register_session` and
  `cmd_bind_session` both emit Stage 10 + Stage 11 together through a shared
  `_emit_handoff_claim_stages()` helper whenever a fresh link is reported --
  Stage 11 is keyed by the *predecessor's* session id
  (`linked_handoff.predecessor`) with `successor_session_id` carrying the new
  head, matching the README's "keep 10 and 11 adjacent in the same code path"
  guidance. Both `tracking.py` and `__main__.py` sit at their exact
  module-size-baseline ceilings, so each addition was offset by an
  equal-or-greater compaction elsewhere in the same file (collapsing
  multi-line `raise`/dict-field/call-arg literals that already fit the
  99-column limit onto one line) -- `tools/check-module-size.py` passes at
  zero slack in both files, and `ruff check` shows no new findings (the
  pre-existing `E501`/`I001`/`RUF003` findings in `__main__.py` are unrelated
  and unchanged). New tests: `test_tracking.py` (`register_session()`'s
  return-value contract across the existing-entry and new-entry code paths,
  the already-linked idempotency case, and the no-token case),
  `test_register_session.py` (Stage 10/11 emission via `--handoff-token`,
  ordering vs. Stage 9, and idempotent non-re-emission on a second hook
  call), `test_bind_session.py` (Stage 10/11 emission via `bind-session
  --handoff-token`, reusing the existing candidate-acknowledgement fixture).
  Full plugin suite: 4191 passed / 20 skipped / same 3 pre-existing unrelated
  installer/binstub failures (confirmed still present against `origin/main`
  before this change). `agent-worktrees` bumped 1.5.5-dev75 -> dev76.
  Remaining Phase 2 stages: 12, 13.
- **PR #2493 review response (stages 7/10/11).** Copilot's review caught two
  real Medium findings and one Low nit. (1) The resident monitor's own retire
  flow can stamp `handoff_predecessor_retire` (outcome="gone") as Stage 11
  *before* a successor's later `--handoff-token` claim runs -- retirement is
  gated on candidate presence, not on the link_handoff() call this PR
  instruments -- so the new emitter could double-record Stage 11 for the same
  handoff. Fixed by having `_emit_handoff_claim_stages()` check
  `activity.read_events()` for an already-stamped `outcome="gone"` retire
  event for the exact token before emitting its own Stage 11, so the terminal
  retire signal (when it fires) wins and the new link-based signal only fills
  the (common, per the Phase 1 case study) gap where it never does. (2)
  `cmd_bind_session`'s call to the new helper omitted `launch_id`, dropping
  Stage 10/11 out of `activity --launch-id` correlation for the normal mux
  pane bind path (which carries `WORKTREE_LAUNCH_ID` in its environment, same
  as `cmd_register_session` already threads through) -- fixed by passing
  `os.environ.get("WORKTREE_LAUNCH_ID")` explicitly. New tests:
  `test_claim_after_a_confirmed_predecessor_retire_only_emits_stage_10` and
  `test_stage_10_and_11_carry_the_mux_pane_launch_id`. `__main__.py` needed
  another compaction pass (six more clean single-line collapses) to stay at
  its module-size ceiling after the added dedup-check code. Full plugin
  suite unaffected outside the touched tests. `agent-worktrees` version
  unchanged at `1.5.5-dev76` (same open PR, not yet merged).
- **Phase 2: stages 12, 13 landed (all stages except Stage 1's deferred
  sub-item).** Stage 12 (`session_end_bound`)
  needed **no code change** -- `cmd_deregister_session` already emits
  `session_ended` unconditionally on every sessionEnd hook call, and Phase
  1's `HANDOFF_STAGE_MAP` already maps it to Stage 12 with no gate; confirmed
  end-to-end (not just the map entry) via
  `test_session_end_emits_stage_12_via_the_existing_session_ended_event`,
  which drives the real hook path. Stage 13 (`handoff_complete`) needed a
  genuinely new emitter: a `_maybe_emit_stage_13()` helper checks BOTH
  confirmations -- `handoff_predecessor_retire` stamped `outcome="gone"` for
  the exact token, AND the tracking record's handoff `state == "linked"`
  (authoritative head transfer, not merely a candidate) -- and emits
  `handoff_complete` exactly once (deduped via `activity.read_events()`).
  Called from BOTH sides of the ordering race the Stage 10/11 review already
  surfaced (retire can complete before or after the claim):
  `_handoff_cutover_retire_result()` right after it logs the retire outcome,
  and `_emit_handoff_claim_stages()` right after Stage 10/11 -- whichever
  confirmation completes second is the one that actually fires Stage 13.
  **Stages 2-13 of the lifecycle vocabulary are now instrumented; Stage 1's
  `predecessor_session_id: null` framing remains its own explicitly-deferred
  sub-item (see the Plan checklist), left open for Phase 3.** New
  tests: `test_register_session.py` (Stage 12 end-to-end confirmation; Stage
  13 firing on the claim side when retire already happened; Stage 13 NOT
  firing when only a candidate, never linked; Stage 13 firing on the retire
  side when the claim already happened, with double-call idempotency),
  `test_handoff_cutover.py` (Stage 13 firing through the real
  `_handoff_cutover_retire_result()` wiring, not just the standalone helper).
  `__main__.py` needed another compaction pass (safe 3-line combines plus a
  handful of comma-preserving/space-joined 4-line combines, each individually
  verified to still parse) to absorb the new helper + two call sites at its
  module-size ceiling -- an earlier attempt at a more aggressive generic
  multi-line-span combiner concatenated adjacent tokens without a separating
  space (e.g. `is not Noneelse`) and broke syntax; reverted via
  `git checkout --` and redone with conservative, individually-verified
  combines only. `agent-worktrees` bumped `1.5.5-dev76` -> `dev77` (catalog
  `1.7.7-dev70` -> `dev71`). Full plugin suite: 4197 passed / 20 skipped / 3
  pre-existing unrelated installer-binstub failures (same three noted
  throughout this effort, confirmed unaffected).
- **PR #2494 review response (stages 12/13, five rounds).** Copilot's review
  caught a real progression of Stage 13 correctness issues, each fixed and
  re-reviewed: (1) Stage 13's dedup was a `read_events()` check-then-act
  across concurrent processes (the successor's sessionStart and the
  resident monitor's retire) -- replaced with an atomic exclusive-create
  claim file; (2) the claim key used the lossy
  `_monitor_handoff_claim_segment()` sanitization, letting distinct tokens
  (e.g. `task:1` / `task_1`) collide -- replaced with a sha256 digest of the
  exact (worktree, token) pair; (3) a detected logger write failure left the
  claim permanently consumed with no retry path -- added a
  `log_event_failure_count()`-based rollback; (4) **HIGH severity**: the
  rollback itself could race a losing caller's own `FileExistsError` check,
  so a loser now waits briefly and retries the claim if it observes the
  holder's rollback rather than just giving up; (5) the claim directory's
  `mkdir()` wasn't inside the same `OSError` guard as the claim file itself.
  Also fixed a missing `launch_id` on the Stage 13 event and a test variable
  typo, and replaced a sequential "race" test that would have passed even
  against the old broken implementation with a real
  `threading.Barrier`-synchronized concurrent test. Every finding was
  replied to individually (citing the fixing commit + new test), explicitly
  resolved via GraphQL `resolveReviewThread`, and a fresh review requested
  each round -- the fifth round returned zero new findings (all nine review
  threads resolved). `agent-worktrees` version held at `1.5.5-dev77`
  throughout (all five commits landed in the same still-open PR; only the
  final shipped version needs to differ from the PR's base per the
  version-bump policy). PR #2494 merged via `pr-merge --now`. **Stages 2-13
  of the handoff lifecycle are now instrumented, tested, and merged. Stage 1
  remains its own explicitly-deferred sub-item** (the
  `predecessor_session_id: null` framing, left open for Phase 3 per the Plan
  checklist -- a subsequent completion-marker PR incorrectly claimed "all 13
  stages" complete; corrected here after review caught the contradiction).
  Next: Phase 3 (durable per-project trace store + cross-linking +
  `handoff-trace` CLI) and Phase 5 (docs).
- **PR #2496 (Phase 3 slice 1: durable per-project trace store), merged.**
  Landed `handoff_trace.py` (namespaced per-project/per-worktree JSONL sink,
  `fcntl`/`msvcrt` cross-process advisory lock, `activity.log_event()`
  write-through for every stage-mapped event) plus two review rounds' worth
  of fixes: (1) path-traversal validation on `project`/`worktree_id` before
  either is used as a path component; (2) a real subprocess-based (separate
  OS processes, not just threads) concurrent-append race test proving the
  lock actually serializes independent processes; (3) Tier C documentation
  in `docs/patterns/lifecycle-activity-logging.md` + `cli-reference.md` plus
  the required "Documentation impact" PR-description statement; (4)
  `remove_trace()`, wired into all three tracking-record removal call sites,
  so a durable trace is reaped with its worktree instead of outliving it;
  (5) `read_trace()` now decodes with `errors="replace"` so one invalid byte
  can't abort the whole read. All five review threads replied-to
  individually and resolved via GraphQL. **A genuine tooling/process gotcha
  surfaced mid-review and is now documented in a related control-repo
  note**: GitHub's Copilot
  code-review app submits its review with API `state: "COMMENTED"` on this
  repo's owner-authored PRs, never `APPROVED`/`CHANGES_REQUESTED` --
  `agent-worktrees`' `pr-watch`/`pr-status` `verdict` classifier deliberately
  excludes `COMMENTED` (correct plumbing, matches this repo's own
  `contributing-to-copilot-extensions` skill: "Never wait for Copilot to
  approve"), so waiting on a `pr-watch` verdict here structurally never
  resolves. A prior leg of this same session spent ~10 consecutive
  `pr-watch wait --timeout 600` calls (several hours) before this was
  recognized; corrected by reading `gh pr view <n> --json reviews` directly,
  assessing the advisory findings, and self-merging once addressed. Full
  plugin suite (8 sub-suites) green throughout. **The durable-persistence
  sub-item of Phase 3's checklist is now done.** Next: cross-linking/backfill
  (stage 9 successor stamping + predecessor stamping), the `handoff-trace`
  CLI, and the remaining Validation Plan items (spawn-kill partial-trace
  test, live end-to-end reproduction, `find_orphaned_handoffs()` naming a
  stalled stage).
