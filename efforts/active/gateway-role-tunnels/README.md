# Gateway Role Tunnels

- **Slug:** `gateway-role-tunnels`
- **Repo:** copilot-extensions
- **Branch(es):** serial, independently reviewed slices against `dev`
- **Created:** 2026-10-19
- **Status:** Draft
- **Vision:** [Declarative Fleet Gateway](../../../visions/machine-fleet/gateway/README.md), [Machine Fleet](../../../visions/machine-fleet/README.md)
- **Umbrella issue:** #7257
- **Parent effort:** [Fleet Control V2](../fleet-control-v2/README.md), #6236
- **Related owners:** #6443, #5789, #6743, #7099, #6804, #6240, #6424, #5862, #6030

## Guiding Intent

Make the selected fleet-manager Gateway a reusable, repository-declared service
front door. Its owner defines expected role slots, API/UX namespaces and trust
references before services connect. Authenticated outbound service channels
supply those roles; routine valid configuration and service changes reconcile
without Gateway process replacement.

This is a finite initial implementation campaign within Fleet Control V2, not
the whole product migration. It ends at an honest isolated container proof and
an attributable adoption-readiness record. Production enrollment and deployment
remain separately authorized; platform-specific app packaging is deferred.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Gateway coordinator | Owns the plan, shared interfaces, integration, PRs and completion gate | Managed source worktree; #7257 |
| Manifest implementer | Owns new manifest and coherent publication modules and focused tests | Coordinator-assigned source scope |
| Service-channel implementer | Owns new connector/protocol modules and focused tests | Coordinator-assigned source scope |
| Container qualifier | Owns reusable packaging and isolated process/container evidence | Existing bounded and clean-room runners |
| Retained integration owners | Preserve auth, index, provider, lifecycle, observation and deployment contracts | Related coordination issues |
| Adopter | Selects private configuration, capacity, edge identity and live activation | Separate explicit adoption approval |

## Coordination

- **Topology:** reviewed plan, then serial coherent PRs. The coordinator owns
  shared source and git publication; delegates never mutate that shared history.
- **Parent boundary:** #6236 remains the integrated product effort. This campaign
  supplies service-manifest/tunnel capabilities, not a competing Gateway.
- **Source reservation:** new `gateway_manifest`, `gateway_reconcile` and
  service-channel modules, their tests and documentation first. Existing
  Gateway/auth/client/provider consumers are not silently reassigned.
- **Shared integration gate:** name the minimal opt-in CLI/serving/auth adapter
  and exclusive files under #6443/#5789 before changing existing consumers.
  Keep static index/credential behavior unchanged outside explicit selection.
- **Retained owners:** #6743/#7099 own identity/admission primitives; #6804
  provider/enrollment; #6240/#6424 lifecycle; #5862 observation; #6030 deployment.
  Reuse their APIs rather than adding replacement authorities.
- **Handoff:** continue from this README and the
  [initial contract](initial-contract.md), retaining open gates and actual
  serving evidence. A merged prerequisite is not campaign completion.

## Context

The current `fleet-manager` source has an authenticated foreground TLS Gateway,
fixed index routes, bounded HTTPS requests and credential-controller polling.
`GatewayConfig` is startup-loaded; there is no general service-role tunnel,
atomic live configuration publication or reusable released Gateway container.
Existing local admission snapshots and permits do not authenticate the
configuration source or fence an external effect by themselves.

The parent [Gateway/index slice](../fleet-control-v2/gateway-index-slice.md)
and [authentication proposal](../fleet-control-v2/runtime-authentication.md)
retain their scopes. Existing Komodo/Periphery role-fleet validation is a
deployment prerequisite, not proof that this new Gateway is already deployable.
The initial proof must distinguish implemented code, isolated runtime behavior,
image architecture support and actual live adoption.

## Request

**Settled public-safe summary:** plan initial phases and then build a reusable
Gateway whose repository-owned manifest declares service routes, namespaces,
roles and key-source references. Services deploy separately and connect
outbound to expected slots. Nearly all ordinary configuration changes should
apply live. The core, configuration language, base container and generic
Cloudflare adapter belong here; platform wrappers and adopter configuration do
not become core dependencies. First qualify Linux container use, leaving
platform-specific app repositories and packaging for later.

Operator authorization, verbatim:

> Great. Start planning some initial phases for the unified (reusable) Gateway and then build them

The operator confirmed the slug `gateway-role-tunnels`.
The [inception record](inception-transcript.md) preserves the relevant
public-safe wording and explicit redaction boundary. Detailed phase design,
wire choices and validation elaborations below are **agent-recommended**.
This request authorizes reviewed source implementation, not live infrastructure
or credential changes.

## Plan

### Phase 0 - Review the contract and preserve ownership
- [ ] Land this finite plan and its governing vision through normal review.
- [ ] Record baseline capabilities and exact non-overlapping source scopes;
  agree any shared consumer integration before editing it.
- [ ] Preserve the current static Gateway, index, credential and provider
  behavior as the compatibility baseline.

