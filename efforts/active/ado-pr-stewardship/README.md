# ADO PR Stewardship

- **Slug:** `ado-pr-stewardship`
- **Repo:** copilot-extensions
- **Branch(es):** Serial proposal and bounded implementation PRs against `dev`
- **Created:** 2026-10-18
- **Status:** Active
- **Vision:** `visions/plugins/agent-worktrees/pull-requests/README.md`:
  `one-surface-no-forking`, `reviewer-capable-provider`,
  `repository-declared-pr-requirements`,
  `durable-shared-pr-transition-subscriptions`
- **Umbrella issue:** #7203

## Guiding Intent

An agent should drive an Azure DevOps PR through the existing `pr-*` commands
and shared PR observation owner until the remaining requirements genuinely need
a human. Forge mechanics belong to the ADO provider; repository requirements,
feature selection and supplemental agent guidance belong to related-repository
configuration. A consumer-specific polling program is not another PR driver.

Persistent reviewer votes and a qualifying approval after the latest source
push are separate evidence. Neither an old approval's continued validity nor
an approval's lack of commit binding proves that the latest-push requirement
was met. Discussion resolution retains its native thread and iteration identity.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Stewardship driver | Owns proposal, provider/config integration, validation and serial PRs | #7203 |
| PR owner integration delegate | Maps and integrates existing shared observation seams | Driver-coordinated bounded slice |
| Existing lifecycle owners | Retain discussion/publication recovery and durable delivery/lifecycle | #7051 and #6007 |

## Coordination

- **Topology:** serial, independently reviewable PRs against `dev`.
- **Host (owns PRs):** Stewardship driver.
- **Delegates:** bounded evidence or implementation slices with exclusive file
  ownership; no delegate publishes from a concurrently edited shared worktree.
- **Handoff:** journal completed changes, live obligations and exactly one next
  slice; an open PR or completed phase is not the effort's completion gate.
- Preserve #7051's addressable discussions and candidate-fenced publication,
  #6007's delivery/installation/lifecycle work, and #6818's broader latency/wait
  planning. This effort adds gate evidence and observation integration; it does
  not take over those campaigns.
- Repository-specific adoption stays outside this public repository. Its
  consumers use this contract, not a second implementation of it.

## Context

The existing ADO snapshot maps current reviewer votes to synthetic chronological
review IDs and reads PR metadata, but does not populate build/policy evidence.
An unchanged reviewer count can therefore hide a changed vote from a
review-ID-watermark waiter. Missing review commit metadata cannot establish
approval-after-push freshness.

The reusable seams are `PRProvider`, `PRSnapshot`, the shared `pr_contract`,
repository `PRConfig` and related-repository overlays, and the existing
`agent-pull-requests` observation owner. Extend these without creating a new
daemon, credential store, subscription lifecycle or caller-owned forge client.

The existing lifecycle proposal explicitly preserves provider distinctions:
an ADO current vote is not an ordered review-history record. This effort closes
that observation mismatch without weakening publication fences.

## Request

Public-safe capture of the operator's implementation request, with private
repository and policy identities omitted:

> There should not be a dedicated consumer-side PR watcher. Everything belongs
> in `pr-watch`, with an ADO driver, repository configuration enabling or
> disabling features and requirements, and supplemental agent guidance.
>
> Require at least one non-author approval, no blocking or rejected reviews,
> and no unresolved comments. Separately require an approval after the latest
> HEAD was posted, from anyone; this is distinct from the non-author minimum.
> Reviews and approvals persist across revisions unless the
> provider resets a required review when its owned code changes; do not impose
> exact-head approval or comment-posting semantics.
>
> Required reviewer sets each need an approval. The same approval may satisfy
> multiple sets. Some CI policies depend on other requirements, including
> latest-push approval, required-owner reviews and comment resolution;
> configuration should identify those composite policies for monitoring.
>
> Comments retain their revision-round associations, and their resolution
> status is more important than reducing all evidence to an overall verdict.
> Reflect these rules in centralized `pr-*` tools and `agent-pull-requests`;
> retire consumer-specific PR tools, or implement genuine extensions behind
> that central surface.

