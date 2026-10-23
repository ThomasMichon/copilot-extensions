# Ready Resident Prompt Receipt

- **Slug:** `ready-resident-prompt-receipt`
- **Repo:** copilot-extensions
- **Branch(es):** Independent planning PR; separately admitted owner implementation
- **Created:** 2026-10-23
- **Status:** Active; delivery/control implementation admitted, backend/subject producer prerequisites retained
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
| Subject producer owner | Existing registration-to-session binding stamp only | This effort's separately reviewed producer amendment |

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
- [x] Integration owner reads and admits the exact proposed API, delivery and
  file ownership in `contract.md`.
- [x] Publish this planning-only change and clear source review/required CI.
  #8193 merged as `57bcc244f8f2b7924f609c1001110a31e430168e`.
- [x] Refresh overlapping claims immediately before any implementation
  assignment. Existing claim statements are not perpetual edit permission.

### Phase 2 — Separately admitted implementation

- [ ] Receipt owner supplies incarnation-qualified idempotency, bounded lock/
  scan admission and honest commit-unknown reconciliation.
- [x] Assigned control owner supplies the closed action, active-election/
  installation checks and drain accounting on the existing server.
- [x] Assigned delivery owner supplies the optional existing-catalog projection;
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

### Subject producer amendment — bounded existing binding stamp

