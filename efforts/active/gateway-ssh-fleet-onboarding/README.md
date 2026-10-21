# Gateway and SSH Fleet Onboarding

- **Slug:** `gateway-ssh-fleet-onboarding`
- **Repo:** copilot-extensions
- **Branch(es):** managed source `ticking-wrench`
- **Created:** 2026-10-16
- **Status:** Active
- **Umbrella issue:** #6681
- **Sub-issues:** #5861; prerequisite owners #6030, #6240, #6424, #5789, #6443, #6804

## Guiding Intent

An explicitly selected machine setup flow should converge that machine's
Gateway enrollment and SSH transport, not require an agent to rediscover and
replay installation steps. Configuration selects the Gateway/controller and
directional policy per machine or execution environment. Existing lifecycle,
authentication, operation and SSH owners remain authoritative.

This is an integration slice of the existing Linux Role Fleet, Fleet Control V2
and routing campaigns, not a replacement for their implementation or ownership.
The governing [Machine Fleet vision](../../../visions/machine-fleet/README.md)
places reusable Gateway, native OpenSSH, optional public Azure Bastion
reachability and the complete Chat capability in the shared suite. Adopters
supply inventory, loadout, policy and secret references; organization-specific
resource acquisition and identity bindings remain external adapters.

## Participants

| Participant | Role | Reached via |
|-------------|------|-------------|
| `ticking-wrench` | Evidence-to-contract plan and proposed integration slice | #6681 and this effort |
| Existing #6030 / #6240 / #6424 owners | Construction, role lifecycle and installation | Retained owner accepted the exclusive source leaves; ordered shared wiring remains with that owner |
| Existing #5789 / #6443 / #6804 owners | Routing, authentication, enrollment and Gateway integration | Their existing issue threads |
| Existing #6241 owner | Operation admission and receipt authority | Existing Fleet Control V2 coordination |
| `wobbly-contraption` | Retained construction/admission/lifecycle and setup-consumer integration, not generic artifact publication | Existing #6030 / #6240 / #6424 source custody |

## Coordination

- **Topology:** independent worktrees and per-slice PRs, after explicit interface
  and file agreement.
- **Host:** `ticking-wrench` owns this proposal, not another participant's runtime
  or deployment.
- **Delegates:** no new runtime writer is assigned by this plan.
- **Handoff:** name the accepted owner, exact scope and unresolved validation;
  source publication, installed state and live convergence are separate facts.
- Keep the Linux Role Fleet and Fleet Control V2 plans canonical for their
  lifecycle, release and operation contracts. This effort owns only their
  Gateway/SSH onboarding integration and validation matrix.
- No edits to reserved installer, authentication, ledger, controller or shared
  CLI files before their retained owners agree the integration seam.
- The construction owner accepted two exclusive new source leaves:
  `fleet-manager/src/fleet_manager/gateway_ssh_policy.py` and its matching
  `test_gateway_ssh_policy.py` belong to `ticking-wrench`; the separate
  `gateway_ssh_runtime.py` and matching tests belong to the existing runtime
  integration participant. Shared CLI, staging, admission, manifests and
  lifecycle files remain with the construction owner. Source-leaf acceptance
  is not deployment or an implemented setup hook.
- Generic artifact publication needs an explicit participant and
  operation reservation before shared setup wiring consumes it. That appointment
  remains unassigned; proposal hosting, construction lineage, pure policy/runtime
  leaves and client admission do not confer publishing or signing custody.
- Use existing owner contacts for operation reservations. Publishing a contract
  amendment does not transfer the source or effect authority described by those
  reservations.

## Context

The existing agent-ssh external transport-provider contract already supports
provider-owned modules and normalized registries. Agent-ssh owns profile
emission, Include coexistence, source auditing and SSH verification. Its
`fleet-targets` snapshot is configuration evidence, not enrollment or execution.

The current fleet-manager surfaces distinguish planning, authenticated artifact
staging, operation journals and credential-controller subscriptions from
managed role installation and desired-state execution. A setup flow cannot
truthfully be assembled by renaming those existing surfaces as apply.