The operator confirmed the slug `ado-pr-stewardship`. The implementation
decomposition and validation methods below are **agent-recommended**; they
preserve the requested behavior rather than adding automatic human actions.

## Plan

### Phase 1 - Reviewed contract and coordination

- [x] Review and merge this proposal and the corresponding vision clarification
  before implementing runtime behavior.
- [x] Record the reusable provider/configuration/owner extension points and
  coordinate the additive slice with the existing lifecycle and watch owners.

### Phase 2 - Repository requirements and normalized evidence

- [x] Extend existing related-repository PR configuration for selected observation
  features, approval/discussion requirements, human-only gates, composite-policy
  identification and supplemental agent guidance. Preserve overlay precedence.
- [x] Define additive, capability-honest gate/readiness evidence with known,
  unknown and incomplete states; keep persistent votes, approval-after-push,
  overlapping required sets, unresolved threads, builds and policies distinct.
- [x] Preserve native review persistence and provider review-reset behavior.
  New pushes invalidate latest-push evidence, not every historical approval.
  Do not loosen the existing analyzed-candidate fence for publishing a review.

### Phase 3 - ADO driver and centralized observation

- [x] Read complete, current ADO PR/iteration/reviewer/thread/policy/build/status
  evidence through the existing internally authenticated provider transport.
  Retain stable actor, object, group and iteration identities.
- [x] Establish approval-after-push from authoritative provider events or native
  policy evidence. Missing or ambiguous evidence stays unknown; a locally
  observed vote or current positive vote alone cannot fabricate freshness.
- [x] Replace chronological-ID assumptions for ADO vote changes. Detect changed
  votes, fresh same-actor approval, thread resolution/replies, policy/build
  transitions and source pushes through the existing wait/cursor contract.
- [x] Use the same observations in `pr-status`, foreground `pr-watch` and
  `agent-pull-requests`. Preserve subscription, delivery, timeout and restart
  ownership; do not introduce another polling owner or service transaction.
- [x] Report dependent policies as waiting on their actual prerequisites when
  evidenced. Configuration classifies a gate; it cannot waive it or convert
  an unexplained failure to success.

### Phase 4 - Agent workflow and adoption contract

- [x] Make centralized reports distinguish agent-actionable feedback/failures,
  pending work, human-only requirements and unknown evidence. Return a
  human-ready checkpoint only when all other required gates are proven clear
  for the current source push.
- [x] Reuse the existing owning agent for code changes, tests and publication.
  Preserve ordinary build triggers and bounded, explicitly authorized retry
  operations; never manufacture approvals or human attestations.
- [ ] Document the consumer migration contract and prove centralized feature
  parity before consumers remove their legacy watcher and its call sites.
- [ ] Land implementation and report contribution, release and authorized
  rollout separately. Do not change a running shared service as a side effect
  of implementing observation.

## Validation Plan

All items below are **agent-recommended** acceptance checks.

- [x] Provider-transport fixtures cover complete and paginated reads, missing
  pages, unavailable policy evidence, concurrent pushes, stale evaluations,
  composite-policy prerequisites and build failure diagnostics.
- [x] Persistent approvals remain valid after unrelated pushes; missing
  latest-push approval still blocks. A fresh same-actor approval is detectable
  without inventing a new chronological review ID or requiring a commit tag.
  A latest-push approval by the author does not satisfy a separate non-author
  minimum, but is not discarded merely because that other requirement exists.
- [x] Non-author minimum approval, blocking/rejected votes, overlapping required
  reviewer sets, provider-reset required reviews, unresolved/outdated/reopened
  iteration-bound threads and authoritative resolution status are distinguished.
