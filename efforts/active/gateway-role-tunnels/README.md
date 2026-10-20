# Gateway Role Tunnels

- **Slug:** `gateway-role-tunnels`
- **Repo:** copilot-extensions
- **Branch(es):** serial, independently reviewed slices against `dev`
- **Created:** 2026-10-19
- **Status:** Active
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
  The operator subsequently assigned the narrow service-role/CLI seam to this
  campaign; the [service-profile contract](service-profile.md) goes through
  review before implementation and does not transfer the retained primitives
  or provider integrations.
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

When asked whether this campaign should own the narrow service-role admission
adapter and one opt-in CLI/serving seam, with reviewed contract, reused
cryptographic verification and unchanged static/index/credential/provider
behavior, the operator selected verbatim:

```text
integration_scope=authorize-narrow-integration
```

This settles that source-scope decision, not production trust or deployment.

## Plan

### Phase 0 - Review the contract and preserve ownership
- [x] Land this finite plan and its governing vision through normal review.
- [x] Record baseline capabilities and exact non-overlapping initial source
  scopes. Shared consumer integration remains a Phase 2 agreement gate.
- [x] Preserve the current static Gateway, index, credential and provider
  behavior as the compatibility baseline.

### Phase 1 - Desired manifest and coherent live publication
- [x] Implement strict bounded service-slot, API/UX route, role, trust-reference
  and declared-capability validation with explicit schema/version handling.
- [x] Add an explicit owner-selected configuration source and coherent,
  revisioned candidate validation/publication. Parsing, a digest or a Git
  commit alone must never confer publication authority.
- [x] Preserve last-known-good state on invalid/stale/conflicting input;
  readers see one complete desired/admission snapshot, not mixed revisions.
- [x] Specify and prove no-op reload, withdrawal, trust rotation and revocation
  at current request/connection admission and execution checkpoints.
- [x] Expose bounded non-secret desired/applied/per-slot evidence without
  presenting this prerequisite as a complete service Gateway.

### Phase 2 - Authenticated outbound role service channels
- [x] Review the [service-profile integration contract](service-profile.md)
  before implementing the operator-assigned narrow adapter/CLI seam.
- [x] Wire the reviewed opt-in serving profile through the same Gateway role,
  reusing compatible identity/admission and current-policy owners.
- [x] Add a service connector that initiates an authenticated outbound channel,
  proves an assigned identity and cannot choose another slot or namespace.
- [x] Route declared bounded HTTP API/UX traffic and streaming through that
  channel; advertise supported capabilities and explicitly refuse unsupported
  upgrades/transports instead of using a direct-backend fallback.
- [x] Prove replacement/reconnect generation fencing, cancellation, policy
  withdrawal, bounded backpressure and truthful uncertain-effect outcomes.
  Real in-process TLS/socket witnesses cover these paths. Independently
  isolated container/process qualification remains a Phase 3 gate.
- [x] Preserve real existing static/index/credential paths and their
  authorization, deadlines, credential custody and error semantics.

### Phase 3 - Reusable packaging and isolated adoption proof
- [x] Supply the base Gateway and connector container specification using the
  selected package/runtime closure, non-root execution and explicit state/config
  mounts; preserve thin client installation defaults.
- [ ] Add the optional generic Cloudflare ingress adapter and external-origin
  contract without creating DNS, Access policies or provider credentials.
- [x] Prove one Gateway and two independently isolated service instances:
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

- [x] **Manifest:** reject unknown/malformed fields, namespace collisions,
  unsafe route forms, undeclared roles/capabilities and secret-bearing inline
  credentials; bound input and output before publication.
- [x] **Authority:** a connector cannot turn self-description into enrollment;
  stale source revisions, revoked identities and old connection generations
  cannot restore authority.
- [x] **Reload:** valid candidates publish coherently under concurrent use;
  invalid candidates leave existing state intact. No-op reload is stable.
- [x] **Serving:** actual authenticated routed requests reach only their
  configured service; expected absence and unsupported behavior are explicit.
- [x] **Streaming:** actual sustained responses, disconnect and backpressure
  stay bounded; revocation/cancellation is not mistaken for successful work.
- [x] **Compatibility:** current real static/index/credential tests remain
  green without changing deadlines or replacing authentication with fixtures.
- [ ] **Ingress:** external-origin/TLS/forwarded-identity rules are verified;
  edge admission never replaces role or fleet authorization.
- [x] **Containers:** separate Gateway/service containers prove routing and
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

### 2026-10-19 - Reviewed plan and first implementation slice
- Plan PR #7259 merged as `f9a0104081fc0f11ac91df1b0915aaad086d6e90`
  after exact-head Copilot approval with no findings and successful CI.
- Re-read the canonical plan and selected Phase 1, starting with new strict
  manifest domain types and tests rather than editing retained server/auth files.
- The bounded current Gateway/auth/fence/admission/transport baseline passed
  197 tests. The owning runner prepared its missing declared test environment;
  no runtime, timeout, credential or production fixture was changed.
