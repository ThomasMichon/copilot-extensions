# Inert attention foundation

Back to the [PR plugin](../README.md). The
[canonical perspective plan](../../../efforts/active/durable-pr-watch-delegation/author-reviewer-perspectives.md)
owns the broader campaign.

`agent_pull_requests.attention` supplies pure typed projections over already
admitted, normalized evidence. It does not fetch forge data, store events,
authenticate callers, perform policy checks, emit Dispatch tasks or publish
feedback. No CLI/HTTP route or standalone-host adoption is added by this module.

The existing observation/selector owner supplies a full provider/endpoint/
repository/PR/account/namespace key, exact head/base, epoch/sequence, observation
time, pending-since time, completeness and current policy recommendation flags.
`Interest` carries a consumer, role and tri-state selection result. Codeowner
matching remains with the selector; unknown paths cannot be converted to a
positive or negative match here. A provider string is identity, not proof of
that driver's qualification.

`project` requires the caller's current time and explicit freshness budget.
It distinguishes initial review, follow-up, rebase, CI re-kick, merge nudge and
final-iteration recommendations from excluded, unavailable, draft, handled and
terminal states. Recommendations carry no mutation permission.

Supply `Handled` only from the current owner's confirmed feedback baseline.
Progress belongs to the exact PR/account/namespace/consumer/role. Matching
candidate/cursor suppresses repeated work, but a new same-head discussion cursor
or new observation epoch remains actionable. Feedback ahead of observation, or
a candidate change under an unchanged cursor, is unavailable rather than an
invented clean baseline. The observation owner must deduplicate self-echoes and
poll nonevents before allocating a meaningful cursor; this module does not
manufacture those guarantees from caller numbers.

`rank` orders at most 4,096 unique PR interests, excludes non-actionable or
expired projections, applies the caller's explicit aging interval and uses a
stable scope-qualified tie break. Pending-since is meaningful unhandled work
time supplied by the existing owner, not the most recent polling time. The
owner records only the feedback's observed candidate/cursor; it must not move
the baseline over newer concurrent events.

This source foundation neither tombstones nor deletes records. Durable
journal/cursor semantics, bounded retention, provider normalization, authenticated
service/CLI adoption, task-lineage integration and actual shadow comparison
remain separate open work in the canonical effort.
