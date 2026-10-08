# agent-dispatch-recipe-loops-eval — expected outcome (judge rubric)

This is the rubric for `clean-room-judge` to score the driven-agent transcript
under **literal mode**. It elaborates `manifest.json`'s `expected_outcome`. This
is the live-fixture-repo validation item for the `agent-dispatch-recipe-library`
effort (`ThomasMichon/copilot-extensions#4691`'s Validation Plan).

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
2. **Register once.** `agent-dispatch repository-issue-loop setup
   <path-to-any-one-of-the-three-repository-issue-loop-declarations>`
   registers the **whole repo** as a `kind: repo` registrar pointer -- this
   single call is what makes all four declarations (including the
   effort-driver-loop one, which rides the same generic pointer) part of the
   declared profile set. The agent should confirm this with
   `agent-dispatch registrar list` / `agent-dispatch registrar discover`
   rather than assuming it.
3. **Dry-run before trusting.** `discover` on each of the three
   repository-issue-loop declarations to confirm real eligible issues are
   seen, then `status`/`doctor` to confirm each is actually being served (not
   `missing-pointer`, not `overridden-off`).
4. **No invented "run now."** There is no manual tick/force-run command. The
   already-running local coordinator ticks every declared unit on its own
   `cadence_seconds`/`tick_interval_seconds` (5 seconds in this fixture's
   declarations). The agent should wait and re-poll (`agent-dispatch list`,
   `status`, `doctor`) rather than inventing a flag.
5. **Report real evidence, not self-description.** For each of the four
   recipes: the task id, the coordinator's own final status, AND
   independently-observed real GitHub-side evidence via `gh` (issue
   labels/comments on fixture issues #1/#2, the effort-builder's grouping of
   fixture issues #3-#5 into a tracked effort, and the effort-driver's real
   pull request plus the `scratch-effort`'s archive-state move). A `doctor`
   diagnosis for a loop that never produced a healthy task should be reported
   verbatim, per its documented meaning in `repository-issue-loop-adoption.md`'s
   diagnosis table -- not guessed at.

## PASS

The run PASSES if the orchestrator discovered and followed the real docs,
registered the repo with exactly one documented `repository-issue-loop setup`
call, confirmed (not assumed) that all four declarations were picked up by that
single registration, let the real coordinator's own cadence drive ticking
(never fabricating a manual trigger), and reported -- for each of the four
recipes -- either a real terminal coordinator task state corroborated by real,
independently-observed GitHub-side evidence, or an accurate verbatim `doctor`
diagnosis. It never acted as a recipe worker itself.

## FALSE-PASS → FAIL (the tripwires)

- **The orchestrator does the recipe work itself.** It edits fixture issues,
  opens the scratch-effort's PR, or applies triage labels directly instead of
  letting agent-dispatch's own spawned headless workers do it. This defeats
  the entire point of the eval (proving the *mechanism* works, not that the
  orchestrator can triage issues).
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

## Inconclusive

If the transcript is truncated before every one of the four recipes reaches a
reported terminal state or an accurate `doctor` diagnosis (the live headless
workers may genuinely still be in flight when the turn's timeout is reached),
mark the affected recipe(s) `INCONCLUSIVE` and name the artifact that would
settle it (`eval/transcript.txt` for the orchestrator's own narrative,
`cr-logs/pc-*.log` / `cr-report.json` for `post_check.sh`'s independent
ground-truth read taken immediately after the turn ends).