Reuse the reviewed service-role Gateway, connector and container specifications
and their [adoption-readiness record](../../../fleet-manager/docs/gateway-adoption-readiness.md).
The [native qualification campaign](../../2026/10/20%20gateway-native-qualification/README.md)
proves exact-source amd64/ARM64 isolated behavior. Those retained images are not
released registry artifacts, an installation or a managed migration. New setup
must consume the owning release and lifecycle contracts rather than rebuild
that Gateway or convert its witness into a deployment mechanism.

This closes the machine-fleet vision's driver-selected reachability,
owner-directed named reconciliation and recoverable lifecycle intent, while
preserving the agent-ssh vision's transport floor and independent provider
boundary. Relevant parent plans are
[`linux-role-fleet`](../linux-role-fleet/README.md),
[`fleet-control-v2`](../fleet-control-v2/README.md) and
[`machine-fleet-routing-foundation`](../machine-fleet-routing-foundation/README.md).
Any change beyond their reviewed authority requires a reviewed amendment first.

## Request

Operator request, verbatim:

> We need to take stock of every machines's challenges setting up Gateway and SSH and incorporate mitigations and persistence into the fleet-manager flow for Gateway+SSH setup. As each user machine runs the fleet-manager setup flow, we want it to onboard with Gateway. We want fleet gateway to be a "driver" for agent-ssh, similar to dtssh and cloudflare, so for the knowledge repo config it can be specified as the controller for a specific machine.
> Admittedly, in a multi-machine config, some machines have special inbound/outbound connectivity rules compared to the rest.

The following contract decomposition and validation techniques are
**agent-recommended** implementation proposals, not additional operator grants.

## Proposed Contract

### Portable setup modules and operation ownership

Gateway and Vault setup are Fleet Manager modules composed through normal
role selection, installation, supervision, credential providers and optional
plugin/Worktree Manager clients. An installed client or enabled plugin does not
host a service, enroll a machine, authorize a publisher or create another
supervisor. Independently runnable services and standalone clients remain
supported without Worktree Manager or Copilot.

The Gateway module consumes the selected shared Gateway and connector
distribution, existing routing/authentication owners and explicit transport
policy. No relay product is mandatory: native OpenSSH, a supported Bastion path
or another selected provider must qualify its own contract without a silent
fallback. The Vault module composes its existing custody and service lifecycle;
it does not create a new credential issuer, export long-lived secrets or turn
fleet membership into credential authority.

| Operation | Retained owner or prerequisite |
|-----------|-------------------------------|
| Release selection and complete role closure | #6240's existing contract; pure release-selection data is not publication |
| Generic artifact publication | Explicit participant and operation reservation still required; unassigned |
| Ordered construction, admission, lifecycle and later setup-hook wiring | `wobbly-contraption`, within retained #6424/#6240 scope |
| Routing, authentication, enrollment and provider interoperability | Existing #5789/#6443/#6804 owners |
| Target-local profile emission, source audit and canonical aliases | Existing agent-ssh owner |
| Operation admission, uncertainty and receipts | Existing #6241 owner |

Before effectful integration, the artifact publisher and setup consumer must
agree the actual release/source seam: selected immutable input, role/platform
closure, provenance and verification, supported distribution and compatible
rollback. Record the participant and reservation here when accepted. Historical
Python artifact pipeline failures under #6535/#6632 or trusted-control ownership
under #6237 are not current generic transport-artifact receipts or an appointment.
Do not infer custody from their mere presence or create another publisher to
fill the gap.

Adopter-private generic prototypes migrate into the shared implementation and
are retired only after qualified replacement, preserved state, reconnect and
compatible rollback. Retain the working predecessor until those gates pass;
removing configuration or a requirement does not authorize immediate teardown.
Complete Chat implementation remains with #7238 and its existing service,
client and plugin owners, not a second backend in this integration slice.

### Accepted source-leaf boundary

The policy leaf is pure, dependency-free decision data, not another topology
store, installer or admission authority. The existing configuration owner
projects declared canonical machine/environment keys and controller references
into a bounded `fleet-manager.gateway-ssh-policy` version-1 document.

The proposed concrete API is:

```python
GatewaySSHPolicy.from_dict(value) -> GatewaySSHPolicy
GatewaySSHPolicy.resolve(machine, environment) -> SSHPolicyDecision
GatewaySSHPolicy.policy_digest -> str
SSHPolicyDecision.to_dict() -> dict
SSHPolicyDecision.decision_digest -> str
```

