# Phase 6: unified dispatch session acquisition

Back to the [canonical effort](README.md#phase-6--unified-dispatch-session-acquisition).
Tracking: [#6076](https://github.com/ThomasMichon/copilot-extensions/issues/6076).
Adjacent work: #4038 (durable CLI workers), #5696 (retained headless
conversation recovery), and PR #6064 (gated CLI-resume stopgap).

## Requested contract

**Operator clarification (2026-10-12):** On first assignment, Dispatch calls
ordinary `agent-bridge create` in the target harness, passing the target
charter and **no initial prompt**. Creation returns the worktree, bridge
handle, and readiness status awaiting the first prompt. Dispatch persists
those facts in task information **before** ordinary `agent-bridge send`
delivers the assigned task as the first prompt, then records the returned
session information.

Follow-up task lifecycle, system/emitter events, and user/evaluator steering
are deterministic ordinary messages to that retained agent. Reviewers are
Bridge agents like any other: the target charter, assigned-task first prompt,
harness rules, and venue bounds provide guidance. There is no reviewer-specific
transport or parallel agent lifecycle.

Bridge owns agent creation, worktree allocation through its existing
agent-worktrees owner, and retained-conversation resume. Dispatch owns task
reservations, concurrency, claims, leases, authentication, and task bookkeeping.
Creation readiness is not task execution. A failed storage write must send
nothing; a failed or ambiguous send retains the known worktree and handle for
ordinary message retry rather than allocating another agent or discarding
history. No new acquisition receipt, journal, or agent lifecycle replaces
Bridge's ordinary lifecycle.

Explicit CLI and fleet mode choices remain unchanged until their ordinary
Bridge creation capability supports them; they must not silently become ACP.
Standalone non-session dispatch remains usable without optional Bridge.

## Local ordinary create/store/send slice

The local headless factory, label-routed supervisor, and one-shot
`create --spawn --spawn-backend bridge` share `ordinary_bridge.start`.
The authenticated `created-agent` reservation operation atomically stores
the returned worktree, `local-body:<bridge-handle>`, native conversation
identity, and observed readiness in `spawn_reservation.agent_info` while the
reservation remains `reserving` and the task remains queued. Normal task reads
expose those facts. Ordinary send's returned session information is stored
with the existing spawned operation; neither operation fabricates execution
or replaces claim/start/lease admission.

Retried reservations carry the same stored handle, including tasks without
an exclusive key. Strict ordinary resume precedes retry send; failures never
authorize fresh creation. The supervisor's existing reserving reconciliation
preserves stopped, unprompted agents for same-handle retry. An ordinary
reservation with an unrecorded creation result requires explicit repair,
not a timeout-based permission to create again.

**Compatibility boundary:** Acquisition-marked tasks and retained allocations
without ordinary created-agent metadata are explicitly refused. Their old
receipt/start-authority checks remain enforced and their markers are not
deleted or fabricated. The existing fresh-acquisition recovery operation
authorizes the old acquisition contract, not ordinary creation, and is not a
migration operation. A separately authorized, identity-verified conversion of
these existing tasks remains with #6076's integration/recovery owner; this
slice makes no live conversion and does not permit raw database edits.

**Agent-recommended realization details, not additional operator requirements:**
store metadata in the existing reservation row; retain the existing
`local-body:` cleanup/liveness handle; stamp the ordinary backend on the
reservation before creation so an ambiguous creation cannot be timed out into
another allocation; use Bridge's existing registered target or explicit
`repo@venue` address to select the target harness. Generic initial prompts
include the native identity needed by the normal owner-gated start command.

The source inventory and receipt-based implementation notes below are
**historical evidence**, not the current lifecycle contract. Their bespoke
acquisition recommendations are superseded by the operator clarification.

### Local validation boundary

Bounded isolated selections exercise creation readiness, exact
create/store/send/publication ordering through a supervisor poll, authenticated
task-information reads and storage, unchanged native start/lease authority,
same-handle interrupted-send retry, restarted unprompted-agent preservation,
reviewer/nonreviewer parity, and retained legacy guards. They do not credit
native process-exit/transcript, Windows, fleet, release, or production proof.

Full-module mypy still reports 50 inherited diagnostics across the client,
spawn factories, queue reservations, supervisor, and Bridge targeting module
(54 on the untouched source baseline, zero introduced after normalizing source
line offsets). Seven touched leaf modules, including the ordinary startup
helper and authenticated storage routes, type-check cleanly. The remaining
baseline typing work is a named integration limitation under #6076, not a
claim that the full-module type check passed.

## Evidence and limits

The headless adapter already has conversation-preserving recovery:
`plugins/agent-dispatch/src/agent_dispatch/bridge.py:spawn_or_resume_worker`
combines carried-session handling with retained-worktree recovery.
`spawn_factories.resume_worktree_eligible` admits directory-level recovery
only for reused allocations whose retirement marker is explicitly false.
This existing strict recovery is prior art, not something to replace.

The CLI stopgap in PR #6064 threads that eligibility decision into
`spawn_embodied_worker`. CLI acquisition still uses an independent worktree
launch boundary; the stopgap does not consolidate session ownership.

`agent-bridge create` historically means a fresh session, whereas bridge
resume/send already recover conversations. The operator's automatic
acquisition expectation requires reconciling these surfaces, not assuming
that cold resume is wholly absent. Inventory current source before coding:
earlier diagnosis against an installed release is not proof of the current
development branch or of which backend an individual incident exercised.

## Coordination

| Participant | Owned slice | Reach |
|---|---|---|
| Plan coordinator | Phase 6 scope and integration | #6076 and the plan PR |
| Implementation owner | Phase 6 runtime and validation, after plan review | Claim #6076 before changing runtime source |

Use serial issue-bound worktrees targeting `dev`. Do not launch a parallel
implementation against these shared modules without recording a disjoint
slice in #6076. A handoff carries the current issue/PR, phase, and unresolved
decisions; this document remains the canonical contract.

## Implementation plan

### Source inventory (2026-10-10)

Verified against the reviewed plan's `dev` snapshot, including PR #6064.
This change closes the existing session-hosting and remote-interactive-sessions
visions; it introduces no new execution host or daemon.

| Caller / surface | Selection and acquisition today | Identity / remaining migration |
|---|---|---|
| Dispatch `create_cli._do_spawn` | `spawn_backend=bridge` defaults to ACP; `embody` explicitly selects CLI, with a fallback to ACP when worktrees is absent | ACP carries `local-body:<bridge-id>`; CLI parses the worktree launch handle. Remove mode-changing fallback and route both acquisitions through bridge |
| `spawn_factories.make_embody_spawn` | Direct `embody.spawn_embodied_worker`; allocation interface `cli` | Worktree/session handle; keep allocation and CLI conclusion semantics |
| `spawn_factories.make_headless_spawn` | `bridge.spawn_or_resume_worker`; allocation interface `acp` | `local-body:<bridge-id>`; replace consumer liveness/resume/create decision with facts |
| `interactive_embody.launch_interactive_embodiment` | Claims/reserves and prepares the worktree before direct CLI launch | Bind the exact native session after launch; keep prelaunch claim rollback |
| `fleet.FleetSpawner` | Explicit `headless` selects fleet ACP vs CLI; caller also decides carried-session recovery | Synthetic task lease owner is not a native conversation ID |
| `embody.spawn_fleet_embodied_worker` | SSH to remote `agent-worktrees embody --new` | CLI worktree/session result; move session acquisition beneath bridge, not allocation or transport |
| `embody.spawn_fleet_headless_worker` | Bridge carrier `create_session`, with remote bridge CLI fallback | `fleet-body:<host>:<bridge-id>`; remote acquisition needs the same versioned contract |
| Bridge `BridgeClient.start_session` / session route | `POST /api/v1/sessions`, caller-affinity reuse unless `force_new` | Returns bridge handle; native ACP ID is separately held by `Session.acp_session_id` |
| Bridge session resume | `SessionManager.resume_session(..., allow_recreate=False)` loads native ACP identity | Verify the native ID after recovery, including live-host reattachment; never use prompt auto-resume's `allow_recreate=True` |
| Bridge worktree resume | Protocol 28 strict resume disables fresh fallback; per-worktree fresh-start locks and SQLite ownership guard already exist | Latest bridge session in a directory alone is not proof of task affinity |
| Bridge CLI create / local CLI-mode launch | Namespaced `create --cli` delegates provider `copilot`; local CLI-mode reservation composes over worktree `embody` | Provider/mux handle is not a native ID; registration must prove exact conversation identity |
| Worktrees `embody_resume` | Seed starts fresh unless gated `resume-head`; `with_resume` uses native ID and `with_seed` uses `--interactive` | `resolve_resume_target` has filesystem fallback; acquisition must not use that fallback without task authority |

`resume_worktree_eligible` requires ownership `reused` and retirement exactly
`False`. Missing retirement is unknown, not fresh intent. The new boundary
must carry those distinct facts instead of collapsing them into one boolean.
`worktree_head.resolve_head` is deliberately fail-open for legacy create;
automatic acquisition must not mistake an unreadable head for proven absence.
Bridge prompt submission can recreate context during automatic recovery, so it
cannot implement strict acquisition/delivery unchanged.

### Foundation slice and remaining runtime work

The first runtime slice defined a strict bridge-owned request/result and a
durable acquisition/delivery fence. It does not advertise a new HTTP protocol
capability until a real route and CLI implementation can uphold it. Existing
create/resume APIs and dispatch launch behavior are unchanged by this slice.
The fence rejects a reused delivery key with different request facts, competing
worktree operations, and replay after an ambiguous interruption; it never
expires a pending operation into permission to create or redeliver.

Remaining Phase 6 work: connect the fence to all live-holder registration and
launch paths, compose native-ID-preserving ACP/CLI lifecycle adapters, expose
the capability-gated CLI/HTTP operation, and migrate **all seven dispatch
surfaces above**. No caller migration, native process-exit integration,
provider parity, release, or rollout is credited by the foundation tests.

### Connected local slice (2026-10-10)

The subsequent slice exposes `agent-bridge acquire --request-file -` and
`POST /api/v1/acquisitions`, capability-gated at HTTP protocol 30. It composes
ACP SessionManager lifecycle and unseeded exact-native CLI embodiment over the
existing worktree/provider owner. The request's generation is verified against
the current task admission fence; same-task historical generation is not
mistaken for immutable transcript lineage. None-valued options mean no override.

| Surface | Connected local status |
|---|---|
| `create_cli._do_spawn` | CLI/ACP acquire and deliver through bridge; no mode-changing direct fallback |
| `make_embody_spawn` | Bridge acquire; CLI/native cleanup handle retained |
| `make_headless_spawn` | Bridge acquire; `local-body:<bridge-id>` retained with separate native ID |
| `launch_interactive_embodiment` | Claim/reservation remains dispatch-owned; bridge acquisition; uncertain launch retains claim |
| `FleetSpawner` | **Not migrated**; remote selection/resume/create still outstanding |
| `spawn_fleet_embodied_worker` | **Not migrated**; remote direct launch still outstanding |
| `spawn_fleet_headless_worker` | **Not migrated**; remote carrier create/fallback still outstanding |
| Legacy `bridge.spawn_or_resume_worker` / `spawn_worker`, `embody.spawn_embodied_worker` | Production local callers migrated; helper removal and external/remote compatibility still outstanding |

Durable stages reconcile interrupted launch and prompt admission on same-request
retry or current same-task reservation. Cross-process executor exclusion prevents
an active attempt being “recovered” concurrently. Native ACP turns carry a
unique acquisition delivery ID; CLI retains existing guarded inbox idempotency.
Live and unknown holders never become launch permission. The lifecycle verifies
selected native evidence and excludes authoritative retired IDs even if the
consumer omitted them.

Remaining: remote/fleet migration and capability gates; complete CLI execution-
option support (currently refused explicitly); unproven/ambiguous native launch
recovery without surviving identity evidence; terminal receipt retention;
native process-exit/transcript and Windows proof; reviewed merge, release and
authorized rollout. Local adapter tests use actual SessionManager and SQLite
with process/native-provider I/O simulated; they do not resolve native integration
items in the validation matrix.

The following are **historical agent recommendations**, superseded by the
ordinary lifecycle contract above; they document the earlier receipt-based
implementation, not instructions to extend it.

1. Inventory every dispatch session-launch entry point (one-shot, supervisor,
   interactive, and remote fleet), mode selectors, carried handles, worktree
   affinity, retirement evidence, and existing bridge resume/launch contracts.
   Record the mapping here before implementation; do not conflate bridge
   handles with native Copilot conversation IDs.
2. Specify one bridge acquisition request/result. Carry venue, explicit mode,
   authoritative task/worktree affinity, prior conversation identity, and
   freshness intent. Return the exact durable conversation identity, bridge
   handle, mode, and whether acquisition reused, resumed, or created.
   Final flag names and protocol version are chosen from current repository
   conventions during this slice, not invented by this plan.
3. Implement bridge-owned acquisition using existing lifecycle/provider
   mechanisms. Deliver to a live matching holder without spawning another
   process; resume a stopped matching conversation by its native ID; create
   only for a proven new allocation or explicit fresh/retired intent.
   Unknown ownership, missing continuity evidence on a retained allocation,
   or failed strict resume yields a typed refusal/deferral, never success
   disguised as a fresh conversation.
4. Reconcile public `create` semantics explicitly. Preserve intentional fresh
   creation with an explicit freshness control; define worktree-bound
   automatic acquisition and its compatibility/version gate in the bridge.
   Do not silently change unbound callers or turn operator retirement into
   permission to revive the retired transcript.
5. Move dispatch session acquisition and delivery onto that boundary for
   local and remote CLI/ACP paths. Dispatch conveys validated mode, affinity,
   and freshness facts but does not implement another resume/create state
   machine. Worktree allocation and terminal cleanup stay separate. Remove
   direct session-launch fallbacks only after caller coverage proves parity.
6. Document the request/result, mode/freshness compatibility, ownership,
   missing-capability behavior, and source-to-release rollout. Land reviewed
   runtime slices with changefiles; promotion and authorized rollout are
   distinct from a merge. Record exact running-version proof separately
   from unit-level continuity evidence.

## Validation Plan

All cases run for CLI and ACP where the provider advertises that capability;
unsupported provider/mode combinations must refuse explicitly.

| Case | Required observation |
|---|---|
| New allocation | One new native conversation; requested mode and worktree binding |
| Live matching conversation | Delivery to the same native ID; no second process or duplicate prompt |
| Stopped matching conversation | New process, same native ID and prior transcript; follow-up work sees prior context |
| Deliberately retired conversation | Fresh conversation by explicit policy; retired ID never resurrected |
| Fresh task targeted at an existing directory | No unrelated transcript inherited merely because a worktree ID exists |
| Retained allocation with unknown/missing identity or retirement marker | Explicit refusal/deferral unless authoritative owner evidence resolves uncertainty; no silent fresh fallback |
| Rewritten, ambiguous, foreign, or busy owner | Fail closed without takeover, stopping, or replacing the holder |
| Concurrent acquisition and retries | One authoritative holder and idempotent delivery; no duplicate turns |
| Pending or untaken steers | Durable steer remains available and is consumed exactly once |
| Mode/protocol/capability skew | No silent CLI/ACP switch or weakened strict-resume guarantee |
| Missing bridge | Standalone dispatch still works; session acquisition clearly unavailable |
| Local and supported remote venues | Same acquisition/result contract and identity proof across transport boundaries |

Use bounded targeted tests for the changed bridge, dispatch, worktree, and
provider contracts. Add an isolated process-exit/resume scenario that records
native conversation IDs before and after the restart and verifies retained
context; matching task/worktree IDs alone does not pass. Exercise each migrated
entry point, including explicit fresh and retired cases. Run applicable
clean-room/provider scenarios where practical, and disclose any unavailable
venue proof rather than crediting a mock as a live integration result.

## Scope boundary and completion

This phase closes existing session-hosting and remote-interactive-sessions
intent: one coordinated hosting boundary, explicit CLI mode, and no duplicated
replay/retirement protocol. Honor the patterns' optional-capability composition,
one command owner, cross-platform parity, and truthful errors.

No new daemon, session-host wrapper for CLI, queue evaluator rewrite, physical
deployment policy, or unrelated historical effort cleanup belongs in this
slice. If a standing vision genuinely contradicts the selected contract,
resolve that contradiction before runtime implementation.

Phase 6 is complete only when the mapped session-acquisition callers use the
bridge boundary, the validation matrix is resolved with honest evidence,
authoritative docs match, and the runtime changes have merged. Record release
and authorized rollout status without claiming uninstalled code is live.
Completing Phase 6 does not close this effort's unrelated remaining phases.

## Recovery incident and validation boundary (2026-10-12)

The local consolidation in #6123 made worktree-owner invocation mandatory for
dispatch acquisition. Its legacy resolver used ambient command discovery,
which failed under a native service's minimal PATH despite an installed owner.
It also marked dispatch acquisition before admitting the bridge journal.
Consequently, an owner-lookup failure could leave a retained allocation without
positive first-creation evidence. #6230 corrected attributable owner lookup;
#6243 retained preflight receipts and first-conversation retry authority.

Fresh-authorized recovery then exposed a second missing state combination:
an interrupted creation left a real native holder, but the carried retirement
marker demanded another creation and refused that holder. #6269 / #6270 address
identity-bound recovery, consume recovery authority after actual admission,
preserve exact delivery replay, and recognize older superseded launching stages
only through positive native caller/driver and task-generation binding evidence.
Historical stages remain unchanged; missing history never proves absence.

The original connected tests mocked worktree commands, native connections,
process spawning, and prompt execution. They covered interruption and retirement
separately, not their important combined retry case. Those passing component
contracts did not establish native-service execution readiness. New CLI/ACP
regressions cover that combination, pre-completion admission, completed replay,
legacy receipt gaps, and interrupted reuse with an unchanged process driver.

The existing native integration gate remains open. Before crediting a rollout,
exercise the real owner invocation under service-equivalent routing/PATH and
prove actual native creation, turn admission, interruption, and same-context
recovery without replacing those boundaries with mocks. Component checks,
daemon health, an allocated directory, or an idle native holder are not substitutes.
