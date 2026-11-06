# Original issue 2475 — description and discussion

[Back to cohort overview](knowledge-pair-receiving-cohort.md) · [Campaign README](README.md)

<!-- ADMITTED_PACKET_BYTES_BEGIN -->
Source: https://github.com/ThomasMichon/copilot-extensions/issues/2475

Historical record copy of the admitted description and complete discussion. Qualification metadata is omitted; source control bytes other than tabs/newlines are displayed as literal \xNN escapes, not repaired commands. Historical OPEN, intake and receipt wording does not countermand a later explicit adoption or supply new authority. Original issue content and source/resource custody are unchanged.

## Original description

## Summary

`agent-worktrees effort-focus bind` fails to resolve an effort README path when the effort lives in a paired external state-root worktree instead of the current checkout. In a stateless harness + paired knowledge-repo topology, that makes the documented effort-focus binding flow unusable from the harness worktree.

## Repro

1. Start in a stateless harness worktree paired with an external state-root / knowledge worktree (`agent-worktrees state-root --pair --json` reports `paired: true`).
2. Create an effort at `<paired-state-root>/efforts/active/<repo>/<slug>/README.md`.
3. From the harness worktree, run:
   ```
   agent-worktrees effort-focus bind efforts/active/<repo>/<slug>/README.md --participant "<p>" --slice "<s>"
   ```
4. Observe `{"error":"effort path does not exist"}` even though the path exists under the resolved paired state root.
5. Retrying from the paired state-root checkout with `--worktree-id <harness-worktree-id>` fails differently: it looks for the harness worktree tracking record under the state-root repo's tracking store instead of the harness repo's tracking store.

## Expected behavior

- Relative effort README paths should resolve against the resolved state root when the current repo is stateless / requires an external state root.
- Worktree-id / tracking-file lookup should use the tracking store of the worktree being bound, not the repo the command happens to be invoked from.

## Impact

This breaks the documented `effort-focus bind` flow for the primary stateless-harness + paired-knowledge topology. The current workaround is to skip focus binding and manage the effort only through its README, which loses worktree-level focus / handoff integration.

## Platform

- Windows
- Copilot CLI worktree-based plugin environment

## Complete discussion

### Comment 5662369317

Hit this exact failure mode today (2026-09-14) in the primary stateless-harness + paired-knowledge topology this issue describes -- confirms it's still live.