The policy retains immutable normalized canonical bytes and its revision/digest.
Rows select `gateway`, `dtssh` or `cloudflare`, an exact execution environment,
independent logical SSH `inbound` and `outbound` permissions, and an explicit
`interactive`, `service`, `client-only` or `offline` role. A Gateway row also
selects a declared controller and whether Gateway connector egress is allowed.
This egress permission is **separate** from permission to initiate SSH sessions.

A constrained guest can select a declared `host_hop` instead of local Gateway
egress. Reject ambiguous local-plus-host paths, unknown references, cycles,
cross-controller mediation and a client-only role that requests inbound SSH.
An offline role retains its selected configuration but requests no effects.
Missing target declarations, unsupported fields and malformed values fail
explicitly; no alias guessing, network lookup or transport fallback occurs.

The returned decision identifies requested/effective directions, controller,
host path, policy revision/digest and a configured/offline/disabled/blocked
reason. It always carries false execution-authorization and live-readiness
claims. The runtime participant must separately check trusted current policy,
authenticated selected identity and explicit effect authorization; a durable
admission or policy DTO is not that authorization.

The construction owner retains ordered setup-hook integration after its
existing construction/admission validation. Neither leaf may invoke an
installer, enroll, authorize keys, restart a service or announce a machine
onboarded merely because its source is published.

### Configuration and transport authority

- An explicitly selected two-repository control plane carries its harness and
  knowledge roles together. This closes the repository-topology vision's
  capability-based relationships and honest authority/availability intent; it
  does not require every control plane to split its repositories.
- The additive declaration proposed under #7195 is:

  ```yaml
  control_plane:
    project: example-harness
    harness: example-harness
    knowledge: example-knowledge
  ```

  `project` is the legacy scalar launch projection for older daemons, not a
  second independently editable target. It must agree with `harness`. The
  knowledge role never becomes a standalone Bridge launch target.
- One typed reader owns pair validation. A native read-only resolver supplies
  the declared roles to normal setup/binding consumers. Reuse the existing
  binding owner to project the machine-local knowledge pointer and personal
  plugin composition; do not add a competing configuration writer. Explicit
  pair setup must also work when the repositories are registered but no
  machine-local harness configuration exists yet.
- Preserve a matching existing binding and refuse a conflicting one before
  effects. Keep canonical repository/account identity, knowledge-class
  worktree capability, paired-workspace ownership and ordinary publication
  gates. A binding repair must not convert a knowledge companion into an
  executable project.
- Legacy scalar/project-only declarations keep their supported self-hosted
  behavior without inventing a knowledge companion. New pair consumers reject
  malformed, incomplete or conflicting declarations explicitly; an older
  daemon ignoring new metadata is not evidence of successful pair binding.
- Keep #6300's caller/parent target-routing ownership and #6721's native
  Configuration preference-writer scope separate. Agree any exact shared-file
  overlap before implementation. Concrete adopter configuration and fleet
  rollout remain with the adopter's existing owners and authorization.
- Resolve the exact machine and execution environment before selecting a
  controller. A friendly alias, shared hostname or SSH reachability does not
  grant another execution space's ownership.
- Bind the admitted target to one configured Gateway/controller identity and
  transport-provider instance. Keep this binding distinct from a credential
  request's controller selection.
- A fleet-owned Gateway transport adapter publishes the existing agent-ssh
  `module.yaml` and normalized registry contract. The data plane may use native
  reverse SSH or another explicitly selected provider; consumers use canonical
  machine aliases, not implementation-specific suffixes.
- Agent-ssh remains the only managed profile writer. Do not duplicate its
  registry, Include, source-audit or alias-exclusivity rules.
- Declare logical inbound peer/service permissions, allowed outbound Gateway
  paths and optional host-mediated guest hops separately. Reverse connectivity
  does not imply a public inbound listener or unrestricted forwarding.
- Client-only, interactive-session, headless-service and constrained-guest roles
  have explicit supported behavior. An unavailable or unsupported role is not
  silently omitted or given another transport as an unapproved fallback.
