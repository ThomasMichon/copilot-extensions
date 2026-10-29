# PR Review Workspaces - Inception

Parent contract: [PR Review Workspaces](README.md).

This preserves the operator's design inputs verbatim and the preceding
assistant design response. It contains no deployment-specific investigation
or private execution evidence. Recommended mechanisms are not operator grants.

## Operator - Reviewer-owned data and analysis

> Right: one agent should ideally be able to read the data *and* do the analysis: we probably need a way to "dump" a PR to a local representation, and then have a tool to incrementally pull from upstream and sync local. If we still have the ADO/reviewer agent split, the ADO agent can just be asked "update the feed", and then later "here are the comments a verdict to post". Ideally, though, we do away with the ADO agent and use pr-* tools to pull off the "load the PR initial state, then load/update incrementally" strategy, so the reviewer can use the tool, make the review, and post the comments, all on its own.

## Assistant - Initial direction

Agreed. **The reviewer should own the review; tools should own transport,
synchronization and safe submission.**

The contract should be:

1. **Load:** materialize a local PR workspace containing description,
   discussions, votes, policies, exact source range and changed-file
   manifest - with explicit freshness and completeness.
2. **Sync:** update changed sections and identify new heads, iterations,
   replies or votes. Use bounded section refresh where upstream lacks
   incremental APIs.
3. **Review:** analyze that local representation and exact-head source without
   another agent translating the evidence.
4. **Submit:** post selected comments/verdict through a tool that rechecks
   required conditions, preserves completion settings, and returns verified
   receipts or an explicit unknown outcome - never blind retries.

We should extend the existing native snapshot/thread capabilities, not build
another adapter or ledger. The missing pieces are structured ADO range/manifest
support, coherent incremental refresh, and integrated review submission.

The interim ADO agent can expose those same operations - **"update the feed"
and "submit this review"** - then disappear from the loop once `pr-*` supports
them directly. The independent finding challenge remains separate where required.

## Operator - Design request

> Great. Help design this out.

## Scope selection

The assistant asked:

> Which scope should the first usable slice target? I recommend proving one complete ADO load -> sync -> review -> submit cycle, while keeping the contract provider-neutral and existing GitHub behavior intact.

The operator selected:

> ado-first

The assistant's resulting design is preserved in the
[architecture proposal](architecture.md), including logical workspace sections,
initial load, incremental invalidation, explicit submission and recovery,
reviewer responsibilities, staged delivery and acceptance cases. That proposal
is agent-recommended; it is not a transcript of additional operator instructions.

## Operator - Successor plugin and service cache

> Come to think of it this might be better added to the successor agent-pull-requests plugin/toolset. That plugin also brings a service, which could host a caching DB, too

The assistant agreed that the successor plugin is the PR-domain owner and its
service is a natural sharing point, while separating rebuildable cached
observations from durable submission receipts. This owner/service direction
supersedes any implication that a second Worktrees-only workspace engine or
new daemon is required.

## Operator - Capture the remaining build-out

> But do otherwise capture the rest in an effort for vision and architecture build-out.

## Effort selection

The assistant proposed `pr-review-workspaces`, planning-only, with either a
private capture or a canonical effort in the plugin's source repository.
The operator selected:

> slug=pr-review-workspaces, home=plugin-source

This selects canonical source planning/publication, not runtime implementation
or service rollout. The normal source review and contribution gates remain.
