# Bypass-config profile template (`projection-reflect`)

Adapt this to whichever review gate the adopting repo already uses (a custom
merge-queue policy, a branch-protection exception scoped by label, etc.). The
conjunction below is the actual safety boundary -- implement every clause; do
not treat identity or label alone as sufficient.

## Required conjuncts (fail closed on any one)

1. **Live consent.** Call `load_consent(repo_root)` fresh for *this* PR --
   never a cached "this repo is enrolled" flag from setup time. `None` means
   refuse the bypass unconditionally.
2. **Producer identity.** The PR's author is the repo's existing trusted
   deterministic identity already used for its own reflect-style automation
   (never a newly minted one -- see the setup skill's own requirement).
3. **Stamp label.** The PR carries the agreed stamp label for this reflect
   kind (distinct from any other bypass-eligible automation the repo runs).
4. **Path scope.** Every changed path is inside `.github/instructions/**` or
   the projection lock file (`.github/copilot/context-projections.json`) --
   nothing else.
5. **Diff shape.** Every changed file is a regular-file add or modify; no
   deletions, renames, symlinks, or mode changes.
6. **Recompute match.** A fresh recompute of the changed lock entries
   byte-matches both the PR's lock-entry diff and the actual generated
   instruction files it declares (see `projection_reflect.py`'s own module
   docstring for the immutable-pin caveat this still carries).
7. **Trusted-source allowlist.** Every changed lock entry's marketplace is in
   `consent.trusted_marketplaces` -- reuse
   `projection_reflect.bypass_decision`'s own check rather than
   re-implementing it.

A PR failing **any** conjunct is review-only; it never falls back to a
partial or best-effort bypass.