- [x] Review and merge the narrow producer contract in
  [contract.md](contract.md#trusted-subject-producer-amendment) before code.
- [x] Add optional actual-record qualification to the existing writer and only
  its existing post-registration caller; preserve the registration transaction.
- [ ] Validate the real caller plus writer against the focused matrix in the
  contract, then clear source review, required CI and verified merge.
- [ ] Return the exact field/identity contract to the receipt integration owner;
  retain backend, native/live, macOS and installed acceptance as separate gates.

## Validation Plan

- [x] Source-plan structure, links, public-safe wording and scope checked.
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

### 2026-10-23 — Source review cleared; implementation admission pending

- #8193 received Copilot approval at its exact head and passed required CI,
  then merged as `57bcc244f8f2b7924f609c1001110a31e430168e`.
- The reviewer left one Low, non-blocking navigation suggestion: add this
  effort's own row to the active index. The planning PR deliberately remained
  limited to its two claimed files. Carry that navigation follow-up into the
  next coordinated documentation update without altering the receipt owner's
  existing row.
- The integration owner's exact API/file admission remains open. No functional
  implementation, runtime qualification, rollout or parent-issue completion is
  claimed. The next action is that owner's review of `contract.md`, followed by
  fresh claims and explicit implementation assignment.

### 2026-10-23 — Bounded delivery/control slice admitted

- The integration owner admitted the reviewed API and assigned the catalog,
  fixed-purpose control adapter, existing control framing/health/drain and their
  focused tests/documentation. Receipt backend/activity/Node/scheduler and
  roster-owner hunks remain separately owned.
- Source changes now keep the optional candidate separate from readiness,
  constrain frames to 8 KiB, cap receipt admission at four with no queue, and
  retain disconnected writes in drain accounting until settlement. An absent
  qualified backend advertises no capability.
- The backend owner accepted the callable/guard contract and bounded storage
  ceilings recorded in the runtime documentation. The canonical trace-lock
  deadline hunk and existing registration-producer project/nonce projection
  remain explicit additional admission gates, not inferred edit authority.
- Validation: 80 focused catalog/control/cutover tests passed, including real
  sockets and real source binding against isolated fixtures; 180 resident
  lifecycle tests passed. Install contract and touched-file lint passed.
  No native/live deployment or full Node/backend qualification was performed.
- The actual source-binding fixture observes one startup selection plus six
  fixed binding calls across health/admission/checkpoints/completion and rejects
  helper process starts. A changed selection cannot relabel the old resident.
  This is not a claim about the full hook enrichment or an installed host.
- The original local review journal was preserved through backed pull-forward.
  Managed sync refused a deleted PR ref; only the distinct journal commit was
  replayed onto the fetched reviewed default branch.
- This source update adds this effort's own index row, resolving the prior
  non-blocking navigation suggestion without modifying another owner's row.

### 2026-10-23 — Source review corrections

- Review identified the deployed hook-client layout as an invalid payload
  anchor. The adapter now uses validated installation context or the existing
  legacy command-binding receipt, then the runtime-packaged validator; an
  isolated deployed-`bin` regression exercises that path. The resulting
  catalog/control plus resident-lifecycle selection passed 229 tests.
- Review also identified a final-delivery gap: a registration snapshot already
  prefixed by the older catalog can hide the enriched catalog. Repair requires
  one narrow final-guidance composition hunk and its regression beyond the
  specifically assigned catalog-builder function. Source merge remains
  blocked until that ownership amendment and correction clear; no readiness
  or complete receipt delivery is claimed meanwhile.

### 2026-10-23 — Final-guidance correction admitted

- The integration owner admitted exactly the final-guidance catalog replacement
  and its regression. The enriched catalog now replaces only the older JSON
  block while registration text and a single catalog are preserved. No broader
  hydration/lifecycle behavior changed.
- The previous 229-test source selection plus five final-guidance regressions
  passes: 234 tests. A separate existing-guidance selection and source re-review
  remain the completion gates.
- The trusted subject producer is a separate design-only proposal: inspect the
  existing worktree-binding writer and its registration caller; do not edit
  either until the exact additive fields and snapshot checks are admitted.
- The existing-guidance compatibility selection subsequently passed 31 tests;
  source re-review and required CI remain open.
- Required CI rejected growth past the hook-client module cap. Catalog
  insertion now lives in the existing projection helper, and final guidance
  performs only the trusted producer's fenced-block replacement rather than
  duplicating catalog validation. The unchanged cap passes without a baseline
  edit; the combined source/guidance selection passes 265 tests.
- Final review also identified that the last owner-validation read could itself
  exhaust the budget. The adapter now checks the deadline after that read;
  received and absent late-result regressions pass. The combined source/guidance
  selection now passes 267 tests; module-size and lint gates remain unchanged.

### 2026-10-23 — Final source qualification clarification

- The final-owner deadline correction was published to #8224; its required CI
  passed and fresh source review confirmed the deadline defect was resolved.
  An independent isolated run of the two new deadline regressions passed.
- Review retained the existing macOS route-publisher identity mismatch as a
  separately gated platform prerequisite (#7659). The runtime documentation now
  names that limitation explicitly; shared publication/identity code is unchanged
  and the receipt check remains fail-closed. Synthetic route fixtures are not
  actual macOS publisher qualification.
- Fresh review and CI on this documentation clarification still precede source
  merge. Backend/Node qualification, trusted subject production, native/live
  validation, release and installed rollout remain separately owned and open.

### 2026-10-23 — Delivery/control source slice merged

- #8224 merged into `dev` at `2026-10-23T21:18:00Z` as
  `eab0a4909575caf9cbd3f09ba7e56831d55c5b92`, from reviewed source head
  `78c2780a6d95eeef3754a1ffef34d751d4c9d4aa`. Copilot review `5634450669`
  approved that exact head; all three existing discussion threads are resolved.
- Required PR gate, source-attribution and workflow-lockdown checks passed.
  Eligible unit coverage, guards/lint and identifier checks also passed.
  A description update lost its stdin at the wrapper boundary; file-based
  restoration of the authored text and actual source marker corrected the
  attribution failure before merge. No CI exception was used.
- The source control/delivery Plan items above are complete. #7942 remains
  open, and this effort remains Active: receipt backend/Node integration,
  trusted subject producer admission, macOS publisher qualification (#7659),
  native/live measurements, release and authorized installed rollout are not
  completed by the source merge.
- The exact next gate is integration-owner admission of the existing trusted
  subject producer proposal. Its two producer files remain unchanged. Retain
  the workspace and evidence for that decision; do not finalize it.
- This post-merge journal is a retained local checkpoint for the next admitted
  documentation integration, not a claim that the checkpoint itself shipped.

### 2026-10-23 — Trusted subject producer admitted for source planning

- The integration owner admitted only the existing binding writer, its
  post-registration stamp caller, focused tests and related documentation/
  changefile. The exact contract below preserves optionality, nonblocking
  acquired-sidecar admission, uncached freshness and existing subject rules.
- This amendment and the retained source-completion journal clear automated
  source-plan review before implementation. There is no edit authority over
  backend/Node, lock/cache internals, other registration transactions or services.

### 2026-10-23 — Producer plan reviewed; focused source validation

- #8320 approved at `8c7cfb728148c77c0884e889288cca2577870666` and merged as
  `134e6e078ac977f80254a7121d975797c56543ac` before functional edits. The
  previously retained source-completion journal was included unchanged in intent.
- The optional caller and writer now use acquired nonblocking sidecar guards and
  uncached record reads. The existing reentrant lock keeps the caller's guard
  across writer revalidation and atomic replacement; no lock/cache internals or
  registration critical transaction changed.
- Both focused caller/writer files passed 86 tests in the isolated runner;
  install contract (13 plugins) and touched-file lint passed. The tests cover
  real caller publication, stale cache, lock refusal, exact non-head/pending
  association, identity/revision mismatch, legacy/stale-field removal and I/O
  failure. An existing Windows test that simulates Linux source identity needed
  its execution-platform reader pinned before the global platform stub; that
  focused regression and the complete selection now pass.
- Functional source review, required CI and verified merge remain open. The
  next handoff is the exact producer contract to the receipt integration owner;
  native, installed, macOS and complete campaign gates remain unchanged.
- Source review found that assigning the head cache did not move the
  authoritative ledger. The non-head fixture now registers a second session,
  uses the existing `set_head_session` transition and asserts the resolved head
  before publication. This corrects the regression's evidence, not runtime
  subject authority.
