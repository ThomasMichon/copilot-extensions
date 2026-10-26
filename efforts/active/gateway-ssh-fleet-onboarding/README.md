# Gateway and SSH Fleet Onboarding

- **Slug:** `gateway-ssh-fleet-onboarding`
- **Repo:** copilot-extensions
- **Branch(es):** managed source `ticking-wrench`
- **Created:** 2026-10-16
- **Status:** Active
- **Umbrella issue:** #6681
- **Sub-issues:** #5861, #8888; prerequisite owners #6030, #6240, #6424, #5789, #6443, #6804

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
| `ticking-wrench` | Completed integration proposal, policy/pair prerequisites and bounded artifact-assembly source slice | Merged source; remaining integration backlog is #6681 / #6030 |
| Existing #6030 / #6240 / #6424 owners | Construction, role lifecycle and installation | Retained owner accepted the exclusive source leaves; ordered shared wiring remains with that owner |
| Existing #5789 / #6443 / #6804 owners | Routing, authentication, enrollment and Gateway integration | Their existing issue threads |
| Existing #6241 owner | Operation admission and receipt authority | Existing Fleet Control V2 coordination |
| `wobbly-contraption` | Retained construction/admission/lifecycle and setup-consumer integration, not generic artifact publication | Existing #6030 / #6240 / #6424 source custody |
| `crooked-circuit` | Bounded SSH-service peer-approval source, client contract and isolated qualification | #8888; [peer-approval plan](peer-approval.md) |
| `squeaky-buckle` | Completed bounded foreground Fleet SSH directory/local-writer consumer after merged #8000; no installation or deployment | Merged #8910; bounded source reservation settled |
| `restless-actuator` | Non-editable installed SSH runtime and source-denying native consumer qualification | #6681 source reservation6397956631 |
| `patient-bearing` | Operator-allocated continuity-backed public-key machine enrollment and normal Manager/engine setup consumer | #6681 source allocation6408998175; [implementation boundary](continuity-bootstrap.md#selected-executable-slice) |

## Coordination

- **Topology:** independent worktrees and per-slice PRs, after explicit interface
  and file agreement.
- **Host:** `ticking-wrench` owns the proposal and bounded assembly/publisher
  integration source, not another participant's runtime lifecycle, operational
  signing custody or deployment.
- **Delegates:** no new runtime writer is assigned by this plan.
- **Handoff:** name the accepted owner, exact scope and unresolved validation;
  source publication, installed state and live convergence are separate facts.
- Keep the Linux Role Fleet and Fleet Control V2 plans canonical for their
  lifecycle, release and operation contracts. This effort owns only their
  Gateway/SSH onboarding integration and validation matrix.
- No edits to reserved installer, authentication, ledger, controller or shared
  CLI files before their retained owners agree the integration seam.
- The operator subsequently allocated the bounded missing first-touch enrollment
  operation and its normal setup consumer to `patient-bearing` within those
  existing authorities. This is the narrow exception recorded in
  #6681 comment6408998175, not a transfer of broader construction, publication,
  lifecycle, provider or SSH custody. Review the selected executable slice
  before changing runtime semantics.
- The bounded normal SSH consumer continuation is now selected under #6681:
  `squeaky-buckle` drives new `ssh_machine.py`/`ssh_machine_config.py`, focused
  tests/docs and narrow `serve --role ssh-client`/optional-closure wiring.
  This is an explicit source slice, not takeover of retained worktrees,
  construction/admission or operational authority. Existing shared lifecycle,
  provider, credential and deployment gates remain with their owners.
- `restless-actuator` owns only the existing standalone SSH preparer's
  non-editable dependency selection, focused preparation tests and a new
  source-denying installed consumer witness. It does not select a release,
  approve an artifact/feed, add an installer or wire normal role lifecycle.
