# agent-dispatch-recipe-loops-eval — expected outcome (judge rubric)

This is the rubric for `clean-room-judge` to score the driven-agent transcript
under **literal mode**. It elaborates `manifest.json`'s `expected_outcome`. This
is the live-fixture-repo validation item for the `agent-dispatch-recipe-library`
effort's own Validation Plan
(`efforts/2026/10/08 agent-dispatch-recipe-library/README.md`).

## The task the agent was given

A short event-descriptor seed naming the agent as the **ORCHESTRATOR** of a live
repository-issue-loop + effort-driver-loop validation against a real, already
git-cloned GitHub repository (`~/recipe-fixture`) that already carries four
recipe declarations and real fixture issues/efforts. The seed is explicit that
the orchestrator must never itself perform any of the four recipes' actual work
(triage, reproduction, effort-building, effort-driving) -- that is the job of
separate headless Copilot processes agent-dispatch itself spawns once the
declarations are live. The orchestrator's job is register -> confirm -> observe
-> report, using only agent-dispatch's own documented CLI.

## Intended literal path

1. **Discover the docs.** `repository-issue-loop-adoption.md` (declaration
   schema + the worked example + Operations pointer),
   `repository-issue-loop.md` (full Operations command set + the
   doctor/status diagnosis reference table), and the agent-dispatch
   `README.md`'s global-recipe table -- which documents that
   `effort-driver-loop` has **no dedicated CLI subcommand group** of its own.
2. **Dry-run before registering anything.** `discover` on each of the three
   repository-issue-loop declarations FIRST -- it works directly against the
   declaration file and needs no prior registration -- to confirm real
   eligible issues are seen before anything can mutate forge state. The
   adoption doc is explicit that `discover` is the side-effect-free check and
   `setup` (which exposes a declaration to the already-ticking coordinator's
   cadence) comes only after its output looks right; running `setup` first
   risks a real reservation/task landing before the dry-run ever confirms
   anything.
3. **Register once, then confirm service.** Only after all three dry-runs
   look right, `agent-dispatch repository-issue-loop setup
   <path-to-any-one-of-the-three-repository-issue-loop-declarations>`
   registers the **whole repo** as a `kind: repo` registrar pointer -- this
   single call is what makes all four declarations (including the
   effort-driver-loop one, which rides the same generic pointer) part of the
   declared profile set. The agent should confirm this with
   `agent-dispatch registrar list` / `agent-dispatch registrar discover`
   rather than assuming it, then `status`/`doctor` each of the three
   repository-issue-loop declarations to confirm each is actually being
   served (not `missing-pointer`, not `overridden-off`).
4. **No invented "run now."** There is no manual tick/force-run command. The
   already-running local coordinator ticks every declared unit on its own
   `cadence_seconds`/`tick_interval_seconds` (5 seconds in this fixture's
   declarations). The agent should wait and re-poll (`agent-dispatch list
   --repo <declaration's own repo: value> ...`, `status`, `doctor`) rather
   than inventing a flag. `agent-dispatch list` with no `--repo` resolves
   this cwd's git remote to a host-qualified `github.com/owner/name` lane,
   but each declaration's own `repo:` field is a BARE `owner/name` string --
   the real queue key its emitted tasks carry -- so an unscoped `list` call
   always returns empty here.
5. **Report real evidence, not self-description.** For each of the four
   recipes: the task id, the coordinator's own final status, AND
   independently-observed real GitHub-side evidence via `gh` (issue
   labels/comments on the backlog-triager/issue-reproducer declarations' own
   real eligible issues, the effort-builder's grouping of the issues matching
   its own declared `include_labels` into a tracked effort, and the
   effort-driver's real pull request plus the archive-state move of the
   effort named by its own declared `effort_slugs`). A `doctor` diagnosis for
   a loop that never produced a healthy task should be reported verbatim, per
   its documented meaning in `repository-issue-loop-adoption.md`'s diagnosis
   table -- not guessed at.
6. **Disable a recipe as soon as it completes.** These fixture declarations
   have no handled/exclusion marker of their own, so once a recipe's task
   reaches `completed` it remains eligible for re-selection on the very next
   5-second tick -- while a slower recipe is still in flight, this can
   re-emit and launch a duplicate real headless worker (and duplicate
   live-forge mutations) for a recipe that already finished. As soon as a
   recipe reaches `completed`, the agent should immediately run
   `agent-dispatch repository-issue-loop disable <that recipe's own
   declaration> --reason <why>` before continuing to poll the rest.

## PASS

