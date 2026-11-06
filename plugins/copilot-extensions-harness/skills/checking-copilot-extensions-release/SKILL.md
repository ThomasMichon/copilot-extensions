---
name: checking-copilot-extensions-release
description: >-
  Checks whether a ThomasMichon/copilot-extensions PR's changes have reached main.
  Use when asked "has this change made it into main", "has it moved from dev to
  main", "is this available yet", or "can people try it out" about a
  copilot-extensions PR.
---

# Check release availability

Contributor PRs merge into `dev`. Promotion regenerates `main` from a validated
`dev` snapshot, possibly batching many PRs under a new commit. Original-commit
ancestry against `main` does not establish release status.

1. Resolve the referenced PR; ask if ambiguous. Use repository-scoped GitHub
   access as in `contributing-to-copilot-extensions`.
2. Read the PR's state, base and diff (`pulls/<number>/files`, with pagination).
   An unmerged PR into `dev` is not released through promotion.
3. Pin the current `main` SHA. Exclude consumed `.changefiles/*.json` entries;
   promotion removes them. Read the remaining affected files at that SHA using
   `contents/<path>?ref=<sha>` and check the PR's effective changes, allowing
   subsequent edits and generated version differences. A merged promotion PR
   or its recorded source snapshot can explain provenance, but its title,
   green CI, or a version bump alone is not proof the change is present.
4. Report **available on main**, **not yet on main**, or **unable to verify**,
   with the pinned SHA and concrete evidence. Failed reads or inconclusive
   comparisons are **unable to verify**, not absence. Availability on `main`
   means released, not installed locally; do not install or deploy.

Reuse successful content reads while `main` is unchanged. This skill checks once;
scheduling belongs to its caller.
