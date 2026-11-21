# Local Service Transport

## Goal

Several service-bearing plugins still bind a **fixed loopback TCP port** and
lean on hand-maintained deconfliction (a reserved port, a Windows/WSL split or
"+1" offset). Per the `plugin-services` vision's **`minimal-network-exposure`**
behavior and the `docs/patterns/service-transport.md` +
`docs/patterns/local-endpoint-discovery.md` patterns, migrate them onto the
transport ladder: an **OS-native local endpoint** (Unix domain socket / named
pipe) by default; an **OS-assigned loopback port advertised through a rendezvous
(port-mapping) file** when a socket is genuinely unavoidable; and a **tunnel over
an already-trusted transport** to cross a host/WSL boundary - never a new fixed
inbound port.

- **Slug:** `local-service-transport`
- **Repo:** copilot-extensions
- **Branch(es):** Independent reviewed component slices
- **Created:** 2026-11-20
- **Status:** Draft
- **Vision:** [Plugin Service Model](../../../visions/plugin-services/README.md)
  `discoverable-local-endpoint`, `collision-free-endpoints`,
  `endpoint-discovered-not-assumed`, `minimal-network-exposure`,
  `standalone-reachability` and successor-preserving endpoint ownership
- **Umbrella issue:** #54
- **Sub-issues:** #5904 retains its independent parser/cleanup tracking and
  already-received #5903 scope; #4040's received Bridge restriction remains
  an unresolved policy choice under #54

## Guiding Intent

Separate transport choice from endpoint discovery and discovery from permission
to mutate an advertisement. A dynamic TCP endpoint is a valid justified fallback,
not proof that native-first migration is complete. A shared publication lock
prevents cooperating writers from interleaving; it does not establish ownership
of a present malformed or unreadable record.

This is a vision-closing plan for existing public requests, not a new
zero-TCP policy, service registry, mandatory broker or cross-host mesh.

## Participants

| Participant | Role in this effort | Reached via |
|---|---|---|
| Planning coordinator | Maintains the direct requirement map and planning publication | #7556 |
| Transport and service owners | Retain Dispatch, Vault, Bridge and affected client implementation | #54 and existing component reservations |
| Rendezvous owners | Retain shared-library, copied-reader and cleanup integration | #5904 |
| Policy owners | Resolve the received Bridge restriction without silently weakening either contract | #54's #4040 intake |

## Coordination

- **Topology:** Independent per-component PRs after planning acceptance.
- **Host (owns PRs):** The admitted owner of each implementation slice; this
  proposal reserves no implementation.
- **Delegates:** Existing component, reader, installer, policy and resource
  owners remain unchanged. No new executor or venue is selected.
- **Handoff:** A slice supplies its exact source/consumer and validation result.
  Publication, source acceptance, release, installed behavior and resource
  settlement remain distinct.
- **Tracking:** #54 remains the transport canonical. #5904 remains independently
  OPEN for the complete parser and uncertain-ownership cleanup outcome. This
  plan does not override either source's current tracking or custody conditions.
  Normal domain-plan review is planning acceptance, not a claim release.
- **Independent lanes:** The #5904 parser/cleanup lane can progress without
  completing Dispatch/Vault native migration. Bridge's policy decision blocks
  the conflicting Bridge policy change, not unrelated discovery repairs.
  #5716's pipe/process lifetime, #5791's account execution and #4183's persistent
  duplex transport remain with their actual separate owners.

## Context

#54 already owns the native-first ladder, shared atomic/stale-aware rendezvous
helper, Dispatch and Vault consumers, trusted host/WSL crossing and accurate
architecture documentation. Its received #4040 request requires exclusive
Bridge loopback binding and rejection of wildcard binds, while the current
transport/discovery patterns permit deliberately selected wider reach. That
choice has not been resolved by this proposal.

#5904 already owns the complete #5903 tolerant-reader family alongside endpoint
cleanup. Its current discussion credits #7074's common runtime-local OS lock
and atomic publication/cleanup repair, and #1424's PID guard. It retains
present-but-unknown ownership, tolerant shape validation, affected copies and
actual shutdown callers. Those credited implementations are not proposed again.
Historical test/native results remain attributed reports, not fresh execution
or installed conformance.

