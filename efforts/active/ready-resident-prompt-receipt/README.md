# Ready Resident Prompt Receipt

- **Slug:** `ready-resident-prompt-receipt`
- **Repo:** copilot-extensions
- **Branch(es):** Independent planning PR; separately admitted owner implementation
- **Created:** 2026-10-23
- **Status:** Draft
- **Vision:** [Worktree lifetime authority](../../../visions/plugins/agent-worktrees/README.md); [same-cell composition](../../../visions/plugin-services/installation-cells/README.md)
- **Umbrella issue:** #7942 (dependency slice only; does not take over the consumer)

## Guiding Intent

Let an already-running, correctly selected Worktrees resident record an optional
handoff prompt observation in process. Eliminate the observer's helper process,
not its ownership checks. Absence of a ready owner stays unavailable/pending:
the prompt callback neither provisions nor starts anything.

This closes the visions' existing attributable-observation and same-cell
composition intent; it does not extend service, credential or process authority.
The single-owner, bounded IPC and installation-source patterns in
`docs/patterns/README.md` and `docs/install-contract.md` continue to apply.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Dependency planner | This two-file proposal and its source review | This effort's planning PR |
| Integration owner | Selects the exact shared contract and implementation boundaries | Planning PR and implementation admission |
| Receipt owner | Existing receipt primitive, bounded storage and context-handoff consumer | #7942; [Prompt Receipt Observation](../prompt-receipt-observation/README.md) |
| Roster owner | Existing control/storage/entry reservations; interface coordination only | Existing roster work and its source review |

## Coordination

- **Topology:** Independent per-slice PRs. This proposal changes no runtime code.
- **Host (owns PRs):** Dependency planner owns this proposal; implementation
  owners remain separately assigned.
- **Delegates:** None launched by this plan. Receipt and roster owners retain
  their existing work, files and review obligations.
- **Handoff:** Parent/integration review of the exact contract **and** source
  plan review precede every functional change. A merged proposal is not
  implementation admission or completion of #7942.
- **Exclusive planning files:** This `README.md` and [contract.md](contract.md).
  Do not change the receipt owner's effort root/index row, consumer or backend.
- Current owner reservations and required refresh are in the contract's
  [file/claim table](contract.md#files-and-owner-reservations). No claim that an
  unreserved hunk is globally free is implied.

## Context

The receipt owner already has an atomic idempotent trace operation, but the
Node-to-Python helper boundary is not qualified. A ready interpreter descriptor
alone cannot establish custody of that helper. The resident's existing
authenticated control server offers a smaller direction: call the operation
directly in its existing Python process.

The source trace and proposed wire/delivery contracts are in [contract.md](contract.md).
The source currently exposes **no receipt control action, no ready-control
catalog projection and no Worktrees Node control client**. The proposal does not
claim a daemon is present, serving or deployed.

## Request

**Public-safe summary of the selected direction and constraints:** prefer the
existing resident with zero helper starts. Preserve authenticated **per-user**
local-control operator authority; SDK `session_id` is observation subject data,
not a new cryptographic caller identity. Validate exact project/worktree,
creation nonce and existing session or explicitly recorded successor
association, without imposing a head-only rule.

Reuse existing binding and exact-session catalog delivery through an optional
additive projection. Do not reinterpret shim-ready, guess homes, select a
previous owner, create a token, start/wake/provision a service or fall back to a
CLI. Use a closed query/record method calling the existing receipt primitive in
process. Preserve exact-identity reconciliation after possible commit, existing
admission/drain accounting and capability/version negotiation.

Bounded lock admission and total history scanning belong to the receipt owner.
A socket deadline cannot promise an fsync deadline or whole-operation cleanup.
Validate the actual Node/server call graph, zero helper starts, failure/replay
cases and the unchanged two-second whole boundary. No functional edits,
installed changes or rollout are authorized by this planning step. The SDK
Job alternative is not an admitted fallback.

The concrete names, byte ceilings and decomposition in `contract.md` are
**agent-proposed specifications**, not already accepted runtime APIs.

## Plan

### Phase 1 — Review the bounded owner contract

- [x] Trace existing control, binding, exact-session delivery and receipt code.
- [x] Obtain narrow interface/claim statements from the existing receipt and
  roster owners, without touching their files.
- [ ] Integration owner reads and admits the exact proposed API, delivery and
  file ownership in `contract.md`.
- [ ] Publish this planning-only change and clear source review/required CI.
- [ ] Refresh overlapping claims immediately before any implementation
  assignment. Existing claim statements are not perpetual edit permission.

### Phase 2 — Separately admitted implementation

- [ ] Receipt owner supplies incarnation-qualified idempotency, bounded lock/
  scan admission and honest commit-unknown reconciliation.
- [ ] Assigned control owner supplies the closed action, active-election/
  installation checks and drain accounting on the existing server.
- [ ] Assigned delivery owner supplies the optional existing-catalog projection;
  no new registry, credential cache or lifecycle path.
- [ ] Receipt owner supplies the asynchronous Node reader/client and switches
  only its observation path. Missing prerequisites remain pending.

### Phase 3 — Qualification and integration

- [ ] Execute the real synthetic-server/Node matrix below; measure all actual
  calls, side effects and process starts.
- [ ] Review and land functional changes through their owners' PRs; return
  integration evidence to #7942 without closing the parent goal prematurely.
- [ ] Leave live host qualification, release and rollout with explicitly
  assigned owners; this proposal does not authorize those operations.

## Validation Plan

- [ ] Source-plan structure, links, public-safe wording and scope checked.
- [ ] Real owned synthetic existing control server plus real Node sockets, with
  isolated installation/state fixtures and known cleanup.
- [ ] Wrong token, wrong source/installation, active/previous-route confusion,
  publication/runtime generation mismatch, malformed/versioned protocol and
  missing/old capability produce no effect.
- [ ] Exact registered subject and explicit pending successor accepted;
  unrelated inferred successor, unknown project/worktree, wrong nonce,
  removed association and revision changes refused or revalidated.
- [ ] Response loss after fsync, pre/postcommit timeout, cancellation, replay,
  source switch and drain races reconcile the same immutable receipt identity.
- [ ] Contended lock, long history, oversized record and storage/fsync failure
  remain bounded at admission or honestly unknown; no unbounded waiter queue.
- [ ] Entire first-touch callback-to-result-or-pending boundary is below
  **2,000 ms**, including delivery reads, authentication, sockets, validation,
  record and reconciliation attempted in that boundary. Separate accounting
  reports preparation cost; no helper-only or socket-only timing claim.
- [ ] Instrument actual consumer/producer/server calls and child creation:
  **zero helper starts**, no credential/discovery subprocess, no ensure,
  bootstrap, wake, previous-owner recovery or CLI fallback.
- [ ] Two windowless cycles, bounded transport buffers, no secret-bearing
  diagnostics and no leftover test sockets/threads/processes.

## Proposal

[Concrete producer, wire, subject, commit and owner contract](contract.md).
All functional phases remain gated even if this proposal merges.

## Journal

### 2026-10-23 — Selected zero-helper proposal

- The existing resident direction was selected for source planning, replacing
  further helper-custody work as the preferred next design.
- Source inspection established a token-authenticated JSON-line control server
  and a reusable session catalog producer, but not the proposed capability.
- Existing owners confirmed exact association evidence and reserved hunks.
  The receipt creation nonce and bounded lock/scan behavior remain required
  changes, not properties of the current backend.
- The request summary was checked against the selected constraints: per-user
  authority, successor support, zero lifecycle/fallback work, exact replay,
  unchanged timing boundary and separate implementation admission are retained.