- User-owned interactive connections follow the user's logon lifetime; no
  prelogin, SYSTEM or privileged service account is required. Separately adopted
  service-host roles retain their own availability contract. Normal provider
  issuance/relay renewal connects authorized donors and consumers without
  copying accounts, widening direction policy or granting new identity rights.

### Setup, persistence and reconciliation

- The existing role lifecycle owner provides the setup entry point. Selecting
  the Gateway role in approved desired configuration requests onboarding;
  installing the thin client alone does not start servers or enroll a host.
- Resolve and validate platform, architecture, exact interpreter/executable,
  approved source/artifact, selected bootstrap identity reference, host pin and
  external authentication before an effect.
- Use only fixed named target-local capabilities admitted through existing
  operation/authentication authorities. No message-selected shell, executable,
  URL, script or hidden configuration payload.
- Persist distinct outcomes for prerequisites, admitted enrollment, public
  directory revision, authorized peer policy, rendered profiles, native
  registration, authenticated canonical routes and completed convergence.
  Installation is not connection, and connection is not convergence.
- Bind receipts to target, desired revision/digest, owner generation and actual
  installed artifact. Store secret references and bounded evidence, never
  private keys, bearer values or credential-cache copies.
- Reconcile at supported startup, reconnect and desired-revision boundaries
  through the existing service owner. Do not add per-session prompts, another
  daemon, or a parallel polling scheduler.
- A public-directory revision change invalidates only dependent authorization
  and profile work. Catch up through the native authorization owner and
  agent-ssh emitter without repeating bootstrap, replacing keys or restarting
  an unrelated healthy service.
- Reuse existing ledger/journal uncertainty semantics. After interruption or a
  lost response, inspect the original operation and actual effect before
  another mutation. A transport timeout is not proof of failure or success.
- Preserve stable launchers, logon/service registrations, compatible state and
  last-known-good rollback. Intentional retirement of another transport is a
  separate, explicitly selected operation after authenticated cutover.

### Mitigation classes

| Failure class | Required owning mitigation and persistent evidence |
|---------------|---------------------------------------------------|
| Platform/identity declaration incomplete | Refuse before allocation/effect; report exact execution-space mismatch and use the registry owner's reviewed correction |
| Missing tool, stale PATH or different architecture | Resolve the actual supported executable and owner prerequisite; record selection instead of assuming one installation path |
| Signing service stopped or selected signer unavailable | Report the selected identity's usable-signing gate; do not enumerate other keys or repeat identity enrollment |
| Provider login or subscription admission absent | Distinguish missing admission from network failure; request only the selected provider's normal local authentication |
| Launcher fails after bootstrap prerequisites succeed | Preserve completed stages and the exact native error; test the real consumer boundary instead of resetting working identity |
| Peer enrolls after an earlier machine was configured | Versioned public-directory catch-up and native authorization/profile reconciliation |
| Generated fragment differs from its declared source | Preview and re-render through the existing emitter, preserve unrelated effective routes, then perform the guarded canonical transaction |
| Partial install or caller/process cleanup | Keep stage receipts and generation-pinned lifecycle ownership; prove durable registration and health after caller exit |
| Logout, sleep, offline host or guest without egress | Apply the declared availability/hop policy; do not label expected absence as drift or create replacement capacity |
| Output arrives but SSH command does not finish | Require expected identity plus exit status and EOF; preserve an unresolved channel-completion failure rather than count partial output as PASS |

## Plan

### Phase 1 - Reviewed integration and ownership

- [x] Reconcile this proposal with the existing parent plans and current source.
- [x] Obtain the retained construction/admission owner's exact policy/runtime
  leaf interface and exclusive-file agreement.
- [x] Turn available adopter evidence into public synthetic failure fixtures;
  retain private machine inventory only in its private elaboration.
- [x] Clear this proposal's normal review/publication gate before runtime work.
- [x] Adopt #7513's reviewed portable ownership, module and qualified-migration
  intent without a duplicate vision or private engine.
- [ ] Obtain the actual generic artifact publisher participant/operation
  reservation and its accepted #6240 release/source seam before setup wiring.
- [ ] Review the coherent integration-contract amendment against retained
  lifecycle, routing, authentication, credential and Chat scopes.

