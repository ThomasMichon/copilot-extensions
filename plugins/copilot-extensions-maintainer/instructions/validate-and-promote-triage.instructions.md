---
applyTo: "**"
---

# `validate-and-promote` failure triage fallback

**Fallback policy `[owner: copilot-extensions-maintainer]`:** Use this procedure
only within an assigned validation or maintenance scope. A red
`validate-and-promote` run or an enabled maintainer package does not assign
release maintenance or authorize an issue/PR.

Within assigned diagnosis, inspect the actual GitHub run through the
repository-scoped account wrapper. Identify every failed job, including
`full - <plugin>` fan-out jobs and `worktree-manager`/`guards-full-sweep`.
Read each failure literally and classify a known accepted flake/transient
failure, CI-environment-specific test bug or genuine regression. Preserve
evidence, disposition and acceptance impact; do not dismiss it as pre-existing.

Perform **fix-forward** source work only when the change or explicit
harness-test maintenance charter covers it. Otherwise report the exact blocker
and offer a report at the established destination without automatically filing
an issue or source PR. Return to the product task; do not wait for unrelated
promotion or rollout. Required CI remains a merge gate: do not weaken tests,
bypass CI or force-retry the pipeline to hide a failure. Assigned fixes follow
the normal `dev` worktree/PR flow in `contributing-to-copilot-extensions`.
See `diagnosing-validate-and-promote-failures` for scoped diagnostic commands.
