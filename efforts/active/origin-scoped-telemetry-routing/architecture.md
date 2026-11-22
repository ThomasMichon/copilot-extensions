# Architecture proposal: scoped telemetry routing

Back to the [canonical effort](README.md).

**Status:** agent-recommended proposal, not supported configuration or deployed
behavior. [P0](README.md#p0---review-standing-intent-and-the-proposal) must clear
review before runtime realization.

## 1. Extend the existing owners

Reuse `process_telemetry` producer slots, projections, bounded buffers and
command custody. Reuse the native `configuration_context` read-data and
`configuration_layers` composition already consumed by
[Index](../../../plugins/agent-index/src/agent_index/configuration_layers.py)
and [Logger](../../../plugins/agent-logger/docs/config-reference.md).

The entry/request owner admits the origin and snapshots effective policy before
callbacks emit. A source offers its existing typed observation through a reviewed
projection. The existing bounded writer delivers only to compatible selected
consumers:

```text
driving operation -> admitted origin + effective policy
                 -> typed source observation
                 -> scoped projection and bounded native writer
                 -> declared compatible transport
                 -> receiver validation, audit and persistence
                 -> optional backend delivery and independent readback
```

Do not create a second event bus, telemetry SDK, knowledge binder, process
registry or mandatory broker. A lone installation stays useful without
knowledge binding or another app. Optional composed policy uses an already
available owner or explicitly supplied native context; emission never
self-provisions a peer or performs a fresh repository lookup per event.

## 2. Separate definitions, usage and runtime state

The conceptual families below are proposed semantics, **not YAML/JSON keys**.
P1 owns the reviewed schema and contract-version decision.

| Family | Authority and contents |
|--------|------------------------|
| Sink definitions and defaults | Harness-owned named consumers, their declared transport/source/signal capabilities and default selection |
| Portable usage preferences | Bound knowledge's usages, logical-machine selection, scope refinements and exclusions |
| Effective machine preferences | Native machine-wide and exact repository+machine sources, resolved through the existing configuration owner |
| Operation admission | The driving origin, delegation relationship, selected policy revision and eligible usages for this operation |
| Generated routing/installation state | Service-owned projections, selected-runtime capabilities, actual endpoint/child lifetime and delivery counters; not ordinary preferences |

Use the established composition order: defaults, repository declarations,
bound-knowledge graft, machine-wide preferences, and exact repository+machine
overrides. Keep winning source/path provenance. Native explicit execution
selectors retain their existing contract, but do not grant eligibility or
installation authority.

Mappings, lists and nulls need documented deterministic merge semantics.
Reuse ordinary composition rather than inventing a telemetry-specific merge
engine; keyed sink definitions need explicit identity and collision rules.
Invalid explicit knowledge/cell/owner data is not optional absence. A genuine
standalone installation with no optional owner can retain native no-export
behavior.

Evaluate eligibility, exclusions and opt-outs separately from ordinary value
precedence. A higher preference can select only an allowed consumer and usage;
it cannot silently widen a harness-specific sink to unrelated sessions.
Selecting an available sink is not permission to install or start it. Named
alternatives do not imply fan-out to every destination.

Physical paths, credentials and generated service descriptors retain their
existing private/machine owners. No app needs another copy of the receiver
command. Normal setup/adoption materializes any required service routing from
the accepted effective policy and records its source.

## 3. Admit the driving origin once

Resolve the actual driving repository/worktree or equivalent native operation
identity at the ordinary entry boundary. A registered canonical repository,
worktree relationship or admitted context is evidence; folder basenames,
collector CWD, target repository labels and telemetry attributes are not.

Keep these roles distinct:

- the driving harness whose usage policy applies;
- the observed operation or task;
- the executing app/process/venue;
- the target repository or resource;
- the receiver and optional backend sender.

The origin is local routing metadata, not a fabricated session/trace/span ID or
an automatic shared-export attribute. Follow existing minimization and shared
attribution requirements before serialization.

Propagate an immutable admitted binding through child execution, existing task
and session ownership, normal service requests and admitted remote handoff.
Changing target CWD does not replace the origin. A paired knowledge workspace
or related repository is not globally enrolled merely because it can serve a
harness; the actual operation must retain that relationship.

Use the existing control boundary and native admission facts. Do not add a
telemetry bearer, credential rollout or independent attestation chain simply
to route optional telemetry. Existing API authentication, task mutation
authority, remote admission and network policy remain unchanged.

Unknown, missing, invalid or excluded context does not enter that harness's
sink. Product work and independent local diagnostics continue. A shared daemon
keeps concurrent origins separate rather than replacing a process-global sink.
Taskless service health requires an explicit service-purpose usage; it cannot
borrow the last request or an asserted task repository.

## 4. One eligibility decision across intakes

The same contract must be usable by command tools, resident services,
consumer-owned native session listeners, registered flow reports, fallbacks and
collection self-observations. Gate before enqueue into storage bound to that
sink; gating only a later uploader cannot recover missing original context.

An observer may need bounded local initialization to learn its actual session
origin. It must not publish startup/registration records to a harness sink
before eligibility is known. Missing context is not a reason to default to the
installed harness's identity.

An already-authorized background sender may drain admitted records without
enrolling arbitrary sessions. Its own health/diagnostic events follow an explicit
service-purpose rule. Local audit evidence should explain excluded, unsupported,
unconfigured, unavailable and dropped outcomes without recursively exporting
that audit or retaining raw record contents.

## 5. Adapt typed signals, not arbitrary logs

Preserve the current lifecycle schemas and source ownership. New families need
closed reviewed projections and support discovery; dependency presence or a
shared buffer import is not enrollment.

The current command transport is JSONL, not OTLP. An OTLP trace/log/metric
exporter cannot be pointed at its command receiver. Where suitable, adapt an
existing typed source before SDK export through the same bounded delivery owner.
Where an adopter explicitly chooses OTLP, prove the receiver's actual signal
support and lifecycle independently. Command-only adoption stays complete and
does not acquire an HTTP-daemon dependency.

Retain the existing update-run record/status/console owner. Retain the Manager
measurement and frame-health owner. Do not turn a source probe into a production
enrollment claim or forward unrestricted diagnostic records, raw identities,
arguments, paths or error strings. Local-only diagnostics and validation-only
canaries remain explicit dispositions, not missing application coverage renamed
as success.

## 6. Evolve contracts provider first

P1/P2 must identify which new policy, origin and source capabilities require a
versioned extension and which can remain inside existing owned admission.
Read support from the selected executing package or running provider, not a
payload version or assumption of lockstep deployment.

Use expand, migrate, contract:

1. Providers and receivers advertise and enforce the additive capabilities.
2. Normal adoption selects only capable peers and creates scoped routing.
3. Ordinary consumers migrate with exact-source evidence before any legacy
   path is narrowed or removed.

An older reader must not be reported as enforcing a new exclusion it ignores.
If required policy cannot be enforced, do not activate that route; use an
existing proven route-specific disable where appropriate rather than a silent
downgrade. Preserve independent legacy integrations and local diagnostics.
Rollback has its own capability and disable proof.

No update, deployment or predecessor-retirement authority comes from this plan.
Any future installer change must retain
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement):
incoming readiness and all current selections define completion, while
outgoing recovery/retirement remain separate.

## 7. Cheap paths and truthful evidence

No per-event filesystem/configuration acquisition, blocking SDK import,
network/export, unbounded queue or per-event receiver process. Owners retain
their bounded snapshot/reload and child/shutdown semantics. A changed policy
does not silently retarget an active operation.

Distinguish source support, selected provider support, offered/enqueued/written
records, receiver persistence, upload acknowledgement and backend-query evidence.
Route health is not proof of complete coverage or a latency budget. Qualify
normal consumer paths under the existing
[performance pattern](../../../docs/patterns/performance-measurement.md) and
[service contract evolution](../../../docs/patterns/service-contract-evolution.md)
without taking over their separate campaigns.