- [x] Foreground CLI and shared-owner cases observe equivalent vote, discussion,
  policy/build and head transitions. Cursor compatibility, deduplication,
  cancellation and restart preserve responsibility without duplicate wait owners.
- [x] Related-repository configuration and overlay tests prove defaults, strict
  input validation, feature/requirement selection and surfaced supplemental
  guidance; unsupported or unknown facts never become human-ready.
- [ ] Existing GitHub/Gitea verdict, consent, lifecycle and observation behavior
  remains intact. Runtime changes pass the relevant contained tests, fatal lint,
  install-contract gate, documentation-impact review and required CI.
- [x] A bounded real-transport/CLI integration exercises the changed consumer
  contract. Where an authorized live ADO read is available, compare its
  centralized gate report with native evidence without mutating a real PR.
- [ ] Consumer retirement demonstrates no remaining legacy watcher invocation,
  no lost observation/remediation responsibility, and a supported centralized
  tool version. Missing rollout permission remains an explicit tracked gate.

## Proposal

The proposal passed automated review and required CI, then merged in #7204.
Source implementation proceeds through the normal contribution gates. Live
approval, human attestation, policy bypass, service cutover and production rollout
remain outside this proposal's authority.

## Journal

### 2026-10-19 - Remote review: absolute HTTP receive budgets

- A late review found socket-operation timeouts did not bound a continuously
  streaming response. Real loopback reads through the production opener and
  HTTP parser reproduced three failures for fixed-length bodies, chunked
  bodies and fragmented headers; their fast counterparts already passed.
- The HTTPS transport now enforces the remaining absolute deadline below HTTP
  buffering on every raw receive, retaining TLS, redirect refusal, in-memory
  authentication and response limits. Budget-bound socket timeouts keep an
  explicit transient budget error even with platform timer rounding.
- The combined native driver, cursor, CLI, contract and wait selection passes
  332 cases. A bounded authorized live TLS metadata/iteration read also passes
  without a backend mutation. Updated-head review/CI, release and rollout
  remain open.

### 2026-10-19 - Integrating current review-boundary contracts

- Current `dev` advanced through the review-exception boundary and output-free
  owner-bootstrap corrections while the source PR awaited reassessment.
  Backed up the published source, consolidated only this PR's commits and
  resolved its single readiness-contract conflict in one rebase.
- Preserved operation-specific direct-merge and review-assessment fields while
  retaining authoritative native gates. Pending human/native requirements
  cannot become direct-merge readiness or eligibility; consent arming remains
  separate. Four new operation/native-gate combinations exercise the seam.
- The combined native driver, cursor, wait, review-policy, facade, CLI and
  merge-boundary selection passes 447 cases. Updated-head publication, remote
  review and CI still precede release and authorized adoption.

### 2026-10-19 - Remote review: nested peers share the outer lifetime

- A deterministic callback regression reproduced both Bridge peers opening
  separate POSIX sessions, escaping outer callback termination. Merely dropping
  session creation would have weakened standalone delivery cleanup.
- Moved bounded execution into the existing canonical process library.
  Generated callbacks select a kernel-validated contained worker; its peers
  inherit the outer scope. Standalone delivery supervises the same worker.
  Existing admission, incarnation, receipt, idempotency and ACK fences remain.
  This is a below-vision-altitude correction to the existing containment
  contract, not another watcher, daemon or authorization surface.
- The owner/writer selection passes 281 cases with ten Linux-only skips;
  the facade passes 33. Independent nested-entry and real Windows standalone
  kernel checks pass two cases, and the canonical-helper regression selection
  passes 14 with the same ten skips. Linux success, timeout, cancellation and
  quiescence late-effect cases still owe actual execution on updated-head CI.
- Materialized only the process library's single real vendored consumer;
  its source matches canonical. All 14 actual consumers have PR-owned release
  intent; source, vendoring and module guards pass without hand-picked versions.
  Pre-flag source-preview callbacks are not silently migrated. The callback
  module is new in this PR, and no supported runtime was changed here.