### Phase 1 - Desired manifest and coherent live publication
- [ ] Implement strict bounded service-slot, API/UX route, role, trust-reference
  and declared-capability validation with explicit schema/version handling.
- [ ] Add an explicit owner-selected configuration source and coherent,
  revisioned candidate validation/publication. Parsing, a digest or a Git
  commit alone must never confer publication authority.
- [ ] Preserve last-known-good state on invalid/stale/conflicting input;
  readers see one complete desired/admission snapshot, not mixed revisions.
- [ ] Specify and prove no-op reload, withdrawal, trust rotation and revocation
  at current request/connection admission and execution checkpoints.
- [ ] Expose bounded non-secret desired/applied/per-slot evidence without
  presenting this prerequisite as a complete service Gateway.

### Phase 2 - Authenticated outbound role service channels
- [ ] Wire the reviewed opt-in serving profile through the same Gateway role,
  reusing compatible identity/admission and current-policy owners.
- [ ] Add a service connector that initiates an authenticated outbound channel,
  proves an assigned identity and cannot choose another slot or namespace.
- [ ] Route declared bounded HTTP API/UX traffic and streaming through that
  channel; advertise supported capabilities and explicitly refuse unsupported
  upgrades/transports instead of using a direct-backend fallback.
- [ ] Prove replacement/reconnect generation fencing, cancellation, policy
  withdrawal, bounded backpressure and truthful uncertain-effect outcomes.
- [ ] Preserve real existing static/index/credential paths and their
  authorization, deadlines, credential custody and error semantics.

### Phase 3 - Reusable packaging and isolated adoption proof
- [ ] Supply the base Gateway and connector container specification using the
  selected package/runtime closure, non-root execution and explicit state/config
  mounts; preserve thin client installation defaults.
- [ ] Add the optional generic Cloudflare ingress adapter and external-origin
  contract without creating DNS, Access policies or provider credentials.
- [ ] Prove one Gateway and two independently isolated service instances:
  expected-but-absent slots, actual routing, valid hot updates, denied hijacking,
  reconnect and stream behavior, with unchanged Gateway process identity.
- [ ] Qualify declared image architectures, including the intended ARM64 host
  shape, without silently installing privileged emulation or using amd64
  execution as ARM64 evidence. Record a concrete approval/capability blocker.
- [ ] Produce adopter-owned rollout prerequisites, exact changes, rollback
  limits and readiness evidence. Do not perform live adoption under this plan.

### Completion
- [ ] Resolve all Plan and Validation Plan items or transfer an explicit
  residual to a named tracked objective before marking Done and archiving.
- [ ] Keep broader Fleet Control V2, provider integration and live adoption
  open where this campaign has not actually resolved their gates.

## Validation Plan

- [ ] **Manifest:** reject unknown/malformed fields, namespace collisions,
  unsafe route forms, undeclared roles/capabilities and secret-bearing inline
  credentials; bound input and output before publication.
- [ ] **Authority:** a connector cannot turn self-description into enrollment;
  stale source revisions, revoked identities and old connection generations
  cannot restore authority.
- [ ] **Reload:** valid candidates publish coherently under concurrent use;
  invalid candidates leave existing state intact. No-op reload is stable.
- [ ] **Serving:** actual authenticated routed requests reach only their
  configured service; expected absence and unsupported behavior are explicit.
- [ ] **Streaming:** actual sustained responses, disconnect and backpressure
  stay bounded; revocation/cancellation is not mistaken for successful work.
- [ ] **Compatibility:** current real static/index/credential tests remain
  green without changing deadlines or replacing authentication with fixtures.
- [ ] **Ingress:** external-origin/TLS/forwarded-identity rules are verified;
  edge admission never replaces role or fleet authorization.
- [ ] **Containers:** separate Gateway/service containers prove routing and
  live configuration with synthetic secrets and no production mounts, host
  socket, public listener, live account or hidden privileged admission.
- [ ] **Architecture:** distinguish image build, emulated run, native run and
  live target readiness. Unsupported or unavailable evidence stays explicit.
- [ ] **Contribution:** targeted bounded tests, applicable repository guards,
  current-head review and actual merge cover each implementation phase.

## Proposal

Use a small desired-manifest/reconciliation boundary feeding the selected
Gateway and service-channel runtime, not another controller above it.
The [initial contract](initial-contract.md) records the recommended interface,
trust, transport, packaging and proof boundaries. It deliberately leaves
provider credentials and production placement with their existing owners.

## Journal

### 2026-10-19 - Initial scope and proposal
- Operator selected the canonical slug and authorized planning followed by
  reviewed implementation.
- Searched current coordination issues and open Gateway PRs; claimed #7257
  under #6236 and notified #6443 of the non-overlapping first source scope.
- Read-only evidence established startup-only configuration, fixed HTTPS
  routes and controller polling, not general tunnels or hot role publication.
- Reused the existing managed source worktree. No runtime, provider,
  deployment, trust-root or production configuration has changed.
