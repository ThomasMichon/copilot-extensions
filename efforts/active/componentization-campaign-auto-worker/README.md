# Componentization Campaign Auto-Worker

- **Slug:** `componentization-campaign-auto-worker`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase (design/prototype work lands as normal reviewed PRs)
- **Created:** 2026-09-23
- **Status:** Draft
- **Vision:** reduce the human-coordinator burden of running a
  module-componentization campaign, moving toward autonomous execution of the
  proven manual runbook
- **Umbrella issue:** #3372
- **Related:** [Module Componentization Discipline](../module-componentization-discipline/README.md)
  (#2805) -- that effort is the *subject matter* (which files get split, and
  how); this effort is about *automating the process of running it*.

## Guiding Intent

A single long session of the `module-componentization-discipline` effort
proved out a full coordinator-in-the-loop pattern for running a
module-componentization campaign hands-mostly-off: dispatch one background
agent per oversized file into its own isolated worktree, let it land the file
as a sequence of incremental, individually-reviewed PRs, unblock it with
concrete guidance when it stalls, detour around shared repo-wide CI blockers
it hits along the way, and monitor long-running slices via scheduled
check-ins instead of continuous polling. That runbook is now captured as the
`orchestrating-componentization-campaigns` skill
(`plugins/customizing-copilot/skills/orchestrating-componentization-campaigns/SKILL.md`).

This effort's goal is to evolve that *manual, coordinator-judgment-driven*
runbook into something closer to an **auto-worker**: a mostly- or fully-
autonomous process that can run the same campaign with progressively less
human judgment in the loop, starting with the purely mechanical steps and
working toward the genuinely judgment-heavy ones.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator session | Designs and pilots automation increments; owns the skill/runbook as the interim source of truth | Interactive Copilot CLI session in this repo |
| Dispatched background agents | Execute individual file-split campaigns per the runbook | `task` tool, `mode: "background"`, one isolated worktree per file |

## Coordination

- **Topology:** independent per-slice PRs (same topology as the parent
  `module-componentization-discipline` effort).
- **Host (owns PRs):** the coordinator session dispatching each background
  agent; each dispatched agent self-merges its own slices per the runbook.
- **Delegates:** background agents, one per file under active campaign.
- **Handoff:** the `orchestrating-componentization-campaigns` skill is the
  durable handoff artifact between sessions -- any session (this one or a
  future one) can pick up the coordinator role by reading that skill, without
  needing this effort's own history replayed.

## Context

Session history that produced this effort (see the parent effort's Journal
for the full blow-by-blow):