- Publication, updated-head review/CI, promotion and authorized rollout remain
  separate gates.

### 2026-10-19 - Remote review: Windows input cannot outrun its deadline

- A real Windows callback with a snapshot larger than its stdin pipe and no
  input reader reproduced a synchronous write blocking beyond the callback
  deadline. An outer owned-Job guard bounded the failing regression safely.
- Windows input now has independent absolute-deadline supervision that
  serializes retirement of the original owned Job and joins supervision and
  pipe-reader threads before return. Completion, timeout, cancellation and
  supervision-start refusal retain cleanup without an unsupervised fallback.
- The real callback regression passes; the independent five-case selection
  passes, and the owner/writer lane passes 273 with six local Linux skips.
  The preceding head's successful Ubuntu CI run selected the entire native
  test file, exercising the six real POSIX descendant cases unavailable on
  this Windows host. Updated-head review and CI still remain separate gates.

### 2026-10-19 - Remote review: retain POSIX child identity through cleanup

- Two native call-site regressions reproduced signalling a process group after
  `communicate()` had reaped its leader. A numeric group identity could then
  have been reused, making descendant cleanup unsafe.
- Native snapshot/callback execution now shares a bounded helper that retains
  exclusive POSIX child custody with `WNOWAIT`, retires the group, then reaps.
  Lost or unsupported custody refuses signalling; Windows retains strict Job
  containment. Completion, timeout and cancellation keep descendant cleanup.
- Owner integration passes 272 cases with six Linux-only skips, and the
  Worktrees facade passes 27. Independent ordering/refusal/timeout cases pass
  11 with the same six skips; real Windows child/descendant execution passes.
  Actual Linux descendant execution is still owed by the available CI lane,
  not established by Windows mocks. Fatal lint and process/module guards pass.
- Current-head review and CI, promotion and authorized rollout remain open.

### 2026-10-19 - Remote review: terminal admission is durable evidence

- Registration previously discarded an already-merged/closed admission read
  and depended on a later read to rediscover it. Six regressions reproduced
  the omission for initial and repeated native registrations.
- Native terminal admission now freezes the selected notification through the
  existing durable event path before registration success. Restart and
  acknowledgement need no second provider read. Repeated continuous admission
  retains generation and older pending delivery; pre-write failure restores
  prior state, while uncertain committed writes retain recovery evidence.
- The owner/writer selection passes 257 cases; an independent ten-case
  admission, restart, acknowledgement and write-failure selection passes.
  Publication, review, CI, release and rollout remain separate obligations.

### 2026-10-19 - Remote review: skipped reviewer evidence stays unknown

- A complete observation can intentionally omit reviewer reads. Two native
  driver regressions reproduced an unchanged negative vote becoming a new
  rejection after reviews were enabled and a prior cursor resumed.
- Observation now carries reviewer-evidence availability separately from
  overall completeness. Skipped reads retain unknown cursor evidence, just as
  unavailable reads do; native events and known actor/vote transitions retain
  their behavior. The combined driver, cursor, wait and contract selection
  passes 303 cases.
- The updated source still owes publication, review and CI, then promotion
  and authorized rollout. No live vote, service or installed payload changed.

### 2026-10-19 - Remote review: project-independent callback entry

- Governed delivery uses the same-cell Worktrees peer's `pr-watch deliver`
  command from the owner's runtime directory. The front door still required
  project context for that internal callback, so handler-level tests missed
  a real delivery blockage.
- Two neutral-directory main-entry regressions reproduced the rejection for
  legacy and governed callback routing. The callback is now admitted without
  relaxing waits/publication gates or the delivery handler's own validation
  and durable-receipt fences. The combined facade/read/routing selection
  passes 180 cases. Runtime delivery proof remains a post-release obligation.

### 2026-10-19 - Remote review: generic fresh-review transition parity

