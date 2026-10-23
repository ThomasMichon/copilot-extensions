# Repository-led deployment preparation and observation

Parent: [Linux role fleet](README.md). Extends the existing #6030/#5861 scope;
client lifecycle #6424, selected releases #6240, Gateway routing and manager/
service authorities retain their ownership. This document is a plan amendment,
not implemented controller behavior or deployment permission.

## Accepted direction

The Gateway or a dedicated controller pulls configuration from explicitly
adopted repositories, resolves expected layout and exact package versions, and
prepares for deployment/updates. A thin fleet-manager client on a development
machine requests activation of that prepared state; it does not upload copies
of the configuration to the controller.

**Immediate scope is deliberately smaller:** use the existing local JSONL spool
on an explicitly selected adopter machine and inspect actual emitted records.
Hosted Grafana and deployment-control implementation are deferred to the
existing Fleet Management owner, not prerequisites for collecting local data.

## Future controller contract

The following realization and sequencing are **agent-recommended**:

1. Register trusted repository identities, selected refs/paths and declared
   precedence through the existing adoption/bootstrap owner. Resolve read
   credentials through their existing provider; no token or arbitrary Git URL
   is accepted in a routine activation request.
2. Acquire bounded published snapshots on the controller. Compose template and
   adopter state using the existing configuration owner, preserving overrides
   and custom roles. Record source revisions and exact package/release identity;
   reject invalid, conflicting or unavailable inputs without changing service
   state.
3. Resolve layout, dependencies and versions through the existing planner,
   role compiler and release/artifact owners. Reuse Komodo repository-backed
   resources/Stacks and existing installers where supported; do not create a
   second scheduler, package manager or container supervisor.
4. Expose source-acquired, validated, prepared and blocked states independently.
   Preparation is not activation, installed state or health. Stage artifacts
   only through the owning admitted staging contract.
5. Let the thin client inspect the diff and say "go" for the selected prepared
   generation. Revalidate current scope, source/version selection and approval;
   no raw configuration, Compose body, shell or executable travels in that
   control request. Changed inputs require a refreshed decision.
6. Track accepted, running, succeeded, failed and unknown effects through
   existing operation/receipt authorities. Interrupted communication is not
   permission to replay deployment. Preserve data, custom roles and the last
   valid state; new candidate construction/start does not wait on old-generation
   cleanup or graceful handoff.

Independently bootstrapping/recovering Core/controller remains necessary.
Configuring a manager to deploy itself through an unavailable copy of its own
API is not a first-adoption solution. No live capacity, credential enrollment,
Gateway replacement or manager mutation is authorized by this amendment.

## Optional personal observation role

An operator-owned persistent OTel-compatible dashboard/storage deployment can be
selected through fleet configuration once managed role deployment is available.
Grafana is an intended UI option; its Collector/storage services remain explicit
components rather than an assumption that the UI itself stores all telemetry.
Image/package selection, resources, retention, access and persistent volumes
belong to that future admitted role.

Later operator and organization sinks may be explicitly combined through the
selected consumer, with separate failure/delivery accounting and unchanged
privacy/collection scope. No centralized destination or automatic off-site export
is selected here.

Local append-only JSONL files remain the small independent fallback. Reuse the
Bridge/Dispatch spool factories and producer-owned reducers rather than add a
new bus, daemon or reporter. The existing legacy spool callback is synchronous;
it is an explicit low-volume debug baseline, not proof of bounded hot-path
overhead or a replacement for the existing bounded-adapter work.

## Validation

- Controller fetches declared sources and resolves the same prepared
  layout/package set without any client-supplied config document.
- Source/credential failure, invalid config and changed revisions preserve the
  prior accepted state; changed plans cannot reuse earlier activation consent.
- Existing manager/bootstrap independence, idempotent repeat, data preservation,
  partial-failure recovery and uncertain outcome handling remain proved.
- Thin client remains free of Docker/controller effects and never holds the
  manager's deployment credential just to request activation.
- Future hosted telemetry proves real receive/query/persistence, private access,
  retention, resource limits and independent fan-out failures.
- Immediate local debug proves actual producer output and content exclusion
  through independent JSONL readback. An HTTP response, synthetic file or
  configuration file alone is not collected-data proof.
- Record missing instrumentation honestly. Existing lifecycle events do not
  establish that every QoS/Engagement catalog measurement is implemented.
