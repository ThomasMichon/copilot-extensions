# Handoff Cutover Lifecycle Journal

- **Slug:** `handoff-cutover-lifecycle-journal`
- **Repo:** copilot-extensions (plugins/agent-worktrees, plugins/context-handoff)
- **Branch(es):** `worktree/lambda-core-wsl-20260911-154029-3df3`
- **Created:** 2026-09-11
- **Status:** Draft
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
one event per stage, on both sides of the cutover (predecessor and successor
sessions), cross-linked by session id like a linked list, replayable at any
time per worktree.

## Participants

Single-machine, single-agent effort (lambda-core). No dispatch/coordination
topology needed.

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
    process-manager agnostic. Actual spawning is the resident agent-worktrees
    status monitor's job. If that monitor is absent, misconfigured, or fails
    silently, `trigger_handoff` still reports success.
  - No context-handoff sessionStart hook inspects or claims a pending handoff;
    that is left to `agent-worktrees` (a session becomes `successor-elect` via
    role calculation) and finally to the session explicitly calling
    `consume_handoff`.

**Net finding:** the 12 stages the operator described map roughly onto real
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
- [ ] Define one canonical event vocabulary covering all 13 stages:
      `worktree_created`, `mux_session_assigned`, `copilot_invoked`,
      `session_start_bound`, `status_reported`, `handoff_triggered`,
      `handoff_host_acknowledged`, `handoff_successor_spawn_started`,
      `handoff_successor_session_start_bound`, `handoff_successor_claimed`,
      `handoff_pickup_confirmed_predecessor_closing`, `session_end_bound`,
      `handoff_complete`. Reuse existing `activity.py` event names where they
      already match 1:1; add the missing ones; do not rename existing events
      relied on elsewhere without an audit of readers (`health.py`,
      `disposition_history.py`, any docs).
- [ ] Every event carries: `worktree_id`, `stage` (ordinal + name),
      `session_id` (the session emitting it), `handoff_token` (when
      applicable), `predecessor_session_id` / `successor_session_id` (the
      linked-list pointers — null until known), `ts`, `source` (which
      component emitted it: agent-worktrees / context-handoff / hook /
      agent-bridge).
- [ ] Make `activity.log_event()` (or a new sibling) **not** swallow errors
      silently in a way that's invisible — keep best-effort delivery (never
      block the caller) but surface a debug-level warning/counter so a
      missing event is itself detectable, not just theorized.

### Phase 2 — Instrument all 13 stages
- [ ] Stage 1 (`worktree_created`) — confirm `cmd_create` already emits this
      with no gaps; add `predecessor_session_id: null` framing.
- [ ] Stage 2 (`mux_session_assigned`) — emit from `sessions.mux_new_session` /
      `build_mux_new_window_argv` success path (ordinary launch, not just
      cutover).
- [ ] Stage 3 (`copilot_invoked`) — emit from the launcher/pane-wrapper right
      before exec'ing the `copilot` binary (both `.sh` and `.ps1`).
