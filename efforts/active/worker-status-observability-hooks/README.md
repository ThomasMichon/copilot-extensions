# Worker-Status Observability Hooks (agent-dispatch + agent-bridge)

- **Slug:** `worker-status-observability-hooks`
- **Repo:** copilot-extensions (`plugins/agent-dispatch`, `plugins/agent-bridge`)
- **Branch(es):** per-phase PRs off `dev`
- **Created:** 2026-10-04
- **Status:** Draft
- **Umbrella issue:** [#5257](https://github.com/ThomasMichon/copilot-extensions/issues/5257)
  (agent-dispatch/agent-bridge: deeper worker-status CLI hooks)
- **Sub-issues:** _none yet_
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

Surfaced during a live, multi-hour session operating the odsp-web-harness
`file-picker-repro` ADO repro loop on `agent-dispatch` (this session also
root-caused and fixed the #4990 spawn-starvation bug, drove a dev→main
promotion, and fixed a real steering-card policy gap in the repro worker's
own prompt — see that session's transcript for the full narrative; this
effort is the tooling fallout from doing all of that by hand). Concretely hit
and worked around, repeatedly, across that session:

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
  transcript could even be located.
- Three tasks sat **silently** dead-ended for days: self-excluded from the
  only machine running the fleet (`excludes: ["machine:tmichon-cloud2"]`),
  left over from a misdiagnosed "permanent" ACP host limitation that the
  session later proved has a working fallback. `agent-dispatch doctor`'s
  sweep (no `--task`) reported `examined: 0` the whole time — it does not
  appear to examine a bare `queued` task with no active/failed reservation
  at all.
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
- [ ] Add task_id as a resolvable input to `peek` (agent-bridge) or a new
      `agent-dispatch peek <task_id>` wrapper (whichever owning repo/plugin
      is the right seam — decide during implementation) that performs the
      `show` → (fallback: `reservations list --task` → `worktree` →
      `worktree-status-bundle` → `head_session`) chain internally.
- [ ] Depends on Phase 1 (the resolved session is usually local-body).

### Phase 3 — `doctor` flags queued-with-excludes
- [ ] Add a diagnosis (e.g. `excluded_from_target_machine`) for a plain
      `queued` task carrying a non-empty `excludes` array, so a default
      sweep (no `--task`) surfaces it instead of reporting `examined: 0`.

### Phase 4 — `list` filters for "what needs me"
- [ ] Add `--awaiting-steer` and `--has-excludes` boolean filters to
      `agent-dispatch list`.

### Phase 5 — liveness signal on `show`
- [ ] Surface a cheap liveness signal (session's own last-event timestamp,
      or a lightweight version of `doctor --check-live-sessions`'s per-
      attempt probe) directly in `agent-dispatch show`'s output, so a single
      task lookup doesn't understate how stale `last_seen_at` can get
      relative to real activity.

## Validation Plan

- [ ] **Phase 1/2:** `agent-bridge peek <session_id>` against a real
      `local-body:*` session spawned by the live `file-picker-repro` queue
      (this effort's own grounding context) returns a rendered transcript,
      not the `has no acp_session_id yet` error. Also re-run against an
      existing ACP-registered session to confirm no regression.
- [ ] **Phase 3:** a task manually given a stale `excludes` entry is
      reported by a default (`--repo`/`--label`, no `--task`) `doctor` sweep.
- [ ] **Phase 4:** `agent-dispatch list --awaiting-steer` /
      `--has-excludes` against the live queue returns exactly the tasks a
      manual full-list-and-filter pass would have found.
- [ ] **Phase 5:** `show` on a task whose session is actively emitting
      events (confirmed via raw JSONL) reports it live, not stale.
- [ ] Dogfood each landed phase against the still-running
      odsp-web-harness `file-picker-repro` queue during this same session,
      per the operator's explicit ask to use it as live grounding context.

## Proposal

_Pending — begin with Phase 1 implementation exploration._

## Journal

### 2026-10-04 — Kickoff
- Effort created from issue #5257, itself filed after verifying (by direct
  testing) which CLI gaps were real vs. already-solved.
- Confirmed via vision search that Phase 1/2 are vision-closing (the
  agent-bridge vision already states the exact intent); Phase 3-5 don't
  appear to need a vision edit, flagged for re-check once design firms up.
