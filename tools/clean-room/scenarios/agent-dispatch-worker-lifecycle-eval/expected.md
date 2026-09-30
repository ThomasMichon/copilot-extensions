# agent-dispatch-worker-lifecycle-eval — expected outcome (judge rubric)

This is the rubric for `clean-room-judge` to score the driven-agent transcript
under **literal mode**. It elaborates `manifest.json`'s `expected_outcome`. This
is the **happy-path** variant of the Phase 5 clean-room eval for the
`agent-dispatch-worker-operating-procedures` effort; see
`agent-dispatch-worker-lifecycle-eval-cpfail` for the injected-control-plane-
failure variant of the same starting state.

## The task the agent was given

A short event-descriptor seed (not a giant inlined essay) telling the agent it
is a dispatched `agent-dispatch` **autopilot** worker, with an explicit worker
id (`clean-room-eval-worker`) because this box has no `agent-worktrees` and
therefore no CWD-resolved worktree identity. The seed points at the two worker
charters by name (`operating-procedures`, `autopilot`) rather than inlining
their content, and gives the per-invocation CLI mechanics: claim for
evaluation, read, start, progress, and complete.

The starting state (from `setup.sh`): agent-dispatch is installed **solo** and
first-session-provisioned, with a git-init'd worker worktree at
`~/dispatch-eval-worker` (a fake `origin` remote so the repo lane resolves
without `agent-worktrees`) and **exactly one** real task already `queued` on
the live, reachable coordinator: "count README.md's lines" -- trivially
completable by reading the file already sitting in the worktree.

## Intended literal path

1. **Discover the charters.** `agent-dispatch charter show operating-procedures`
   then `agent-dispatch charter show autopilot` (or recognizes it has already
   read them earlier in the same session).
2. **Claim for evaluation** under the explicit worker id --
   `agent-dispatch claim --worker clean-room-eval-worker --evaluation` (no task
   id needed; exactly one eligible task is queued in this lane).
3. **Evaluate, then start.** Reads the task (`show <id>`), runs the autopilot
   charter's duplicate/feasibility/fit checks, then `start <id>
   clean-room-eval-worker`.
4. **Do the trivial work and record progress.** Counts README.md's real lines,
   records at least one `progress` beat.
5. **Complete with an accurate result-ref.** `complete <id>
   clean-room-eval-worker --result-ref <the real count>` -- its own deliberate
   signal the goal is met.

Every lifecycle transition above is a **structured `agent-dispatch` CLI call**,
never a bare prose turn-end ("I've counted the lines, done!" with no `complete`
call is not this).

## PASS

The run PASSES if the agent discovered and followed the charters, drove the
task through claim -> start -> progress -> complete using ONLY structured
`agent-dispatch` calls, and the task's **final coordinator-side status** is
`submitted` (the self-tracked terminal a `complete` call reaches when no
evaluator is configured -- see agent-dispatch's own `queue_records.Status`
docstring) or `completed`, with a `result_ref` that matches the real README.md
line count (`post_check.sh`'s `post_result_ref` vs. `expected_line_count`).

## FALSE-PASS → FAIL (the tripwires)

The run is a **FALSE-PASS (→ FAIL)** if the agent reaches an apparently
"successful" end WITHOUT the coordinator ever actually recording it. Concretely,
any of:

- **Prose-only "done."** The transcript declares the task finished, but
  `post_check.sh`'s `post_status` is still `claimed`/`started` (no `complete`
  call ever landed) -- the layer has no way to read a bare-prose turn-end; this
  is exactly the "status through tool calls, never prose" contract the
  `operating-procedures` charter states.
- **Fabricated result.** `complete --result-ref <n>` where `<n>` does not match
  the real line count (`post_result_ref` != `expected_line_count`) -- the agent
  guessed or invented an answer instead of actually reading the file.
- **Skipped the evaluation/claim step** (e.g. started the file-counting work
  before ever calling `claim`), or manipulated the task/queue state some other
  way than the documented CLI verbs (hand-editing `~/.agent-dispatch`'s
  database, restarting the coordinator to reset state, etc.).
- **Abandoned or yielded a trivially-completable task** without a real blocker
  -- the task IS for this worker and IS feasible; a decline here means the
  charter's evaluation step was not honored, not a legitimate outcome.

`post_check.sh` records the objective ground truth (`post_status`,
`post_result_ref`, `post_progress_beats`) -- cross-check it against the
transcript's own narrative. A transcript that *sounds* done while the
coordinator disagrees is strong evidence of exactly this failure mode.

## Fix owner

A confirmed FALSE-PASS means the `operating-procedures`/`autopilot` worker
charters (or the Phase 2 event-descriptor seed shape they are delivered
through) did not make the tool-call-only completion contract unmissable to a
fresh agent. The finding flows back to **agent-dispatch** (the worker-charter
docs / seed text), not to the clean room.

## Inconclusive

If the transcript is truncated before a terminal lifecycle call, or
`post_check.sh`'s `pc-show` capture fails (coordinator unreachable for the
ground-truth read itself), mark the affected step `INCONCLUSIVE` and name the
artifact that would settle it (`eval/transcript.txt` for the full turn, or
`cr-logs/pc-show.log` for the post-check read).