- [ ] Stage 4 (`session_start_bound`) — emit from `cmd_register_session` once
      a session id + worktree are actually resolved (already close to
      `session_started`; make sure the *binding* moment, not just tool entry,
      is what's logged).
- [ ] Stage 5 (`status_reported`) — emit from the `status`/status-report tool
      path when it first runs in a session (marks "Copilot did something in
      this worktree").
- [ ] Stage 6 (`handoff_triggered`) — already covered by context-handoff's
      `handoff_requested`; align the name/shape to the new schema instead of
      duplicating.
- [ ] Stage 7 (`handoff_host_acknowledged`) — new: emit when the resident
      status monitor (or `agent-bridge handoff-request` receiver) first
      observes and accepts the pending handoff, distinct from the later claim.
- [ ] Stage 8 (`handoff_successor_spawn_started`) — emit at the start of
      `_handoff_cutover_spawn_result` / `mux_new_window`, before success is
      known, so a spawn that later fails still leaves a trace.
- [ ] Stage 9 (`handoff_successor_session_start_bound`) — emit from the
      successor's own `cmd_register_session` when it recognizes the
      `--handoff-candidate-token` and calls `associate_handoff_candidate`.
- [ ] Stage 10 (`handoff_successor_claimed`) — emit from `consume_handoff`'s
      success path (both task-backed and file-backed), not just from
      agent-worktrees' internal claim file.
- [ ] Stage 11 (`handoff_pickup_confirmed_predecessor_closing`) — emit from
      wherever the runner acts on the successor's claim to begin retiring the
      predecessor (near/alongside `handoff_predecessor_retire`).
- [ ] Stage 12 (`session_end_bound`) — audit whether a `sessionEnd` hook
      exists in agent-worktrees/context-handoff today; add one if missing, and
      emit here.
- [ ] Stage 13 (`handoff_complete`) — emit once the runner has confirmed the
      predecessor pane is gone and the successor is head.

### Phase 3 — Cross-linking (the "linked list")
- [ ] When the successor's `session_start_bound` (stage 9) fires, stamp its
      own session-state handoff record with `predecessor_session_id`.
- [ ] When the predecessor retires (stage 11/12), stamp its own session-state
      record with `successor_session_id` (it may not have known this at
      trigger time).
- [ ] Write/append the full per-stage trace into **both**:
      `~/.copilot/session-state/<predecessor-sid>/handoff-trace.jsonl` and
      `~/.copilot/session-state/<successor-sid>/handoff-trace.jsonl` — each
      side gets every event it can see, plus the other side's session id, so
      either session-state folder alone lets you walk the chain.
- [ ] Add a `agent-worktrees handoff-trace <worktree-id|session-id>` CLI
      command that reads `activity.jsonl` + both session-state trace files and
      renders the ordered 13-stage sequence for a worktree/handoff-token, with
      gaps visibly marked ("stage 8 logged, stage 9 never observed").

### Phase 4 — Fix the reported failure class
- [x] Reproduce the "ack but no pane, retry says already-claimed" symptom —
      **found live**, not synthetic: `lambda-core-wsl-20260910-012212-9395`
      (see Proposal § Case study). Two distinct bugs identified from real
      `activity.jsonl` data:
      1. a second `trigger_handoff` from a session id reused across mux
         resume collides with its own already-consumed prior handoff
         record/token and silently no-ops (no claim attempt, no log line);
      2. `handoff_predecessor_retire` reports `outcome: "left-running"` /
         `copilot_reaped: 0` on **every** observed cutover in this worktree,
         even ones with `successor_verified: true` — the predecessor pane may
         never actually be retired even on a "successful" cutover.
- [ ] Fix bug 1: make the file-backed (and task-backed) handoff record keyed
      by **token**, not solely derivable from a reused session id, or have
      `storeHandoff()`/the monitor's pending scan detect "this session already
      has a resolved handoff" and mint + arm a **new** token/claim rather than
      silently treating the request as a no-op duplicate.
- [ ] Fix bug 2: root-cause why the retire step consistently reports
      `left-running`/`copilot_reaped: 0` despite `successor_verified: true` in
      `_monitor_claim_handoff_cutover` (or wherever the retire branch lives in
      `plugins/agent-worktrees/src/agent_worktrees/__main__.py`), and make it
      actually retire the predecessor (or log why it deliberately doesn't).
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
      successful cutover.
- [ ] A test that intentionally kills the spawn step and asserts
      `handoff-trace` shows stages 1-7 present and 8+ absent (proves the tool
      surfaces partial traces usefully, not just full ones).
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

### Case study: `lambda-core-wsl-20260910-012212-9395` (live, reproduces the bug)

Pulled directly from `agent-worktrees list --json` + `~/.agent-worktrees/logs/activity.jsonl`
+ `~/.agent-worktrees/status-monitor-handoffs.d/lambda-core-wsl-20260910-012212-9395/`
on 2026-09-11. This worktree's `reciprocal_relation` is currently
`"state": "ambiguous"`, `binding.handoff_state: "pending"`,
`binding.session_id: 0381a5d0-...` (a session that is **not** the live one),
while `live_session_ids` names a completely different, fourth session id
(`95645cff-...`). Timeline reconstructed from `activity.jsonl` (all times UTC,
09-10/09-11):

| time | event | session | notes |
|---|---|---|---|
| 08:23:48 | `session_started` | `205cc0f6` | original head |
| 16:31:39 | `handoff_requested` (context-handoff) | `205cc0f6` | token `b2ccc...`, stored via agent-dispatch |
| 16:31:39 | `handoff_cutover_claim` | `205cc0f6` | outcome **acquired** — monitor claim works |
| 16:31:42 | `handoff_cutover_spawn` | `205cc0f6` | new pane `%3`, `candidate_status: awaiting-session-association` — spawn works |
| 16:32:02 | `session_started` | `3697ec79` | successor's sessionStart fires |
| 16:32:02 | `handoff_predecessor_retire` | `205cc0f6` | `successor_verified: true` but **`outcome: "left-running"`**, `copilot_reaped: 0` — predecessor pane is *never actually killed* |
| 17:21:38–17:22:05 | (repeat cycle) | `3697ec79`→`0381a5d0` | same claim→spawn→retire pattern, same "left-running" outcome |
| 19:10 – 09:42 (six `worktree_resumed` cycles over ~14h) | `session_started` for `205cc0f6` / `3697ec79` **recur verbatim** across unrelated launch ids | — | expected: ordinary mux resume reattaches the *same* Copilot session id |
| 08:35:33 (09-11) | `handoff_requested` | `3697ec79` | **same `handoff_id`** (`65ae0764...`) as the 09-10 17:21 cycle, reused verbatim | 
| — | *(nothing)* | — | **no `handoff_cutover_claim`, no `handoff_cutover_spawn`, no "already-claimed" log entry follows** — the second trigger is silently absorbed |
| 09:38:11 | `session_ended` | `3697ec79` | session just ends normally, unaware its handoff never re-armed |
| 21:56:40 | `session_started` | `95645cff` | a **brand-new** session id, never seen before, becomes the live head after the next resume |

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

Secondary bug found in the same trace, independent of the above: **every**
`handoff_predecessor_retire` in this worktree's history reports
`outcome: "left-running"` with `copilot_reaped: 0` despite
`successor_verified: true` — the predecessor pane/process is apparently never
being retired even on a *successful* cutover generation. That alone would
explain "no replacement pane" reports even on a technically-successful cutover
if the operator is looking at the (still-running) predecessor pane instead of
the new one — a second, distinct thing for Phase 4 to root-cause and fix
(`_monitor_claim_handoff_cutover` / the retire step in
`plugins/agent-worktrees/src/agent_worktrees/__main__.py`).

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
- Operator flagged a **live** instance of the exact bug:
  `lambda-core-wsl-20260910-012212-9395`. Traced it in full from
  `activity.jsonl` + tracking record — see Proposal § Case study. Found two
  concrete, distinct root causes (handoff-token reuse across a resumed
  session id silently no-ops re-triggering; predecessor retire logs
  `left-running` on every cutover generation seen). Phase 4's reproduction
  step is now done against real data; the plan's fix items were rewritten to
  target these two specific causes instead of the original generic
  hypothesis.
