# Explicit selected-release intent

`fleet_manager.release_selection.ReleaseSelection` retains a supplied release
decision as immutable canonical bytes. It does not choose a release, contact a
decision owner, authenticate that owner, verify signatures, persist admission,
construct a runtime or grant activation. A signed release or higher sequence
is not automatically selected.

The existing [client lifecycle admission plan](../../efforts/active/linux-role-fleet/client-lifecycle-admission.md)
requires this input before candidate execution. Product release decisions remain
with their trusted decision owner; installation state and admission remain
separate consumer responsibilities.

## Shape and binding

The strict version-1 `fleet-manager.release-selection` document contains:

- `owner`, `decision`, `authority` and `installation`: existing bounded logical
  identifiers, not credential values, filesystem paths or proof of ownership.
- `revision`: a positive integer naming the supplied decision revision.
- `role`: exactly `client`, `gateway` or `node`.
- `release_digest`: lowercase SHA-256 of the exact canonical release manifest.
- `sequence`: the positive release sequence expected from fresh authentication.
- `components`: the nonempty, sorted, unique component IDs in that role's full
  declared closure.

Existing bounded JSON, identifier and integer limits apply. Missing or extra
fields, duplicate JSON keys, noncanonical encoded input, mutable byte buffers,
invalid roles and unsorted or duplicate components refuse. Supplied intent is
not silently normalized. `from_dict(...)` snapshots a validated document;
`ReleaseSelection(encoded)` requires already-canonical immutable `bytes`.
`to_dict()` returns an independent copy.

`selection_digest` hashes the whole canonical intent, including its owner,
decision/revision, scope and selected content. This is content correlation, not
an authenticated decision signature or durable admission receipt.

## Explicit consumer check

```python
selected.check_selected(
    current_selected_intent, canonical_manifest_bytes,
    authority=authority_id, installation=installation_id,
    role=selected_role, sequence=freshly_authenticated_sequence,
)
```

`current_selected_intent` must be a separately obtained `ReleaseSelection` from
the consumer's independently trusted current decision owner. Do not construct
both arguments from candidate-controlled input or use a stale cached record as
evidence that the owner still selects it. The caller establishes provenance,
currentness and authorization and coordinates that observation with its
installation ownership and admission transaction. This data API has no owner
resolver, clock-based freshness authority or atomic read of external policy.

The check requires byte-identical current intent, explicit matching authority,
installation and role, the exact freshly authenticated sequence, a valid
canonical manifest with the selected digest, and the complete selected role
closure. A newer decision revision, changed owner, different manifest at the
same display release ID, higher unselected sequence, partial closure or widened
closure fails. Changing an unselected manifest component still changes the
whole-manifest binding. No alternative release or fallback role is chosen.
Success returns `None`; there is no report that can be replayed as an
authorization grant.

The caller must freshly authenticate the manifest and sequence under current
trust policy and consume authenticated immutable artifact bytes through the
[retained stage](artifact-staging.md). Selection correlation does not replace
signature verification, artifact integrity, compatibility, exclusive
construction/activation ownership or health. It also does not enforce durable
anti-rollback state. The installation owner must retain the accepted sequence,
manifest and selection binding, refuse content conflicts and unknown commit
outcomes, and preserve its active healthy runtime on later failure. Existing
active-runtime recovery is not permission to admit an older new candidate.

The module performs no filesystem, network, provider, CLI or credential access
and adds no default dependency. Installed isolated consumer tests cover all
three role closures with explicit bytes, deny Python socket entry points and
leave their working directory empty. These are data-contract proofs, not
OS-level network isolation, a complete installer, authenticated decision-owner
transport or live readiness.
