# Retained authenticated artifact staging

`fleet_manager.artifact_staging.stage_authenticated_artifacts(...)` is a
context manager for selected release bytes, not an installer or an activation
API. It consumes the existing canonical release manifest, explicit proof and
trusted policy, and the existing `fleet-manager.artifact-sources` mapping.
Client, Gateway and node assembly can share this byte boundary without sharing
hosting authority. A client consumer must still select only the client role.

```python
with stage_authenticated_artifacts(
    manifest_bytes, proof, trusted_policy, artifact_sources,
    authority=authority_id, installation=installation_id, role="client",
    staging_parent=private_stage_directory,
) as staged:
    current = staged.reverify(current_trusted_policy)
    for component in staged.artifacts:
        content = staged.read_artifact(component, current_trusted_policy)
        # A separate owner admits construction from these immutable bytes.
```

The caller supplies a protected, caller-controlled staging parent and current
authenticated local trust configuration. Paths must be absolute, ordinary
directories with no linked/reparse ancestors. This is a local assembly API;
do not expose its filesystem paths or clock callbacks as a remote command
surface. The default clock reads integer epoch seconds from the local clock;
an explicit clock callback is a trusted caller/test seam, never proof-supplied
historical time.

## Admission and lifetime

Before creating a stage, the API authenticates exact immutable canonical
manifest bytes and performs actual role-selected integrity preflight. Missing
selected cryptographic support or unimplemented GitHub verification refuses;
there is no unsigned, RECORD-only or alternative-method fallback.
The installation-scoped policy is supplied explicitly; staging neither enrolls
keys nor discovers credentials.

The installed SDK's own runtime resource must pass its exact version and
SHA-256 RECORD admission. The primitive is loaded from those verified byte
snapshots, not from candidate artifacts, another plugin, a checkout or PATH.
Unit fixtures may inject the canonical primitive to isolate this packaging
boundary; real installed-wheel tests exercise the unmodified loader separately.

The stage reserves a unique private spool and uses the unchanged canonical
`versioned-runtime` construction, slot and retention primitives. It copies only
selected artifacts, preserves their filenames inside distinct component
directories, checks bounded ordinary-file identity/content and re-verifies the
frozen bytes before yielding. Retention is acquired before construction
ownership is released. A mutable `dev` identity is unsupported.

The spool is not an installation root. No interpreter, completion marker,
current/last-known-good selector, binstub, daemon or service is created.
Do not build, activate or run a runtime inside it. A consuming installer uses
its own separately admitted, durable candidate and lifecycle.

The context exposes selected component names and scoped release identity, not
reusable artifact or slot paths. `read_artifact(component, current_policy)`
reads a bounded ordinary-file snapshot through a held descriptor, re-verifies
current policy and staged integrity, and compares that snapshot with the
authenticated component hash and size before returning immutable `bytes`.
Replacing a path after verification cannot substitute what the consumer reads;
each new read independently authenticates its own snapshot. At most one
64 MiB component is materialized per call. Returned bytes remain an immutable
snapshot, not a lifetime, freshness or execution grant.
`artifact_filenames` exposes a fresh mapping of portable component filenames,
not their stage paths. `read_manifest(current_policy)` returns the exact
immutable manifest bytes only after fresh in-context authentication. Neither
surface authorizes construction; the separate lifecycle owner still admits
its own installation, selected release and compatibility.
`reverify(current_policy)` re-reads the
frozen manifest, authenticates it under the supplied current policy and clock,
and hashes selected frozen artifacts again. It does not trust a saved report,
original mutable source, or the stage's first verification. Changed bytes,
expired proof/policy, revoked keys, raised sequence floors and lost ownership
refuse. There is no persistent high-water mark or rollback permission here.
Snapshot reads, re-verification and context shutdown share one operation lock.
Shutdown waits for an in-flight read, verification and report serialization before marking the
handle closed and releasing retention; a later verifier refuses. This gives
each operation a consistent in-context observation, not a retention grant
that survives context exit. A consuming assembly owner must keep the context
open throughout its separately admitted use and coordinate that lifetime.

On normal exit or interruption, retention is released, the handle is closed,
and cleanup binds traversal to the admitted directory identity, not a prior
path-only check. Windows keeps ancestor and entry handles open without delete
sharing and deletes through those handles. Windows stage and recursive child
admission use the same native 128-bit file
identity as deletion, rather than depending on Python's version-specific stat
file-index width.
POSIX atomically captures the named
root into a fresh owner-only quarantine, verifies the captured identity before
traversal, and removes entries only inside that exclusively owned namespace
using held descriptors and no-follow, directory-relative operations. It never
unlinks from the former published stage namespace: replacing a published file,
child or root between a final check and removal cannot substitute the deletion
target. A capture mismatch is not restored over another entry or deleted;
the captured entry and quarantine remain, and the error names the quarantine
for explicit owner investigation.

The caller must exclusively own the quarantine and stop its other stage
mutators before context exit. Owner-only permissions exclude other unprivileged
OS principals; this local API is not isolation from a hostile process running
as the same OS user or an administrator. Such a threat requires an isolated
cleanup owner, not another pathname check. Unsafe entry types, changed
ownership and unavailable native cleanup support refuse rather than falling
back to a path-based recursive deletion. Closed handles cannot be reused;
serialized reports cannot recreate retention or authorize artifact execution.
An abruptly terminated process may leave its uniquely named spool for explicit
owner investigation; no automatic sweep of unrelated directories is added.

## Evidence and remaining authority

Re-verification returns `fleet-manager.staged-artifacts`, version 1, with
`status: retained-byte-stage`, the existing release/manifest digest,
authority/installation/role, current policy/trust and signer identities,
sequence/lifetime, component hashes/sizes and current retention evidence.
Authenticity and actual artifact integrity are true only after the existing
verifier and preflight pass. `mutations_performed: true` identifies the private
spool creation/copy that this stage performed; re-verification itself is
read-only. Activation authorization and live readiness remain false.

This is not a bearer grant or completed install. Before any candidate code
execution, the consuming lifecycle must re-verify current policy/freshness and
the same owned bytes, enforce durable sequence/content conflict and supported
rollback policy, and separately admit its installation root, construction,
health and activation. Existing release/artifact provenance remains canonical;
no signing workflow, trust root, publisher or second installer is introduced.

Installed tests prove independent real Ed25519 verification, actual byte
copying, retained incomplete slots, interruption/descriptor cleanup, changed
policy/bytes refusal, source independence and preserved user configuration.
Their Python network-entry-point guard is not OS-level network isolation.
Managed client install/update/rollback/removal, Gateway/node assembly and
production signing/deployment remain separate owner gates under #6424/#6240.