- The construction owner accepted two exclusive new source leaves:
  `fleet-manager/src/fleet_manager/gateway_ssh_policy.py` and its matching
  `test_gateway_ssh_policy.py` belong to `ticking-wrench`; the separate
  `gateway_ssh_runtime.py` and matching tests belong to the existing runtime
  integration participant. Shared CLI, staging, admission, manifests and
  lifecycle files remain with the construction owner. Source-leaf acceptance
  is not deployment or an implemented setup hook.
- The artifact-assembly/publisher-integration source appointment belongs to
  `ticking-wrench`, reserved under #6681/#6030. Its first new source/test/docs
  seam produces the existing role/release schema from actual artifact bytes.
  Shared verification, staging, client admission, construction, default CLI,
  publisher workflow and agent-ssh files remain outside that reservation.
- Privileged publication must use an existing approved mechanism with compatible
  provenance verification. Source appointment, construction lineage, pure
  policy/runtime leaves and client admission do not grant a signing purpose,
  new credentials or permission to bypass a refused proof method.
- Use existing owner contacts for operation reservations. Publishing a contract
  amendment does not transfer the source or effect authority described by those
  reservations.
- The closing source participant releases its worktree responsibility to the
  existing tracked objective #6681. Broader publication/proof interoperability,
  provider/setup/lifecycle and live adoption remain in this active plan and
  #6030/#6240/#6424/#5861; no replacement writer or runtime authority is inferred.

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

The [continuity-backed first-touch amendment](continuity-bootstrap.md) adds
README/harness-context selection, already-authenticated user continuity,
scoped Gateway enrollment before SSH and separate ordinary human sessions.
It preserves the retained owners below and the separately selected
[peer-approval policy](peer-approval.md); it is not delivered setup behavior.

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
| Artifact assembly and publisher-integration source | `ticking-wrench`, reserved under #6681/#6030 |
| Privileged publication and signing | Existing approved mechanism only; verify the actual compatible release/proof seam before invoking it |
| Ordered construction, admission, lifecycle and later setup-hook wiring | `wobbly-contraption`, within retained #6424/#6240 scope |
| Routing, authentication, enrollment and provider interoperability | Existing #5789/#6443/#6804 owners |
| Target-local profile emission, source audit and canonical aliases | Existing agent-ssh owner |
| Operation admission, uncertainty and receipts | Existing #6241 owner |

Before effectful integration, the appointed source owner and setup consumer must
agree the actual release/source seam: selected immutable input, role/platform
closure, provenance and verification, supported distribution and compatible
rollback. Source assembly uses the existing `fleet-manager.release` schema;
it does not require a new consumer protocol. The existing approved Python
publisher's hosted attestation and the Fleet verifier's pinned-key-only support
remain a concrete proof-compatibility boundary, not permission to select another
method or create a signer. Historical
Python artifact pipeline failures under #6535/#6632 or trusted-control ownership
under #6237 are not current generic transport-artifact publication receipts.
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
- [x] Reserve the appointed artifact-assembly/publisher-integration source
  participant and exact new-file seam under #6681/#6030.
- [ ] Verify the approved publication mechanism and compatible #6240
  release/source/proof seam before privileged publication or setup wiring.
- [x] Review the coherent integration-contract amendment against retained
  lifecycle, routing, authentication, credential and Chat scopes.
- [ ] Review and implement the [continuity/bootstrap contract](continuity-bootstrap.md)
  through the retained installer, credential/enrollment and human-session
  adapters; preserve existing machine-proof and peer-approval requirements.
