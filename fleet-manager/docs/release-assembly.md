# Explicit artifact-to-release assembly

Back to [fleet-manager](../README.md).

`fleet_manager.release_assembly.assemble_release_manifest(components, sources,
*, release_id, roles)` produces the existing canonical schema-v1
`fleet-manager.release` bytes from actual, explicitly supplied local artifacts.
It is the unprivileged producer boundary for the existing role/release contract,
not another installer, publisher, registry or signing implementation.

`components` maps declared component IDs to exactly `kind`, `api` and
`data_schemas`, with the same meanings and limits as the existing release
component contract. Do not supply `artifact_sha256`: assembly computes every
component digest from the existing bounded, ordinary-file reader.
`sources` is the existing `fleet-manager.artifact-sources` mapping and must
supply exactly those components. `roles` explicitly supplies the full client,
Gateway and node closures; no default role, dependency resolution, provider
selection or fallback is invented. A client closure cannot contain a control
or executor component.

```python
manifest_bytes = assemble_release_manifest(
    component_contracts,
    artifact_sources,
    release_id="release-r1",
    roles={
        "client": ["cli"],
        "gateway": ["cli", "gateway", "ssh"],
        "node": ["cli", "ssh"],
    },
)
```

The caller supplies contracts and artifacts from its normal selected build
mechanism. Gateway, native OpenSSH and optional public Bastion components
remain explicit declarations, not ambient binaries discovered from a host or
proof that a named capability works. The `fleet-manager` SDK already
participates in the ordinary first-party wheel build/release path; adding this
producer changes neither that publisher nor default client dependencies.
The existing Gateway clean-room source materializer explicitly includes this
SDK module in its closed-world package roster. That packaging declaration
does not make assembly part of Gateway startup or qualify a live service.

## Validation and byte boundary

All declarations and source paths are validated before artifact access. Inputs
are snapshotted; caller changes during hashing cannot change the selected
contracts, closures or source mapping. Canonical input bounds, exact schema
fields, component/API/schema types and role isolation reuse the existing
validators. Paths are bounded nonempty UTF-8 strings without NUL; relative paths
retain the existing current-working-directory meaning.

Every artifact must pass the existing link/reparse, regular-file, identity,
size, content-stamp and bounded-stream checks. The whole release, across all
unique components rather than one role at a time, is limited to 512 MiB;
each artifact remains limited to 64 MiB. Invalid input or a failed artifact
raises `PlanError` without returning a partial manifest or printing supplied
paths/content.

The immutable result uses the exact existing canonical encoding, with no
trailing newline. Component-map order does not change it; explicitly supplied
role-list order remains preserved. The producer writes no files, installs no
package, starts no service and makes no network or credential call.

## Publication, authentication and consumption remain separate

These are hashes of the actual streams read, not retained file ownership or
authentication. A file may change after assembly. Consumers must freshly
authenticate the same selected manifest and reverify/retain its actual artifact
bytes through the existing admission/staging owner before construction.
The existing [artifact preflight](artifact-preflight.md) consumes the emitted
schema without an adapter; its integrity result is not publication or activation.

The approved ordinary Python publisher uses its existing hosted attestation
mechanism. The Fleet [release-authentication API](release-authentication.md)
currently implements pinned-key verification and explicitly refuses GitHub
attestation. This producer does not create another proof, silently select a
different method or enroll keys to bridge that interoperability gap. An
approved compatible publication/consumer seam is required before privileged
publication or effectful setup wiring.

Existing release selection, current trust, durable sequence/conflict state,
role/platform closure, installation ownership, health, update/rollback and
activation stay with their existing owners. Gateway/Vault modules, Chat and
agent-ssh registry/profile integration do not move into this producer.

Focused tests bind real file bytes, complete role closures, metadata refusal,
aggregate limits, input snapshots and changed-artifact rejection. A separate
installed first-touch case builds the actual SDK wheel, removes copied source
and canonical-library inputs, installs into a fresh offline target and runs the
producer plus the existing artifact CLI with site packages disabled. It also
checks linked-input refusal, changed bytes and state-preserving package removal.
Synthetic SSH/Bastion fixture bytes are not real SSH/Bastion runtime proof;
this does not demonstrate a live Gateway, signing, deployment or role lifecycle.