The reported gaps are source-request premises, not a new current-code absence
claim. This planning unit did not repeat the product audit. An implementation
slice must credit the actual current foundations before changing its owner.

The governing patterns are
[service transport](../../../docs/patterns/service-transport.md),
[local endpoint discovery](../../../docs/patterns/local-endpoint-discovery.md)
and [contract evolution](../../../docs/patterns/service-contract-evolution.md).
The transport pattern's constrained reverse-SSH listener exception applies only
within existing finite permissions; it is not an ordinary fixed-port fallback
or permission to broaden a listener grant.

## Request

The Goal preserves #54's published Summary, with an ASCII dash substituted for
its final typographic dash. Its four original scope items and received #4040
tasks map directly to Phase 1 below.

#5904's original required outcome is retained here:

> Make cleanup conditional on the same owned publication identity at mutation
> time, using the service's shared publication/lifecycle authority or an
> equivalent reviewed atomic protocol; another repeated read is not sufficient.

> Preserve a successor's record and retain uncertainty when ownership cannot
> be read/validated. Do not equate malformed/unreadable state with permission
> to erase it.

> Preserve explicit owner cleanup, conservative stale detection, standalone
> per-service discovery, immutable-generation cutover and cross-platform behavior.

> Test publisher/cleanup interleavings against the real file protocol plus each
> affected caller/copy; no live host-service race reproduction is needed.

Phase 2 also directly retains its full #5903 intake. Source discussions remain
historical evidence, not a new grant to run their examples or change a service.

## Scope Approvals

This publication is research and planned tracking under #7556. No implementation,
listener/configuration change, credential or connectivity operation, installation,
test execution, cleanup, service/process action or source/resource transfer is
authorized by creating or reviewing this plan.

## Plan

### Phase 1 - Existing transport and policy contract

- [ ] **P54-H:** Reuse the shared write-on-bind/client-resolve helper and its
  transport, endpoint, PID and start-time convention, atomic publication and
  stale detection. Preserve existing independent service runtime ownership;
  no required shared registry, reverse proxy, broker or new resident helper.
- [ ] **P54-D:** Complete Dispatch's native local endpoint and actual client
  path: UDS on Linux/WSL and named pipe on Windows, OR a documented genuinely
  necessary OS-assigned LOOPBACK+rendezvous fallback. Cross host/WSL boundaries
  through an opt-in already-trusted tunnel, not a fixed shared-loopback port or
  platform offset. Preserve truthful unsupported/bind/discovery outcomes.
- [ ] **P54-V:** Complete Vault's Windows named-pipe/native-first default and
  discovered transport/endpoint instead of a fixed loopback assumption. Retain
  a durable discovered WSL-to-host path rather than brittle interop probing.
  Existing authentication, credential, service and client owners remain.
- [ ] **P54-Docs:** Reconcile the architecture ports table and affected client/
  service documentation with each actually delivered migration. Preserve
  standalone reachability and the separate cross-host mesh boundary; do not
  report all-service/native conformance from helper or ephemeral-bind credit.
- [ ] **P4040:** Resolve the actual policy alternative through its existing
  owners before changing Bridge policy. Retain the received request's generated,
  default and installer binding exclusively to `127.0.0.1`, server rejection of
  `0.0.0.0` and `::`, SSH-forwarded-only remote-reach documentation and installer
  regression, AND the current local-first/explicit-wider pattern pending that
  decision. Client probe normalization and authentication are not server-side
  bind enforcement. No live exposure or trust change follows from this plan.

### Phase 2 - Independent complete rendezvous contract

- [ ] **P5904-Read:** Validate decoded top-level object, alternate-entry and
  field shapes BEFORE field access or coercion in the canonical and affected
  tolerant readers. Preserve normal malformed/unsupported/unavailable diagnostics,
  not `AttributeError`, broad exception swallowing, payload/secret logging or
  fabricated successful endpoints. Preserve strict authorization/readiness,
  ordinary unconfigured legacy migration and scope: malformed PRESENT scoped
  state must not grant default or cross-cell reach. Credit #1284's separately
  completed strict Vault/Container parser without reopening it.