- [x] Review the [selected executable slice](continuity-bootstrap.md#selected-executable-slice):
  explicit public-key machine authentication, a real current-policy producer
  and original-operation enrollment in the existing journal, followed by the
  normal Manager/engine setup consumer. No new issuer or parallel registry.

### Phase 2 - Driver and directional policy

- [x] Implement the first bounded artifact-to-release producer in the existing
  SDK and qualify actual source-independent package/role-consumer behavior.
  This does not sign, publish, authenticate or activate a release.
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

- [x] Implement the allocated public-key receiver/current-policy/journal and
  normal Manager/engine local-projection consumer; qualify the actual synthetic
  source/process/TLS path. Installed distribution and native/provider/live
  acceptance remain open below.
- [x] Implement the bounded explicit foreground Fleet SSH-directory consumer,
  composing the existing authenticated client and local profile/login-key
  writers with native lease and shutdown custody. This is source capability,
  not managed setup, enrollment or an adopted installation.
- [ ] Wire the accepted role setup entry point to admitted target-local
  lifecycle/enrollment/authorization/profile effects.
- [ ] Consume the harness-selected continuity source and delegated first-touch
  or confirmation path before assuming a Gateway connection or SSH transport.
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

### Explicit peer enrollment approval

The selected policy is **`explicit-peer-approval`**. The detailed decomposition
in the [peer-approval plan](peer-approval.md) is agent-recommended realization,
not installation, credential or live-adoption authority.

- [x] Review the #8888 vision/contract amendment before implementation:
  #8894 merged at `1fcf4f5f16926762e3a85ef799f55eaf02aeb81a`.
- [x] Implement optional exact-fingerprint peer admission and separate explicit
  bootstrap/recovery in the existing SSH service, preserving its automatic
  configured-binding baseline when the policy is absent.
- [x] Qualify the actual client/Gateway/store contract, restart, original-request
  recovery, stale/unauthorized decisions and mixed-version refusal.
- [x] Merge the bounded source and record its retained release/adoption boundary;
  do not mark the broader setup or live mesh items complete.

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
- [ ] Qualify the [continuity bootstrap validation](continuity-bootstrap.md#validation-obligations),
  including source/account/grant refusal, Gateway-first targets, pairing
  recovery and the separate normal human-session boundary.
- [ ] Qualify the selected public-key profile and actual source-to-setup-to-Gateway
  path, including restart/lost replies, current-source revocation and rollback
  floors, private local key custody, mixed-version refusal and unchanged token
  profiles. Installed and live acceptance remain distinct.
- [x] Qualify the bounded POSIX source path with synthetic delegation, real TLS,
  original-operation recovery, private-file custody, unchanged active channels,
  ordered publication, mixed-version refusal and two-harness state isolation.
  This does not qualify source-denying installed distribution or native providers.
- [x] Qualify the Linux noneditable Fleet SDK closure with checkout access
  physically refused before imports, through the real synthetic TLS CLI and
  Manager/engine consumer. Restart the receiver at the same origin/journal and
  retain the original machine key and operation. Manager/engine installation,
  native providers, released construction and live adoption remain open.
- [x] Peer-approval selection: pending public identities never enter active
  directory or native relay authorization before exact authenticated approval;
  explicit recovery cannot become a missing-approver fallback.
- [x] Peer-approval compatibility: absent policy retains the old contract;
  incapable or rolled-back providers refuse the required policy before effects.

## Proposal

The proposal is reviewed and merged. Accepted policy/runtime source leaves are
being implemented independently; shared setup integration remains with its
retained authority, with the bounded operator allocation above. No managed
setup/apply, enrollment, live rollout, lifecycle takeover or implementation
completion is asserted.

## Journal

### 2026-10-26 - Source-denying installed continuity SDK qualified

- #9066 merged to `dev` as `a15737268e591255b077bea514bb2e3e0ed0f551`.
  Exact head `6b777583bda552793cb3e6275777bc26d681b389` received Copilot
  approval `5650118978` with no open findings; all required checks passed
  before the normal managed Maintainer completion.
- Re-read the full parent Plan and Validation Plan. Resolve only the bounded
  Linux installed-SDK item: the final contained packet passed all 11 cases,
  including an intentional source-import refusal, actual CLI and source
  Manager/engine consumers, and same-origin receiver/journal restart with the
  original key and operation. This does not install the Manager/engine product
  or qualify real continuity providers, native Windows/macOS or live enrollment.
- Continue through the retained release/construction owners for the selected
  engine's supported service-Gateway SDK closure. The independently bootstrapped
  Komodo substrate has its own reviewed #8860/#8872 source appointment under
  #6030; do not replace it with a second installer or count its plan as execution.
  Human sessions, actual role setup/lifecycle, published/installed revisions and
  live adoption remain open. Neither #6681 nor #6236 is complete.

### 2026-10-26 - Executable first-touch source merged; installation stays gated

- #9047 merged to `dev` as `528c8b1c53d00ff8c561f78324f5b951339eaf71`.
  Exact head `33f1782779388e0335605dad5bd23ed1e5651bfa` received a
  current Copilot Comment with no open findings; required CI passed and all
  review threads were resolved before the normal managed Maintainer merge.
- Re-read this full Plan/Validation Plan. Resolve only the bounded source and
  synthetic qualification items above: 643 affected Fleet cases passed, followed
  by 57 engine/setup cases with 23 platform skips and eight continuity controls.
  The normal Manager -> engine -> isolated SDK -> real TLS path is exercised,
  not replaced by an injected enrollment result.
- The retained construction/release owner must supply Fleet's service-Gateway
  SDK closure in the selected engine environment. No unsupported cross-project
  dependency, private installer, new token issuer or second journal was added.
  Continue installed source-denying/native qualification and owner-approved
  provider/human-session/setup/lifecycle integration under the existing trackers.
  #6681 and #6236 remain open; no runtime update, live enrollment, deployment
  or predecessor retirement occurred.

### 2026-10-25 - Public-key enrollment implementation plan reviewed

- #9039 merged the selected executable slice to `dev` as
  `f1f5a3862c3a5ee30969a147349eda354357dab2`, after exact-head Copilot
  approval with no open findings and required successful CI.
- Re-read the full Plan and Validation Plan and resolve only the selected
  executable-plan review item. Public-key authentication, actual enrollment
  publication/journal recovery and normal Manager/engine setup consumption
  remain to implement and qualify; no runtime or installed gate closes.
- Continue the allocated receiver and current-policy/operation integration
  before migrating its normal setup caller. No live credential, enrollment,
  installation, service, network or deployment effect occurred.

### 2026-10-25 - Continuity bootstrap and human-session contract source

- #9038 merged the three governing vision refinements and proposed
  continuity-bootstrap contract to `dev` as
  `5bd6275d19f409ed4e6515eec5d86c1f7040414e`, after current-head Copilot
  approval and required CI. The parent Plan/Validation Plan remains open.
- The amendment adds no executable setup, verifier, issuer, enrollment or
  provider behavior. Existing peer approval and retained implementation owners
  remain unchanged. The next slice is the normal installer/adoption and
  receiving identity/enrollment integration, not a new private installer or
  live adoption inferred from a documentation merge.

### 2026-10-25 - Peer-approval source merged; integration remains open

- #8917 merged to `dev` as `e5ec299aceb81808a987e8752fa47566a630ae23`
  at 13:34:11Z. Exact submitted head
  `ccd78d077dea205091d515e89a38f4ef3b1930b7` received approval
  `5643362836`; all three review threads were resolved and required CI passed.
- Both full native service suites passed on that exact head in
  [40197079356](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40197079356):
  Windows control-plane and Linux service/native-relay qualification. These
  are disposable authenticated Gateway/CLI fixtures, not installed adoption.
- The rebuilt local focused packet passed all 55 enrollment cases. Seven
  installed production modules matched source. The local whole-suite startup
  timeouts under measured CPU saturation were not relabeled passing or waived:
  the same unchanged deadlines passed in the pinned full native suites.
- Re-read the Plan and Validation Plan and resolved only the four explicit
  peer-approval source/qualification items. The parent remains Active.
  Release, authentication/setup composition, provider interoperability, actual
  fleet adoption and predecessor retirement remain with #6681 and its existing
  owners; this source slice transfers its remaining responsibility there.
- No installation, runtime update, credential action, fleet deployment or
  serving-predecessor retirement occurred. Source merge is not release or
  evidence that a running deployment enforces the selected policy.

### 2026-10-24 - Foreground Fleet SSH consumer source

- Added the explicitly selected `serve --role ssh-client` path and strict local
  configuration, with packaged runtime contract metadata and an optional
  dependency closure. Default installation/planning stays thin and inert.
- The fixed adapter composes the existing authenticated directory client,
  credential references, profile writer and target-login authorization owner.
  Repeated cancellation retains the watch and native lease through settlement;
  restart revalidates the current snapshot and existing writer floors.
- Focused local qualification covers schema/refusal behavior, real local writes,
  restart/no-op, lease/signal custody, missing dependencies and existing Gateway
  runtime compatibility. Installed CLI fixtures exercise the real disposable
  TLS/JWT/DPoP Gateway and registered service, including a connected later join
  and offline catch-up with a second consumer. The Windows process fixture
  qualifies abrupt kernel-lease recovery, not graceful Windows signal delivery.
- This source does not install or update a managed role, enroll a machine,
  acquire/rotate keys, renew a provider, change sshd, deploy or retire a
  predecessor. General node hosting and the broader Plan/Validation items stay
  open; release/artifact selection and actual adoption require their owners.

### 2026-10-25 - Explicit peer-approval source

- #8894 merged the scoped intent and plan after approval and required checks.
  The selected policy extends the existing service; automatic configured-binding
  admission remains the absent-policy baseline.
- Implemented durable pending public candidates, exact authenticated peer
  decisions, separately configured confirmed recovery, and the supported
  runtime-selected client contract. The original store owns activation,
  historical decisions, generation checks and original-request recovery.
- The native Windows contained suite passed 116 tests with six explicit
  Linux-only skips. Real TLS/JWT/DPoP and the installed CLI's retained-state
  restart are exercised with synthetic identities. All five installed changed
  source bodies match the candidate. Linux native/disposable
  qualification and implementation review/publication remain outstanding.
- No live installation, credential access, provider/setup takeover or
  production migration is included. Broader integration and live adoption
  remain with #6681 and its retained owners.
- Review of #8917 found that an embedded near-limit directory could strand
  committed outcomes and a maximum-size pending list could exceed its frame.
  The new unreleased receipt now retains the decision's original directory
  revision instead of embedding the global catalog, and checks its complete
  encoded size before commit. Pending inspection uses byte-bounded `after` /
  `next` pages without dropping candidates or reducing the binding limit.
- The review-corrected Windows suite passed 120 tests with six explicit skips;
  four focused wire-limit/transaction controls and the installed CLI's signed
  paging/restart control also passed. All five changed installed source bodies
  match the candidate. The earlier exact-head Linux/Windows run
  [40176923839](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40176923839)
  passed on `a8e7d19df91f7b8fed0e05541803996d0b83ac6a`; these corrections still
  require fresh current-head review, required CI and native qualification.
- The next review identified a retained outcome synthesized after a policy
  round trip without persisting its decision. Observation now repairs that
  receipt transactionally, reuses the original decision for equivalent request
  aliases, and rolls back if the observing request is withdrawn.
- The rebuilt Windows suite passes 127 tests with six Linux-only skips,
  including four retained-history controls and real-process metadata controls.
  The offline distribution check also passes with its unchanged ten-second
  CLI deadline; metadata parsing no longer imports runtime-only dependencies.
  All six installed changed production modules match the candidate and Ruff
  passes. The preceding head passed both hosted native jobs in run
  [40182383847](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40182383847);
  this corrected head still requires fresh review, CI and native qualification.
- Review of the next head found an intrinsically invalid inbound candidate
  could be journaled without its required host pin. Validation now refuses
  that candidate before recording its request, and likewise refuses a relay
  key identical to its own client key. Corrected original-ID retries remain
  usable in both modes; outbound-only clients may still omit host pins.
  The host-pin controls first reproduced the defect against the preceding
  installed wheel. The rebuilt final Windows suite passes 133 tests with six
  Linux-only skips; Ruff passes. The preceding head passed both native jobs
  in run [40187342145](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40187342145).
  Fresh current-head review and qualification remain required before merge.
- The rebased review found a one-byte terminal-page edge: `null` can exceed a
  one-character continuation cursor. The regression reproduces an actual
  32,769-byte terminal response against the 32,768-byte transport bound through
  the client/service path. Page construction now budgets both representations.
- Qualification of the newly merged Fleet consumer also exposed its frozen
  Gateway clock versus a restarted realtime CLI. Both native restart fixtures
  now share the existing realtime-clock helper. Invalid local writer scopes
  refuse before credential-runtime imports; an import guard reproduces and
  protects that ordering without changing the CLI's fifteen-second deadline.
- All 55 focused enrollment regressions pass against the rebuilt provider,
  including the exact transport boundary. The latest local whole-suite run
  passed 139 cases with six Linux-only skips but hit four native startup
  deadlines while aggregate host CPU remained at 100%, including an unchanged
  foreground-custody fixture. No timeout or authentication allowance was raised.
  Fresh pinned Linux/Windows whole-suite qualification remains a merge gate.

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

### 2026-10-21 - Integration amendment reviewed and tracker aligned

- #7537 merged as `0430fbc259153bd90105e386703aaba0ee496306` after
  exact-head approval `5595997783`, required CI and no open findings.
- Re-read the Plan and Validation Plan after merge and resolved only the
  contract-review item. The artifact publisher participant, accepted release
  seam, setup wiring and actual qualification remain open.
- Aligned #6681's title and body with the reviewed portable module/transport
  contract. No relay product is mandatory and no new source, signing,
  credential, enrollment or deployment authority is assigned.

### 2026-10-22 - Artifact assembly and installed consumer qualification

- Implemented the reserved unprivileged `release_assembly` producer in the
  existing SDK. It validates and snapshots explicit contracts and complete role
  closures before artifact access, hashes the bounded actual streams, and emits
  existing canonical schema-v1 release bytes without writes or publication.
- Refreshed onto current source, including the existing compatible optional
  dependency floor. Normal governed standalone preparation now succeeds;
  no feed, seasoning, dependency-set or timeout override was introduced.
- All 31 focused assembly cases pass. The existing release/artifact
  compatibility selection passes 116 cases with one native FIFO case skipped
  on Windows. A real built wheel is installed offline into a fresh target;
  copied SDK/library source is removed before isolated producer and all three
  role consumers run. Link/tamper refusal, package removal and unrelated user
  state preservation pass. Synthetic SSH/Bastion bytes are not runtime proof.
- Required unit-packet execution exposed a Windows framework-bootstrap defect:
  pytest's existing Colorama I/O initialization attempted its native load only
  after unit observation started. The narrow correction initializes that
  framework adapter before observation; unit-side native-load rejection is
  unchanged. Added admitted platform/error cases and a fresh native negative
  counterpart, which passes against a real attempted Windows DLL load.
- The exact admitted packet now passes all 759 cases with per-unit production
  call coverage, no skips and no forbidden effects. Platform-dependent
  open-flag IDs are semantic, and the descriptor mock uses a portable opaque
  bit. The runner is an explicit required coverage target for its new cases;
  no existing node, target, observation or timeout check was removed.
- Source review and merge remain pending. Local unit evidence is not Main
  qualification, artifact publication or installed fleet adoption.
- The existing committed-SDK roster guard caught the producer missing from the
  Gateway witness's closed-world source materializer. Included only this module
  in that existing roster; the same guard checks the exact committed closure,
  without changing Docker, execution privileges or witness grants.
- Existing release/authentication/staging/admission, default CLI, publisher
  workflow, credentials and SSH registry/profile owners remain untouched.
  Hosted-attestation versus pinned-key verification interoperability is still
  a separate pre-publication gate, not a reason to create keys or another proof
  method. No setup wiring, live service, enrollment or deployment is claimed.

### 2026-10-22 - Bounded source completed; remaining integration stays tracked

- #7848 merged at `5f13a07986365de28ba912ad912947e435385ccc`,
  10:32:01Z, from head `725878f823243f558ab154d06bf8e4bcb0f8534b`.
  Current-head review `5612711282` approved; the sole Low documentation-impact
  finding was addressed in the description and its thread resolved. Refreshed
  required checks passed before the normal managed self-merge.
- Reread this Plan and Validation Plan and resolved only the bounded
  artifact-to-release producer/package-consumer item. The source includes the
  unchanged canonical manifest contract, actual byte integrity and installed
  first-touch coverage, plus the narrowly required Windows unit-bootstrap and
  closed-world witness-roster corrections.
- The source worktree's remaining effort responsibility transfers to the
  existing tracked objective #6681 for the still-open integration items.
  #6030, #6240/#6424 and #5861 retain their existing deployment, lifecycle and
  reconciliation scopes. This records backlog custody, not another contributor's
  acceptance or an invented implementation owner.
- Keep the shared effort Active. Source completion does not establish release,
  authentication/proof compatibility, managed setup, live transport, migration
  or fleet adoption. No keys, credentials, installation, runtime update, service
  restart or deployment were performed by this source slice.

### 2026-10-25 - Foreground SSH consumer publication follow-through

- #8910 adds the explicitly selected Fleet SSH directory subscriber after
  #8000. Source review exposed unsupported cross-plugin UV references; the
  optional SDK names now use normal dependency metadata and standalone test
  preparation selects the first-party packages explicitly. The actual release
  materializer regression passes without a plugin-vendoring exception.
- The isolated native run found a coupled elevated Windows authorization-file
  owner check. The correction accepts only OS-default privileged file owners
  under the unchanged protected current-account directory and closed user/SYSTEM
  grant policy; foreign owners/grants still refuse, with no ACL repair.
  The corrected published head still owes both native platform lanes.
- Local focused Fleet/Gateway/SSH/materializer checks passed 72 cases; the
  installed real TLS/DPoP service packet passed 24 and affected preparation
  families passed 10. These are disposable source/package fixtures, not a
  production deployment or complete source-free shared-library closure.
- Managed publication hit its unchanged 30-second transport deadline because
  a timed-out live identifier sweep waited on descendant-held pipes. Reused the
  canonical caller-owned capture helper with its unchanged 10-second sweep
  deadline. All identifier sources, configuration-failure/partial-entry rules
  and both independent publication guards remain enabled. Missing capture code,
  output overflow and unconfirmed cleanup fail closed; cleanup errors retain
  exact custody instead of masquerading as optional-source absence.
- The 63-case identifier/publication packet passes, including native
  parent-exits/descendant-holds-pipes retirement with a birth-pinned observer.
  The same-input identifier guard body fell from 42.266 to 12.674 seconds.
  This measures deadline recovery, not a successful complete live sweep:
  the optional producer still times out and contributes no additional entries.
  Review, required current-head CI and the corrected native lane remain open;
  this shared effort and its release/setup/adoption obligations stay Active.
- Reconciliation with the concurrently merged #8855 retains its equivalent
  caller-owned sweep, no-self-provision environment, Windows batch quoting and
  32 MiB/1 MiB stream limits. This branch adds canonical-source verification,
  explicit missing-helper refusal and exact cleanup-error custody, plus the
  birth-pinned inherited-pipe control; it does not replace those upstream fixes.
- Review follow-through reproduced the stale Fleet-only preparation assertion
  and corrected it to the explicit SSH/lease source contract. Reject all three
  output destinations inside the normalized lease-state tree before effects;
  unit controls cover equality, nesting, normalized aliases and sibling names,
  and installed CLI controls prove refusal before credentials/state creation.
  The complete preparation/scope/authorization packet passed 126 cases with
  six native-platform skips; the installed connected/restart/refusal file
  passed all six cases locally.
- Exact-head native run40185328158 passed Linux's 78 service cases (seven
  platform skips) and two real StrictModes/login cases, but both new Windows
  connected/restart cases still refuse an authorization descriptor. The earlier
  privileged-file-owner correction is not a qualified Windows fix. Added bounded
  descriptor-shape diagnostics that redact every account SID; the owner/grant
  policy is unchanged while this failure is diagnosed. No blind retry, ACL
  relaxation or production repair is authorized by that observation.
- Diagnostic run40186763879 identified the actual remaining mismatch:
  Windows renders the exact current account SID as `LA` in both owner and
  grants. Normalize the expected current SID through the same native
  owner-only SDDL round trip before comparing. This is not a RID-based guess
  or blanket acceptance of `LA`; foreign owners and extra group grants still
  refuse, and the protected directory/current-account policy is unchanged.
  The 55-case Fleet/authorization packet and six rebuilt installed-role cases
  pass locally, including real native SID formatting and private ACLs.
  Corrected hosted Windows qualification and fresh review are still required.
- Exact-head run40187652857 qualified that correction: Windows passed 82 cases
  with six platform skips; Linux passed 81 with seven platform skips plus two
  real StrictModes/login cases. Every selected native job/step succeeded.
  Subsequent reconciliation preserves #8953's independently merged
  configured-only publication policy: live discovery is now an explicit manual
  audit and size enforcement stays in CI. This branch does not restore retired
  push scans or opt out of any current gate. The reconciled identifier packet
  passed 65 cases with one POSIX-only skip on Windows.
- Followed through the body-only review observations as well: preflight the
  exact source/lock directories derived by the canonical local writers, not
  just the three configured destinations. Fleet calls that adapter-owned check
  before policy/credentials/lease effects; an older partial adapter refuses
  before effects. Failure-path test teardown now settles all retained tasks,
  process, diagnostic and rig even if one owner fails.
  The 69-case Fleet/authorization/adapter packet and nine rebuilt installed
  consumer cases pass locally. Final-head native/CI/review remain pending;
  earlier native success is not substituted for the newer source.

### 2026-10-25 - Non-editable installed runtime qualification

- The preceding foreground source is delivered by merged #8910 at
  `6254e0f9eb49231db76f23da54fe7dc5223f753e`. Final-head native
  run40189515793 passed Windows85/6 platform skips, Linux84/7 platform skips
  and both real Linux StrictModes/login cases before merge. Its review and
  required checks passed; that settles only the preceding bounded source.
- Follow-through found a narrower qualification gap: the standalone SSH
  preparer still allowed editable shared-library dependencies, while its
  installed-origin assertion covered only Fleet and agent-ssh. Selecting
  `--no-editable` in that existing guarded preparer keeps the disposable
  runtime's entire dependency graph installed, without a new resolver policy.
- The new witness starts the actual foreground CLI without site startup or
  `.pth` processing, admits only the selected environment's package locations,
  checks every installed distribution's file inventory and non-editable
  metadata, and refuses source-checkout reads outside that exact environment.
  An intentional source import is refused by the same guard. The existing
  connected/later-join and disconnected/restart scenarios exercise real
  authenticated transport and native local writers; native OS identity/ACL
  helpers remain allowed.
- Local Windows qualification passed all 88 service cases with six native
  platform skips, including the three new cases. The affected preparation
  packet passed 73 cases with six platform skips. Hosted exact-head Windows
  and Linux, current-head review and required CI remain publication gates.
  This is installed-runtime evidence, not authenticated release-artifact
  admission, managed setup, live fleet adoption or predecessor retirement.