- `agent-bridge/agent_registry.py`: 2,963 -> 451 lines, one PR (#3251).
- `agent-dispatch/__main__.py`: 5,046 -> 919 lines, across 8 sequential
  slice PRs (#3261, #3265, #3269, #3274, #3278, #3280, #3282, #3283), plus
  two unrelated shared-CI-blocker detour PRs (#3316 fixing #3276's
  payload-invocation fallout and a version-consistency drift; #3325 a
  doc-only follow-up) discovered and fixed mid-campaign, and one
  architecture-shift caveat recorded mid-campaign (#3365 -- CLI `__main__.py`
  files are moving toward on-demand/lazy per-subcommand loading, which the
  eager-import compatibility-root splits used so far would need to rework
  around once that pattern lands).
- `agent-dispatch/queue.py`: 4,508 -> 799 lines, across 4 sequential slice
  PRs (#3330, #3345, #3351, #3355), plus one more shared-CI-blocker detour PR
  (#3341, a `terminal_fragment.py` module-size baseline drift on `main`).

Every one of those slices was driven by a background `general-purpose` agent
dispatched with a long, hand-written prompt re-derived (with variations) each
time by the coordinator. The genuinely repeated, mechanical parts of that
prompt/loop are the first automation target.

## Request

_(operator, 2026-09-23, paraphrased from the live session)_: "Make a skill or
doc learning based on our methodology from this worktree's sessions so we can
repeat it easily. Or create an effort journaled from what was done and we'll
pick it up later. End goal is an auto-worker."

Both halves of that request were acted on: the skill
(`orchestrating-componentization-campaigns`) captures the methodology now, for
immediate reuse without automation; this effort tracks the "end goal is an
auto-worker" half as its own, separately-paced piece of work, since building
actual automation is a materially different (and slower, riskier) undertaking
than writing down what already works.

## Plan

### Phase 0 — capture the manual runbook (done, this session)
- [x] Write `orchestrating-componentization-campaigns` skill capturing the
      per-file dispatch loop, incremental multi-PR slicing discipline,
      unblocking-a-stalled-agent guidance, the shared-CI-blocker detour
      protocol, and long-running-agent monitoring (direct polling vs.
      `manage_schedule` 2-hour check-ins).
- [x] Register the skill in `plugins/customizing-copilot/README.md`'s skill
      table; verify `check-docs-consistency.py`, `check-runbook-references.py`,
      and `check-skills.py` all pass.
- [x] Open this effort + its umbrella issue (#3372) to track the
      automation half of the request separately from the now-captured skill.

### Phase 1 — automate backlog selection + dispatch _(agent-recommended)_
- [ ] Design a small tool/script that reads `tools/rank-module-size.py`'s
      output, picks the next eligible target (skipping standing-exclusion
      files and anything already under active campaign), and emits the
      isolated-worktree-creation + background-agent-dispatch prompt
      mechanically rather than hand-written each time.
- [ ] Decide where this tool lives (likely alongside
      `tools/rank-module-size.py`, or as a new `tools/dispatch-componentization-slice.py`).

### Phase 2 — structured agent reports for automatable judgment calls _(agent-recommended)_
- [ ] Define a structured (e.g. JSON) report contract a dispatched agent
      emits at each checkpoint (slice merged / blocked / fully done), so the
      "genuinely blocked vs. needs a smaller bite" judgment (see the skill's
      *Unblocking a stalled agent* section) can be partially automated by a
      verifier reading the structured report plus live repo state
      (`git log`, `gh pr view`, ranking tool) instead of a coordinator reading
      free-form prose.
- [ ] Prototype a verifier pass that cross-checks a dispatched agent's
      self-report against the live repo before accepting a "done" or
      "blocked" claim, per the skill's existing manual-verification step.

### Phase 3 — CI-blocker auto-classification _(agent-recommended)_
- [ ] Codify the "check `main`'s own `check-runs` for the same job before
      assuming a red PR check is caused by the diff" step (see the skill's
      *Detouring around a shared repo-wide CI blocker* section) into a small
      reusable tool, so an automated worker can classify a red gate as
      pre-existing/unrelated without a coordinator's manual `gh api` call.
- [ ] Design the boundary for "trivial, obviously-safe correction" the tool
      is allowed to auto-fix (version-string drift, baseline-ceiling widen)
      versus what must still escalate to a human or a dedicated fix effort.

### Phase 4 — orchestration wrapper (a `run_factory`/agent-dispatch recipe?) _(agent-recommended)_
- [ ] Once Phases 1-3 have working pieces, evaluate whether the full loop
      belongs in a `factories_manage`-authored factory (bounded, credited,
      operator-approved run) or an `agent-dispatch` worker-pool recipe
      (matching the pattern other facility efforts use for standing
      autonomous workers), and prototype accordingly.
- [ ] Define the human checkpoints that remain even in the fully-automated
      version (e.g. approving the very first dispatch of a new campaign,
      or any auto-fix PR the CI-blocker classifier proposes).

## Validation Plan

- [ ] The skill (Phase 0) is validated by direct reuse: a future
      componentization session should be able to follow it without
      re-deriving the pattern from scratch.
- [ ] Each later phase's automation piece is validated against the *actual*
      remaining backlog (`agent-index/cell-runtime.py`,
      `agent-codespaces/config.py`, `tools/clean-room` fixtures,
      `agent-dispatch/supervisor.py`, and whatever's ranked next by
      `rank-module-size.py` at the time) rather than a synthetic test case,
      since the whole point is real-world reduction of coordinator load.

## Proposal

_Pending._ Phase 0 is complete; Phases 1-4 need operator sign-off on scope and
sequencing before implementation starts, per the standing review-gate rule for
effort plans.

## Journal

### 2026-09-23 — Kickoff, Phase 0 capture
- Closed out the `module-componentization-discipline` effort's explicitly
  ordered three-file backlog this session (`agent_registry.py`,
  `agent-dispatch/__main__.py`, `agent-dispatch/queue.py` — see that effort's
  own Journal for the detailed slice-by-slice record).
- Wrote and registered the `orchestrating-componentization-campaigns` skill
  distilling the coordinator-side methodology actually used across that
  session: per-file worktree + background-agent dispatch, incremental
  multi-PR slicing with monkeypatch-safe re-exports, unblocking a stalled
  agent with concrete (not just encouraging) guidance, a three-step protocol
  for diagnosing/classifying/detouring around shared repo-wide CI blockers
  (exercised for real against #3276's fallout, #3315, and a
  `terminal_fragment.py` baseline drift), and a long-running-agent
  monitoring strategy (direct polling for short campaigns, `manage_schedule`
  2-hour check-ins for multi-hour ones).
- Opened this effort + umbrella issue #3372 to carry forward the "end goal is
  an auto-worker" half of the operator's request as its own paced piece of
  work, since it's a materially larger undertaking than the skill capture.
- Phases 1-4 are marked `_(agent-recommended)_`: the operator's literal ask
  was "make a skill/doc... or create an effort... end goal is an auto-worker"
  — the phase breakdown itself (backlog automation, structured reports,
  CI-blocker classification, orchestration wrapper) is this session's own
  proposed decomposition of "auto-worker," not something the operator
  specified in that level of detail.
