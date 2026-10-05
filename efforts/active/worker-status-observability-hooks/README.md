# Worker-Status Observability Hooks (agent-dispatch + agent-bridge)

- **Slug:** `worker-status-observability-hooks`
- **Repo:** copilot-extensions (`plugins/agent-dispatch`, `plugins/agent-bridge`)
- **Branch(es):** per-phase PRs off `dev`
- **Created:** 2026-10-04
- **Status:** Draft
- **Umbrella issue:** [#5257](https://github.com/ThomasMichon/copilot-extensions/issues/5257)
  (agent-dispatch/agent-bridge: deeper worker-status CLI hooks)
- **Sub-issues:** _none yet_

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Operator workstation | Sole implementer; drives all five phases | a copilot-extensions worktree, per-phase PRs off `dev` |

## Coordination

Solo effort, single participant, no multi-agent branch topology. The
participant above opens and drives every phase's PR directly off `dev`
(no shared feature branch, no delegate hand-off).

- **Vision:** mostly **vision-closing** —
  [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
  already states the intent Phase 1-2 close against: §*Features*/
  `any-session-any-registered-worktree-regardless-of-liveness` ("This applies
  to a session's **transcript/event content**, not only its metadata...") and
  `resolve-by-any-origin-reference` ("it has... a delegated task's own
  reference (an agent-dispatch task id)... The fabric resolves **any** of
  these to the same session the same way"). `peek`'s failure on a
  `local-body:*` session is a gap between that stated intent and the CLI's
  actual reach, not a missing feature. Phases 3-5 (agent-dispatch's `doctor`/
  `list`/`show` CLI ergonomics) are incremental completions of already-stated
  intent (the lifecycle/liveness concepts in
  [`visions/plugins/agent-dispatch`](../../../visions/plugins/agent-dispatch/README.md)
  §Concepts/*The supervisor*, §Behaviors/*liveness-not-lease*), not new
  conceptual territory — no vision edit identified as necessary for those;
  flag here if one turns out to be needed once Phase 3+ design firms up.

## Guiding Intent

Give an operator (human or supervising agent) a genuinely fast path from "a
task_id I'm worried about" to "I can see exactly what that worker is doing
and decide whether to intervene" — without manually chaining 4+ commands and
hand-parsing raw session JSONL, which is what today's tooling actually
requires. `agent-bridge peek` is the one command purpose-built for this job;
it should reach every worker a fleet actually spawns, not only ACP-registered
ones.

## Context

Surfaced during a live, multi-hour session operating a downstream harness's
ADO-backed repro-queue fleet on `agent-dispatch` (this session also
root-caused and fixed the #4990 spawn-starvation bug and a steering-card
policy gap in that downstream fleet's own worker prompt; this effort is the
tooling fallout from diagnosing all of that by hand without the hooks
below). Concretely hit and worked around, repeatedly, across that session:

- `agent-bridge peek <session_id>` refused every `local-body:*` session
  (`has no acp_session_id yet -- copilot has not written a transcript`) —
  which is the dominant embodiment kind `agent-dispatch supervise` actually
  spawns. Every "what is this worker doing" check instead required manually
  resolving `~/.copilot/session-state/<session_id>/events.jsonl` and parsing
  raw JSONL in an ad hoc Python one-liner.
- Resolving task_id → session_id, when `agent-dispatch show`'s own
  `owner_session_id` was still null (pre-claim), required falling through to
  `agent-dispatch reservations list --task <id>` for the `worktree`, then
  `agent-worktrees worktree-status-bundle --worktree <id> --json` for
  `facts.lineage.value.head_session` — a 3-command chain before the
  transcript could even be located. agent-bridge already owns exactly this
  resolution server-side as `GET /api/v1/dispatch-tasks/{task_id}/session`;
  the gap is that no CLI command wires a caller to it, not that the
  resolution logic is missing.
- Three tasks sat **silently** dead-ended for days: self-excluded from the
  only machine running the fleet (a stale `excludes` entry), left over from
  a misdiagnosed "permanent" ACP host limitation that the session later
  proved has a working fallback. `agent-dispatch doctor`'s sweep (no
  `--task`) reported `examined: 0` the whole time — it does not appear to
  examine a bare `queued` task with no active/failed reservation at all.
- Finding "what needs my attention right now" (an `awaiting_steer` task; a
  task carrying `excludes`) required pulling the **full** `list` output and
  filtering client-side every check-in — no server-side filter for either
  condition exists.
- `show`'s `last_seen_at` sat 30+ minutes stale on a task whose actual
  session was emitting events every few seconds — had to cross-check the raw
  transcript's own last-event timestamp to tell "alive" from "stalled."
  `doctor --check-live-sessions` already solves the underlying liveness
  question but is opt-in/sweep-scoped, not part of a single task's `show`.

Credit where due — several things initially assumed missing already exist
and work well, confirmed by testing before filing the umbrella issue:
`agent-dispatch events <task_id>` (clean lifecycle + progress narrative),
`agent-dispatch reservations list --task <id>` (already scoped, no need to
filter client-side), and `agent-dispatch doctor --check-live-sessions`
(already does per-attempt liveness probing). This effort is scoped to the
remaining real gaps only.

Full narrative and the exact commands/outputs that surfaced each gap: this
effort's own `inception-transcript.md` is not needed — the gaps are fully
captured above and in issue #5257; no additional back-and-forth to archive.

## Request

Operator's verbatim asks, across two turns in the live session:

> Identify what CLI hooks would be more useful in agent-dispatch and
> agent-bridge to deep-dive into status for workers, and file an issue
> requesting such a buildout

> Let's just detour and tackle this now; we'll occasionally touch base on
> the repro tasks while we work. Nothing bea a live situation to use as
> grounding context for why we one something. Use your own challenges
> querying for worker agent status to guide vision updates, effort buildout,
> implementation, and choses validations.

(Typos in the second quote — "bea"/"one"/"choses" — preserved verbatim per
capture policy; read as "beats" / "want" / "chosen.")

## Plan

### Phase 1 — `agent-bridge peek` reaches local-body sessions _(highest leverage)_
- [ ] Locate `peek`'s session-kind resolution (where it currently requires
      `acp_session_id`) in `plugins/agent-bridge`.
- [ ] Extend it to recognize a `local-body:*` (or any session with an
      on-disk `events.jsonl` under session-state, regardless of
      `acp_session_id`) and render the same summarized/bounded transcript
      view it already produces for ACP-registered sessions.
- [ ] Confirm this doesn't regress the existing ACP-session path (render
      the same output shape for both kinds — a caller shouldn't need to
      know which kind it got).

### Phase 2 — task_id → transcript resolution, one command
- [ ] Do not reconstruct the show→reservations→worktree-status-bundle
      resolution chain in the CLI. agent-bridge already owns this exact
      resolution as `GET /api/v1/dispatch-tasks/{task_id}/session`
      (`plugins/agent-bridge/src/agent_bridge/routes/dispatch_tasks.py`) —
      it ranks the task's current owner then attachment history
      newest-first, tries live/cold-store/live-registration resolution for
      each candidate, then falls back to the target worktree's own latest
      known session. This is `resolve-by-any-origin-reference` already
      realized for agent-dispatch task ids specifically.
- [ ] **Verified gap (review):** that route's `dispatch:` namespace
      resolver (`plugins/agent-dispatch/src/agent_dispatch/bridge_namespace_cli.py`,
      `_cmd_namespace_resolve`) only derives a worktree from a *claimed*
      task's `owner` or from the task record's own `target_machine`/
      `target_worktree` fields — never from a live **spawn reservation**.
      An unclaimed/pre-claim task (status `queued`/`started` with no
      `owner` yet, but an active `spawned` reservation naming a real
      worktree — the exact shape observed repeatedly this session) returns
      bad-state (exit 4) before the route's own cold-store/worktree
      fallback ever runs. Extend the resolver to also consult the task's
      current spawn reservation's worktree before falling through to
      bad-state, THEN wire `peek` to the (now-complete) existing endpoint
      — keep resolution in agent-bridge/agent-dispatch's existing seam,
      extend it rather than duplicating it in the CLI.
- [ ] Depends on Phase 1 (the resolved session is usually local-body).

### Phase 3 — shared queued-task filter seam + `doctor` excludes visibility
- [ ] Build a server-side filter/query seam for "which queued tasks carry a
      non-empty `excludes` array" (and, while there, `awaiting_steer`) —
      the existing sweep deliberately excludes bare `queued` tasks from its
      bounded `--task`-less pass (`plugins/agent-dispatch/src/agent_dispatch/doctor.py:104-110`),
      so an unbounded scan of the whole backlog isn't the answer; this
      needs a real filtered query, not a client-side list-then-filter.
- [ ] **Corrected from review:** `excludes` is an intentional anti-affinity
      selector, not inherent evidence of a dead end — a task excluded from
      one machine/worktree may still be claimable by another eligible
      target (confirmed against `tests/test_selectors.py:24-42,68-81`).
      Add a **neutral** `doctor` finding (e.g. `queued_with_excludes` —
      visibility, not an assumed-blocked verdict) built on the Phase 3
      seam. A stronger "genuinely dead-ended" diagnosis is in scope only
      if it can first prove the exclusions eliminate every eligible
      target; treat that as a stretch goal within this phase, not a
      requirement for it to ship.
- [ ] This phase and Phase 4 share one filter implementation — build it
      once here, consume it from both `doctor` and `list`.

### Phase 4 — `list` filters for "what needs me"
- [ ] Expose Phase 3's filter seam as `--awaiting-steer` and
      `--has-excludes` boolean filters on `agent-dispatch list`.

### Phase 5 — audit (not duplicate) `show`'s existing liveness signals
- [ ] **Corrected from review:** `show` already carries two liveness
      surfaces — the task record's own `activity`/`activity_updated_at`,
      and `tracking.enrich_task()`'s `embodiment.{turn_state,liveness,
      updated_at}` overlay (`task_lifecycle_cli.py:107-119`,
      `tracking.py:682-723`). A third, new signal would conflict rather
      than help. **Verified (partial) during planning:** `enrich_task`
      only attaches the `embodiment` overlay when `tracking.
      resolve_live_session()` finds a live session for the task's
      worktree — every `show` observed this session lacked an
      `embodiment` key at all, suggesting that resolution silently misses
      the same local-body session kind Phase 1 fixes for `peek` (unverified
      — `resolve_live_session` reaches the same local `agent-bridge
      sessions --json` call Phase 1 touches, but the exact failure point
      wasn't traced during planning). This phase is now: audit why the
      existing signals didn't answer the observed case, fix their
      freshness/presentation (likely resolved as a side effect of Phase 1,
      or needs its own small fix in `resolve_live_session`), and only add
      new surface area if the audit finds the existing fields are
      genuinely insufficient once working — never a parallel third signal.

## Validation Plan

Each phase requires **automated regression coverage** in the repo's existing
focused suites, with live dogfooding against a real downstream repro-queue
fleet (when one is available during implementation) as supplemental
evidence only — never a substitute for an automated test.

- [ ] **Phase 1:** automated test in `test_peek_snapshot.py` covering a
      `local-body:*` session (no `acp_session_id`) resolving to a rendered
      transcript, plus a regression test confirming the existing
      ACP-session path is unchanged. Supplemental: `agent-bridge peek
      <session_id>` against a real local-body session from a live
      downstream repro-queue fleet, if one is running during this phase.
- [ ] **Phase 2:** two distinct test layers, not one — (a) a route-level
      case in (or alongside) `test_dispatch_task_session_route.py` /
      `bridge_namespace_cli`'s own test covering the new spawn-reservation
      fallback specifically; (b) a **CLI-level** regression that invokes
      the chosen task-id `peek` surface, verifies it calls the endpoint
      (not a reimplemented chain), and verifies the resolved session
      reaches Phase 1's renderer. Supplemental: dogfood against a live
      pre-claim task.
- [ ] **Phase 3:** automated test in `test_doctor.py` confirming (a) a task
      manually given a stale `excludes` entry is reported by a default
      (`--repo`/`--label`, no `--task`) sweep as a neutral
      `queued_with_excludes` finding, and (b) a **negative** test proving
      an intentionally-excluded-but-still-claimable task is not
      misdiagnosed as blocked.
- [ ] **Phase 4:** automated test confirming `agent-dispatch list
      --awaiting-steer` / `--has-excludes` returns exactly the expected
      filtered set against a fixture queue.
- [ ] **Phase 5:** automated test confirming the audited fix (if any) makes
      `embodiment`/`activity` correctly reflect liveness for a local-body
      task; if the audit finds the existing signals already work once
      Phase 1 lands, this becomes a regression test proving exactly that
      (no new signal needed) rather than a feature test for one.

## Proposal

_Pending — begin with Phase 1 implementation exploration._

## Journal

### 2026-10-04 — Kickoff
- Effort created from issue #5257, itself filed after verifying (by direct
  testing) which CLI gaps were real vs. already-solved.
- Confirmed via vision search that Phase 1/2 are vision-closing (the
  agent-bridge vision already states the exact intent); Phase 3-5 don't
  appear to need a vision edit, flagged for re-check once design firms up.

### 2026-10-04 — Plan PR #5264 review (COMMENTED, Medium + 2 Low)
- Medium: Phase 2 would have reconstructed a resolution chain
  (`GET /api/v1/dispatch-tasks/{task_id}/session`) agent-bridge already
  owns and already ranks owner/attachment-history correctly. Revised Phase
  2 to wire into that existing endpoint instead of reimplementing it.
- Low: added the required `## Participants`/`## Coordination` sections
  (this repo's addendum keeps the canonical template set even for a solo
  effort, overriding the generic skill template's "omit when solo" note).
- Low: added the required Documentation impact statement to the PR
  description itself (not the effort file — confirmed via
  `CONTRIBUTING.md`'s own requirement that it lives in the PR body).

### 2026-10-05 — Plan PR #5264 second review round (COMMENTED, 4 Medium + 2 Low)
- Medium: Participants table named a personal machine alias and a raw
  worktree identifier — replaced with role-based/generic identities per
  this repo's public-effort policy.
- Medium: Context named a downstream harness and its internal repro-queue
  by name, plus referenced a private session transcript — generalized to
  "a downstream harness's ADO-backed repro-queue fleet," dropped the
  transcript reference. Scrubbed the same alias from the PR description.
- Medium: Phase 3/4 restructured so Phase 3 builds the shared filter/query
  seam (doctor's bounded sweep can't just scan the whole queued backlog)
  and Phase 4 reuses it for `list`'s CLI flags, instead of each phase
  building its own filtering.
- Medium: Validation Plan rewritten to commit each phase to automated
  regression coverage in the repo's existing focused suites
  (`test_peek_snapshot.py`, `test_dispatch_task_session_route.py`,
  `test_doctor.py`), with live dogfooding demoted to supplemental evidence
  only.
- Low: removed review-history wording ("Correction from review...") from
  Context/Phase 2 — stated the current facts directly; review history
  belongs only in this Journal.
- Low: Documentation impact statement finding was stale (already present
  in the PR body from the first round) — no action needed beyond the
  repro-queue alias scrub already covered above.

### 2026-10-05 — Plan PR #5264 third review round (COMMENTED, 1 High + 3 Medium + 1 stale Low)
- **High, verified by direct source read:** Phase 2's target endpoint
  resolves a worktree from a *claimed* task's `owner` or from
  `target_machine`/`target_worktree` (`bridge_namespace_cli.py`'s
  `_cmd_namespace_resolve`) — never from a live spawn reservation. An
  unclaimed/pre-claim task (observed repeatedly this session: `queued`/
  `started`, no `owner` yet, but an active `spawned` reservation naming a
  real worktree) hits bad-state before the route's own fallback chain
  runs. Added an explicit sub-step to extend that resolver with the
  reservation-worktree case before wiring `peek` to it.
- Medium, verified against `tests/test_selectors.py`: a non-empty
  `excludes` is an anti-affinity selector, not inherent evidence of a dead
  end (another eligible target may still claim it). Softened Phase 3's
  diagnosis to a neutral `queued_with_excludes` visibility finding, added
  a negative-test requirement.
- Medium, verified by direct source read: `show` already enriches via
  `tracking.enrich_task()`'s `embodiment` overlay (`task_lifecycle_cli.py`
  calls it on every `show`) — but every `show` observed this session
  lacked that key entirely, consistent with (unverified, not traced to
  the exact failure point) the same local-body resolution gap Phase 1
  fixes, since `resolve_live_session` reaches the same local
  `agent-bridge sessions --json` surface. Reframed Phase 5 from "add a
  signal" to "audit the existing ones, fix or confirm-working, never add
  a third parallel signal."
- Medium: Phase 2's validation split into a route-level test for the new
  reservation-fallback behavior and a separate CLI-level test for the
  `peek`-wiring behavior itself (a route test alone can't prove the CLI
  wiring works).
- Low (stale): Documentation impact finding persisted from an
  already-resolved round — the bot appears to echo an unresolved finding
  ID across passes rather than re-checking the PR body each time; no
  action needed (confirmed live in the PR body).