- A fresh native approval with an unchanged positive vote reached the shared
  owner but not a foreground waiter selecting only `review_changed`. Three
  regressions reproduced missed same-vote events and duplicate generic events
  when both vote state and neutral history changed.
- Foreground observation now includes approval-history fingerprints in the
  selected generic transition and emits it only once. The event/wait/contract
  selection passes 238 cases. A real JSON snapshot decoded by the shared owner
  produces the same single generic transition as the foreground contract.
- Documentation, fatal lint and module limits are current. Publication, review,
  CI, promotion and authorized rollout remain separate open gates.

### 2026-10-19 - Late review: unavailable votes remain unknown

- A late-arriving review of the pre-rebase head identified incomplete reviewer
  evidence being serialized as a known-empty negative-vote baseline. Recovery
  could then synthesize a new rejection from an unchanged existing vote.
  Two returned-wait-cursor regressions reproduced the false event.
- Incomplete observations now retain unknown actor/vote evidence. Genuine
  native negative events remain observable independently. The combined
  event/wait/contract selection passes 234 cases; fatal lint and module limits
  pass. Updated-head publication, review and CI remain open.

### 2026-10-19 - Integrating merged owner and review contracts

- The PR's base advanced through #7160's governed installation-cell owner and
  #7225's review/maintainer-completion corrections. Preserved the reviewed
  branch on a local backup, consolidated only this PR's commits, and reconciled
  the overlapping contracts rather than replacing upstream work.
- Native/continuous observation now follows the existing entry/control/writer
  path, immutable same-cell peer prefixes and private delivery routing.
  Absolute deadlines, frozen pending notifications, acknowledgement fences and
  callback-descendant containment remain intact. Missing cell peers refuse or
  retain pending delivery; caller-provided owner-command overrides are refused
  for governed cells.
- The upstream nonblocking approval/change-request/CI transitions coexist with
  native discussion/gate transitions. The combined contract/wait/event selection
  passes 231 cases. Independent native handoff/budget/containment checks pass
  ten cases and the central owner facade passes 25. Owner integration selections
  additionally pass 247 core/writer, eight governed, 15 control/peer and four
  installer cases, with one platform-specific installer skip.
- Integration still owes publication, updated-head review and required CI.
  No installed payload, shared owner or live backend was changed.

### 2026-10-19 - Remote review: pushed identity and actor-bound rejections

- Successful checked publication now returns the candidate SHA that was
  actually pinned and pushed. A real bare-Git regression advanced the local
  ref during lock admission and reproduced an empty result SHA despite the
  remote correctly receiving the earlier candidate. The 44-case publication,
  receipt and stale-tip selection now passes.
- An unchanged blocking reviewer no longer turns another reviewer's approval
  into a synthesized change-request event. Rejection inference uses bounded
  actor/vote digests; native history retains precedence. Legacy or oversized
  optional evidence remains unknown rather than inventing a rejection.
  The core event/wait selection passes 230 cases and the independent event
  selection passes 40, including compatibility and cursor-size cases.
- Fatal lint and module limits pass. The corrected head still owes remote
  review and required CI; source correctness does not imply release or rollout.

### 2026-10-19 - Remote review: timeout, target fallback and cursor recovery

- Corrected expired continuous subscriptions so changing nonterminal evidence
  cannot postpone the final timeout indefinitely. Merged/closed priority and
  frozen pending deliveries remain intact. Three failures reproduced the
  defect; the owner integration selection passes 217 cases and the independent
  deadline/pending-event selection passes seven.
- Matching registry entries without an available checkout no longer prevent
  explicit checkout-free bindings from resolving. Valid registered policy
  retains precedence; malformed or inaccessible configuration remains a
  visible failure. Nine regressions reproduced the fallback omission.
- Returned wait cursors preserve a monotonic approval-event watermark across
  unavailable history, gate wakeups and timeouts. Recovery cannot replay old
  approvals as new; a genuinely newer event remains observable. Six failing
  regressions reproduced the reset; the core wait/contract selection passes
  214 cases.
