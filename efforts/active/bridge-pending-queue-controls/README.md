*The following contribution was assisted using Copilot.*

# Bridge Pending Queue Controls

## Goal

Independently build the pieces of the phantom pending-queue fix that are not
covered by #13770: the supported outside-session pending-queue inspection/
reconciliation command, read-only live diagnosis, and any distinct necessary
fixes that diagnosis establishes. Do not wait for or seek further owner
coordination. Build on current Dev and the merged #13770 idle-submission and
truthful-hint repair without editing its files, merging it or taking it over.
Drive this slice's
own PR through focused validation, normal review/CI and merge, then authorized
normal suite deployment without risking termination of all agents.

After that repair rolls out, inspect the selected live repro read-only to
determine whether it is sufficient and retain any remaining queue-visibility
or delivery gap honestly.

Do not hand-replay or drain a live queue, replace sessions, or restart services
outside normal deployment. Preserve original message/session identity,
ordering, mute, uncertain effects and the distinction between admission, SDK
acceptance, model input and consumption. A queue view must expose unknown or
unsupported coverage rather than infer an empty queue from registry liveness
or an attention response.

- **Slug:** `bridge-pending-queue-controls`
- **Repo:** copilot-extensions
- **Branch(es):** independent, dependency-ordered PRs targeting `dev`
- **Created:** 2026-11-20
- **Status:** Active
- **Vision:** [Bridge inspectable delivery decisions](../../../visions/plugins/agent-bridge/README.md#inspectable-delivery-decisions)
- **Umbrella issue:** #13734
- **Related effort:** [Bridge Queued Steering](../bridge-queued-steering/README.md)

## Guiding Intent

Give an authorized outside observer a truthful, useful queue/progress view and
an explicit reconciliation operation through the existing delivery owner.
Do not create a second inbox, sender, receipt ledger, watcher service or
prompt-per-message acknowledgment loop. Diagnose the actual disagreement
between a repeated post-tool pending notice and a queue-free attention view
before choosing a remedy.

## Participants

| Participant | Role | Reached via |
|---|---|---|
| Queue-control implementer | Read-only diagnosis, outside command, focused controls and this slice's PRs | #13734 and this effort |
| Existing idle-delivery author | Retains #13770 and its P13734-1/2 implementation | #13770 |
| Existing Bridge/Core and receipt owners | Retain session, registration, inbox, SDK and model-input custody | Existing delivery interfaces and parent effort |

## Coordination

- **Topology:** independent per-slice PRs; no shared branch or merge slot.
- **Host (owns this slice's PRs):** queue-control implementer.
- **Delegates:** none assigned.
- **Handoff:** preserve exact observations, unknown effects, source/queue holds,
  this Goal and all open delivery/release/adoption obligations.
- [Public coordination](https://github.com/ThomasMichon/copilot-extensions/pull/13770#issuecomment-7054244975)
  records the non-overlap. Do not duplicate the idle/hint repair, edit
  #13770's eight files, merge that author's PR or take over its workspace.
  A later operator instruction removes further contact/coordination as a
  prerequisite: proceed independently with the disjoint pieces. Preserve an
  actual overlapping-file gap rather than editing those files or posting
  additional coordination notes.
- The parent effort README remains with that in-flight PR. This separate,
  indexed home captures the newly assigned command/diagnosis scope without
  changing the parent's files or claiming its broader acceptance.
- The separately reported carrier handshake, exact model-input receipts,
  duplicate-safe timed fallback and broader #6394/#10765 outcomes retain
  their actual owners and gates. A command or successful source fixture does
  not close them.

## Context

#13734 reports a Windows interactive registered session whose post-tool hint
repeats a pending count, but ending the turn does not submit that work.
A later unrelated turn receives it instead. A later observation also reports
an attention response with no pending detail while the same receiver's hook
continues reporting pending SDK submission. These are attributed incident
observations, not a confirmed common cause or a live reproduction by this
implementer.

The existing #13770 merged as
`68d1a74b1712f194c811ab00225647a57cc774e8`, adding coalesced
idle/registration wakes, current native-session binding and truthful hint text
through its original owner.
Its author reports 153 controlled source/API cases, not installed or native
model acceptance. This effort composes with that work instead of repeating it.
Registry presence, turn state, server inbox state, SDK acceptance and actual
model input must remain distinguishable.

Initial read-only observations from the actual receiving host identify
different projections for the same selected native identity: attention reports
a live registration at reduced fidelity, while full status reports an ended
session without project/worktree binding and neither view exposes queue
fields. This does not establish that the receiver is dead or locate the
pending notice's cause. Inspect the owning resolution and queue seams before
interpreting one projection as authoritative for another.

## Request

Public-safe summary of the 2026-11-20 operator assignment and its later
coordination clarification; deployment-specific communication is retained
separately, not reproduced here:

1. Perform the additional end-to-end diagnosis and necessary #13734 repair,
   initially naming P13734-1/2 and a supported outside pending-queue
   inspection/reconciliation command.
2. After finding actual OPEN #13770, leave its idle/hint implementation and
   files to its author. Coordinate on GitHub rather than wait for a Bridge
   contact, and proceed with the disjoint command and read-only diagnosis.
3. Drive this slice through ordinary source validation, review/CI and merge.
   Normal suite deployment is pre-cleared unless it risks terminating all
   agents.
4. Manual live queue replay/draining, session replacement and service
   restarts outside normal deployment are not granted; ask the operator if
   one is necessary.

The latest operator amendment is retained verbatim where public-safe:

> Don't coordinate; just make te pieces of the fix that it can make

It supersedes further owner contact or GitHub coordination notes, not the
no-overlap boundary. Build the outside command, read-only diagnosis and any
additional necessary fixes not covered by #13770; leave that PR and its files
alone and drive this slice's own reviewed PR to merge. Earlier completed
coordination is history, not a condition to repeat.

The subsequent direction confirms that #13770 is merged: build the remaining
pieces on current Dev and use the selected live repro read-only after rollout
to assess sufficiency and any remaining gap. A source merge is not that
rollout or a reason to operate the live queue manually.

The initial local placement of this amendment in the parent README is
superseded by this non-overlapping canonical home, without dropping its scope
or broadening deployment/queue authority. The parent retains its original
Goal, receipt/fallback intent and all technical check states.

## Scope Approvals

The assignment permits read-only diagnosis of the selected live failure,
necessary source changes for this command slice, bounded owned fixtures and
normal reviewed publication. It grants normal suite deployment, subject to
the all-agents termination-risk gate. It does not grant manual live queue
effects, session replacement, outside-normal-deployment restart, credential
or access changes, new spending, other-owner resource custody or edits to
#13770's files.

Implementing a reconciliation operation is not permission to use it to drain
the live incident. Production inspection remains non-mutating; exercise
effectful reconciliation only in owned fixtures unless separately authorized.
Preserve actual source/copy restrictions and prior spent allowances rather
than treating a new effort or observation as a reset.

## Plan

The decomposition and implementation choices below are agent recommendations,
not additional operator requirements.

### Phase 1 - Diagnose and review the finite contract

- [x] Find the actual overlapping PR and publish a non-overlap coordination
  note, leaving its author, branch, files and delivery responsibility intact.
- [ ] Inspect the selected live state read-only and trace the existing inbox,
  notice, SDK submission and receipt owners; record the actual causal boundary
  and which observations are unavailable.
- [x] Review this finite command/diagnosis plan before implementation.

### Phase 2 - Implement through the existing owner

- [x] Add supported outside-session pending-queue inspection with structured
  stage/identity/coverage/freshness information, existing bounds and truthful
  empty, unavailable, unsupported and uncertain outcomes. Do not expose
  message bodies by default or make an inspection submit/consume work.
- [x] Add explicit bounded reconciliation through the existing queue/delivery
  owner, preserving original IDs/order, mute, serialization, receipt
  monotonicity and unknown native effects. Do not resend bodies, rewind
  cursors, forge consumption or silently clear uncertainty.
- [ ] Compose with #13770's selected consumer behavior without editing its
  files; preserve supported old/current runtime behavior. Implement distinct
  causal fixes that the diagnosis establishes outside that PR's coverage,
  without further owner contact or coordination notes.
- [x] Document the actual command, scope and refusal semantics through its
  existing CLI/service documentation owners.

### Phase 3 - Deliver and verify

- [ ] Pass focused source/API and command/provider controls, relevant
  lint/type/contract checks, current-head review and every required CI gate.
- [ ] Merge this slice and identify its containing ordinary release
  separately from the other author's PR and the source merge.
- [ ] Complete authorized normal rollout and selected running-state
  verification without live queue replay/drain or session replacement.
  Ask first if the actual deployment risks terminating all agents.
- [ ] Resolve every finite Plan/Validation item or transfer an independent
  remaining adoption obligation to a named accepted objective before
  closeout; retain the broader parent and carrier outcomes.

## Validation Plan

- [ ] Current read-only observations distinguish actual backend inbox,
  receiver notice/SDK stage, attention and registry state. Missing visibility
  remains unknown, not zero or model-consumption proof.
- [ ] The actual CLI/provider inspection path covers pending and empty queues,
  exact target/registration changes, active/idle/resumed/muted receivers,
  stale observations, unavailable or unsupported runtime and bounded
  pagination/output without submitting or settling any work.
- [ ] Reconciliation has a useful positive owned-fixture case and explicit
  unsafe/unsupported controls. It preserves original message IDs/order and
  cannot duplicate a submission when an idle wake races it, replay an
  uncertain native effect or fabricate consumption.
- [ ] Structured output and documentation describe real executing-runtime
  support; compatibility refusal is not an implicit successful empty queue
  or a requirement to update every peer together.
- [ ] Existing receipt, mute, send serialization, normal messaging and
  #13770 composition controls retain their original semantics. Test helpers
  keep finite containment, caller-owned deadlines and exact cleanup.
- [ ] The final diff is disjoint from #13770's eight files and passes
  ordinary review/CI. No simulated test or author-reported result is called
  native model acceptance.
- [ ] Source merge, containing release, authorized installation and actual
  selected running behavior are reported separately. No excluded live queue
  operation, session replacement or outside-normal-deployment restart occurs.

## Proposal

First determine where the reported pending count lives and which existing
owner can reconcile it safely. An attention result is not documented as an
inbox census, and a server-empty view cannot erase SDK-accepted or uncertain
native work. Reuse an existing queue/consumer seam where possible; any needed
boundary expansion must advertise supported semantics and retain old-peer
refusal without inventing a second delivery authority.

## Journal

### 2026-11-20 - Keep schema bootstrap within its module boundary

- Moved the existing exact-native column validator into the cohesive
  live-message schema owner alongside unresolved-original index preparation.
  The bootstrap retains its existing compatibility export and invocation;
  no validation condition or receipt semantics changed.
- The changed-file hard module-size check passes without raising a baseline.
  The same 41 focused tests and twelve-file Linux/Windows strict typing
  analyses pass after extraction. Current-head CI and re-review still precede
  merge; source approval does not establish release or live adoption.

### 2026-11-20 - Bound unresolved-page database work

- An output limit alone does not bound a history scan or temporary sort under
  the shared database lock. Added a schema-managed partial `(session_id, id)`
  index with the same unresolved-original predicate used by both operations.
- Ordinary writable preparation adds only this derived index. Durable schema
  version and message/receipt state are unchanged; an unprepared read-only
  database refuses the optional page rather than performing an unbounded scan.
- The focused packet now passes 41 tests. Both actual operations select the
  matching index without a temporary sort over 10,000 settled fixture rows
  and execute their page in fewer than 1,000 SQLite VM steps. Legacy read-only
  refusal and receipt-preserving writable preparation pass.
- The expanded twelve-file whole-file strict ratchet passes Python 3.10
  analysis for Linux and Windows at the actual managed-publication merge base.
  Current-head re-review, required CI, merge and running qualification remain
  open; no native latency or live queue effect is claimed.

### 2026-11-20 - Backend command implementation begun

- The finite plan passed review and merged as #13774 before implementation.
- Traced the distinct owners: delivery attention observes pending native
  questions/permissions and mute, while the pending hook observes a local
  offered-but-not-submitted set. Neither is a backend inbox census. The
  existing retained ledger separately records offers, started attempts,
  SDK acceptance and consumption.
- Added a disjoint metadata-only backend inspection path and bounded original
  offer preparation through the existing compatible inbox owner. This does
  not implement a second native sender or clear a receiver-local notice.
  Current SDK/notice-set coverage remains unavailable; live sufficiency
  still awaits the containing release and authorized rollout.
- Kept a bounded, expiring diagnostic projection of the existing emitted hook
  notice separate from backend receipts and the unobservable native SDK queue.
  An older notice or changed consumer cannot establish a current pending set.
- The contained command/API and existing delivery-state packet passed 38 tests,
  including original-offer preparation racing normal polling, unchanged
  uncertain receipts, mute and strict old-provider refusal. The changed
  nine-file strict typing cohort passed for both Python 3.10 analysis targets,
  Linux and Windows; this is not native Windows execution or project admission.
- Required changed-Python lint, documentation consistency and the installation
  contract guard passed. Whole-project Bridge typing remains pending under
  #9807; its broader probe stops at duplicate spike module names. A moving
  base also selected unrelated files, so the passing changed-code ratchet is
  explicitly pinned to this implementation's actual base.
- Source review/CI, merge, containing release, authorized installation and the
  selected running-state observations remain open.

### 2026-11-20 - Non-overlapping assignment captured

- Found actual OPEN #13770, read its qualified complete description and
  preserved its P13734-1/2 custody. Published and read back the separate
  queue-command coordination note.
- Captured the additional diagnosis/command assignment, later no-overlap
  clarification and normal-deployment class with their live-effect
  exclusions. Restored the parent's unpublished local amendment to its
  unchanged incumbent content; the newly assigned intent lives here.
- Captured the later independent-execution instruction: no further owner
  contact or coordination notes; retain #13770's files and responsibility
  while building the disjoint necessary pieces.
- No command implementation, live queue operation, installation or deployment
  is claimed by this plan.
