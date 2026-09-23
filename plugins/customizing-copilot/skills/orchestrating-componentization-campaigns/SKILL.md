---
name: orchestrating-componentization-campaigns
description: >
  Runbook for *running* a multi-file module-componentization campaign as a
  coordinator dispatching background agents -- one isolated worktree and one
  background sub-agent per file, incremental multi-PR slicing within a file,
  the "don't race review" merge discipline, detouring around shared
  repo-wide CI blockers, and long-running-agent monitoring (polling vs.
  scheduled check-ins). This is the *process/orchestration* layer;
  `componentizing-modules` is the *mechanical* how-to-split-one-file layer --
  use both together. Distilled from the `module-componentization-discipline`
  effort's live sessions.
  Trigger phrases include:
  - 'run the componentization campaign'
  - 'dispatch a slice for this file'
  - 'work down the module-size backlog'
  - 'coordinate the split across multiple files'
  - 'the background agent is blocked'
  - 'main is red, unblock the PR'
  - 'auto-worker for componentization'
---

# Orchestrating Componentization Campaigns

A runbook for the **coordinator** role in a multi-file module-componentization
campaign: picking targets off the backlog, dispatching one background agent
per file into its own isolated worktree, watching it through incremental
multi-PR slices, unblocking it when it stalls or is genuinely blocked, and
detouring around shared repo-wide CI failures that would otherwise stall every
slice. Pair this with `componentizing-modules` (the mechanical seam-finding
and extraction how-to) -- that skill is what the *dispatched* agent should
read; this one is what the *coordinator* does.

## When to use this

- You (the coordinator) are working down `tools/rank-module-size.py`'s
  backlog and want to parallelize/delegate the actual splits rather than do
  every extraction yourself.
- A dispatched background agent reports "blocked" and you need to judge
  whether that's a genuine dead end or just needs a smaller bite.
- A PR's `guards + lint` (or any CI gate) is red for a reason that looks
  unrelated to the diff, and you need to decide whether to fix it, detour
  around it, or escalate.
- A background agent has been running for a very long time (hours) and you
  need a monitoring strategy that doesn't burn the whole session on polling.

## The per-file dispatch loop

For each file on the backlog, repeat this loop (this is what actually landed
`agent-bridge/agent_registry.py` 2,963->451, `agent-dispatch/__main__.py`
5,046->919 across 8 slices, and `agent-dispatch/queue.py` 4,508->799 across 4
slices, in one session):

1. **Sync the anchor first.** From the plain (non-worktree) checkout: fetch,
   checkout `main`, pull `--ff-only`, and re-run
   `python tools/rank-module-size.py --limit 8` to confirm the target is
   still current and get a fresh baseline. Never trust a stale ranking from
   earlier in a long session -- other agents/PRs land continuously.
2. **Create a fresh, isolated worktree per file** (never reuse one worktree
   across files/slices) -- e.g. `copilot-extensions create --json`.