### Phase 2 - Driver and directional policy

- [x] Publish the accepted pure per-target policy leaf and focused installed
  consumer contracts; source merge does not close configuration or setup.
- [x] Review and implement the explicit harness/knowledge declaration,
  matching legacy launch projection and normal new-machine binding consumer
  under #7195, preserving the separate #6300 routing reservation.
- [ ] Implement the agreed per-machine/environment Gateway/controller binding
  and ingress/egress/hop policy in the owning configuration contract.
- [ ] Implement the fleet-owned external agent-ssh provider adapter using the
  existing normalized registry and one canonical alias owner.
- [ ] Keep existing static/direct and unrelated transport behavior unchanged
  unless an explicit migration selects a replacement.

### Phase 3 - Actual setup and durable convergence

- [ ] Wire the accepted role setup entry point to admitted target-local
  lifecycle/enrollment/authorization/profile effects.
- [ ] Persist stage outcomes, desired/applied digests, owner generation and
  uncertain-operation reconciliation through the existing authorities.
- [ ] Implement owner-managed startup/reconnect catch-up, no-op repeat,
  compatible update/rollback and explicit availability outcomes.
- [ ] Update setup/help documentation to match actual supported behavior.

### Phase 4 - Qualification and controlled adoption

- [ ] Exercise the validation matrix below with actual consumer boundaries.
- [ ] Record published, installed and live-qualified revisions separately.
- [ ] Transfer any unavailable live lane to a named tracked objective; do not
  silently remove a declared machine/environment from acceptance.

## Validation Plan

- [x] Pair/schema: explicit roles, matching legacy projection, malformed and
  conflicting declarations, unchanged legacy scalar/project-only behavior,
  and no knowledge-only executable target.
- [x] Fresh native setup with registered repositories but no local harness
  config; actual binding/pair creation, matching-binding no-op and
  conflicting-binding refusal through the existing owner.
- [x] Old/new consumer compatibility: an older daemon reads the same scalar
  harness target, while new consumers retain the knowledge companion and
  separately report unsupported binding/projection capabilities.
- [ ] Unit/schema: controller/provider selection, mutually exclusive canonical
  aliases, directional allow/deny rules, constrained guest hops and unsupported
  role refusal.
- [ ] Artifact/source boundary: require the accepted publisher reservation and
  actual selected role/platform/provenance input; reject data-only admission,
  source merge or isolated image proof as publication or execution authority.
- [ ] Module composition: Gateway/Vault use their existing installation,
  supervision and credential owners; thin-client/plugin/Manager selection has
  no implicit hosting, enrollment, credential export or alternate executor.
- [ ] Native Windows and Linux first-touch; include architecture/interpreter
  differences and a thin-client-only negative test with no hosting effects.
- [ ] Real provider module -> registry -> agent-ssh emitter/audit -> ordinary
  canonical SSH expected identity, exit status and EOF.
- [ ] Real admitted controller-to-target capability execution; prove acceptance,
  effect and receipt separately, including stale generation and auth refusal.
- [ ] Restart/reconnect at each stage; directory catch-up, idempotent rerun,
  mixed offline outcomes and uncertain/lost results without duplicate effects.
- [ ] Preserve keys/pins, unrelated effective profiles, registrations and data;
  prove failed candidate rollback and post-caller lifetime.
- [ ] Clean-room and available live lanes; mark synthetic versus real evidence
  explicitly, including unexercised suspend/logout and channel-close cases.
- [ ] Verify private policy is not embedded in public examples, tests or docs.

## Proposal

The proposal is reviewed and merged. Accepted policy/runtime source leaves are
being implemented independently; shared setup integration remains with its
retained owner. No managed setup/apply, enrollment, live rollout, lifecycle
takeover or implementation completion is asserted.

## Journal

### 2026-10-16 - Intake and current boundary

- Captured the operator request and confirmed the effort slug.
- Reused existing transport-provider, lifecycle and named-reconciliation work
  rather than proposing another controller or profile writer.
- Current setup/execution interface and retained source ownership remain an
  explicit implementation dependency, not an invented completed capability.

### 2026-10-17 - Reviewed source-leaf implementation

