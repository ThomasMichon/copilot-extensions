# Current direction and coordination

Canonical component of [this same Active effort](README.md). This is not a child effort, transfer or independent completion gate.

## Guiding Intent

The [worker-decline preparation slice](journal/2026/11.03-worker-decline-preparation.md)
retains a native-fenced decision and failed-closeout blocker without releasing
the allocator's assignment; its source merged in
[#10298](https://github.com/ThomasMichon/copilot-extensions/pull/10298).
The [own-worker Worktrees provider slice](journal/2026/11.03-own-worker-closeout.md)
adds exact local-native admission around normal finalization without task release.
Its source merged in [#10306](https://github.com/ThomasMichon/copilot-extensions/pull/10306).
The [local coordinator observation and atomic routing/requeue slice](journal/2026/11.03-local-decline-closeout.md)
is published in [#10319](https://github.com/ThomasMichon/copilot-extensions/pull/10319).
It merged after exact-head approval and required CI as `7b0d58030f`.
Its drain, allocation-driver, replacement-creation and retained-budget snapshot
corrections pass 546 Dispatch and 256 unchanged Worktrees controls.
Ordinary consumer source integration merged in
[#10325](https://github.com/ThomasMichon/copilot-extensions/pull/10325)
as `cfe20b2d97` after exact-head approval and required CI.
Its canonical Worktrees CLI, worker decline command, independent decision
nudges and existing allocator supervision pass 682 Dispatch and 247 Worktrees
controls. Initial and retry templates retain their released bytes.
Remote decline and installed/native qualification remain open.

**Current consumer-adoption prerequisite (2026-11-03):** the
[independent resident lifetime slice](journal/2026/11.03-independent-resident-lifetime.md)
repairs shared Linux daemon launches that remain in their caller's systemd
cleanup cgroup; its source merged in #10234. Source controls and isolated native lifetime proof do not move
existing residents or qualify ordinary Dispatch recovery.

The neutral-service source repair merged in
[#10268](https://github.com/ThomasMichon/copilot-extensions/pull/10268).
It uses Dispatch's existing durable machine binding for
managed-lane admission from neutral service directories, retaining project
fallback, unavailable/mismatched-host refusal and literal execution identity.
Its source/contained controls do not establish installed lane or allocator
acceptance; see the [usability plan](usability-validation.md).

**Operational milestone (2026-11-02):** the
[ordinary usability plan](usability-validation.md) records earned source
progress, the ordered remaining lifecycle/integration work and the actual
running acceptance matrix. Use authorized source-backed live or whole-stack
container validation for fast feedback; a source PR/release wait is not itself
an operational test. This does not waive review, resource ownership or effect
authorization, and does not declare the parent complete.

**Continuing direction (2026-10-30):** the
[assignment/startup amendment](assignment-startup-amendment.md) distinguishes
pool-owned placement before embodiment from worker acceptance, and adds bounded
startup, scoped decline routing/closeout and retry-eligibility design. The existing
queued due-time admission fix merged in
[#9755](https://github.com/ThomasMichon/copilot-extensions/pull/9755);
The explicit atomic placement and affirmative-start provider source merged in
[#9859](https://github.com/ThomasMichon/copilot-extensions/pull/9859).
Admitted caller/startup integration, the remaining design and installed/native
qualification remain open.
The [explicit caller boundary](../../../plugins/agent-dispatch/docs/pool-claim-start.md)
adds negotiated allocator requests and opt-in current-worker acceptance;
its source merged in [#9955](https://github.com/ThomasMichon/copilot-extensions/pull/9955).
The [explicit startup-policy source](journal/2026/11.02-bounded-startup-source.md)
merged in [#9976](https://github.com/ThomasMichon/copilot-extensions/pull/9976):
existing supervision consumes bounded claim episodes and original same-native
retry receipts for explicitly placed assignments. Complete configured
publication, ordinary supervisor/initial-prompt migration and installed/native
qualification remain separate gates.
Existing submission/conversation phases and their custodians remain unchanged.

**Priority clarification (2026-10-31):** drive the
[five lifecycle outcomes](assignment-startup-amendment.md#prioritized-lifecycle-clarification):
claim/spawn/affirmative start ordering, explicit rejection and attempt/timeout
semantics, reserved-slot/work-start deadline consistency, suspended recovery
with retry/backoff, and mechanical closeout readiness before `SUBMITTED`.
The November 3 [submission clarification](submission-closeout-amendment.md)
retains the worktree and task claim through review, resumes the same worker on
rejection and permits permanent finalization only after `COMPLETED`.
An opt-in worktree-owned agenda
keeps this roster ahead of nonblocking discoveries without changing the
canonical completion gate or existing source/native ownership.

**Current direction (2026-10-08):** extend the existing lifecycle, result
storage, evaluator, and steering machinery into a durable task conversation.
Workers only SUBMIT; independent policy-driven confirmation makes a task
COMPLETED. Manual tasks default to operator approval. The
[task outputs and review vision](../../../visions/plugins/agent-dispatch/task-outputs-and-review/README.md)
is canonical intent; [the current amendment](task-conversation-amendment.md)
contains the approved design decisions, dependencies, and validation matrix.
The older `completed`/`confirmed` terminology below records earlier phases;
new work uses `SUBMITTED`/`COMPLETED`, not a new `CONFIRMED` state.

Realize the two new core primitives the
[agent-dispatch vision](../../../visions/plugins/agent-dispatch/README.md)
declared (PR #3622/#3625): **the monitor** (a suspend's companion resolution
handler, with a default cooldown kind for round-robin time-slicing) and
**confirmed** (the true lifecycle terminal beyond a provisional `completed`).
Both close the exact reliability gap an operator observed: a much simpler
external task-queue wrapper — no suspension, force-run to completion, no
opt-in — was proving *more* reliable than this richer machinery, because it
never accumulates the failure modes a monitor-less suspend and an
unconfirmed completion both invite (a task parked with nothing watching it;
a "done" claim nobody ever actually checked).

A third, related idea from the same conversation — recasting the whole
delegation layer as **ACP with unbounded tool-call latency** (a suspend is
an ordinary tool call whose result may take days, delivered by a cold-started
process) — is the mental model this effort should keep coherent with,
particularly for Phase 3's crash-recovery behavior.

## Coordination

The proposed #4202/#4214 finite backend families retain existing task,
writer/client, evaluator/history, steering, owner/session/generation,
handoff and resource custodians. Planning remains documentation only;
separately approved backend slices do not absorb Tasks-pane/UI/native
hosting or #2879/#2887 source-specific reservations. Preserve all holds,
nonexpiry and exact complete-transfer conditions. Normal review and actual
complete direct Plan/local AND actual global Validation/Coordination/Journal
delivery, followed by permitted whole-current readback, precede any separate
source judgment. Unknown acceptance retains the source; no new ACK,
assignment, identity store, writer failover or resource release follows.

The finite #4464 producing-harness guidance contract below continues through
existing producer/task-command/guidance owners and this ROOT's reviewed
Plan, actual global Validation and Journal. Preserve backend versus
Tasks-pane custody and current SUBMITTED/COMPLETED direction. This intake
does not appoint an implementation owner, adopt a code target as a harness,
move a task, change routing defaults or release a source/resource claim.

The complete #3681 retained finite family continues through this existing Active ROOT's
reviewed Plan, actual global Validation and Journal/open bounded-source process under
the SAME declared and source-specific custodians. Preserve every incumbent alternative,
native/source/resource reservation and independent acceptance gate. Closed source
trackers are historical provenance, never required live claim tokens; no assignment,
claim expiry or new blanket ACK is created.

One canonical backend effort; the Tasks-pane effort owns only its UI consumers.
The planning owner lands this documentation amendment before implementation.
Later phase owners record distinct eligible OPEN bounded-source claims through
this ROOT and drive their own PRs
through merge, journaling results and handing unresolved work to a named
successor. The current planning slice does not adopt responsibility for
executing every historical open phase. Reconcile #5701/#5668 after merge rather
than opening competing implementations.

<!-- backlog-reconciliation:7556:transport-bridge-receiving -->

### Continuing source-specific Transport/Bridge custody

#### Source #2885

The planning owner retains documentation only; separately approved
backend owners retain reason/lifecycle/history/output/monitor/delivery
implementation, and existing Tasks-pane, general Worktree Manager viewer
and native-session hosting owners retain their boundaries. Preserve
#2879's specific resource/complete-transfer and #2887's reviewer-policy
reservations without absorbing or expiring them. This is LONG-TERM intent,
not immediate implementation authority. #3681 is provenance, not a required
live claim token. No new blanket ACK or executor appointment is created.

<!-- backlog-reconciliation:7556:qualified-thirteen:3002:Coordination -->

The proposed exact #3002 null-owner-session family retains the existing
steering/delivery and binding-lifecycle custodian. The planning owner handles
documentation only; separately approved backend and UI owners retain their
boundaries. Preserve #2879's exact-worker/resource/complete-transfer and
#2887's policy reservations. #5701/#5668 delivered interfaces remain credit,
not reopened claims; no task takeover, new ACK, implementation appointment or
restoration of historical terminal enums follows.