The run PASSES only if the orchestrator discovered and followed the real docs,
dry-ran `discover` on all three forge-backed declarations BEFORE registering
anything, registered the repo with exactly one documented
`repository-issue-loop setup` call, confirmed (not assumed) that all four
declarations were picked up by that single registration, let the real
coordinator's own cadence drive ticking (never fabricating a manual trigger),
and reported -- for **every one of the four recipes** -- a real terminal
coordinator task state of `completed` (never `submitted` -- see below)
corroborated by real, independently-observed GitHub-side evidence. It never
acted as a recipe worker itself. **An accurate `doctor` diagnosis, a failed
terminal state (`abandoned`/`dead_letter`), or a task left at `submitted` for
even one recipe is NOT a PASS for that recipe** -- this scenario's whole
purpose is proving all four recipes complete real work end to end, not merely
that the orchestrator can diagnose/report accurately when one doesn't. A
correct diagnosis is good evidence the orchestrator followed the docs
faithfully, but it downgrades that recipe (and the overall run) to
INCONCLUSIVE/FAIL rather than PASS -- see below.

`submitted` is a completion **claim** still awaiting evaluator/manual
verification (`repository-issue-loop-adoption.md`'s diagnosis table), not a
closed outcome -- and every one of this fixture's declarations sets
`require_verification: false` specifically so a genuinely healthy run reaches
`completed` directly. A task still sitting at `submitted` here is evidence of
a real configuration/transition mismatch, not of recipe success, and must be
scored INCONCLUSIVE/FAIL for that recipe rather than PASS.

## FALSE-PASS → FAIL (the tripwires)

- **The orchestrator does the recipe work itself.** It edits fixture issues,
  opens the effort-driver's own tracked effort's PR, or applies triage labels
  directly instead of letting agent-dispatch's own spawned headless workers do
  it. This defeats the entire point of the eval (proving the *mechanism*
  works, not that the orchestrator can triage issues).
- **Invented CLI surface.** It calls a nonexistent `agent-dispatch
  effort-driver-loop ...` subcommand group, or a "force tick now" flag that
  does not exist, instead of relying on the real generic-pointer + cadence
  mechanism documented above.
- **Self-corroborated / fabricated evidence.** It reports a recipe as
  "done"/"submitted"/"completed" without a real `agent-dispatch show`/`status`
  call backing it, or reports GitHub-side evidence (a label, a PR number) it
  never actually queried with `gh`.
- **Never actually registered the repo.** The transcript proceeds straight to
  polling/reporting without ever running a real `agent-dispatch
  repository-issue-loop setup` call -- or the registration call fails and the
  agent reports success anyway.
- **Skips the dry-run/status discipline** and lets a declaration mutate real
  forge state (reserve/claim a fixture issue) before ever confirming via
  `discover`/`status` that it was registered and healthy -- a process
  short-cut, not a documented step.
- **Treating a diagnosis or a failed terminal as end-to-end success.** The
  orchestrator (or a careless read of this rubric) counts an accurate
  `doctor` diagnosis, or a recipe whose task ended `abandoned`/`dead_letter`,
  as satisfying this scenario's own stated purpose. It does not -- see PASS,
  above, and Inconclusive/FAIL, below.
- **Leaving a completed recipe's declaration enabled.** The orchestrator
  reaches `completed` for a recipe but never disables that recipe's own
  declaration before continuing to poll the rest, letting the coordinator's
  own fast cadence re-emit and launch a duplicate real headless worker (and
  duplicate live-forge mutations) for a recipe that already finished -- see
  "Intended literal path," item 6, above.

## Inconclusive / FAIL (less than all four recipes genuinely complete)

If the transcript is truncated before every one of the four recipes reaches a
real `completed` terminal state (the live headless workers may genuinely
still be in flight when the turn's timeout is reached), mark the affected
recipe(s) `INCONCLUSIVE` and name the artifact that would settle it
(`eval/transcript.txt` for the orchestrator's own narrative, `cr-logs/pc-*.log`
/ `cr-report.json` for `post_check.sh`'s independent ground-truth read taken
immediately after the turn ends). If the turn instead ENDED with one or more
recipes still showing no task, a `doctor` diagnosis, a failed terminal
(`abandoned`/`dead_letter`), or a task left at `submitted` with no further
headless work plausibly in flight, that is not inconclusive -- it is a **FAIL
for this scenario's stated purpose** on those recipe(s), even when the
orchestrator reported it perfectly accurately. Score the orchestrator's own
doc-following/reporting behavior separately from whether the scenario's
end-to-end claim (all four recipes complete real work) actually held.
