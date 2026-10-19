# PR Lifecycle Conformance

- **Slug:** `pr-lifecycle-conformance`
- **Repo:** copilot-extensions
- **Branch(es):** independent, serial per-phase contribution PRs targeting `dev`
- **Created:** 2026-10-16
- **Status:** Active
- **Vision:** `visions/plugins/agent-worktrees/pull-requests`:
  `reviewer-capable-provider`, `foreign-repo-pr-operations`,
  `conformance-verified-mock-provider`, and `one-surface-no-forking`;
  `visions/plugins/agent-dispatch/reviewer`
- **Umbrella issue:** #7051

## Guiding Intent

A persistent reviewer or submitter should be able to complete a multi-round PR
workflow through the existing provider-neutral CLI on GitHub, Gitea and Azure
DevOps. Complete means reliable discussion and candidate identity, honest
capabilities, safe recovery after uncertain publication, and observation through
a verified terminal outcome, not merely a matching set of method names.

This closes existing PR-capability vision items. It does not introduce another
forge client, lifecycle state machine, review rubric, or credential store.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Lifecycle driver | Owns the contract, serial implementation PRs and acceptance | Public coordination issue #7051 |
| Existing lifecycle owners | Retain session/task architecture and shared watch ownership | #2357 and the linked owning efforts |

## Coordination

- **Topology:** serial, bounded PRs against `dev`, with the reviewed plan first.
- **Host (owns PRs):** Lifecycle driver.
- **Delegates:** none assigned. Provider slices may acquire separate owners
  through #7051 before edits; no overlapping adapter ownership.
- **Handoff:** keep the Plan, Validation Plan and Journal current; publish the
  next unfinished slice and any open PR obligation through #7051.
- Reuse #2357's review/task/session model. This effort owns PR operation
  conformance, not the dispatch lifecycle or session-host control contract.
- Reuse existing durable PR observation and reviewer recipes. Coordinate changes
  with their owners rather than adding another watcher or consumer-side poller.
- A partial slice uses `Refs #7051`, never a closing reference. The umbrella
  remains open until the complete acceptance matrix is resolved.

## Context

The existing `PRProvider`, `pr-*` commands and portable reviewer recipe are the
extension points. Reviewer primitives from #2699 and the fabricated provider
already exist; they are not missing implementations to recreate. Their presence
does not establish identical multi-round behavior across all real adapters.

#7051 records the remaining contract gaps: candidate-unbound verdict methods,
incomplete discussion identity and pagination, reply/reopen capability gaps,
inconsistent unsupported outcomes, and missing authoritative reconciliation
after ambiguous publication. These are the starting inventory, not an assertion
that every adapter has every defect.

The governing design is the existing PR capability's one-surface guarantee and
the patterns in `docs/patterns/README.md`: explicit command ownership,
cross-platform parity, attributable identity and literal failure reporting.
Provider transports stay internal implementation details. This effort neither
requires callers to use raw REST nor forbids an adapter's existing MCP transport.

## Request

Public-safe summary of the continuing request: drive the existing reliability
objective to completion, including the complete provider-neutral PR lifecycle
contract captured in #7051. The operator confirmed the effort slug
`pr-lifecycle-conformance` on 2026-10-16.

The detailed decomposition and validation methods below are
**agent-recommended**, derived from #7051's acceptance contract. They are a
proposal for review, not a claim of prior implementation or production consent.

## Plan

### Phase 1 - Contract and evidence inventory

- [x] Reconcile each existing CLI operation and provider capability against
  #7051. Record supported, unsupported, incomplete and unproven behavior
  separately; retain existing owners and reusable tests. See the
  [source and fixture inventory](provider-inventory.md).
- [x] Define the normalized structured results for provider/repository/PR
  identity, source/head/base, draft/holds, consent, reviews, mergeability and
  terminal disposition. Preserve provider distinctions and incomplete evidence.
  See the [proposed normalized contract](contract.md).