3. **Dispatch one `general-purpose` background sub-agent per file** (`task`
   tool, `mode: "background"`) with a thorough prompt that includes:
   - The exact worktree path to work in.
   - A pointer to the effort README (Guiding Intent + relevant Journal
     entries) and to `componentizing-modules` for the extraction mechanics.
   - **Explicit instruction to land the file as MULTIPLE sequential
     slices/PRs**, not one giant PR -- see *Incremental multi-PR slicing*
     below. Don't let the agent attempt the whole file in one shot; large
     CLI-registration/orchestration files with broad monkeypatch surfaces
     are exactly the shape that turns "one big PR" into an unvalidatable,
     unmergeable mess.
   - The full validation bar (test suite, ruff, module-size, install-contract,
     version-consistency, read-only dogfood) and the requirement to run it
     through the bounded test path only, never bare `pytest`.
   - Any standing exclusions (e.g. "never touch `cmd_copilot`" in
     `agent-worktrees/__main__.py` -- another agent's concurrent effort).
   - The "watch review, don't race it; detour around unrelated red-main
     failures" merge discipline (below).
   - An explicit stopping condition: continue until at/near the cap, **or**
     a genuinely unsafe-to-move piece is found (document why and stop only on
     that piece, not the whole file).
4. **Don't idle-poll the parent turn.** Continue independent coordinator work
   (or end the turn to let the notification wake you) rather than spinning on
   `read_agent` calls that produce no new information turn after turn -- see
   *Long-running-agent monitoring* below for what to do when a slice runs for
   hours.
5. **When the agent reports "blocked," judge before accepting it.** A report
   like *"the code is in an intermediate, unvalidated state; I stopped rather
   than force it"* after only exploratory reads is usually **not** a genuine
   dead end -- it's the agent trying to do too much in one pass. Send back
   concrete, mechanical unblocking guidance (see *Unblocking a stalled agent*
   below) before accepting the blocker as real. A genuine blocker looks like
   a specific closure/state-sharing problem the agent can name precisely,
   after having actually attempted a careful incremental extraction.
6. **After each PR lands, re-sync the anchor** and confirm the file actually
   dropped out of (or down in) the ranking -- don't just trust the agent's
   own self-report; verify with `git log`/`rank-module-size.py` yourself
   before considering that file done.
7. **When the file is at/near cap or the agent reports a confirmed genuine
   blocker, stop dispatching for that file** and move to the next backlog
   item, or check in with the operator if the next candidates span unrelated
   plugins.

## Incremental multi-PR slicing (within one file)

A single oversized file (especially a `__main__.py` CLI-registration giant or
a large orchestration module like `queue.py`) should almost never land as one
PR. Instruct the dispatched agent explicitly:

- **One command-family / responsibility-band / mixin group per PR.** Finish
  that one extraction completely -- including every re-exported symbol a
  test monkeypatches -- validate it fully, commit, PR, merge, *then* start
  the next slice. This mirrors exactly how `agent-worktrees/__main__.py`
  (29,173->8,162 across many slices), `agent-dispatch/__main__.py`
  (5,046->919 across 8), and `agent-dispatch/queue.py` (4,508->799 across 4)
  actually landed.
- **Monkeypatch-safety discipline:** when a test does
  `unittest.mock.patch("pkg.module.some_name", ...)`, the moved function's
  *body* can live in a new sibling module, but `some_name` must still be
  reachable and rebindable at `pkg.module.some_name` -- re-export it (e.g.
  `from .new_module import some_name` at the top of the compatibility root)
  so the old patch target keeps working unchanged. Grep the test suite for
  every monkeypatched symbol on the module you're splitting before deciding
  the split is complete.
- **It's fine -- expected -- to land as a multi-PR sequence for one file.**
  The effort's own default is "one PR per module," but that's explicitly a
  default, not a hard rule; a single oversized file is exactly the case where
  a deliberate multi-PR sequence is the safer, more tractable choice.
- **Full validation bar before every slice's merge, not just at the end.**
  Each slice should leave the file in a fully green, fully working state --
  never chain "I'll validate everything once at the very end" across
  multiple half-finished slices.

## Unblocking a stalled agent

When a dispatched agent reports it's stopping rather than finishing:

1. **Don't accept the first "blocked" report at face value if the agent
   hasn't yet attempted a careful incremental pass.** Send guidance that:
   - Explicitly rejects doing the "whole file in one shot" and asks for one
     command-family/responsibility band at a time instead.
   - Names the specific re-export/monkeypatch pattern to follow (see above),
     with a concrete code example if the agent's report suggests it's
     missing that mechanic.
   - Explicitly permits landing as multiple smaller PRs instead of one big
     one, citing the prior-art sequence (e.g. "this is consistent with how
     `agent-worktrees/__main__.py` was split -- N sequential slices").
   - Asks it to run the full validation bar *before* starting the next
     family, so each increment is provably safe before building on top of
     it.
   - Only accepts a genuinely-unsafe-to-move piece as a stopping point if the
     agent can name the specific problem (a closure capturing local state, a
     subtle ordering dependency) -- not merely "many things reference it."
2. **Keep sending "keep going" nudges between merged slices.** A background
   agent that just merged a slice and is now idle needs an explicit
   instruction to continue to the next family without stopping to be
   re-prompted for every single slice -- otherwise the campaign stalls one PR
   at a time waiting on the coordinator.
3. **When the agent reports fully done (or as far as safely possible),
   verify independently** (git log, `rank-module-size.py`, PR merge states)
   before treating the file as closed.

## Detouring around a shared repo-wide CI blocker

A long campaign will eventually hit a PR whose gating check (e.g.
`guards + lint`) is red for a reason that has nothing to do with the diff --
main itself is broken. This happened three times in one session
(`#3276` payload-invocation manifest drift, `#3315` version-consistency
drift, a `terminal_fragment.py` module-size baseline drift). The protocol:

1. **Diagnose before reacting** (this is the `error-response` discipline
   applied to CI): confirm the failure is genuinely pre-existing and
   unrelated by checking the same job on current `main`'s own head commit --
   `gh api repos/<owner>/<repo>/commits/<main-sha>/check-runs` -- rather than
   assuming from the PR's own red X alone.
2. **Classify the fix, don't just react:**
   - **Trivial, obviously-safe correction** (a stale version-string mismatch
     across `marketplace.json`/`plugin.json`/`pyproject.toml`; a
     grandfathered module-size ceiling that needs widening to match a file's
     *already-merged* legitimate growth) -- fix it directly: open a small,
     narrowly-scoped PR for *just* that correction, watch it through review
     and CI like any other PR, merge it, then rebase/merge the stalled slice
     branch onto the now-fixed `main` and continue.
   - **A real regression in a plugin that isn't the one being split, and
     isn't a trivial correction** -- do not attempt to fix it as part of this
     campaign. File it as its own tracked issue if it isn't already, note it
     in the effort README, and move on; fixing unrelated regressions is
     scope creep that risks its own review cycle derailing the campaign.
   - **A standing-exclusion file** (e.g. `agent-worktrees/__main__.py`'s
     `cmd_copilot`) -- never touch it, no matter how tempting the fix looks;
     another agent owns that surface concurrently.
3. **Never force-merge or bypass the gate** to route around a red check --
   fix the actual cause (via the trivial-correction path) or leave the PR
   open and escalate, exactly as `error-response-discipline` requires for any
   destructive/side-effecting reaction to a failure signal.
4. **Recheck required-status-check reality before assuming a red job blocks
   merge.** Not every CI job is a required/gating check
   (`gh api repos/<owner>/<repo>/branches/main/protection` -- a 404 means no
   branch-protection rule requires anything). A confirmed pre-existing,
   unrelated, non-required job failure doesn't have to block your merge even
   if you don't fix it.

## Long-running-agent monitoring

A single file's multi-slice campaign can run for hours of wall-clock time
across many PRs, review rounds, and CI cycles. Don't spend the whole session
turn-by-turn polling a background agent that's still legitimately working:

- **Short campaigns (roughly under an hour of expected activity):** poll
  directly with `read_agent(wait: true, timeout: 180)` in a loop, sending a
  brief one-line progress note between polls. This is fine for the first
  file or two while you're still calibrating how long the pattern takes.
- **Long campaigns (multiple hours, expected to span many PR review
  cycles):** switch to `manage_schedule` with a **2-hour interval** instead
  of continuous polling. The scheduled prompt should:
  - Check the agent's status with `read_agent(wait: false)` first.
  - If there's new output, review it: if the agent is blocked, send
    unblocking guidance (per *Unblocking a stalled agent* above); if it's
    genuinely done or genuinely blocked with no path forward, summarize the
    final state to the operator and **stop the schedule**.
  - If it's still actively progressing with no new report, just note brief
    progress and let it keep working -- don't spam redundant nudges into an
    agent that's already moving.
  - Update the session's todo-tracking (or effort README) status as
    appropriate so the campaign's state survives a session boundary.
- **Always verify a background agent's own "done" self-report independently**
  before either mode declares victory -- `git log`, `gh pr view`, and
  `rank-module-size.py` are the ground truth, not the agent's prose summary.

## Recording mid-campaign architectural shifts

If new information arrives mid-campaign that changes the calculus for a
*class* of remaining targets (e.g. an operator flags that CLI `__main__.py`
files are moving toward a lazy per-subcommand loading pattern, making further
eager-import compatibility-root splits a near-term rework risk), don't just
let that live in the current session's memory:

1. Record it directly in the effort README's Plan/checklist, right next to
   the affected backlog item, so a future session (or a different
   coordinator) sees the caveat *before* dispatching another agent at that
   class of target.
2. Land it as its own small, focused, reviewed PR (doc-only changes still go
   through review -- see `CONTRIBUTING.md`'s Documentation impact
   requirement) rather than folding it silently into an unrelated slice's PR.
3. Explicitly scope the caveat: state which remaining targets it *does* and
   *doesn't* apply to, so it doesn't get over-applied to unrelated shapes
   (e.g. large non-CLI classes/free-function modules are usually unaffected
   by a CLI-loading pattern shift).

## Toward an "auto-worker": what's still manual

This runbook is the human-coordinator-in-the-loop version of the pattern. The
manual steps that would need to become automated policy/tooling for a fully
autonomous "auto-worker" version of this campaign are the natural next design
questions (see the related effort, if one exists, for the current state of
that automation work):

- Picking the next backlog target and dispatching the isolated worktree +
  background agent (mechanical -- a good first automation candidate).
- Judging "genuinely blocked" vs. "needs a smaller bite" from an agent's
  prose report (currently coordinator judgment; would need either a stricter
  structured report contract from the dispatched agent, or a
  reviewer/verifier pass).
- Diagnosing and classifying a shared CI blocker (trivial-fix vs.
  escalate-vs-leave-alone) -- currently coordinator judgment using
  `error-response-discipline`; codifying the "check main's own check-runs
  first" step into a reusable tool would remove most of the judgment call.
- Deciding when to switch from direct polling to a scheduled check-in cadence
  -- currently a coordinator heuristic based on expected campaign length.

## Anti-patterns

- **Reusing one worktree across multiple files/slices.** Always create a
  fresh isolated worktree per file being split.
- **Dispatching a single agent to "do the whole file"** for anything over a
  couple thousand lines with a broad monkeypatch surface -- this reliably
  produces the "intermediate, unvalidated state, I'm stopping" report on the
  first attempt. Ask for multi-PR slicing up front.
- **Force-merging or disabling a CI check** to get around a red gate instead
  of diagnosing and classifying the failure first.
- **Continuous-polling a multi-hour background agent for the entire
  session** instead of switching to a scheduled check-in -- this burns the
  coordinator's own turn budget for no benefit once the agent is legitimately
  mid-review-cycle for the third or fourth time.
- **Trusting a dispatched agent's own "fully done" report without
  independent verification** via `git log` / `gh pr view` / the ranking
  tool.