- The independent combined recovery/binding/read selection passes 71 cases.
  Review and required CI remain open on the corrected head, followed by
  promotion and authorized adoption; none of these tests updates a runtime.

### 2026-10-19 - Remote review: real entry point and config loading

- The next review found two integration gaps that dispatcher-only and
  constructed-config tests missed. Project-free reads were rejected by the CLI
  front door, and the real config loader discarded machine PR declarations
  for targets without local checkouts. Eight new regressions failed before
  correcting those paths; ordinary tracked/publication gates remained intact.
- Explicit one-shot reads now reach their own context resolver. Anchorless
  machine PR declarations are retained separately from managed checkouts,
  layered over related policy and used for target resolution. Retargeting a
  machine declaration suppresses the obsolete related binding.
- The combined routing/configuration/read selection passes 293 cases.
  Expanded discussion-route and deep-merge checks pass the final 32-case
  read/binding selection; fatal lint and module limits pass. The actual
  `python -m agent_worktrees` entry point was also invoked from a neutral
  directory against an authorized native read, then decoded through the
  shared owner. It returned complete evidence without a PR mutation.
- Required CI passed on the preceding head, but the corrected head still owes
  its own review and CI. Release, supported availability and authorized
  consumer adoption remain open.

### 2026-10-19 - Remote review: restart and discussion revisions

- #7226's review identified two notification defects. Non-native head-change
  subscriptions now persist their head baseline across owner restarts, while
  older records without a head retain lazy first-observation compatibility.
  Restart regressions failed before the correction; the focused independent
  head-baseline selection passes seven cases and the owner integration
  selection passes 210.
- A discussion's last reply did not reveal edits or deletions to an earlier
  comment. Three native-driver/cursor regressions reproduced that omission.
  Observation now retains a thread-wide revision fingerprint, not bodies;
  the 98-case native observation/cursor selection passes. A JSON snapshot
  decoded by the actual shared owner emits one `threads_changed` transition
  for an earlier-comment edit and none for an unchanged reread.
- These corrections remain source changes pending an updated review and
  required CI. Publication, release, supported consumer availability and
  authorized rollout remain open.

### 2026-10-18 - Final source integration and publication fences

- Completed built-in preparation and bounded remediation parity through the
  central surface. Required and optional policy selection are independent:
  an unrelated optional evaluation cannot block recovery of the uniquely
  selected required build evaluation.
- Independent review identified a last-mile rewrite path omitting the
  repository's publication policy and a lossy UTF-8 diff fingerprint that could
  collapse distinct source bytes. The corrections preserve the candidate
  fence and hash bounded Git/lens bytes without replacement decoding. New
  regressions exercise candidate advancement and a real non-UTF-8 Git diff.
  Independent named-defect verification found both defects resolved; all 39
  publication-check/preflight tests, fatal lint and module-size guards pass.
- The 72-case integrated observation/CLI/preflight selection, 39-case owner
  selection and native installer checks passed. Simple end-to-end source CLI
  and shared-owner decoding, plus a finite foreground timeout/cursor check,
  passed. An authorized live read proved native evidence without mutation;
  no private response or target is retained here. Real mutation and shared
  rollout were not authorized; synthetic transport cases cover writes and
  isolated installer tests cover first-install behavior.
- Publication, required CI, promotion, supported consumer availability and
  authorized rollout remain open. These source checks do not mark the effort
  Done or authorize deleting a consumer's live watcher.

### 2026-10-18 - Portable preparation and remediation integration

- Added strict optional publication-check configuration, exact-diff author
  self-review receipts, scope reports and mandatory publication preflight.
  These local receipts do not cast native approving reviews.
- Added bounded, durably deduplicated discussion replies and recovery of an
  existing build-policy evaluation. Comment freshness binds the thread and
  comparison round, not the PR's latest source head. Requeue recovery never
  creates a pipeline run or alters human approval/attestation requirements.