- [x] Define complete and incremental read semantics, durable identifiers,
  cursor validity and typed errors, including pagination and candidate changes.
  Do not report a partial read as a complete result. Defined in the contract;
  adapter implementation and transport proof remain open below.
- [x] Define mutation authority and operation identity on the existing command
  surface: stale candidate refusal, discussion ownership, authoritative
  read-back, and an explicit unresolved outcome where safe reconciliation is
  impossible. Do not promise exactly-once writes without provider evidence.
  The contract distinguishes native conditional guarantees from preflight
  checks; it does not claim that every provider already enforces them.

### Phase 2 - Complete, addressable reads and discussion operations

- [ ] Extend existing provider and CLI operations for complete and incremental
  diffs, reviews, comments and threads across all three real providers.
- [ ] Preserve addressability across rounds: edits and replies keep stable
  identity; resolve/reopen targets the requested thread; deleted or outdated
  objects and expired cursors return explicit outcomes.
- [ ] Add capability-honest reply, edit, resolution and reopening behavior.
  Missing provider features remain typed unsupported results, not no-ops
  presented as success; acceptance must name any remaining unsupported
  requirement rather than treating its disclosure as parity.
- [ ] Keep registered foreign-repository addressing and internally resolved
  identity consistent with author-side commands. Do not substitute the caller's
  checkout or provider for the addressed target.

### Phase 3 - Candidate-fenced, recoverable mutations

- [ ] Bind verdict publication to the analyzed candidate and refuse stale
  authority. Apply actor/ownership checks to discussion mutations without
  inventing a second authorization model.
- [ ] Carry operation identity through comment, review and lifecycle writes.
  Reconcile dropped responses against authoritative provider state before
  retrying; unresolved ambiguity blocks replay instead of duplicating writes.
- [ ] Preserve submitter-owned refresh/rebase/publication, consent and final
  disposition. A conflicting rebase hands back to the submitter; no reviewer
  conflict-resolution worker or merge-policy bypass is added.
- [ ] Preserve internally brokered credentials and existing provider transports.
  Callers neither acquire tokens nor construct forge-specific requests.

### Phase 4 - Persistent-agent lifecycle acceptance

- [ ] Compose existing blocking observation and dispatch wait/resume for an
  external reviewer/submitter transition. Pending work survives a session
  interruption without an agent-authored polling loop or parallel state model.
- [ ] Run the same ordinary persistent-agent workflow on GitHub, Gitea and Azure
  DevOps using deterministic fixtures or explicitly authorized isolated test
  repositories: inspect, discuss, request changes, revise, re-review, consent,
  and verify merge; separately verify abandoned disposition.
- [ ] Prove each real adapter's transport behavior with shared conformance cases,
  not only protocol-shape assertions or a mock-only lifecycle demonstration.
- [ ] Update the owning CLI/provider and reviewer-recipe documentation and record
  exact remaining gaps. Archive only when all Plan and Validation Plan items are
  resolved or explicitly transferred to named tracked objectives.

## Validation Plan

All additions follow `TESTING.md`: focused, change-scoped required CI with the
exhaustive matrix in the existing scheduled/manual portfolio. Use the repository
test wrappers and synthetic data; no live credentials or production state in
fixtures. Each implementation PR records its smallest relevant test command.

- [ ] The normalized contract rejects wrong-target, unavailable-provider and
  incomplete-evidence cases without falling back to the caller's repository.
- [ ] Shared behavioral cases exercise GitHub, Gitea, Azure DevOps and the
  explicit mock adapter. Real adapters traverse their request/response
  boundaries; replacing them with mock-provider behavior is insufficient.
- [ ] Multi-page and incremental reads retain all records, stable IDs and cursor
  semantics across edits, replies, deleted/outdated threads and moved candidates.
  Invalid or expired cursors produce explicit recovery instructions.
