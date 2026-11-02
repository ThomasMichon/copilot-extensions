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

## Direct registrations

A direct supervised-lane registration can retain the same complete version-1
object in `spec.pool_definition`. It must supply an explicit registration `id`
and an identified `machine`/`env`; the definition's identity and concurrency cap
must agree with that actual registration. Missing priority or either filter
side, unsupported versions and conflicting scope/capacity are refused before
an insert or upsert. Other registration kinds cannot carry a pool definition.
The existing registration spec JSON is the only persistence owner; there is no
second pool registry. Reopening the queue and listing/getting the registration
preserve the full object, including additive version-1 fields.
The existing `supervise register --spec <json-or-@file> --id <id> --machine
<machine> --env <env>` path accepts that explicit spec without new CLI syntax.

For example, the existing registration body can include:

```json
{
  "id": "review-worker",
  "kind": "supervised-lane",
  "machine": "host-a",
  "env": "default",
  "spec": {
    "repo": "example",
    "labels": ["review"],
    "max_concurrent": 2,
    "pool_definition": {
      "version": 1,
      "registration_id": "review-worker",
      "machine": "host-a",
      "env": "default",
      "priority": 10,
      "max_concurrent": 2,
      "filters": {
        "permit": {"repo": ["example"], "task-type": ["review"]},
        "reject": {"worktree": ["excluded"]}
      }
    }
  }
}
```

The running coordinator advertises
`pool_catalog_transport.registration_definition_versions: [1]` in `/health`.
For an explicit definition, `DispatchClient.register_registration()` requires
that support and posts the existing body to
`POST /registrations/pool-definition`. That route uses the same registration
writer and ordinary API authorization as `POST /registrations`, not catalog
control authority. The distinct route prevents a provider rollback after
discovery from silently accepting the request through an older permissive
writer. A missing capability refuses before POST; an absent route refuses
without retrying the legacy write. Neither case blocks legacy registrations
without `spec.pool_definition`, which keep the existing `/registrations` path.
An upgraded provider also validates explicit definitions received on that
legacy path. No old registration form is retired.

Collection reads either the declaration's top-level definition or the direct
row's stored definition, rejecting conflicting copies. Runtime-equivalent
declarations supersede explicit direct rows only when the complete normalized
pool policy also matches (apart from the registration ID). A differing priority
or permit/reject predicate keeps both capacity owners instead of dropping a
policy. Legacy rows without definitions retain existing declaration precedence;
if still effective on their own, they withhold the entire pool snapshot rather
than acquiring guessed filters. Pause/resume, concrete/logical overrides and
confirmed declaration withdrawal retain their existing owners.

**This is definition support, not ordinary allocation activation.** Pool metadata
does not alter legacy launch argv or restart fingerprints; a metadata-only
update still advances the coordinator registration revision and invalidates
catalog inputs. The declaration publisher continues to transmit its legacy spec,
not the top-level definition. A definition's version is not an executing-runtime
support advertisement. Ordinary allocation, initial prompts and mixed-driver
qualification remain separate; old reservation/claim and interactive/suspended
paths are unchanged.

The [catalog provider and bounded preparation](pool-publication.md) add
control-authorized metadata transport, fenced scope replacement, registry-input
invalidation and local source/request custody. Its
[default-off existing-owner cadence](pool-publication-cadence.md) does not
establish ordinary execution or complete configured-scope admission.

The [canonical assignment/startup amendment](../../../efforts/active/agent-dispatch-monitor-and-confirmed-state/assignment-startup-amendment.md#selected-pool-scope-and-ordering)
owns the selected policy and remaining gates.
See [current-versus-target architecture](spawn-supervisor.md#assignment-lifecycle-current-versus-target)
for the allocation boundary.
