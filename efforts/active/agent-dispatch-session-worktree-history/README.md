# agent-dispatch: Durable Session/Worktree Attachment History + Origin-Agnostic Resolution

- **Slug:** `agent-dispatch-session-worktree-history`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `main`
- **Created:** 2026-09-19
- **Status:** Active
- **Vision:** [`visions/plugins/agent-dispatch`](../../../visions/plugins/agent-dispatch/README.md)
  §*Features*/`durable-attachment-history` ·
  [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
  §*Features*/`resolve-by-any-origin-reference` (both added by this effort,
  vision-extending — see each vision's Provenance)
- **Umbrella issue:** _to be filed_
- **Sub-issues:** _to be filed per phase_

## Guiding Intent

Any consumer that needs to show or link to "the session working this
worktree/task" — Neuron Forge, the Worktree Manager, Intelligence Dampener, a
future Adjudication Board tenant, an operator reading agent-dispatch's own
history — resolves it through **one shared, origin-agnostic primitive**,
never a bespoke per-consumer convention. And that primitive answers honestly
across a task's **entire lifetime**, not just its current moment: every
session and worktree that has ever attached to a task remains durably
queryable, even after a release, a resume, or a fresh re-embodiment moves
the task on to a new owner.

## Participants

Single-machine effort for its initial design/schema phases (agent-dispatch's
task store is local, single-writer); later phases touch agent-bridge's
resolver, which already has cross-machine reach via the existing peer-bridge
mesh — no new coordination topology needed.

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Lambda-Core WSL | Primary dev seat for agent-dispatch + agent-bridge phases | local |

## Context

Surfaced from `aperture-labs` (the facility's private consumer monorepo)
during a session investigating a stuck Intelligence Dampener PR review. Two
distinct but related gaps were found:

1. **No durable attachment history.** Diagnosing/fixing the stuck review
   required releasing its agent-dispatch task twice for a fresh embodiment.
   Each release **discarded** the prior session's identity — `owner_session_id`
   is a single mutable column
   (`plugins/agent-dispatch/src/agent_dispatch/queue.py`'s `bind_owner_session`
   does a plain `UPDATE tasks SET owner_session_id=?`). There is no way to
   later ask "what sessions has this task ever had, and when did each start
   and end" — useful for audit, debugging a flaky worker, or building a
   durable link to a *specific* past attempt rather than only "whoever has it
   now."
2. **No shared, origin-agnostic resolver.** A first attempt to fix
   Intelligence Dampener's own "View reviewer" link
   (`aperture-labs` PR #7200, since reverted) hardcoded Dampener's own
   `dampener-pr<N>` worktree-naming convention directly into the link
   builder — solving Dampener's problem while leaving every *other* consumer
   (an Adjudication Board run, Permanent Record's journal, a plain
   agent-dispatch CLI user) to invent its own equivalent trick. agent-bridge
   already has almost everything needed
   (`any-session-any-registered-worktree-regardless-of-liveness`: resolve a
   session by ID for *any* registered worktree, live or archived, via its
   cold-store-provider mechanism) — the missing piece is resolving *starting
   from an origin's own durable handle* (a dispatch task reference) rather
   than requiring the caller to already have derived a worktree/session ID
   through its own logic.

Prior art this effort extends rather than replaces (see each vision's own
Concepts & Components):
- agent-bridge's cold-store-provider mechanism (`docs/architecture.md`'s
  provider-registration section) — the live-or-archived session-serving half.
- agent-dispatch's `bind_owner_session` / `_audit` trail
  (`queue.py`) — the existing (single-slot) ownership bookkeeping this effort
  extends to a full history, and the existing state-transition audit log that
  may already carry enough raw material to backfill from.
- `aperture-labs`' `session-worktree-archive-linkout` effort — the consumer
  -side effort whose Phase 4 (Dampener's "View reviewer" link) is blocked on
  this landing; see its 2026-09-19 course-correction journal entry for the
  reverted bespoke attempt.

## Request

> Also, I don't want anything bespoke to ID here. Whatever strategy should
> work for any worktree+session combo, whether created in Neuron Forge, the
> Worktree Manager, or agent-dispatch. We also need to add robustness for
> getting the session id out of agent-bridge and durably binding it to
> agent-dispatch tasks. A task should have a history of attached agent
> worktrees and sessions.

(verbatim, from the aperture-labs session that surfaced this; "ID" =
Intelligence Dampener)

## Plan

### Phase 1 — agent-dispatch: durable attachment history (the data source)
- [ ] Design the history record shape: `(task_id, session_id, worktree_id,
      machine, attached_at, detached_at, detach_reason)`. Detach reasons
      cover at least: `completed`, `suspended`, `released`, `superseded`
      (context-exhaustion handoff to a successor session per
      *suspend-idle-resume-same-session*).
- [x] Add an append-only `task_attachments` table (or equivalent), written
      by the existing lifecycle transition points that already touch
      `owner_session_id`/`target_worktree` (`bind_owner_session`, `suspend`,
      `resume`, `release`, `reattach`) — every write to the mutable
      current-owner fields gets a paired history append, never a
      current-owner mutation without one. **Delivered:** `task_attachments`
      table + `TaskQueue._record_attachment` (a single choke point hooked
      into both `bind_owner_session` and the generic `_transition`, so
      every current call site that ever sets `owner_session_id` — release,
      yield, reset-to-proposed, a handoff-adopting `resume` — is covered
      without per-call-site duplication). A same-session suspend/resume is
      correctly a no-op (compares old vs. new session id before writing).
- [x] New read API: `GET /tasks/{id}/attachments` (or CLI
      `agent-dispatch show <id> --history`) returning the full ledger,
      newest-first. **Delivered:** both — the REST route, a
      `DispatchClient.attachments()` method, and `show --history`.
- [ ] Backfill consideration: audit-log entries already carry
      "owner session bound (...)" notes for some transitions — evaluate
      whether a one-time backfill can reconstruct partial history for
      already-existing tasks, or whether it's acceptable for history to
      start from this effort's landing forward only (flag-don't-guess, per
      the facility's own reconstruction-honesty convention — no fabricated
      timestamps for genuinely unknown transitions). **Deferred** — history
      starts from this landing forward; no backfill attempted this phase.
- [x] Tests: every lifecycle transition that changes the current owner
      correctly appends to history; a chain of release→claim→release→claim
      preserves every prior session's record; concurrent-writer safety
      (the existing `BEGIN IMMEDIATE` transaction pattern already used by
      `bind_owner_session` extends naturally to the paired write).
      **Delivered:** a queue-level test walking bind→suspend/resume(same
      session, no-op)→suspend→release→re-bind(session-b)→handoff(session-c),
      plus HTTP-level coordinator tests for the new route. Concurrent-writer
      safety is inherited for free — the write happens inside the SAME
      `BEGIN IMMEDIATE` transaction as the existing owner-session mutation,
      not a separate one.

### Phase 2 — agent-bridge: resolve by dispatch-task reference
> **2026-09-19 progress note:** the *ranking* logic (which session IDs to try,
> in what order) shipped as a pure, dependency-free module. The live
> HTTP-route wiring (fetching the task + attachment history from
> agent-dispatch, looping candidates through the existing
> live-then-cold-store resolver, and a worktree-level "latest session"
> fallback when nothing resolves) is the concrete next slice — deliberately
> not rushed into a shared repo's live FastAPI app without settling the
> agent-dispatch URL/token config shape first (see below).
- [x] Extend the existing session/worktree resolver
      (`any-session-any-registered-worktree-regardless-of-liveness`'s
      implementation) to accept a dispatch-task reference as an additional
      resolution key, not only a session ID or worktree ID directly.
      Resolution derives the worktree from the task (the delegation layer
      already exposes this — e.g. `target_worktree`/payload fields consumers
      like `neuron-forge`'s `task_worktree()` already read), then applies the
      existing any-session-any-worktree resolution to that worktree.
      **Delivered (ranking half):** `dispatch_task_resolution.
      candidate_session_ids()` / `task_worktree_id()` — pure functions
      given a task JSON + its attachment history, testable with no HTTP or
      SessionManager dependency.
- [x] When the task's *current* owner has nothing live, consult Phase 1's
      attachment history (not just the cold-store providers) before giving
      up — a task's most recent **detached** session may still be
      individually resolvable even if the worktree itself is gone, if a
      cold-store provider still has that exact session. **Delivered:**
      `candidate_session_ids()` orders the current owner session first,
      then every attachment history entry newest-first, deduped.
- [ ] New/extended REST surface mirroring the existing worktree-scoped
      routes' shape (so consumers' interface doesn't change shape, only
      gains a new valid key) — exact route design TBD at implementation
      time; keep consistent with `docs/architecture.md`'s existing
      provider-registration and resolver documentation. **Remaining:** wire
      a route (e.g. `GET /api/v1/dispatch-tasks/{id}/session`) that fetches
      the task + attachments from agent-dispatch (needs an `AGENT_DISPATCH
      _URL`/`AGENT_DISPATCH_TOKEN` config surface on agent-bridge, mirroring
      `neuron-forge`'s own established pattern for the same optional
      dependency) and loops `candidate_session_ids()` through
      `SessionManager.get_session()` / `fetch_cold_store_session()`.
- [ ] Tests: resolving a live dispatch task, a suspended one, a fully
      terminal/released one whose worktree is gone but whose last session
      is still cold-store-resolvable, and one with no resolvable session at
      all (graceful 404, never an error). **Delivered so far:** unit tests
      for the ranking module (current-owner-first, dedup, empty/malformed
      input). **Remaining:** integration tests for the actual route once it
      exists.

### Phase 3 — consumers: adopt the shared resolver
- [ ] `aperture-labs` Intelligence Dampener: re-attempt the "View reviewer"
      link (`session-worktree-archive-linkout` Phase 4, previously reverted
      as PR #7200) using the new dispatch-task-reference resolution instead
      of the `dampener-pr<N>` naming convention.
- [ ] Neuron Forge's own `/dispatch/:taskId` "exact live session" viewer
      (`worktree_tasks.py`): evaluate whether it should be retired in favor
      of always routing through the general resolver, or kept as a distinct
      "insist on live, nothing else" affordance for a narrower use case.
      Decide, don't guess — this route is used by more than just Dampener's
      link (any dispatched-task viewer), so retiring it needs its own
      callers audited first.
- [ ] Document the shared resolution primitive in each vision's Concepts &
      Components (not just Features) so a future consumer finds it before
      inventing its own convention.

## Validation Plan

- [ ] Phase 1: a synthetic multi-release/resume/reattach sequence on one
      task produces a complete, correctly-ordered attachment history with no
      gaps or duplicate records.
- [ ] Phase 2: resolving the same task by its dispatch-task reference and by
      its worktree ID directly produce identical results when both are
      available; resolving after the task's worktree is reclaimed still
      answers for its last cold-store-resolvable session.
- [ ] Phase 3: Dampener's "View reviewer" link, re-implemented, resolves for
      an in-flight review, a just-completed one, and one whose review
      worktree has since been reclaimed — with zero Dampener-specific
      resolution logic in the link builder itself.

## Proposal

_Pending — Phase 1's schema/API design is the first concrete artifact._

## Journal

### 2026-09-19 — Phase 2 ranking logic landed
- `agent_bridge.dispatch_task_resolution`: pure `candidate_session_ids()` /
  `task_worktree_id()` — given a task JSON + attachment history JSON (Phase
  1's `GET /tasks/{id}/attachments` shape), rank which session IDs to try,
  current owner first then history newest-first, deduped. No HTTP client or
  SessionManager dependency, so fully unit-tested (6 tests) without mocking
  agent-dispatch.
- Deliberately did NOT wire the live HTTP route this session — that needs a
  new `AGENT_DISPATCH_URL`/`AGENT_DISPATCH_TOKEN` config surface on
  agent-bridge (mirroring `neuron-forge`'s own established pattern for the
  same dependency), which is a real design decision worth its own reviewed
  slice rather than rushing into a shared repo's live app under time
  pressure. Documented as the concrete next step in Phase 2's checklist.
- Full `agent-bridge` suite: 343 passed, 10 skipped, no regressions.

### 2026-09-19 — Phase 1 landed
- `task_attachments` table + `TaskQueue._record_attachment`/`attachment_history`,
  hooked into `bind_owner_session` and the generic `_transition` (single
  choke point covering every current owner-session-clearing call site:
  `release_suspended`, `yield`, reset-to-proposed, a handoff-adopting
  `resume`). `GET /tasks/{id}/attachments` route, `DispatchClient.
  attachments()`, and `agent-dispatch show <id> --history`.
- Full plugin suite: 667 tests, 666 passed, 1 pre-existing unrelated failure
  (`test_requeued_task_is_not_double_spawned`, confirmed identical with and
  without this change via `git stash`). `ruff` error count (32) also
  unchanged before/after.
- Backfill (partial history reconstruction from existing `task_events` audit
  notes) deferred — history starts from this landing forward.

### 2026-09-19 — Kickoff
- Effort created from an aperture-labs session's operator request (verbatim
  above), surfaced while diagnosing an Intelligence Dampener stuck-review
  incident and a first (reverted) bespoke attempt at Dampener's "View
  reviewer" link.
- Revised `visions/plugins/agent-dispatch` (`durable-attachment-history`) and
  `visions/plugins/agent-bridge` (`resolve-by-any-origin-reference`) ahead of
  this plan, per vision-first discipline.