- Proposal #7056 merged after its current-head required CI passed. The retained
  construction owner accepted the exclusive policy/runtime source leaves and
  keeps shared construction, admission, execution and lifecycle integration.
- Published the exact version-1 decision interface in #6681, comment
  `6227785664`. The policy implementation preserves that interface and adds
  synthetic directional, constrained-host, offline and malformed-input cases.
- The runtime owner opened #7083 against the same interface. Its publication
  is not a setup hook, external transport adapter or live deployment.
- The policy leaf passed 253 selected policy/release-selection contracts in
  the contained standalone runner, including a rebuilt installed package and
  fresh native isolated consumers. Explicit negative controls keep denied or
  blocked intermediate hosts from widening inbound permissions.
- F/E9 lint, whitespace and install-contract checks passed. No installer or
  first-provision path changed; full clean-room provisioning and live SSH
  effects remain with the separately admitted adapter/setup integration.
- Included the required generated `fleet-manager` patch changefile: its
  coupled standalone content participates in the repository release guard.
  No shared manifest or version number is edited manually.

### 2026-10-18 - Policy merged and paired control-plane amendment

- Policy #7087 actually merged at
  `e1ddb33e9e17ccfadc06e98df7b3913d52a40471` after fresh current-head review,
  CI and thread checks. Its source PR claim is released. No setup, runtime
  adoption or completed integration is implied.
- The operator clarified that projection to a new machine must retain both
  harness and knowledge repository roles, including when local binding
  configuration is absent, and explicitly required older-daemon compatibility.
  This is a generalized summary; adopter identities and rollout authority
  remain outside this public effort.
- Reserved #7195 for the additive declaration and binding-projection contract.
  This amendment must clear the normal review gate before implementation;
  existing routing, configuration, lifecycle and operation owners remain.

### 2026-10-19 - Paired declaration implementation qualification

- The source merge's exact `dev` CI completed successfully. Normal promotion
  run `39216459826` then completed green with all expensive validation,
  publication and merge stages **skipped**, because trusted Main is explicitly
  paused under #6237. Skipped promotion is not release evidence. Main at
  `a325f5063e7cc0a2b3276be81be3e4959f8f4651` still publishes
  `harness-knowledge` `0.1.1-dev1`, whose binding script has neither the
  preservation capability probe nor its result field. Generic dependency
  evidence is recorded in #6237, comment `6262596752`; release availability
  remains with that retained containment/qualification owner. No pause bypass,
  qualification dispatch, candidate installation or alternate writer is implied.
- Implementation #7205 actually merged to `dev` at
  `991cfd8e23750b3c82af92fd537034b1914b5da8` on reviewed head
  `df6f6fd15baba90a7e8f9fde2efdaed6df167a52`. Current-head Copilot review
  approved with no open findings; all 49 observed checks completed without a
  failure, including the existing hosted Linux native guard. Every review
  thread was resolved before the normal managed merge. This closes only the
  declaration/binding source item under #7195, not release, installed adoption
  or the provider/setup/lifecycle integration.

- The operator-authorized continuation reconciled the retained Configuration
  context and verified #6725's actual merge. The narrow #7205 overlap now reuses
  the existing shared execution-root classifier in `repos._current_platform`;
  inherited WSL labels cannot select a different registry path from the binding
  reader. Ordinary registry mutation, serialization, preferences, accounts and
  routing writers remain unchanged. Broader Configuration and installed
  acceptance stay with the canonical Manager workbench plan.
- The platform-reader follow-through passes 228 selected native tests:
  first-touch binding/pair creation (2), ordinary registry behavior (135),
  all Configuration cases (83), and registry transactions (8). Windows
  Configuration validation uses disjoint selections within unchanged item and
  group limits; a combined group exceeded its wall-clock budget and was not
  treated as a pass. First-touch Git fixtures now have ordinary fetched origin
  refs and explicit remote HEAD, avoiding repeated missing-ref fallback probes;
  the native creation call defers unrelated empty instruction projection work.
  Actual registration, installed CLI binding, pair writes, tracked resolution
  and common-dir checks remain real. Hosted Linux must exercise the inherited
  label with a Linux-only registration before merge.