- A real read-only source CLI invocation passed its output through the shared
  owner's native decoder. Native discussion reads also supplied usable reply
  fingerprints; no real comment, build-policy, approval or presence write was
  performed.
- The native proof exposed a roster applicability defect: a branch-policy
  roster also includes repository-only settings and conditional path policies.
  Missing conditional configurations no longer invent PR requirements; native
  evaluations establish their applicability. Applicable evaluation revision
  mismatches still remain unknown. Added synthetic regressions without
  retaining provider data.
- Combined tests exposed a separate read/mutation-evidence distinction.
  Readable discussions with incomplete mutation metadata retain their bodies
  and report an explicit per-thread reply error rather than losing the read.
  The resulting 67 observation/CLI cases pass; the earlier portable mutation
  and preparation selections pass independently. Cohesive extraction keeps
  grandfathered modules within their shrink-only limits. The integrated
  observation/CLI/publication-preflight selection passes 72 cases, and a real
  finite foreground wait returns complete evidence and a timeout cursor.
  Final combined review, publication and consumer availability remain open.

### 2026-10-18 - Observation review: native chronology

- Corrected policy-evaluation and native-status selection to compare parsed UTC
  instants. Lexical timestamps can invert whole-second/fractional and offset
  dates; opaque evaluation IDs are not ordering evidence.
- Missing, invalid or conflicting latest timestamps now leave policy evidence
  unknown, including native latest-iteration approval proof. Fourteen added
  regressions cover these cases through the configured observation driver;
  all 52 observation tests pass.
- Shared-owner race fixes, consumer preparation/remediation parity and the
  read-only native CLI proof remain active work. No service or payload rollout
  was performed.

### 2026-10-18 - Native driver and foreground contract

- Implemented strict related/machine PR target and observation configuration,
  native ADO gate reads, persistent-vote versus latest-push approval evidence,
  overlapping required native sets, iteration-bound discussion resolution and
  configured composite-policy classification.
- Added target-bound observation cursors and vote/discussion/policy/gate wake
  signals without treating synthetic current-vote indexes as review history.
  Foreground status/wait and explicit-target one-shot reads share the driver.
- Discussion bodies have an on-demand central read; persisted observation
  records retain metadata only. Older-round replies remain addressable when
  the source head changes.
- Contained source fixtures and legacy contract/watch/provider/status cases
  pass; final integrated owner, live read and consumer retirement acceptance
  remain open. This is implementation progress, not a release or rollout.
- The bounded owner integration is ready in its isolated slice. Next integrate
  that existing owner's decoder, durable notifications and cancellation
  contract, then finish adoption parity before retiring consumer tools.

### 2026-10-18 - Proposal reviewed; contract implementation

- #7204 merged at `e3a392153` after exact-head approval, no inline findings and
  successful CI. Synced the implementation worktree onto the reviewed proposal.
- The shared watch owner currently uses a GitHub-specific fetcher and a separate
  snapshot contract. The integration will consume a versioned, one-shot central
  observation operation rather than copy ADO transports or pull in a new runtime
  dependency graph. Preserve its acknowledged notification and one-shot
  subscription contracts.
- Confirmed the public native API contracts for iteration creation/push times,
  structured vote-update events, revision-bound threads and policy/status reads.
  A projected service catalog can omit those fields; absence from that
  projection is not evidence that the native forge lacks them.
- Next: implement repository requirements and normalized evidence through the
  existing driver/CLI seams, then integrate the shared owner.

### 2026-10-18 - Intake and ownership

- Operator selected centralized ADO stewardship and confirmed the effort slug.
- Claimed #7203 after searching existing issues. Retained #7051, #6007 and
  #6818 ownership rather than duplicating their campaigns.
- Source inspection and an isolated pure observation example identified the
  synthetic-vote-ID and missing approval-freshness gaps. These are diagnosis,
  not implementation or live deployment.