- Live configuration publication, service tunnels, container qualification and
  adopter deployment remain open; plan merge is not their completion.

### 2026-10-19 - Desired-state prerequisite implemented
- Added strict stdlib-only role/route/reference domain records and canonical
  normalization using the existing bounded JSON helpers.
- Added an explicitly selected-source, process-local publication store.
  Invalid/unavailable input preserves the previous desired snapshot; revision
  rollback and same-revision conflicts refuse, no-ops reuse the snapshot, and
  concurrent publications re-check the latest revision under one lock.
- Bounded manifest/publication and existing Gateway compatibility validation
  passed 483 cases. Independent read-only review found no significant issues.
- This is not serving integration: the store does not authenticate its source,
  persist a restart fence, replace verifier/admission state, rotate live trust,
  or route service traffic. Those Plan and Validation gates remain open.

### 2026-10-19 - First prerequisite merged and serving scope assigned
- PR #7265 merged as `ba438f6cc24435dd44f4748f0577ff0b15b0ffeb`
  after exact-head Copilot approval and successful current CI.
- Fixed the actual release guard requirement with a fleet-manager patch
  changefile. A native Windows fixture failure was a 1 MiB auto-generated
  pytest identifier; its explicit short ID preserves the full oversized input.
- The standard bounded fleet-manager portfolio passed 1,454 tests with two
  platform skips and fourteen explicitly excluded full-portfolio cases.
- Re-read this effort. The operator authorized the narrow service-role/CLI
  integration scope after the retained-owner coordination request, preserving
  provider custody, shared verification primitives and unselected serving.
- The next slice reviews the service-profile contract, then implements actual
  current-role admission and coherent context publication. Service channels,
  streaming, container and live-adoption gates remain open.

### 2026-10-19 - Opt-in service adapter and actual channel implementation
- Service-profile contract PR #7267 merged as
  `aead93314929b0c111b1f7ef4711d8e24215e349` after clean exact-head
  review and successful CI; implementation follows that contract.
- Added coherent current service/caller policy using the existing JWT/PoP
  verifier, with public key-reference resolution, explicit scoped callers,
  retained grant custody, live trust epochs and invalid-candidate preservation.
- Added the explicitly selected foreground TLS profile and actual outbound
  WebSocket connector SDK. Real socket tests route two services, API/HTML,
  opaque queries and bounded SSE without replacing the Gateway on policy reload.
- Independent review identified candidate preparation prematurely evicting an
  active channel, an incorrect buffered response bound, and reconnect budget
  reset before attachment. Candidate/active custody is now separate, response
  budgets are independent and only actual attached lifetime resets retries;
  focused regressions cover each correction.
- Qualification remains in-process, with synthetic issuer/provider fixtures.
  Proxy/Cloudflare compatibility, container/ARM64 evidence, durable restart
  revision custody and live adoption are not claimed by these tests.

### 2026-10-19 - Service runtime merged; isolated packaging next
- Runtime PR #7278 merged as
  `a335f39283af46ccd3a1df499134bf56a6cc5268` after clean current-head
  Copilot review and successful required CI, through the managed merge flow.
- The standard bounded portfolio passed 1,647 cases with two platform skips
  and fourteen documented full-portfolio exclusions. Additional real TLS
  peer-close/protocol-error and Python 3.10 timeout cases cover bounded closure
  and downstream aborts; no provider account or production runtime was used.
- Phase 2 and the corresponding in-process validation items are complete.
  Restored the prepared non-root image and exact-source host-driven witness
  candidate for the next serial slice. Its separate-container execution is
  still unrun, not inferred from SDK tests or mechanical runner guards.
- Phase 3 now selects ordinary isolated amd64 image/routing qualification.
  Generic ingress, ARM64 qualification and adopter readiness remain open.

### 2026-10-19 - Native amd64 isolated container proof
- Built the committed noneditable wheel/extra closure into the non-root base
  image and executed the host-driven [container witness](../../../tools/clean-room/gateway-role-tunnels/README.md)
  on a native amd64 Linux engine. All ten assembled stages passed; the
  twenty-seven mechanical runner/ownership guards passed separately.
- Independently isolated Gateway, two services and observer proved actual API,
  HTML and unfinished-backend SSE delivery; denied caller/slot/principal
  hijacking; invalid-candidate preservation; higher role/route publication;
  connector replacement and predecessor retirement; service restart/reconnect;
  and caller revocation. Seven Gateway PID/start observations were identical.
- The witness used only offline synthetic credentials, an internal network,
  bounded non-root containers and exact-ID cleanup, which completed. No host
  socket, published port, privileged container or live provider was admitted.
- This qualifies native amd64 direct-TLS behavior, not ARM64, Cloudflare,
  durable restart revision authority, managed deployment or adopter readiness.
  The next slice closes the generic ingress/external-origin contract.
