---
applyTo: "**"
---

# `validate-and-promote` failure triage fallback

**Fallback policy `[owner: copilot-extensions-harness]`:** A red
`validate-and-promote` run (`.github/workflows/validate-and-promote.yml`) is
a repo-wide release-pipeline gate, not a routine test failure to shrug off --
it blocks **every** already-merged contributor's work from ever reaching
`main` until it's green again, whether or not the failure traces to your own
change. It is entered only via `repository_dispatch` (`dev-advanced`, fired
by the separate `promote-trigger.yml`) or a human `workflow_dispatch` --
never directly by `workflow_run`. Diagnose it with the GitHub run itself,
never a guess: route every `gh` call through the repository-scoped account
wrapper (never a bare `gh`; see `AGENTS.md`'s account-routing rule), list
recent runs, open the failing run, and identify every job that actually
failed first (`fail-fast: false` means more than one can be red at once) --
it may be one or more `full - <plugin>` fan-out jobs (one job per plugin
listed in the full matrix), or it may instead be one of the promotion gate's
other required jobs (`worktree-manager` or `guards-full-sweep`), whose own
failure needs its own diagnosis rather than the fan-out-specific guidance
below. For a fan-out job, pull its log and read the pytest `short test
summary info` / `FAILED` lines literally before touching anything, and
classify it three ways: a **known, accepted flake or transient
runner/checkout/setup failure** already tracked by its own open issue -- link
that issue rather than forcing an unrelated code change (see
`contributing-to-copilot-extensions`'s flake exception); a
**CI-environment-specific test bug** (for example a test that references a
Windows-only `subprocess` constant, like `CREATE_NEW_CONSOLE`, while running
on the Linux `ubuntu-latest` runner); or a **genuine regression** in the
changed code. The latter two are owed a real, versioned fix in the same
spirit -- **fix-forward, never dismiss it as pre-existing** (see
`contributing-to-copilot-extensions`'s full-suite fix-forward obligation).
Land the fix through the normal worktree/PR flow against `dev` -- never
patch `main` directly and never force-retry the pipeline as a substitute for
a real fix. See the `diagnosing-validate-and-promote-failures` skill for the
exact command sequence.
