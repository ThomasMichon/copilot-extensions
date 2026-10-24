# Conventional Fleet Connectivity

- **Slug:** `conventional-fleet-connectivity`
- **Repo:** copilot-extensions
- **Branch(es):** Plan PR, then serial independently reviewed slices against `dev`
- **Created:** 2026-10-18
- **Status:** Active
- **Vision:** agent-ssh `conventional-repository-connectivity`, `derived-agent-roster`; agent-bridge `conventional-repository-discovery`; agent-fabric `derive-dont-duplicate`, `unattended-tiered-self-convergence`
- **Umbrella issue:** #7206 (bounded campaign under #63)

## Guiding Intent

One stable, repo-owned topology should produce the source machine's permitted
SSH profiles and let Bridge discover declared targets and their existing
agents/worktrees after basic installation. Conventional discovery must not
require a second topology registration, duplicate agent roster, or blind repair.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Plan owner | Proposal, integration and subsequent serial PR stewardship | Managed upstream worktree and #7206 |
| Session guidance implementer | Read-only source/profile snapshot and exact-session delivery under #7527 | Separate managed upstream worktree and #7527 |
| Operator | Plan review and explicit actuation/rollout authorization | Proposal PR |

## Coordination

- **Topology:** Proposal first; no runtime implementation before operator plan
  review and the normal plan PR review/merge gate.
- **Host (owns PRs):** Plan owner; implementation slice ownership is assigned
  through #7206 before edits, not inferred from this proposal.
- **Delegates:** None assigned. This proposal does not reserve peer-owned code.
- **Session guidance slice:** #7527 owns the read-only snapshot and writer in a
  separate worktree. It may consume the existing source contract but does not
  take profile refresh, provider mapping, Bridge discovery or identity-migration
  ownership. Review this additive slice before implementation.
- **Handoff:** Resume this canonical README and the issue, then claim one slice.
- **Confirmed boundary:** The configuration-consolidation owner retains
  canonical source/configuration consolidation and per-environment bindings;
  this campaign consumes those declarations for profile refresh and discovery.
  Existing topology remains at `.agent-worktrees/machines.yaml`; migrated
  repository policy remains in `.copilot-extensions/<owner>/`.
  A separately owned, default-off per-module maintenance-safety primitive is
  consumed rather than reimplemented here.
