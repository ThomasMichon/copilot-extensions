# Author and Reviewer perspectives

Back to the [canonical effort](README.md). This amendment extends the existing
[caller-driven subscriptions](caller-driven-subscriptions.md),
[author-publication plan](author-publication.md) and
[review workspaces](../pr-review-workspaces/README.md); it does not replace
their unfinished acceptance. Design choices and sequencing below are
**agent-recommended**, distinct from the captured operator direction.

## Direction and scope

**Public-safe capture of the 2026-11-03 operator direction:** extend the existing
plugin/service into a central, initially API-only PR daemon with Author and
Reviewer perspectives. Agents always use the CLI, which calls the HTTP service;
Gateway can host the coordinator-facing endpoint and a later UX. Authors use
the current PR-operation semantics and register confirmed authored PRs for
shared subscription/polling observation and notifications. Reviewers subscribe
to repository PR sets selected by criteria, receive current actionable work,
and operate with current PR state and incremental event cursors.

Treat a PR as a collaborative conversation around a current code candidate:
normalize commits, comments, per-file threads, CI gates, reviews and participants
without retaining all code diffs or becoming a chat archive. At merge,
tombstone the PR, retaining existence, original author, other reviewers, our
author/reviewer/merger roles, review-round count, final verdict and compact
statistics. Track closed-unmerged/abandoned PRs too.

Reviewer interests include whole-repository Maintainer, path-scoped Codeowner
and interested Subject Matter Expert. Actionable work includes initial and
follow-up review, merge nudges, rebase needs, CI re-kicks and final-iteration
stamps; some actions are mechanical and others need an agent. Feedback moves a
PR to the back of the line while higher-priority new events surface. Optional
auto-merge and statistics belong to the reusable core. A running service can
be the subscription target of Dispatch's `pull-request-reviewer-loop` recipe.
Ship ADO, GitHub and Gitea drivers. Eventually retire Worktrees' legacy PR verbs
only after exact semantic parity, caller migration and compatibility proof.
Pattern reviewer commands on existing prepare/read/sync/submit mechanics.

Use the adopted Agenda to drive design, reviewed source, bounded isolated
container qualification and a read-only shadow consumer; a comparison pane can
overlay the service later. Deployment-specific identities and the unredacted
operator exchange remain outside this public artifact. This direction does not
grant live mutation or deployment authority.

## Existing source to compose

| Owner | Baseline and required extension |
|-------|---------------------------------|
| `services/pr` | Independently packaged foreground service, writer/lifetime/readiness, Gateway connector and observer; extend this host, not another daemon. |
| PR `watch_replay*`, `watch_service`, `watch_gateway` | Exact scopes, durable request/event/replay/ACK and active serving lifetime; extend one storage/registration authority. |
| PR `review_workspace`, `generation_cache`, `owner_read` | Current ADO read generations, completeness/observation intervals, immutable bounded materialization; expand normalized provider coverage without retaining historical bodies indefinitely. |
| PR `review_intent`, `review_executor`, `workspace_operations` | Protected selected submission and unknown-outcome reconciliation; preserve candidate, identity and action-specific checks. |
| Worktrees native provider/PR operations | Current provider/account resolution, publication, policy, votes/checks and caller claims; remain source-control/resource owner behind compatibility adapters. |
| Dispatch reviewer recipe/emitter | One-task-per-target deduplication, assignment/session/worktree lineage and resumption. Service attention is not another execution task queue. |

Source libraries and reviewed proposals are not evidence that the standalone
launcher adopts every operation or that existing CLI verbs already use HTTP.
That integration is explicit work below.

## Domain model: a bounded live conversation

Use provider-qualified identifiers throughout. A logical PR key includes forge
kind, canonical endpoint, repository identity and provider PR ID; authorized
account/principal and service namespace qualify access, caches and observation
sharing. Display names, repository-relative PR numbers and role labels are
never global identities.

Recommended logical records, not a mandated wire or physical schema:

| Record | Purpose and retained meaning |
|--------|------------------------------|
| PR | Current state, branches, exact head/base/common baseline, original author, merge identity/time, provider-native status and observation completeness |
| Participant / interest | Provider actor identity and author/reviewer/merger relationship; repository/path/subject interest and assigned reviewer are separate from permission |
| Review round | Candidate and discussion lineage, reviewer, verdict, stale/dismissed state, timestamps and counts; a CI poll or repeated comment is not a new round |
| Thread / comment | Current provider object/reply/review links, path/side/line/revision anchor, resolved/outdated/deleted status, current bounded body and modification evidence |
| CI / policy gate | Provider run/attempt/check identity, requiredness, state, candidate binding, native conclusion and freshness; unknown is distinct from passing |
| Event | Monotonic local sequence, provider object/revision identity, kind, candidate, observation interval and minimal changed-field summary |
| Subscription / delivery | Criteria, lifetime, independent cursor/ACK progress and existing durable request/event identities |
| Attention projection | Role/policy-qualified reason, priority, age, latest meaningful event, last handled candidate/cursor and availability; not a Dispatch task |
| Operation receipt | Original selected effect identity and confirmed/unknown outcomes under the current protected owner |
| Tombstone | Compact terminal identity, relationships, outcome, aggregate rounds/counts/timings and provenance of terminal evidence, without message or patch bodies |

