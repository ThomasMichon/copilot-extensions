---
applyTo: "**"
---

# `validate-and-promote` failure triage fallback

**Fallback policy `[owner: copilot-extensions-harness]`:** A red
`validate-and-promote` run is a repo-wide release-pipeline gate, not a
routine failure -- it blocks **every** contributor's merged work from
reaching `main`, whether or not you caused it. Diagnose via the GitHub run
itself (never guess), routing every `gh` call through the repository-scoped
account wrapper: open the failing run and identify every job that actually
failed (`fail-fast: false` allows more than one) -- it may be one or more
`full - <plugin>` fan-out jobs (one job per plugin in the full matrix), or a
gate job (`worktree-manager`/`guards-full-sweep`), each needing its own
diagnosis. For a fan-out job, read the pytest failure literally, then
classify three ways: a **known, tracked flake/transient failure** (link its
issue, per `contributing-to-copilot-extensions`'s flake exception); a
**CI-environment-specific test bug** (e.g. a Windows-only `subprocess`
constant referenced on the Linux runner); or a **genuine regression**. The
latter two are owed a real, versioned **fix-forward** fix, even if the
failure predates your own change -- never dismissed as pre-existing. Land
it via the normal worktree/PR flow against `dev`, never `main` directly, and
never force-retry the pipeline as a substitute for a fix. See the
`diagnosing-validate-and-promote-failures` skill for the exact commands.