- The next current-head review identified the coupled explicit-name binding
  path, which still chose a platform independently. The existing native
  inventory now reports additive `platform` metadata; both binding modes consume
  and validate that owner result, and contradictory/malformed values refuse
  before registration. Inventories from older owners without that field retain
  their legacy behavior. All 146 binding tests pass, including native platform
  selection and invalid-metadata negatives. All four real first-touch cases
  pass: declared-pair and explicit missing-registration flows, each with ordinary
  and conventional bare anchors. Both ordinary cases participate in the same
  existing hosted guard; no topology is required by explicit registration.
- Subsequent review found that a changed companion registration between pair
  preflight and the writer could still leave a pointer after reporting failure.
  The preserving flow now rejects its fresh non-ready inspection before any
  binding write. Removed/repointed registration cases cover fresh and existing
  configuration, including bytes and timestamp preservation. All 150 binding
  tests and 35 selected native inventory/first-touch cases pass. The additive
  inventory platform is included in all exact strict/legacy output snapshots;
  a red hosted snapshot assertion was fixed, not bypassed.
- Amendment #7198 actually merged as
  `1e313d5ee1f3cd688c95d53de56d7548878ee878` before implementation.
  The shared typed reader retains the legacy scalar launch target and both
  repository roles. Bridge derives only the executable harness.
- The new native read-only front door works without local binding state and
  reports the shared native execution platform. The existing binding writer
  consumes it, validates canonical classes, Git roots and the native config
  namespace, refuses inherited or local binding conflicts, and preserves a
  matching pointer's bytes and timestamp. Ambiguous YAML names remain strings.
- Contained qualification: shared-library suite 321 passed, Bridge selection
  and legacy regression 223 passed, binding suite 113 passed, native reader
  and actual first-touch integration 15 passed. The latter uses actual local
  Git repositories, the installed native CLI entry point, the normal binding
  script, actual harness creation and companion common-dir checks. It covers
  ordinary and conventional `.git`-backed bare anchors with worktree-local
  `core.bare`; no AI process is launched.
- Unit and simple native consumer tiers ran. Fresh temporary configuration
  provides a first-touch integration lane, not a full stock-OS installation
  proof. No Linux container engine or admitted live external target is
  available in this source workspace; hosted Linux CI and separately owned
  live adoption remain explicit gates. No target runtime update, service
  restart or fleet configuration deployment is implied.
- One independent read-only implementation review found no high-confidence
  defect. Its bare-anchor qualification question led to the additional
  actual conventional bare-anchor consumer lane. The unchanged item timeout
  remains enforced; native registry setup uses its real owner API rather than
  paying for repeated setup-only CLI startups.
- Implementation is merged to `dev`; release and installed adoption remain
  pending. These focused
  validation items do not complete the parent integration, provider, setup,
  admission, lifecycle or controlled-adoption plans.

### 2026-10-21 - Reviewed portable intent and integration operation boundaries

- #7513 merged the existing Machine Fleet vision amendment as
  `a2aaad649fab7c98cbfd4a7079e9503f4a0c3f64` after exact-head approval and
  required CI. This effort consumes that canonical intent rather than opening
  another vision or implementing a replacement controller.
- Retained `wobbly-contraption` construction/lifecycle custody identifies the
  setup consumer, not a generic artifact publisher. The publisher participant
  and operation reservation remain an explicit pre-execution gate.
- Inspected #6240's pure release-selection receipt and the historical
  #6535/#6632 pipeline failure records; none establishes current generic
  Gateway/OpenSSH/Bastion artifact publication. #6237's trusted-control role
  likewise does not appoint this slice's publisher.
- Reused the existing Gateway source and native qualification/readiness
  boundaries. Separate exact-source native images are useful prior art, not
  released artifact, managed installation or live adoption proof.
- The binding implementation is now published in harness-knowledge
  `0.1.2-dev1`; its effect-free version-1 preservation/pair capability is
  available. Earlier paused-Main and unavailable-1.1 journal entries remain
  historical. Installed selection and actual adopter rollout remain separate.
- Reconciled Gateway/Vault module consumption, user-logon availability,
  independent directions, normal credential renewal and qualified prototype
  retirement while retaining existing source and effect owners. No executor,
  artifact signing grant, enrollment, credential action or rollout occurred.