Follow Gitea's established conceptual split rather than cloning its database.
The public [PR model](https://github.com/go-gitea/gitea/blob/main/models/issues/pull.go)
separates head/base, merge state and merger;
the [review model](https://github.com/go-gitea/gitea/blob/main/models/issues/review.go)
binds verdicts to reviewers/commits and preserves official, stale and dismissed
states; the [comment model](https://github.com/go-gitea/gitea/blob/main/models/issues/comment.go)
distinguishes discussion, inline feedback and timeline events.
These are design references, not a dependency, copied ORM or license exemption.
Any later source reuse requires pinned upstream provenance/license review.
Do not inherit provider-specific numeric enums as the cross-provider contract.

Use the current storage owner for atomic state. A relational SQLite
implementation is a candidate for indexed normalized records and journal
queries, not permission to add a parallel truth store beside ReplayStore.
Before changing the current JSON owner envelope, review migration, transaction
boundaries, active-reader compatibility, capacity and crash recovery.
Extend the existing owner/caches first where that suffices.

## Events, cursors and delivery

Webhook/subscription inputs are hints until admitted, scoped provider evidence
confirms the objects needed for an action. Polling and push use one normalization
and deduplication path. Preserve native provider revisions and completeness;
do not infer a gap-free provider timeline from a local sequence.

A consumer reads a bounded page after its own cursor. The response names its
observed candidate, interval, section completeness, high-water mark and
continuation. Cursor ACK is not the same as review completion or provider
mutation success. Out-of-order/duplicate events do not resurrect deleted
comments, old approvals or earlier CI attempts.

The open-PR journal has explicit size/age limits. A cursor older than the
retained low-water mark returns an explicit resync-required gap and current
generation; never fabricate the missing dialogue or silently advance an ACK.
Cursor eviction cannot discard durable effect receipts or pending terminal
delivery. Initial membership, later change, departure, timeout and intentional
reevaluation retain their different meanings.

Notifications use the existing replay/ACK delivery to admitted callers.
Disconnected agents resume the same identity; an accepted PR creation whose
registration fails remains an already-created PR with recoverable follow intent,
not another publication. Many author/reviewer consumers share reads only within
the same admitted provider/account/target boundary. Each keeps its own cursor,
criteria and original deadline.

## Role-aware attention, not autonomous authority

Selections combine repository/branch/label/path or supported native criteria
with interest: Maintainer, Codeowner or SME. Unsupported predicates refuse
rather than broaden. Path/ownership evaluation needs complete current changed
paths; incomplete evidence is unavailable, not proof of no matching work.
One person/agent may hold several interests; the service coalesces their PR
facts without conflating permissions or assignments.

Proposed reason families:

| Reason | Necessary distinction |
|--------|-----------------------|
| Initial review | Eligible candidate has no applicable handled review baseline |
| Follow-up | Meaningful source, discussion, dismissal or policy evidence after that baseline, including unchanged-head dialogue |
| Merge nudge | Current review/CI/consent policy says completion may proceed; this observation does not grant the mutation |
| Rebase | Confirmed current conflict/base condition; no automatic force-push |
| CI re-kick | Specific failed/cancelled/stuck attempt and configured eligible policy; provider rerun permission and retry budget still apply |
| Final-iteration stamp | Defined final candidate/round outcome; not an invented substitute for review or required checks |
| Blocked/unknown | Missing, stale, partial or explicitly held evidence; explain the actual blocker |

Policy decides priority. Within a class, last meaningful unhandled event and
age provide deterministic fairness. A confirmed posted comment/verdict records
the handled candidate/cursor and returns that PR to the tail, without hiding
newer unhandled input. Repeated polls, the service's own echoed feedback and
unchanged snapshots do not continually promote the PR. Aging prevents a busy
high-priority population starving lower-priority admitted work.

Dispatch consumes the selected PR, action reason, candidate and cursor through
its existing emitter contract; it owns assignment and durable target/revision
deduplication. Replayed attention must reconcile that same task/lineage before
ACK. A reviewer posting feedback cannot end another participant's subscription.

Mechanical actions are individually configured and disabled by default.
Auto-merge consumes the same fresh exact-candidate, required checks, independent
review, current consent, holds and repository completion policy as the current
owner. Role, cached eligibility, source equivalence or a shadow proposal never
grants write/merge authority. Reconcile uncertain effects before retrying.

## Terminal retention

On authoritative merged evidence, stop content acquisition and converge to a
tombstone. Closed-unmerged/abandoned is a distinct terminal outcome; disappearance
from a selector, a 404, loss of access or partial discovery is not abandonment.
Capture terminal outcome and minimal consumer notification atomically with
compaction progress so a crash cannot leave a forgotten terminal PR.

Retain PR locator, original author, other reviewers, our author/review/merge
relationships, final native and normalized verdict, merge/closure time,
round count and aggregate timing/count statistics. "Unknown" stays possible
for information the service never observed. Do not retain comment/description
bodies, prior body versions, full patch/diff history or source archives.
Current code is fetched on demand through the existing exact-source owner,
not stored in the event journal.

Pending externally uncertain operation receipts remain a separate minimal
recovery obligation. Remove body-bearing cached generations, materialized
server views, journal payloads, outbox duplicates and submission payloads
through the same retention transaction/recovery policy. Keep only the minimum
identifiers/status necessary to reconcile an unsettled effect, with its owner,
deadline/escalation and no indefinite message retention. Never blindly erase an
unknown receipt to make compaction appear complete. Backups and logs must obey
their declared retention too; logical removal is not a claim of physical secure
erasure, and no zero-fill/TRIM operation is introduced.

A genuine reopen creates a new live observation epoch from fresh provider data,
retaining the compact prior terminal summary. Old cursors report their epoch
gap; they cannot apply old feedback or claims to the new candidate.

## CLI/API convergence and qualification

Keep CLI parsing strict. The eventual Author CLI covers every current
`create-pr`, `*-pr` and `pr-*` behavior, including body/labels/reviewers, foreign
already-pushed creation, ready/update, claims/attribution, status/wait/watch,
review/thread operations, consent/merge and completion. Source publication
still executes through Worktrees' rightful local/delegated source owner.
The daemon does not acquire arbitrary source-shell authority.

Reviewer CLI families are prepare/load, status, events-after-cursor,
source/reference reads, sync, selected comments/thread actions and explicit
verdict submission/inspection. Exact syntax follows parser/contract design,
not this proposal. Both perspectives use the same HTTP-backed domain model,
not a second client forge implementation. Standalone use can host the same
service locally without Fleet; legacy native compatibility remains until its
reviewed retirement.

Use the existing authenticated Gateway/service seam. Gateway may host the
coordinator endpoint and a UI may consume it, but neither becomes a second
registrar. No new unauthenticated listener, arbitrary URL callback, credential
store or task queue is implied.

### Plan and exit evidence

1. **P0 - Reviewed intent:** land the vision and this amendment; preserve all
   parent acceptance and workspace/conformance boundaries.
2. **P1 - Normalized attention foundation:** reusable provider-neutral candidate,
   conversation/cursor and inert attention projection; focused synthetic
   cases before authenticated service adoption. No provider writes.
3. **P2 - Central intake and journal:** realize caller-driven exact sets and
   selections, bounded current views/event journal, cursor gaps, tombstones and
   recovery through the existing writer/transport. Review the actual persisted
   migration separately before enabling it.
4. **P3 - Author and reviewer CLI consumers:** actual HTTP/CLI parity matrix,
   reviewer prepare/read/sync/submit, current running capabilities and strict
   old/new/rollback behavior; Dispatch emitter consumes real service facts.
5. **P4 - Driver qualification:** ADO, GitHub and Gitea each exercise their own
   native votes/reviews, threads, CI/policy and terminal states. A shared
   dataclass or GitHub mock does not qualify three drivers.
6. **P5 - Isolated and shadow proof:** actual CLI -> authenticated HTTP ->
   service -> provider fixture, durable restart and terminal compaction;
   admitted container inputs when available, then a separately authorized
   read-only live shadow. Compare decisions/statistics without effects.
7. **P6 - Optional automation and migration:** policy-gated mechanical actions,
   opt-in auto-merge and a thin optional UX; staged consumer migration and
   legacy retirement only after semantic/dependency acceptance.

### Validation matrix

- Per-provider identity, source/head/base, threads/replies/edits/deletions,
  dismissed/stale votes, CI attempts, participants and partial-page coverage.
- Push versus poll duplicates/out-of-order evidence and identical normalized
  meaning; unchanged observation emits no fake change. Measure provider fetch
  counts with overlapping author/reviewer subscriptions.
- Maintainer/path-Codeowner/SME selection; unknown paths/unsupported filters;
  deterministic priority, tail rotation after feedback and aging fairness;
  newer event arriving during feedback is not swallowed.
- Independent cursor/ACK and assignment lineage; same-head dialogue;
  low-water/epoch gap, lost response/ACK, restart and disconnection recovery.
- Merged and closed-unmerged compaction removes all body-bearing active copies;
  unsettled minimal receipts/terminal delivery survive a crash; reopen, stale
  terminal hint and permission loss cannot fabricate closure or resurrect data.
- Every legacy CLI operation's argv/defaults/output/exit status, identity,
  policy/claim/resource side effects and uncertain outcomes compared with the
  new HTTP path before any retirement. Document unsupported evidence explicitly.
- Exact-candidate, independent-review, consent/holds/checks and action-specific
  revalidation at effect boundaries; ambiguous writes reconcile, not replay.
- No effects from a shadow projection or comparison UI. Container, actual
  provider, native platform, installed and live-shadow evidence remain separate
  from source fixtures and require their own applicable authority.

The parent remains Active until its complete Plan and Validation Plan resolve;
none of these finite slices silently closes #6007 or the workspace campaign.
