# Backlog Reconciliation - Additional Operator Direction

Parent: [Backlog Reconciliation](README.md).

## 2026-11-03 - Weighted thematic graph and completed-work matching

The semantic service's private name is redacted; the surrounding request is
preserved verbatim.

> With still 1000+ issues open, we need to start aggressively theme-aligning remaining issues, perhaps leveraging [the semantic index] or just its semantic indexer. We should make a big graph of issues, and use weighted-density-type analysis to identify common themes then link chunks of issues under efforts with those bugs listed. We can continue to scrub for duplicates or already-resolved issues, but perhaps it's time to "get serious".
>
> It's might also be worth doing a [semantic-index] dedupe between issue descriptions, and descriptions of completed PRs and commits.

This changes research selection and aggregation, not author eligibility,
existing holds, implementation/deployment authority, source ownership,
review gates or the fixed whole-queue checkpoint. The
[execution method](semantic-density-consolidation.md) separates requested
outcomes from agent-recommended algorithms.

## 2026-11-05 - Canonical cohort bugs and parallel research

> My intent at this point is that we research clusters of "related" issues, organize the thoughts into cohort-size bugs, then resolve the individual bugs against the main parent, which should now encompass the planned work.

The operator selected #8689 as an insufficiently evidenced report, #8398 as
a recovered CI incident whose signature/history should remain available,
#6375 and #841 as probable fixes, #1193 and #2256 as already done, and #951
as in progress or already done. These are individual triage directions, not
permission to claim every example fixed without reading the actual outcome.

> Honestly you can use API queries to grab the whole list (avoid throttling), splat everything to markdown files with context and metadata, then sick a fleet of Luna agents on doing the research against them all in parallel, using [the semantic index] to find matching close-out PRs and commits, or scrutizing bug quality.

The research engine therefore uses one rate-aware collector and independent
Luna workers, with coordinator-owned integration and tracker effects. The
principal output is actual canonical parent work and individual dispositions,
not more leaf documentation. Historical campaign retention prose is not
independent authority to add acceptance gates to an original finite defect.
Actual source/owner holds, author eligibility, resource custody, normal review
and the whole-queue numerical checkpoint remain unchanged.