- [ ] A candidate change before verdict publication prevents the stale write;
  the current candidate can be reviewed normally. Unauthorized thread mutation
  fails without affecting another actor's discussion.
- [ ] Lost write responses, duplicate retry and interrupted recovery produce one
  authoritative result or an explicit unresolved state, never duplicate comments,
  reviews or lifecycle transitions. Include non-unique content and pagination in
  read-back cases so text matching alone cannot masquerade as operation identity.
- [ ] Three provider-specific multi-round persistent-agent scenarios use normal
  code-inspection tools and the supported CLI only. Each includes a change
  request, submitter revision, addressed discussion, fresh verdict, consent and
  verified merged outcome; each provider also proves abandoned disposition.
- [ ] At least one external transition per provider suspends and resumes through
  the existing observation/dispatch contract. Restart recovery preserves the
  target, candidate and pending responsibility without duplicate mutation.
- [ ] Unsupported features and ambiguous outcomes stay visibly typed. No required
  workflow step is silently skipped to make a provider scenario pass.
- [ ] Existing author-side PR workflows, provider transports, repository review
  policy and credential brokerage remain intact on Windows and Linux/WSL.
- [ ] Documentation impact is reconciled against each final implementation diff.
  Source completion, release publication and authorized rollout are reported
  separately; no production rollout or fault injection follows from this plan.

## Proposal

The planning-only proposal passed review and merged in #7060. Phase 1 now
records source/fixture evidence and an agent-recommended contract for review.
No runtime contract implementation or lifecycle acceptance is complete.
No live test writes, credential configuration, service lifecycle or deployed
host changes are authorized by the plan.

## Journal

### 2026-10-16 - Endpoint source prerequisite landed

- #7074 merged as `b87c0723961c56dfab9a5e350529d4202e4f6497`,
  closing the separately claimed source repair #7062. Canonical and vendored
  endpoint publication/cleanup now share the native ownership-preserving lock.
- Native Windows and POSIX library cases and the dispatch advertisement
  regressions passed. This is source completion, not release or runtime
  adoption; no deployed service was changed by this contribution.
- #7051 remains open. The preserved Phase 1 contract/inventory delta is the
  next serial review; none of its adapter implementation or multi-round
  acceptance requirements is waived by the endpoint repair.

### 2026-10-16 - Contract and evidence inventory

- Compared the three real adapter tracks independently and retained their
  source anchors in the [inventory](provider-inventory.md). Existing protocol
  conformance is structural for real providers; mock lifecycle behavior is
  not evidence of complete real-provider parity.
- Defined the [proposed normalized contract](contract.md), including opaque
  identities, collection completeness, scoped cursors, candidate authority,
  actor checks and unresolved ambiguous writes. A preflight read is not an
  atomic conditional write, and post-timeout absence does not authorize replay.
- Existing provider/conformance fixtures passed 291 cases on native Windows
  through the bounded repository runner. This is baseline evidence only;
  every Validation Plan item remains open.
- Next: review this Phase 1 delta, then implement complete/addressable reads
  through the existing provider/CLI seams. Native mutation fence and recovery
  capabilities require per-provider transport proof before being advertised.

### 2026-10-16 - Planning intake

- Claimed the contract/conformance planning slice in #7051 and reused the
  existing PR-capability vision and #2357 lifecycle ownership.
- Operator confirmed the slug. Created the canonical public plan with explicit
  provider acceptance, safe ambiguous-write recovery and no rollout consent.
- Implementation remains pending plan review.

### 2026-10-16 - Proposal reviewed

- #7060 merged after an exact-head Copilot approval and successful required
  checks. #7051 remains open; no implementation or acceptance item is complete.
- The next contract slice is Phase 1's provider/capability inventory. The driver
  first retains the separately claimed endpoint-discovery repair #7062; that
  repair does not implement or transfer this effort's lifecycle requirements.