- **Existing owners:** [machine-transport-convergence](../machine-transport-convergence/README.md)
  retains identity/consumer convergence (#5737, #5973). #7050 retains Bridge SSH
  profile precedence; #2530/#4179 retain session/listing contracts. Consume or
  coordinate those contracts, do not reopen their implementations here.
  [machine-fleet-routing-foundation](../machine-fleet-routing-foundation/README.md)
  and [gateway-ssh-fleet-onboarding](../gateway-ssh-fleet-onboarding/README.md)
  retain fleet driver/enrollment authority; no new Gateway requirement.

## Context

Read-only baseline: `dev` at `d2639ee0f`, checked 2026-10-18.

- [`docs/configuration.md`](../../../docs/configuration.md) documents canonical
  `.copilot-extensions/<owner>/` configuration, marketplace overlays, legacy
  compatibility, repo topology and machine-local state. Reuse these owners and
  current formats, not a new campaign-specific config tree.
- [`mesh_refresh.refresh_mesh`](../../../plugins/agent-ssh/src/agent_ssh/mesh_refresh.py)
  gathers only `dtssh_alias` entries and returns early without them; direct/LAN
  topology does not currently receive this refresh path.
- [`mesh.find_machines_file`](../../../plugins/agent-ssh/src/agent_ssh/mesh.py)
  checks Git-root/ancestor `machines.yaml`, not
  `.agent-worktrees/machines.yaml`. Bridge adoption recognizes the latter.
  Existing source-location asymmetry is a gap, not an acceptable new convention.
- [`emit-profile`](../../../plugins/agent-ssh/src/agent_ssh/__main__.py) already
  renders a normalized registry with the direct module. The
  [provider contract](../../../plugins/agent-ssh/docs/transport-provider-contract.md)
  and [registry schema](../../../plugins/agent-ssh/contract/registry-record.schema.json)
  exist; the gap is conventional topology-to-profile convergence, not a missing
  direct SSH renderer.
- [`agent_registry.build_resolver`](../../../plugins/agent-bridge/src/agent_bridge/agent_registry.py)
  derives remote machines from configured topology profiles and independently
  discovers local agents. With neither, it returns no resolver. Thus an empty
  profile set does not imply no local discovery, but supplies no declared peers.
- [`config.adopt_topology`](../../../plugins/agent-bridge/src/agent_bridge/config.py)
  can find conventional topology and persist a user profile. The
  [machine guide](../../../plugins/agent-bridge/docs/machine-config.md) still
  requires that explicit adoption path; this is not automatic cold-start discovery.
- [`self_update_dtssh.refresh_dtssh_mesh`](../../../plugins/agent-machines/src/agent_machines/self_update_dtssh.py)
  is the existing maintenance adapter to extend, not a reason for another timer.

## Request

Public-safe request capture, summarized with private identifiers and operational
context redacted; no private transcript is published or linked:

> Keep all declared machines in the repository's current standard configuration,
> discoverable by agent-ssh and agent-machines. A quick maintenance refresh should
> converge each host's client profiles across declared transport providers,
> including direct and multiple execution environments, without regressing dtssh.
> After basic installation, Bridge should derive targets and existing active
> agents/worktrees without bespoke manual registration. Retain explicit overrides,
> standalone operation, provider boundaries, and separate discovery from mutation.
> Publish and review the canonical plan before runtime implementation.

## Plan

### Phase 1 - Review the source and authority contract
- [x] Obtain operator review, then satisfy the normal plan PR review/merge gate (#7217).
- [ ] Confirm supported topology fields, conventional locations, package discovery,
  source-machine key-path references, provider selection and explicit override
  priority against the peer-owned configuration contract before coding. Reuse
  existing module/normalized-registry formats when expressive; any proven missing
  capability needs one generic extension reviewed with that owner, not a second
  format or independent configuration-consolidation slice.
- [ ] Define a read-only resolved-source view with provenance: explicit selection
  wins; conventional repo/state-root fallback cannot override an explicit invalid
  selection. Ambiguous sources and unavailable owners report actionable errors.
  Stable anchors must survive disposable worktree removal; installation-cell and
  marketplace boundaries must not be crossed by PATH or global-root guesses.
  Cover `.agent-worktrees/machines.yaml` compatibility and the peer's selected
  current canonical location without independently redefining precedence.

### Phase 2 - Converge permitted outbound profiles
- [ ] Map declared topology through existing normalized registry/provider recipes,
  preserving logical machine keys, exact environment aliases, source-specific
  key paths, host-key policy and existing user profiles. Resolve client identity
  and key-path policy for the source machine/OS, not from a target's private-key
  location; preserve distinct Windows, Linux and WSL routes. Never read private keys.
- [ ] Extend quick refresh and the existing opted-in maintenance adapter across
  selected providers. Retain live dtssh discovery, bounded probes and native/WSL
  distinctions; a working native route must not imply a sibling route is ready.
- [ ] Keep discovery/probe read-only; make profile writes an explicit command or
  already-authorized maintenance action. Retain report-first diagnostics, durable
  source provenance and atomic fragment publication on validation failure.

### Session snapshot - Declared topology and this client's profiles

Additional operator direction, captured without downstream-specific identifiers:

> We would expect SSH guidance to be dyanmically computed as session-guidance by
> the agent-ssh addon, given the machines config and the generated ssh profiles on
> the current machine.

Execution structure below is agent-recommended:

- [x] Review and merge this additive plan before implementing #7527 (#7530).
- [ ] Add a read-only snapshot using the existing topology parser and managed
  fragment classifier. Resolve the caller's conventional source and, when the
  authoritative optional Worktrees peer declares an external state root, compose
  its topology without guessing private binding paths or redefining source
  priority. Preserve selected-source errors and same-installation peer refusal.
- [ ] Distinguish declared aliases/readiness, generated fragment coverage and
  provenance, and unknown connectivity. Missing generated coverage must not mean
  a host is down; a file on disk must not imply an active effective route.
- [ ] Deliver a bounded, fresh exact-session snapshot through the existing
  writer, preserving the command catalog and atomic replacement. Reuse the
  payload's no-self-provision boundary: absent runtimes or optional peers remain
  explicit unavailable evidence, not implicit setup or another installation.
- [ ] Prove no network probes, OpenSSH `Match exec`, profile generation, service
  startup or configuration writes occur during snapshot production. Use
  non-executing fragment validation; retain stale and indeterminate evidence.
- [ ] Update SSH operating guidance and record source merge, release and any
  separately authorized rollout without claiming the wider campaign complete.

### Phase 3 - Derive Bridge discovery without a second roster
- [ ] Discover conventional repository topology on cold start when no explicit
  profile controls the scope, deriving configured targets without writing user
  profiles. Manual `config adopt` or special topology registration is not an
  acceptable endpoint. Basic installation does not enroll machines or start agents.
- [ ] Read target-local repo inventories and existing worktree/session owners over
  authorized routes; compose their existing listing contracts rather than copy
  their registries. Distinguish declared targets, reachable hosts and active work.
- [ ] Invalidate derived views on source changes through existing Bridge lifecycle
  seams. Preserve explicit profiles/agents, standalone local/provider-only use
  and SSH-profile connection identity. Missing, malformed, inaccessible and
  confirmed-empty sources must remain distinct; no blind writes or restarts.

### Phase 4 - Prove, document and close
- [ ] Land scoped regressions and native isolated acceptance evidence below.
- [ ] Update configuration/adoption, provider, SSH refresh and Bridge operating
  docs with actual commands, precedence and permission boundaries once implemented.
- [ ] Record release versus authorized rollout separately; resolve every Plan and
  Validation Plan item before marking Done. Downstream inventories stay downstream.

## Validation Plan

- [ ] Session snapshot fixtures cover caller and external-state topology,
  absent/refused optional peers, local managed-profile coverage, stale and
  indeterminate sources, hostile configuration strings, output budgets and
  resume replacement. Exercise the actual writer/CLI boundary in isolated homes
  and assert it neither probes nor self-provisions; report unsupported runtime
  availability explicitly.
- [ ] Native isolated source-to-profile proof on Linux and Windows, with WSL
  environment coverage: use synthetic topology, redirected home/config/install
  roots, actual emitter and `ssh -G`; prove alias/user/port/key-path/proxy choices
  across source/target OS combinations and conflicting explicit overrides without
  contacting production hosts. Prove supported topology locations, including
  `.agent-worktrees/machines.yaml`, use the peer-owned resolution contract.
- [ ] Fresh-install fixture with conventional repo config and no Bridge adoption,
  manually authored agents file, or bespoke registration: prove cold-start target
  derivation and authoritative existing agent/worktree listings. Include optional
  siblings absent, empty topology and provider-only standalone cases.
- [ ] Read-only inventory/probe fixture through the native transport subprocess
  boundary: prove remote-local locations, active versus merely declared state,
  bounded unreachable results and no remote bootstrap or writes.
- [ ] Prove direct + dtssh + provider-contract compatibility, multi-environment
  identity, deterministic reruns, stale/removed/malformed sources, I/O uncertainty,
  disposable-worktree deletion, explicit invalid selection and installation-cell
  isolation; preserve last-known state only where the owning contract permits.
- [ ] Assert discovery performs no config writes/restarts and unauthorized refresh
  performs no actuation. Prove no sshd/key/firewall changes, private-key capture,
  provider enrollment, duplicate authority, or new scheduler. Run affected bounded
  suites and relevant clean-room scenarios during implementation, not this plan PR.

## Proposal

Extend existing owners and contracts; no replacement registry or coordination
service. The phase decomposition and fixture matrix are agent-recommended
execution structure for the requested outcome, not authorization to implement.

## Journal

### 2026-10-18 - Proposal
- Compared current `dev` source and public coordination with the requested
  outcome; retained existing peer ownership and claimed planning under #7206.
- Reconciled existing derived-roster/self-convergence intent and added minimal
  conventional-source/discovery intent to the two owning leaf visions.
- Runtime implementation, native acceptance and rollout remain unstarted.
- Confirmed the non-overlapping source/consumer boundary with the
  configuration-consolidation owner before publication. Provider-specific
  registry emission remains provider-owned; generic refresh must compose those
  recipes rather than introduce a parallel profile inventory.
- Proposal #7217 received an approving review and passing required checks,
  merged to `dev`, and the source worktree was reconciled through `pr-complete`.
  The next slice is the source-resolution contract and direct-profile mapping;
  the maintenance-safety primitive remains separately owned by #7213.
  No runtime capability or deployment is claimed by the proposal merge.
- Began the bounded conventional-source slice under #7206: reuse Bridge's
  existing repository candidate order in agent-ssh, honor the calling Git
  boundary, and fail explicit/selected invalid sources before transport
  discovery or profile writes. The direct-profile/client-identity mapping,
  state-root composition, Bridge cold-start discovery and rollout gates remain
  open. No schema, provider inventory or configuration authority is introduced
  by this slice.

### 2026-10-21 - Proposed exact-session snapshot slice

- Claimed #7527 after checking existing SSH work. The requested snapshot is
  complementary to #5973's identity migration and #4224's live-probe repair.
- Proposed consuming the reviewed conventional-source contract and this
  client's generated fragments without activating transports or changing
  profiles. This amendment does not claim runtime implementation or rollout.

### 2026-10-24 - Session snapshot plan accepted

- #7530 merged at `6feef3baeea32ea111e34d2e4f799510035aaa6f` after exact-head
  approval and passing required checks. Only the additive plan-review gate is
  complete; no runtime, profile, service or configuration change was performed.
- Re-read this complete effort after merge. Snapshot implementation and its
  isolated writer/CLI validation remain open under #7527; the wider campaign
  and separately authorized rollout remain open.
