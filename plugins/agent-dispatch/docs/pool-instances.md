# Supervised pool-instance definitions

The registrar accepts an integer worker-pool `priority`, default `0`.
Read-only declaration summaries expose it. Higher priority precedes lower
priority in the pure `ordered_pool_instances` helper; ties use registration
identity, execution machine and environment. Each tuple is a distinct capacity
owner, even when multiple hosts use the same logical registration.

For an identified worker-lane machine/environment, declaration conversion
carries a top-level version-1 `pool_definition` with the full effective
permit/reject predicate and cap. Explicit filters retain their precedence over
repo/task-type shorthand. Workspace and native identities are not pool identity.
An unidentified scope has no definition; the definition reader refuses it rather
than manufacturing a `local` instance. A legacy row's labels do not prove its
missing predicate.

`pool_instances.py` owns validated JSON round-trip, scope/cap consistency and
deterministic ordering. Readers tolerate additive fields at version 1 but
refuse missing semantics, invalid types, unknown filter dimensions and
unsupported versions. The ordered effective set refuses duplicate instance
identities; it does not reconcile raw declaration sources or count live slots.

**This is definition support only.** Metadata stays outside the legacy lane's
launch spec and does not alter its argv or restart fingerprint. The current
declaration publisher does not transmit this top-level definition to the
coordinator. Complete effective-set publication, provider capacity accounting,
atomic task placement and runtime capability negotiation remain pending.
A definition's version is not an executing-runtime support advertisement.
Old reservation/claim and interactive/suspended paths are unchanged.

The [catalog provider and bounded preparation](pool-publication.md) add
control-authorized metadata transport, fenced scope replacement, registry-input
invalidation and local source/request custody. They have no production
publication sender and do not establish execution or complete configured-scope
admission.

The [canonical assignment/startup amendment](../../../efforts/active/agent-dispatch-monitor-and-confirmed-state/assignment-startup-amendment.md#selected-pool-scope-and-ordering)
owns the selected policy and remaining gates.
See [current-versus-target architecture](spawn-supervisor.md#assignment-lifecycle-current-versus-target)
for the allocation boundary.
