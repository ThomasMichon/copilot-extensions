# Existing-owner native workspace host

Back to the [canonical effort](README.md). This is an **agent-recommended,
review-first source proposal**, not deployment authority or completed P1/P2/P3.
Existing workspace #9535, provider #7051 and source/lifecycle owners retain
their scope. Resolve concrete overlap through normal source coordination.

## Finite outcome

An explicitly configured existing standalone PR host can serve read-only ADO
workspace load/sync, materialization and conversation events through its
existing authenticated Gateway route, using the existing native review-evidence
exporter and already authorized typed credential owner. Default watch-only
hosting remains unchanged. This adds neither a forge HTTP client nor another
watcher, listener, database, credential issuer or execution task queue.

This closes a prerequisite of the existing
[Author/Reviewer plan](author-reviewer-perspectives.md), composing with the
[service contract](pr-service-contract.md) and
[workspace integration](../pr-review-workspaces/service-client-integration-slice.md).
It does not revise standing intent or take over their remaining acceptance.

## Actual source gap

`WatchService` already accepts optional trusted workspace operations, but the
standalone `HostConfig`/foreground entry does not construct them. Workspace
reads currently require a callable protected `OwnerReadHandler`; static JSON
cannot manufacture that owner's current context. The
[native exporter](../../../plugins/agent-worktrees/docs/pr-review-evidence.md)
separately implements versioned finite acquisition and actual effective-principal
checks using renewable observe credentials. It is a distinct acquisition
profile, not an owner-executed callback to relabel. Thin watch snapshots, even
with exact `base_sha`, do not supply equivalent complete conversation or
principal evidence.

## Proposed source contract

- Add an optional native read-owner adapter to the existing workspace owner.
  Its trusted embedding selects an absolute immutable command prefix,
  explicit provider endpoint/repository, canonical project/repository IDs,
  expected provider principal and existing credential-command reference.
  Remote requests cannot supply executables, credentials, endpoint/account,
  namespace, state roots or a different binding.
- Discover the exact executing native exporter's existing
  `agent-worktrees.review-evidence-capabilities` version-1 contract before
  issuing its read form. Missing/unsupported acquisition is explicitly
  unavailable for this optional workspace feature, never ambient-auth fallback.
  Do not mistake a newer payload or package version for executing support.
- Reuse the canonical finite owned-process primitive, full-section generation
  normalizer, existing cache CAS and journal transaction. Native actual
  principal and canonical target evidence must match the admitted workspace
  scope. Preserve the native acquisition profile, partial sections, timing and
  error semantics; do not forge protected-owner provenance.
- Carry the request/service checkpoint and remaining deadline across discovery,
  each native operation, result decoding, cache publication and journal commit.
  Withdrawal signals cancellation and prevents subsequent work; actual in-flight
  process/disk settlement retains the current service writer and drain custody.
  Timeout is not proof of no local commit or completed child cleanup.
- Extend the standalone host's strict trusted configuration to opt into these
  read-only bindings on the already admitted caller/target route. No submission,
  auto-merge, feedback/handled write, Dispatch emission or autonomous discovery
  is enabled. Unknown configuration refuses rather than broadening a binding.
- Explicit fresh provisioning may select the required existing owner layout
  and cache once. Serving never provisions, adopts or downgrades a volume.
  Existing-volume migration must use the current storage/lifecycle owner and
  exclude incompatible readers; a configuration label is not a migration grant.
  Preserve old watch-only volumes and replay identities without rewriting them.
- Keep cache publication and journal commits separately reconcilable. This
  slice does not close global terminal body/outbox/intent retention, candidate
  eligibility, fair durable handled state or a full current-body conversation
  reader.

## Compatibility and adoption

Follow [service contract evolution](../../../docs/patterns/service-contract-evolution.md):
expand the optional executing read owner and host first. Retain the prior
workspace-v1 baseline and conversation-v1 reader semantics and default disabled
embedding. Advertise the actual selected acquisition profile separately.
Existing clients continue baseline operations; missing optional support names
the gap without weakening strict CLI parsing or other owner operations.
Existing readers must refuse unsupported adopted disk layouts without data
loss. No legacy command is deprecated and no simultaneous upgrade is assumed.

Operational configuration, credential binding, provisioning, retained service
activation, installed clients and actual provider/shadow qualification remain
separately approved effects. Source availability is not enrollment or a grant.
The [existing lifecycle](watch-owner-cutover.md) still owns writer, connector and
drain; no installer, replacement protocol or resident cleanup loop is added.

## Validation and finite exit

Use focused contained controls through the actual native process boundary and
existing service/Gateway/CLI seams: supported/prior/missing capabilities;
canonical scope/principal mismatch; full/partial/oversized/malformed output;
same-head dialogue and target movement; cache conflict and uncertain journal
commit; expiry/withdrawal with actual drain; restart without lost replay or
unknown-effect identity. Prove no forge mutation or Dispatch/merge/rebase/CI
operation is reachable from this read-only configuration.

Exercise default watch-only config/volume unchanged and explicit fresh adopted
workspace storage, plus refusal of incompatible existing state. Keep synthetic
native output, actual exporter/credential transport and real provider tests
distinct; a supplied callback or fixture is not installed/live acceptance.
The finite source exit requires reviewed implementation and the affected
bounded packet, normal required CI, strict service typing where admitted, and
truthful remaining adoption gates. It cannot close the parent effort.
