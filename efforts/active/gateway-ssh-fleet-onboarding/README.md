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

## Participants

| Participant | Role | Reached via |
|-------------|------|-------------|
| `ticking-wrench` | Evidence-to-contract plan and proposed integration slice | #6681 and this effort |
| Existing #6030 / #6240 / #6424 owners | Construction, role lifecycle and installation | Retained owner accepted the exclusive source leaves; ordered shared wiring remains with that owner |
| Existing #5789 / #6443 / #6804 owners | Routing, authentication, enrollment and Gateway integration | Their existing issue threads |
| Existing #6241 owner | Operation admission and receipt authority | Existing Fleet Control V2 coordination |
| `wobbly-contraption` | Retained construction/admission/lifecycle integration | Existing #6030 / #6240 / #6424 source custody |

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

## Context

The existing agent-ssh external transport-provider contract already supports
provider-owned modules and normalized registries. Agent-ssh owns profile
emission, Include coexistence, source auditing and SSH verification. Its
`fleet-targets` snapshot is configuration evidence, not enrollment or execution.

The current fleet-manager surfaces distinguish planning, authenticated artifact
staging, operation journals and credential-controller subscriptions from
managed role installation and desired-state execution. A setup flow cannot
truthfully be assembled by renaming those existing surfaces as apply.

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
- [x] Obtain the retained setup/construction/admission/execution owners' exact
  interface and exclusive-file agreement.
- [x] Turn available adopter evidence into public synthetic failure fixtures;
  retain private machine inventory only in its private elaboration.
- [x] Clear this proposal's normal review/publication gate before runtime work.

### Phase 2 - Driver and directional policy

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

- [ ] Unit/schema: controller/provider selection, mutually exclusive canonical
  aliases, directional allow/deny rules, constrained guest hops and unsupported
  role refusal.
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
