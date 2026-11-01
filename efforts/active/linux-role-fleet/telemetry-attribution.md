# Keyed telemetry attribution continuation

Parent: [Linux role fleet](README.md#diagnostic-logging-audit-and-retention-amendment).
Existing owner: [logging sub-plan](logging-conventions.md), #8377.
Intent: [Portable Telemetry](../../../visions/telemetry/README.md#self-lookup-without-identity-disclosure).
**Status: proposal only; no API, configuration or deployment below is delivered.**

## Request and boundary

Public-safe settled request summary, not a verbatim private transcript:
use stable keyed pseudonyms for shared telemetry while allowing explicitly
controlled local/internal diagnostics to retain raw attribution. A user must
easily find their own observations using their own telemetry-purpose key without
uploading it or requiring matching operating-system login names. Central
aggregation may group opaque owners but must not resolve a real person without
that person's voluntary disclosure. Each user keeps a high-entropy key across
their approved machines; there is no tenant/global re-identification key or
central escrow.

The user selected the keyed family, not a particular hash protocol.
The concrete API, HMAC-SHA256 construction and sequence below are
**agent-recommended**, subject to coordinator interface acceptance before code.
This slice publishes intent and an implementable plan only. It generates no
keys, configures no credentials, starts no collector, and authorizes no export,
deployment, new authentication service or key-provisioning flow.

## Source-backed baseline

Read at source `6e00fc54326c23c43df683df536b3a590e3ca165`; these are
implementation constraints, not evidence of installed or catalog-wide coverage.

| Source owner | Observed contract and consequence |
|---|---|
| Bridge `src/agent_bridge/live_runtime.py::_log` | Rebuilds `EventLog(session_id=record.session_id, telemetry_source="represented")`; validates exactly `acp_session_id`, `worktree_id`, `source` in serialized reducer identity. Represented root IDs are native session IDs, not necessarily Bridge escrow IDs. Do not extend this frozen shape to add attribution. |
| Bridge `src/agent_bridge/events.py::EventLog` and `telemetry.py::SessionTraceReducer` | Construction, `from_db`, `from_captured_events`, later identity fill and rebuild preserve existing identities/replay meaning. Owned ACP and represented paths have different session namespaces. Enrich emitted diagnostics outside constructors, event storage and captured wire fixtures; do not relabel all `session_id` values as Bridge IDs. |
| Dispatch `src/agent_dispatch/telemetry.py::task_lifecycle_event` | Existing structural allowlist includes `owner`, routing targets, repo and exclusive key, but not `owner_session_id`. Its legacy privacy contract is not sufficient for shared export. Do not infer actor location from targets or treat the collector's ambient session as the task owner's. |
| Dispatch `client.py::claim/start`, `acquisition_receipts.py`, `script_worker.py::ScriptTaskRuntime` | Claim accepts worker/machine/worktree; start separately accepts native owner session. Acquired-start checks compare native conversation identity to the existing receipt. Script runtime remembers returned owner-session context, but cannot invent one for a service-only script. Existing receipt/task authority supplies facts; telemetry does not create authority. |
| `libs/process-telemetry/src/process_telemetry/emission.py::SinkSlot` | Per-consumer process-local sink slot, not ambient per-request caller context. Plugins import this as `SinkSlot`; no separate `SharedSinkSlot` class is needed. Keep legacy synchronous callback payloads and behavior unchanged unless explicitly opting into an adapter. |
| Shared `buffer.py`, `configured_spool.py`, `retained_spool.py`, `append.py` | Existing bounded worker, producer admission snapshots, epoch-ms default timestamps, owner cleanup and atomic append/retention are the extension seams. Accepted #8495/#8577 retained configuration is only `{"retained_spool":{"directory":"<absolute base>"}}`; unknown nested retained options are rejected, but top-level `attribution` is currently ignored. The buffered adapter and final retained sink stamp missing `ts` with precise milliseconds. Neither behavior establishes attribution or shared timestamp-policy support. |
| Worktrees `codename_tracking.py`, `providers/attribution.py`, `identity_marker.py` | Codenames are random persisted project-local aliases; AWI1 is encrypted recovery identity, not a hash-label scheme. Reuse opacity and user-custody concepts, never the public PR alias, recovery payload or PR recovery key as shared telemetry labels/secrets. Public PR correlation could identify authors. |

Paths in the first four rows are under their named `plugins/agent-{bridge,dispatch}/`
owners. No private inventories, native identifiers or raw captures are copied here.
The [measurement pattern](../../../docs/patterns/telemetry-qos-engagement.md)
already excludes arbitrary names/paths and treats IDs as correlation fields,
not metric dimensions. This proposal extends that boundary, not the closed
performance validator or the catalog's unimplemented producer roster.

## Recommended common contract

Extend the existing stdlib-only `process_telemetry` owner, not a new registry,
collector or event framework. Proposed Python names:

```python
IdentityRef(namespace: str, value: str)
AttributionContext(producer, subject, collector, availability)
make_attributed_sink(sink, *, context_for_event, mode,
                     policy=None, key_for_owner=None)
project_shared(event, context, *, key: bytes, policy) -> dict | None
derive_owner_token(key: bytes, *, collection_scope: str, version=1) -> str
```

These are proposed signatures, not executable examples or existing imports.
`producer`, `subject` and `collector` are closed role records:

- `producer`: registered component and actual emitting execution identity.
- `subject`: the observed user-owned work/session/task, separately from the
  process that reported it. Only known machine, worktree, native session and
  Bridge session references are populated.
- `collector`: the actual collecting service/process identity; its ambient
  session/user must never fill a missing subject field.
- `availability`: closed per-role/per-field states `known`, `unavailable` or
  `service_only`. Missing identity is not zero, a random replacement ID or
  proof of another actor. References use explicit authority namespaces; native
  and Bridge sessions remain separate even if their strings happen to match.

Role records have only declared identity slots (machine, process/instance,
runtime/generation, worktree, native session, Bridge session and, for task
correlation, task), not arbitrary mappings. Process/instance and generation
references use the owning producer's admitted identity, not a bare reusable PID;
mark unavailable when that owner cannot establish them. Distinct concurrent
daemon generations must remain distinguishable even when observing the same
subject session on the same machine.
The local context also names its consenting telemetry owner through an
owner-provided local handle. That handle is never serialized to shared output.
An owner token identifies the consenting key holder's telemetry collection,
not evidence that the holder personally performed every observed action.
Do not label other users' actions with that key as if they were the holder's.
Cross-owner actor details are omitted or the observation is refused unless a
separately reviewed multi-owner policy supplies correctly bound contexts/keys.

The existing owning producer/storage adapter supplies a bounded immutable
context snapshot appropriate to the observation. `context_for_event` reads only
that prepared, generation-scoped context; `key_for_owner` resolves the matching
already-admitted in-memory key, never a last-caller global. Owner canonical
machine resolution, user-policy admission and key access occur off the hot path.
Bound caches to the owner's current roster/generation and invalidate on
ownership/session changes; no per-event Git, subprocess, network, vault lookup,
full-history read or unconstrained process-global identity map.
If evidence cannot be bound to the observation, mark it unavailable.

`make_attributed_sink` explicitly composes an existing sink. `mode` is
`controlled_internal` or `shared`; legacy paths do not opt in automatically.
Internal enrichment may retain native IDs and an explicitly admitted canonical
user, but raw-user collection defaults off and requires controlled-internal
policy. Never discover/add raw usernames to an arbitrary configured external
factory. The shared branch always invokes `project_shared` before its destination
can serialize or spool anything. Compose with the existing worker; do not add a
second buffer/thread or insert enrichment into frozen Bridge constructors.
Capture event/ownership context before it can drift across a delayed drain.
Admission also covers the complete sink chain: a downstream worker, retained
sink or serializer must not reintroduce a precise timestamp after projection.
Rounding a timestamp must not advance the exact elapsed 30-day retention cutoff.
Until the existing storage owner supplies compatible privacy and retention
semantics, refuse that sink for shared output rather than silently timestamping
or shortening retention. Internal capture keeps its existing exact-age contract;
this proposal does not invent a second storage system.

Recommended future additive configuration, **not accepted by today's loader**:

```json
{
  "retained_spool": {"directory": "<absolute owner-provisioned base>"},
  "attribution": {
    "version": 1,
    "mode": "controlled_internal",
    "include_raw_user": false
  }
}
```

The configuration/lifecycle owner must explicitly approve the internal sink
boundary before accepting this declaration; a declaration alone is not consent.
Missing `attribution` preserves current behavior. Invalid/mixed modes do not
replace the active owned writer. No key, key reference, vault-entry name,
username or local owner handle belongs in this JSON example or shared records.
Older loaders may ignore the top-level object entirely. Before treating an
explicit policy as effective, the configuration owner must verify support in the
actual selected writer, including rollback/mixed-version cases; unsupported or
unknown support refuses policy activation and reports a bounded diagnostic.
Configuration acceptance alone is not active-writer or privacy proof.
The initial shared projection is caller-injected Python only: explicit key,
collection policy and destination, with no automatic shared config, key lookup,
fan-out or network transport. A future shared loader needs its own review.

## Keyed projection and local self-lookup

Recommend full-length HMAC-SHA256 with unambiguous length-prefixed UTF-8 tuple
encoding and versioned output. Separate purpose/domain, collection scope,
schema version and identity kind. A conceptual input is
`("ce.telemetry", version, scope, kind, namespace, canonical_value)`.
For `kind="owner"`, omit namespace/value entirely: derive the lookup token
from the key itself and fixed domain/scope/version, not a raw username.
This supports different login names on approved machines without uploading
the key or constructing a central identity directory.

For machine/process-instance/runtime-generation/worktree/task/native-session/Bridge-session tokens, consume only
truthful canonical inputs in their actual authority namespaces (including
project/installation scope where needed). Do not case-fold identities unless
their authority defines that normalization. Separate domains prevent equal
raw strings in different identity kinds/namespaces from joining accidentally.
Role names alone do not change the token for the same known canonical identity;
different roles remain structurally distinct in the output.

The user's high-entropy telemetry-purpose key is independently held, not
derived from a password, username, public codename or PR root/recovery key.
Provisioning, cross-machine custody, rotation and recovery stay with the
existing user/key owner; this proposal does not implement them. No tenant/global
key or escrow, no telemetry-triggered key generation. Version/scope changes or
key rotation deliberately break links; historical lookup needs the user's old
key/scope/version locally, never a centrally published alias bridge.

The local lookup helper computes an owner token and selects an existing local
projected index/fixture using that token. Only that derived token could be
submitted by a future query adapter, never the key or raw username.
The token is a correlation selector, **not** an authentication credential;
real ingest/query authorization must remain intact. Do not publish self-lookup
tokens beside known public identities by default.

## Strict shared allowlist and failure behavior

Build a new output from cataloged fields, never redact a copy of an arbitrary
input. Initial shared envelope: schema/version/scope, registered component,
registered event kind/name, closed outcome/state values, policy-admitted time
and numeric measurements, role availability and derived identity tokens.
Every event family needs a closed value vocabulary as well as field names.
Scope is a reviewed opaque collection label, not a repo/user/host name.

Drop raw aliases in task `owner`/targets, repo/source/exclusive-key/path/user
strings, original session/worktree/task IDs, log epochs and tool-call IDs.
Drop arbitrary tool names, free-form messages/errors, payloads, nested unknown
attributes, trace/baggage/resource identifiers and unrestricted `ce.*` attributes.
If a correlation is actually required, add its separately namespaced keyed
projection and tests explicitly; do not pass a purportedly opaque original
through unchanged. No key bytes, local key handle/reference, vault-entry name,
AWI1 field/payload or public PR codename is exportable by default.
Initial shared support may refuse uncataloged event families; it must not pass
them through under a generic kind.

Missing/unavailable/wrong-owner key, invalid scope/policy/context or failed
projection returns no shared record and increments a bounded sanitized local
reason counter. No raw fallback, exception-value logging, recursive telemetry
or retry queue. A service-only event does not acquire a fictional user; it needs
explicit consenting collection/key policy or remains internal-only.
Optional shared failure does not disable independent internal capture.
The unchanged bounded copier precedes any general serialization; projection
also rejects unsupported values without invoking user repr/equality hooks.

## Implementation ownership after separate admission

1. **Common owner:** small new `libs/process-telemetry/src/process_telemetry/attribution.py`
   and `shared_projection.py` (names provisional), composed through existing
   `emission.py`/`buffer.py`/`configured_spool.py` only where needed. Preserve
   legacy callback bytes/shape, ms stamping, snapshot bounds, one-write append,
   retention/quiet maintenance and nonjoining configured-owner cleanup.
2. **Producer/storage adapters:** Bridge `telemetry.py` emit/configuration owner
   and Dispatch `telemetry.py` lifecycle shaping plus the existing task/receipt
   owner only if required to admit its already-known native context. First
   inspect the exact emit/storage boundary; do not edit Bridge `events.py`,
   `live_runtime.py`, captured application/health/routes, immutable schemas,
   registrar or captured event/API fixtures to smuggle in identity.
   Escalate an unavoidable frozen-boundary change rather than weakening it.
3. **Tests/docs:** existing Bridge `tests/test_telemetry.py`,
   `test_process_telemetry.py`, retained-configuration tests and corresponding
   Dispatch tests, with focused attribution/projection fixtures if necessary;
   shared library README and owning telemetry/diagnostic guides. Add ordinary
   per-plugin changefiles for later code, not this docs-only proposal.
4. **Integration owner:** preserve #8370 installer and #6812 formatter custody,
   existing retained configuration/helper and collector ownership, and the
   separate Performance Telemetry measurement/catalog work. This common optional
   API can serve other producers later; it does not claim every catalog entry
   is instrumented, attributed or running.

Existing daemon activation/cutover can update collector-side enrichment of
other sessions' events without changing their native identities. Already-loaded
hooks, client modules and session extensions need explicit fresh-process
qualification; updating a collector alone cannot qualify those producers.
No forced process restart, owner transfer, rollout or new service is authorized.

## Plan and validation gates

- [ ] Publish this intent/proposal through current-head review and required CI;
  record source acceptance separately from implementation and keep #8377 open.
- [ ] Coordinator accepts final API/config shape and admits the bounded code
  slice. No implementation begins merely because this proposal merges.
- [ ] Implement optional role enrichment and pure keyed projection through the
  existing owners, with deterministic synthetic tests for represented native,
  ACP-owned native+Bridge, restored/rebuilt and delayed-drain identity, distinct
  subject/producer/collector, unavailable and service-only contexts.
- [ ] Prove Dispatch missing session is explicit; native owner receipt wins over
  target routing and ambient collector identity. Verify old lifecycle callback
  payloads are unchanged without opt-in; exercise actual emit/storage consumers,
  not just a helper that mocks past them.
- [ ] Exercise two concurrent producer/collector process generations observing
  the same subject; preserve owner-bound instance/generation distinctions and
  their separately namespaced shared tokens, with unavailable evidence explicit.
- [ ] Reject attribution activation when the actual writer ignores or lacks
  the policy, including mixed-version and rollback cases; preserve the existing
  writer and distinguish parse acceptance from effective support.
- [ ] Inspect final serialized/spooled bytes after every downstream stamping
  boundary. Prove policy-admitted timestamp precision and exact elapsed-age
  retention coexist, or refuse that shared sink without changing internal
  retention or falling back to precise/raw output.
- [ ] Test deterministic same-key/scope across approved synthetic machines,
  different keys/scopes/versions/domains/namespaces, canonicalization boundaries,
  and unknown identities. Key A selects only A's fixture rows without username;
  a test spy proves no key is passed to a query/transport callback.
- [ ] Inspect serialized/spooled bytes with deliberately identifying synthetic
  values in every legacy/nested/free-form field. Prove strict omission, malicious
  objects rejected without hooks, no key/ref/AWI1/public-codename leakage, no raw
  fallback on missing/invalid/wrong-owner keys and preserved internal capture.
- [ ] Prove no-op/legacy compatibility, bounded copied/queued work, timestamp
  admission semantics, blocked sink/replacement cleanup, atomic append and quiet
  retention. Use existing unit-only required packet and separately selected real
  process/Windows/POSIX integration lanes; do not widen CI into native matrices.
- [ ] Exercise the real consumer with fresh isolated owner-provisioned buckets,
  changed/missing context/key and delayed storage; qualify available native
  Windows/Linux/macOS and fresh-process hooks/extensions explicitly. Report
  unavailable lanes and disabled/enabled/unavailable-sink wall/CPU/spawn/drop
  measurements honestly, preserving the performance pattern's budgets.
- [ ] **Shared deployment privacy gate:** existing deployment/security owners
  review and prove ingest identity, network/source addresses, auth/account
  association, access/query/audit logs and retention, timestamps, small cohorts,
  behavioral correlation and voluntary disclosure/revocation boundaries. Define
  admitted precision/aggregation/access controls and residual risks without
  weakening authentication. Until then no central shared export/lookup rollout
  or anonymity claim. A pure local lookup fixture is not anonymous-ingress proof.
- [ ] After admitted source merge, separately qualify release, installed/running
  versions, selected policy and actual projected receiver readback through the
  existing rollout owner. Parent campaign remains Active until its full gates.

## Journal

- 2026-10-31 - Reconciled the planning slice with current source while preserving
  newer fleet plans and the earlier API acceptance journal. Corrected the
  top-level loader baseline, admitted owner-bound process/generation identity,
  and made mixed-version policy support and final-spool timestamp/retention
  compatibility explicit validation gates. This is planning only; no runtime,
  key, exporter or deployment was implemented or activated.
- 2026-10-25 UTC — The coordinator accepted the proposed API family and
  configuration boundary for a later bounded implementation: closed immutable
  role records, owner-bound admitted in-memory keys, separated full-length
  HMAC-SHA256 tokens, username-free owner lookup, freshly allowlisted shared
  output, no raw fallback and unchanged callbacks without opt-in. This accepts
  the design, not execution. Implementation still requires merged source intent,
  the driver's own intent publication and a separate explicit execution ACK.
  Shared deployment retains its full identity-separation gate.
- 2026-10-24 — Reconciled canonical telemetry intent and existing #8377 logging
  ownership against the source baseline above. Proposed user-key-derived owner
  lookup, explicit identity roles and pre-shared-storage allowlisting. No code,
  key, configuration, collector or deployment changed. Implementation awaits
  reviewed proposal publication and explicit coordinator interface acceptance;
  the complete central identity-separation gate remains open.
