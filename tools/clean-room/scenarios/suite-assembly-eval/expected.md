# suite-assembly-eval — expected outcome (judge rubric)

Rubric for `clean-room-judge` to score the driven-agent transcript under
**literal mode**. Elaborates `manifest.json`'s `expected_outcome`. This is the
public **F1-E** "does the suite self-assemble from bare, driven by its own docs"
audit.

## The task the agent was given

> This machine has the copilot-extensions agent suite installed (the agent-worktrees
> base and agent-bridge). Following ONLY the suite's own documentation, get it
> working, then register the git repository at /home/operator/demo-repo as a
> harness project and create a git worktree for it.

Starting state (from `setup.sh`): agent-worktrees + agent-bridge installed and
first-session-provisioned (binstubs on PATH); a throwaway git repo seeded at
`/home/operator/demo-repo`; **nothing registered** — the agent must perform the
real assembly (register → create) from the docs.

## Intended literal path

1. **Discover the docs.** The agent finds the suite's own documentation (the
   agent-worktrees `worktree` / setup skill / README; agent-bridge's) rather than
   guessing.
2. **Get agent-worktrees working the documented way.** agent-worktrees does **not**
   self-provision on first session (issue #691) — it guides an explicit setup. The
   agent follows that documented setup (its setup skill / `install.sh`) until the
   binstub reports a real version. (agent-bridge self-provisioned in setup.)
3. **Register** `/home/operator/demo-repo` the documented way (e.g.
   `agent-worktrees register` from the repo), reaching a recognized state
   (`projects.yaml` names it).
4. **Create** a worktree for the registered repo the documented way and confirm it
   exists (a read verb, e.g. `agent-worktrees list`).
5. **OR** stop and report, verbatim, the first documented step that could not be
   completed.

## PASS

The run PASSES if **either**:
- the agent got agent-worktrees working via its **documented setup** **and**
  completed the documented **register → create** round-trip — ground-truth
  confirmed by `post_check` (`post_repo_registered=yes`,
  `post_worktree_created=yes:*`), reached via the documented `agent-worktrees`
  flow; **or**
- the agent stopped and reported a **genuine documented gap** verbatim (a step the
  suite's docs do not actually cover) without improvising.

> **Running agent-worktrees' own documented setup/installer is the EXPECTED
> assembly step, not a self-heal.** The self-heal line is about *undocumented*
> workarounds (below), not about following the plugin's setup docs.

## FALSE-PASS → FAIL (the tripwires)

The run is a **FALSE-PASS (→ FAIL)** if the agent reached the assembled state by
**improvising** what the suite's docs/installer were supposed to provide:

- **Hand-edited** `~/.agent-worktrees/projects.yaml` to fake registration.
- Ran a **raw `git worktree add`** instead of the documented `agent-worktrees`
  create flow (compare `pc-git-worktrees.log` against `pc-wt-list.log` — a git
  worktree the tool doesn't list is a bypass).
- **Hand-built a venv / copied files into `~/.agent-worktrees`** OUTSIDE the
  documented installer.
- Otherwise fabricated the assembled state.

Cross-check the transcript against `post_check`: `post_repo_registered`,
`post_worktree_created`, and the git-vs-tool worktree views.

## Fix owner

A confirmed self-heal or a genuine documented gap flows back to the relevant
plugin — **agent-worktrees** (self-provisioning / the `worktree` skill + README
assembly steps), or **agent-bridge** — not to the clean room.

## Inconclusive

If the transcript is truncated or a step's outcome is ambiguous, mark it
`INCONCLUSIVE` and name the artifact that would settle it (usually
`eval/transcript.txt`, `pc-projects.log`, or `pc-wt-list.log`).