- [ ] **P5904-Clean:** At the existing publication/cleanup mutation authority,
  establish the SAME owned publication identity when cleanup mutates. Preserve
  the common stable runtime-local OS lock, atomic write/cleanup, successor
  records, legitimate explicit owner cleanup and conservative stale handling.
  Genuinely absent state is a harmless no-op; PRESENT malformed, unsupported or
  unreadable ownership stays UNKNOWN. A tolerant `None`, another ownership read
  or serialization alone is not erase permission. An equivalently reviewed
  atomic protocol is allowed; no new PID-incarnation design, unnecessary second
  external startup lock or parallel lifecycle authority is required.
- [ ] **P5904-Copies:** Reconcile the actual canonical, Dispatch, Index and
  Vault copies/callers as applicable to each lane, including Dispatch/Vault
  shutdown. Preserve per-service fields, standalone discovery, immutable-
  generation cutover and cross-platform behavior. An unused helper or a parser-
  only repair is not whole cleanup consumer acceptance.
- [ ] **P-Skew:** State each changed discovery/transport boundary's supported
  reader/provider contract and real selected-runtime discovery. Expand providers
  before migrating callers, retain supported old/new and rollback behavior, and
  refuse unsupported required semantics truthfully. Do not infer support from
  package equality or add a new per-call preflight process. Old noncooperating
  endpoint writers require a compatible owner-managed transition, not another
  unprotected read or a universal all-services rollout prerequisite.

## Validation Plan

- [ ] **G54:** Cover the actual shared publication/client resolution/stale
  convention and each selected Dispatch/Vault native/default/client path on its
  supported platform. Exercise a changed endpoint without client edits, justified
  dynamic-loopback fallback, host/WSL crossing and literal bind failures with
  bounded fixtures. Record unavailable native/installed evidence separately;
  no universal live fleet or every-plugin test is added.
- [ ] **G4040:** Validate the attributable policy decision against BOTH retained
  constraints, generated/default/installer output, supplied-bind server refusal,
  architecture security documentation and the installer regression. Neither
  a loopback health probe nor transport authentication establishes binding policy.
- [ ] **G5904:** Exercise malformed JSON top-level/alternate/field values through
  actual resolver/consumer boundaries and fail-closed scoped discovery. At the
  real file protocol, cover publisher/cleanup interleavings and affected callers/
  copies, PRESENT invalid/unreadable/unsupported versus absent state, valid owned
  cleanup and a readable different successor. Retain the existing Windows/POSIX
  protocol controls and delivered #7074/#1424 safeguards. No live service race,
  universal installed rollout or unrelated lifecycle benchmark is required.
- [ ] **GSkew:** Cover supported old caller/new provider, new caller/old provider,
  new/new and provider rollback at the affected real serialization/transport
  seam. Verify meaningful unsupported outcomes and no silent weakening of
  required scope, authorization or cleanup semantics. Keep required PR CI
  focused; larger native/operational work retains its own admission.
- [ ] **GCustody:** Read back the complete current original/refined contracts
  and these direct Plan/global Validation/Coordination clauses after planning
  delivery. #54 and #5904 remain OPEN; no source retirement or claimant handback
  is proposed here. Preserve every actual reader, cleanup, service, installer,
  policy, source and resource owner, reservation, independent pause and hold.
  Source, reported historical tests, newly executed tests, release and installed
  behavior must not be conflated.

The installer-facing Bridge slice follows
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement):
incoming readiness and all current selections define completion, while outgoing
recovery and cleanup remain independent. Conservative endpoint-record cleanup
does not become a stale-incumbent veto on an otherwise authorized replacement.
No replacement, process termination or deployment is performed by this plan.

## Proposal

Use this single plan for #54's transport migration and #5904's independently
tracked rendezvous lane. All fourteen technical rows remain unchecked.
Planning acceptance supplies neither implementation nor resource authority.

## Journal

### 2026-11-20 - Complete existing-contract planning proposal

- Captured the whole current #54/#4040 and #5904/#5903 contracts with direct
  Plan/global Validation and unchanged component ownership.
- Retained #7074/#1424/#1284 delivery at its reported scope, the unresolved
  Bridge-policy choice and both OPEN source trackers. No new implementation,
  native test, service, endpoint, cleanup or rollout result is claimed.
