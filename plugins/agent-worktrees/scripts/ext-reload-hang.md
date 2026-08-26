# CLI release pending: extension-reload "Loading…/Resuming…" hang (temporary)

The upstream fix for github/copilot-agent-runtime#13492 merged in
github/copilot-agent-runtime#13494 on 2026-08-25. Keep this banner and its
workarounds active while waiting for that fix to reach the installed Copilot CLI
release. Once the deployed CLI contains the fix, this temporary warning and
**Bare resume** workaround can be retired.

A generation race in extension reload can leave the `extensions` env-loading
participant incomplete, so startup never finishes on affected builds. Until the
installed CLI includes the merged fix, account for:

1. A newly launched **headed** session with a queued startup prompt can hang on
   "Loading…" with the prompt queued **indefinitely** (it never submits). This
   hits headed agents kicked via agent-dispatch -- verify a dispatched headed
   session actually reached an interactive/ready state before assuming its
   startup prompt ran.

2. A user- or machine-**resumed headed** session can hang on "Resuming…"
   indefinitely unless it is started with **Bare resume**. Bare resume launches
   Copilot with its working directory set to `~/` (home) instead of the
   worktree, purely to keep the cwd off the repo for the brief startup window
   that trips the race. The launch **still exports the usual env overrides**
   (including `COPILOT_CUSTOM_INSTRUCTIONS_DIRS`), so per-project custom
   instructions still load, and once started you are free to `cd` into the
   worktree. Caveat while the pane's cwd is still `~/`: `agent-*` commands that
   infer the worktree from the cwd will misbehave -- **`cd` into your worktree
   directory first** before running any `agent-worktrees` / `agent-*` command.

3. Extensions may reload several times during a single startup, emitting
   duplicate / conflicting "extension ready" notifications -- treat repeated
   ready signals from one startup as expected noise, not separate successful
   loads.

Workarounds for the hang itself: launch from `~/` then `/resume`, or run with
`--no-experimental` (disables extensions).
