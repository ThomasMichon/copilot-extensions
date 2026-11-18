# Retire previously projected maintenance guidance

The package split does not automatically delete guidance already projected into
an adopting workspace. Ordinary projection sync preserves old lock entries,
selectors and fallback bodies. Package enablement and stale guidance never
expand the current assignment.

Before claiming that an existing workspace has migrated to consumer-only
guidance, obtain authority for this workspace configuration change and use its
normal writable-workspace and review flow. This is not permission to change
installed package code, update services or begin source maintenance.

1. Inspect `.github/copilot/context-projections.json`. Select only entries with
   `plugin: copilot-extensions-harness@copilot-extensions` and these source IDs:
   - `contribution-boundary`
   - `cross-repo-debug-tracking`
   - `commented-review-verdict`
   - `validate-and-promote-triage`
2. Inspect each selected entry's destination and fallback. Confirm their
   managed markers match that entry's owner and source ID. Preserve foreign,
   unmarked or locally changed policy; resolve any ownership/content conflict
   with the workspace owner rather than deleting it.
3. Retire only those verified selectors, fallback bodies and lock entries.
   The former selectors are
   `.github/instructions/copilot-extensions-harness/<source-id>.instructions.md`;
   fallback bodies are
   `.github/copilot/context-fallbacks/copilot-extensions-harness/<source-id>.md`.
   Preserve unrelated entries and the new `consumer-boundary` entry.
   Keep the lock's schema/version and deterministic serialization: sorted
   object keys, projection entries sorted by destination, two-space indentation,
   ASCII JSON escapes and a final LF. The projection owner rejects other lock
   formatting; the contained rehearsal uses its existing canonical serializer.
4. Ask the workspace's current projection owner to regenerate its managed
   instruction namespace and retire copies for the same four source IDs.
   Do not hand-edit an owner-managed cache. If that owner has no supported
   retirement path, report the gap and leave migration pending; do not claim
   that a package update or ordinary sync removed those copies.
5. Use the normal projection sync for the current consumer declaration.
   Check that the four retired selectors, fallback bodies, lock entries and
   managed copies no longer apply, and that `consumer-boundary` is present.
   Review the configuration diff, then restart the affected session so old
   loaded guidance is not mistaken for the new configuration.

Do not enable `copilot-extensions-maintainer` as a migration workaround.
Ongoing maintenance remains an explicit opt-in and assignment. This procedure
is a source-level migration guide, not an automatic updater or rollout claim.
