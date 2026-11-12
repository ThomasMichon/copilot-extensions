# Latest Waiting Update

## Goal

Keep the running update intact and at most one waiting successor; a newer
accepted update replaces the older waiting request rather than executing stale
queued installer code.

- **Slug:** `latest-waiting-update`
- **Repo:** copilot-extensions
- **Branch(es):** independent proposal and implementation PRs against `dev`
- **Created:** 2026-11-10
- **Status:** Done
- **Vision:** [single installation execution owner](../../../visions/installer/README.md#single-installation-execution-owner);
  [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
- **Umbrella issue:** #11985

## Guiding Intent

Extend the existing durable update driver rather than introduce another queue,
scheduler, withdrawal command or installer. Newness means admission order of
authorized requests, not a guessed version ordering or permission to roll back.
Supersession is observable history, not successful installation.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Queue driver | Proposal, implementation, focused validation and publication | Managed source worktree; public issue #11985 |

## Coordination

- **Topology:** sequential proposal and implementation PRs.
- **Host (owns PRs):** Queue driver.
- **Delegates:** none.
- **Handoff:** canonical README, issue and journal; preserve pending gates.
- This is a bounded scheduling slice under #7758, not ownership of its whole
  execution-foundation campaign. #10377 retains incompatible-footprint recovery;
  recurring maintenance and its open PR retain enrollment and lifecycle work.

## Context

The driver currently coalesces identical pending requests. Different source,
runner and option selections remain separate FIFO records, so an old retained
bootstrap can execute before a newly requested update. Admission and dequeue
already share SQLite transactions; running installation custody is separate.
Component provisioning and borrowed subordinate execution use the same owner
but are not interchangeable update requests.

The replacement vision remains the completion contract: incoming readiness and
all applicable current selections establish actual installation success;
outgoing retirement is independent. This slice does not claim to resolve the
existing interrupted/foundation-state replacement gaps.

## Request

> We should only allow a "queue size of one" for updates: if there is an update in progress, and another update is queued, queueing yet-another update should replace the one already in the queue. That should deal with "stale version is queued" problems

## Scope Approvals

The request authorizes this source behavior change. It does not authorize
installation, queue mutation on an existing host, replay, cleanup, automatic
maintenance enrollment or unrelated product effects.

## Plan

### Phase 1 - Reviewed contract

- [x] Reconcile the Installer vision and land this proposal through review.

### Phase 2 - Admission and observable supersession

The following implementation decomposition is agent-recommended.

- [x] Define addressed update equivalence using existing installation and
  execution-authority identity. Replace waiting update selections atomically
  with acceptance of the successor, including old never-started pending rows;
  never select by age, PID or runner version.
- [x] Preserve the active operation, unknown/interrupted execution evidence,
  independent installations, selected project authority and required component
  provisioning. Automatic requests cannot replace a waiting explicit request.
  Keep ordinary identical-request coalescing where its execution identity is
  truly unchanged, without losing the one-waiting-successor invariant.
- [x] Retain the replaced request's identity and invocation plus a durable
  non-success superseded outcome naming its successor. Existing wait/status
  callers must terminate honestly, including across restart and version skew.
  Prefer the existing terminal nonzero result contract over a new schema/CLI
  when it expresses the outcome completely.
- [x] Update owning API/consumer documentation, release metadata and this
  journal; pass review and required CI and confirm release publication.

## Validation Plan

These controls are agent-recommended and must exercise the actual owner.

- [x] Focused admission/status/wait controls: active A, waiting B, newer C;
  only A and C execute, B observes explicit supersession and no success.
- [x] Burst and actual separate-process enqueue/dequeue races retain one
  waiting update per addressed scope and one writer; committed history survives
  a fresh reader. Rollback of failed successor acceptance preserves B.
- [x] Old pending bootstrap/source replacement, equivalent arrivals, explicit
  option changes, manual/automatic precedence, independent installation/project
  authority, required component work and unknown-execution refusals remain
  correct. Do not equate synthetic controls with installation acceptance.
- [x] Exercise the real supported submission/observation consumer seam and
  focused clean-room/native fixtures where applicable, documenting unavailable
  tiers explicitly. No production host mutation as a test side effect.
- [x] Record reviewed source merge and actual promoted release separately;
  close this bounded effort only when its source/contract gates are resolved.
  Host rollout and installed incoming-readiness/current-selection proof remain
  with each explicitly authorized adopter, not implicitly granted by this plan.

## Proposal

The reviewed proposal is implemented within the existing enqueue transaction.
Supersession uses the existing completed/nonzero/error result without a schema
or CLI change. No daemon, scheduler, credential path, global auth change or
expansion of required CI is introduced.

## Journal

### 2026-11-11 - Actual release verified; bounded source effort complete

- Automatic promotion run42946279514 completed successfully, including frozen
  candidate installed-TTY and Windows startup qualification, source-unit
  coverage, attestation, publication/readback and exact-head merge.
- Its generated #12018 merged to `main` as
  `3c6449997a8afc18ddd1d9b9043cd3c82e033759`. The recorded Dev source
  `ed944e3bfe7efaf0690ac7b78349a56a8599a07d` contains #11997's merge.
  The released canonical queue core, pending-selection helper, engine adapter
  and Manager adapter blobs exactly match the reviewed implementation.
- All Plan and Validation Plan items for this bounded source behavior are
  resolved. Installation, host pending-request disposition and incoming
  readiness/current-selection acceptance remain separately authorized adopter
  obligations; this source effort performed none of those host effects.

### 2026-11-11 - Reviewed source merged; release pending

- #11997 merged as `2a9679b4c1b48bf4600340f9d8413525909aaa3b` after
  current-head Copilot approval, resolved review threads and green required CI.
- Exact reviewed head `033b346556ca0b6c1338f53550937a78ec07c1d0` passed hosted
  native run42944828014: Windows 83 passed/three platform skips and Linux
  84 passed/two platform skips, including latest-wins/caller-loss and original
  coalescing/independent-project controls. Declared source-retention checks
  passed on both platforms. Local adapter packets passed 238 cases; the
  expanded two-directory borrowed re-entry control also passed separately.
- Actual promotion to `main` remains open; source merge is not release or
  adopter installation. No host queue or installation was changed.

### 2026-11-10 - Reviewed proposal and implementation

- #11987 merged after current-head Copilot approval and required green CI.
- Added owner-internal update scoping and atomic replacement of never-started
  waiting selections. Running/uncertain work and component recipes are not
  changed. Preserved automatic/manual admission authority.
- Superseded wait/status observers retain the original request and receive
  terminal exit 125 with the successor identity, never installation success.
- Focused admission/core/authority packet: 187 passed. Actual concurrent
  process submitters behind a real active custodian: one passed; only one
  waiting successor survived, active work completed and replaced requests
  never started. Public consumer and final publication checks remain open.
- Admission plus legacy-record readback: 25 passed. The public Manager
  submission/status control passed through real queue admission without an
  installer. Broader Manager/bootstrap controls exposed an outdated engine
  participant fixture capability; that fixture is corrected and the affected
  real-process handoff controls are being rerun before publication.
- The affected real-process handoff residual passed all four cases after
  correcting the fixture's declared capability; the preceding full two-file
  Manager run passed its other 173 cases. Final addressed-scope packet passed
  21 cases, including relocated installation homes.
- Evidence tiers: actual fresh-queue SQLite admission, native Windows
  concurrent processes and public Manager submission/status were exercised in
  contained disposable state. No bootstrap construction, service lifecycle or
  installer effect changed, so a full machine-provisioning clean room and live
  host installation are not source acceptance proxies for this queue change.
  Native Linux/macOS execution was unavailable here and is not inferred from
  Windows evidence. Required CI retains its existing unit-only scope.
- Initial implementation CI exposed the module-size cap, unchanged actor-free
  policy source admission and a bootstrap project-normalization defect.
  Extracted the focused pending-selection helper, refreshed only the existing
  policy source's admission hash, and recovered logical bootstrap projects
  from their retained arguments. No unit nodes, actor permissions or coverage
  gates were removed or expanded.
- Review also identified a standalone reconciliation phase outside the automatic
  `--no-update` shape. Standalone bootstrap, staged and reconciliation phases
  now remain independent component work. The queue/core/authority packet passed
  197 cases after these corrections. A separate native bootstrap control adds
  an actual same-project successor and checks correlated exit 125, no execution
  of the replaced recipe and retained caller-loss ownership. The original
  native coalescing/independent-project control remains intact.
- An initial combined native scenario reached correct supersession and
  independent-project completion but exhausted its original 90-second limit
  while the last successor was still running. Split the new latest-wins
  scenario from the original coalescing test rather than enlarging limits or
  omitting lifetime, outcome or cleanup assertions. Native validation remains
  pending until the bounded scenarios complete.
- The separate local native scenario also exhausted 90 seconds, this time
  before second-request admission. Repeated local execution stopped rather than
  enlarging bounds or treating partial progress as a pass. Exact fresh hosted
  native acceptance remains required before merge. The actor-free source
  admission reader independently verified every declared binding, and the
  extracted queue module is 994 lines.
- Hosted run42937626010 qualified the corrected actual bootstrap selection:
  Windows 83 passed/three platform skips and Linux 84 passed/two platform skips,
  including the new latest-wins/caller-loss cases and original independent
  project/coalescing controls. This does not turn the local deadline failures
  into passes.
- A further review identified relative `AGENT_HOME` scoping against the source
  anchor rather than the actual execution directory. Update admission now
  captures the absolute execution home; old records compare using their retained
  runner directory. New controls cover two callers sharing an anchor, old
  relative records and the actual product environment. Fresh review and CI
  remain required after this correction.
- The engine adapter must also capture its already-selected home before queue
  normalization: an explicit project can change the process directory while
  retaining the original invocation directory. Engine and Manager adapters now
  persist absolute homes from their actual execution directories. The adapter
  packet passed 117 engine/JSON cases and 121 Manager cases, including real
  queue admission and borrowed engine re-entry with installation lookup intact.
  The engine control covers re-entry at both the selected project and caller
  directories. Lint and all 13 installation-contract checks passed. Fresh
  current-head review and hosted qualification remain required.
- Refreshed the existing Manager adapter source-admission binding after its
  home-capture change; the admission reader verified every manifest source.
  Unit nodes, actor permissions and coverage gates are unchanged. Reconciled
  the effort-index-only conflict with current `dev`, preserving peer entries
  and status changes; tested adapter and queue code remained byte-identical.

### 2026-11-10 - Proposal

- Claimed #11985 after checking existing queue and maintenance work.
- Captured the request verbatim and checked Goal, Guiding Intent and Plan
  against it. Authority, component preservation and validation details above
  are explicitly agent-recommended implementation constraints.
- Inspected current enqueue/dequeue, pending identity, terminal result,
  participant and update-authority boundaries; implementation awaits review.