`effort-focus bind <path>` from the harness worktree failed with `{"error":"effort path does not exist"}` even though the effort README existed at the resolved paired state-root path. Working around it required `-p <knowledge-project> effort-focus bind <path> --worktree-id <knowledge-worktree-id>` (i.e. invoking the command as if operating directly on the knowledge project/worktree, bypassing the harness worktree's own resolution entirely) -- matching this issue's step 5 exactly.

Adding as a fresh data point plus one adjacent friction I hit while working around it: once the repo/worktree resolution issue was worked around, the effort-focus validation errors themselves (separate from this issue's scope, so not trying to conflate) were opaque about *what* was missing -- `{"error":"effort slug is required"}`, `{"error":"effort README is missing Plan or Validation Plan"}`, and `{"error":"participant is not declared in the effort participant/coordination section"}` each required trial-and-error against the `assets/TEMPLATE.md` schema to resolve (a plain `**Slug:**` line vs. the required `- **Slug:**` bulleted form, missing `## Plan`/`## Validation Plan` headings, missing a `## Participants` table row matching the declared `--participant` string verbatim). Filing that piece separately if it isn't already tracked.\x0d


### Comment 6314479598

*The following contribution was assisted using Copilot.*

Retain for the unfixed cross-project target-record authority half, while crediting the paired effort-root correction. #2725 resolves bind/show/release README paths through the paired knowledge sibling for stateless repositories and explicitly documents that a different-repository --worktree-id still fails lookup. Current cmd_effort_focus and _resolve_worktree_id still choose cfg.tracking_dir() from the invoking project, not a target-qualified record store. Complete both original cases: resolve the README through the target worktree's authoritative paired state source, and locate/write the binding and duplicate-binding registry under that target's owning tracking authority from either cwd. Preserve canonical project/identity, ambiguity refusal, containment/reparse guards and state-root failure diagnostics. Binding a different knowledge worktree or using an absolute path is not equivalent to binding the intended harness worktree. #2631's validation-message work is a separate adjacent scope.

Evidence references:
- https://github.com/ThomasMichon/copilot-extensions/pull/2725 - provider-confirmed MERGED 2026-09-15T21:14:56Z; merge commit 83d9f0ab7b7f9d699ac862357a976a34fdf07b2a; PR explicitly says cross-repository tracking-file lookup remains unfixed.
- https://github.com/ThomasMichon/copilot-extensions/blob/9d612c2d05f1401dfd94e9eb40165bdb8d80e9f4/plugins/agent-worktrees/src/agent_worktrees/session_metadata_cli.py#L432-L454 - existing paired effort-storage-root resolver.
- https://github.com/ThomasMichon/copilot-extensions/blob/9d612c2d05f1401dfd94e9eb40165bdb8d80e9f4/plugins/agent-worktrees/src/agent_worktrees/session_metadata_cli.py#L497-L531 - target record lookup still uses cfg.tracking_dir() after ID resolution.
- https://github.com/ThomasMichon/copilot-extensions/blob/9d612c2d05f1401dfd94e9eb40165bdb8d80e9f4/plugins/agent-worktrees/src/agent_worktrees/worktree_identity.py#L271-L305 - default ID resolution remains within the current project's tracking directory.
- https://github.com/ThomasMichon/copilot-extensions/blob/9d612c2d05f1401dfd94e9eb40165bdb8d80e9f4/plugins/agent-worktrees/src/agent_worktrees/effort_focus.py#L192-L220 - README containment and link/reparse rejection remain relevant.
- https://github.com/ThomasMichon/copilot-extensions/issues/2475#issuecomment-5662369317 - both topology failure and separate schema-message friction are preserved distinctly.

[Inspected released source](https://github.com/ThomasMichon/copilot-extensions/tree/0b30012da7c80d102384a50fdd4941bb8577b47c). Reconciliation: #7556.

<!-- backlog-reconciliation:7556:batch-16:issue-2475 -->

### Comment 6356668692

*The following contribution was assisted using Copilot.*

Retain the remaining normal cross-project effort-binding authority gap. Actual2725/current source resolves a stateless harness README via its paired knowledge sibling, but target-ID resolution and record construction still use the invoking project's tracking directory. Binding another knowledge worktree or supplying an absolute README does not bind the intended harness record. Preserve bind/show/release and duplicate-binding bookkeeping under the actual target's owning authority, canonical identity/ambiguity refusal and containment/reparse/failure diagnostics.

Concrete receiver7097 owns report-first repair of misplaced existing records, not normal target-qualified binding;1419 owns pair allocation/retrofit, not this already-paired command's binding transaction. Neither currently accepts this exact remaining scope. Keep schema-message friction separate and do not manufacture a generic state repair or silently relocate records. No binding, doctor, config, record or resource action was attempted.

Evidence references:
- Fresh complete paginated collaborator-roster verification admitted the original author before body access. Eligible comments were filtered before rendering; metadata and membership are rechecked before publication. Nonmember-origin issues remain untouched and count in the whole-queue census.
- https://github.com/ThomasMichon/copilot-extensions/issues/2475 : full original, both member comments, timeline and no-edit history; both CWD/topology failure cases and adjacent-only schema friction preserved. OPEN/unlocked/unassigned.
- https://github.com/ThomasMichon/copilot-extensions/pull/2725 : FULL actual MERGED83d9f0ab7b7f9d699ac862357a976a34fdf07b2a; paired storage-root correction explicitly leaves different-repo --worktree-id tracking lookup unfixed.
- https://github.com/ThomasMichon/copilot-extensions/blob/69bdc07d996d2720eaf89333cde1d97e014cd139/plugins/agent-worktrees/src/agent_worktrees/session_metadata_cli.py#L432-L559 : current paired storage-root helper plus actual cmd_effort_focus still deriving yaml_path from cfg.tracking_dir after target ID resolution.
- https://github.com/ThomasMichon/copilot-extensions/blob/69bdc07d996d2720eaf89333cde1d97e014cd139/plugins/agent-worktrees/src/agent_worktrees/worktree_identity.py#L284-L305 : default exact/suffix resolution remains invoking-project scoped unless an explicit project is supplied; caller does not supply that target authority.
- https://github.com/ThomasMichon/copilot-extensions/blob/69bdc07d996d2720eaf89333cde1d97e014cd139/plugins/agent-worktrees/src/agent_worktrees/effort_focus.py#L192-L220 : current containment/link/reparse safety must survive correct root selection.
- https://github.com/ThomasMichon/copilot-extensions/issues/7097 : full fresh eligible OPEN source/body/member disposition read as necessary receiver comparison; exact-record duplicate/orphan quarantine/repair scope is not normal effort binding.
- https://github.com/ThomasMichon/copilot-extensions/issues/1419 : full eligible OPEN body/member refinements read; late-binding pair retrofit and overlay recomposition do not establish target-qualified effort binding.

[Inspected released source](https://github.com/ThomasMichon/copilot-extensions/tree/9489a0fe8e4284538c3e00d5aaad2ac0cf474f75). Reconciliation: #7556.

<!-- backlog-reconciliation:7556:batch-73:issue-2475 -->

<!-- ADMITTED_PACKET_BYTES_END -->

[Back to cohort overview](knowledge-pair-receiving-cohort.md) · [Campaign README](README.md)
