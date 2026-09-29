---
applyTo: "**"
---

# `validate-and-promote` failure triage fallback

**Fallback policy `[owner: copilot-extensions-harness]`:** A red
`validate-and-promote` run (`.github/workflows/validate-and-promote.yml`) is
a repo-wide release-pipeline gate, not a routine test failure to shrug off --
it blocks **every** already-merged contributor's work from ever reaching
`main` until it's green again, whether or not the failure traces to your own
change. Diagnose it with the GitHub run itself, never a guess: list recent
runs (`gh run list --workflow=validate-and-promote.yml`), open the failing
run, and isolate the one red `full - <plugin>` job -- the workflow fans out
one full-suite job per runtime plugin, so a fan-out failure never means every
plugin is broken. Pull that job's log and read the pytest `short test summary
info` / `FAILED` lines literally before touching anything. Distinguish a
**CI-environment-specific test bug** (for example a test that references a
Windows-only `subprocess` constant, like `CREATE_NEW_CONSOLE`, while running
on the Linux `ubuntu-latest` runner) from a **genuine regression** in the
changed code -- the fix differs (harden the test's platform assumption vs.
fix the production defect), but both are owed a real, versioned fix in the
same spirit: **fix-forward, never dismiss it as pre-existing** (see
`contributing-to-copilot-extensions`'s full-suite fix-forward obligation).
Land the fix through the normal worktree/PR flow against `dev` -- never
patch `main` directly and never force-retry the pipeline as a substitute for
a real fix. See the `diagnosing-validate-and-promote-failures` skill for the
exact command sequence.
