# Telemetry event catalog

**Proposed catalog, not an inventory of hooks already installed.**
Use the [QoS/Engagement contract](patterns/telemetry-qos-engagement.md) and the
[design effort](../efforts/2026/10/22%20telemetry-measurement-contract/README.md).
`Q` means QoS operation; `E` means point-in-time Engagement/diagnostic event.
All Q rows have matched attempt starts and terminal records. Shared custom
metrics are `agent.operation.started`, `agent.operation.completed`,
`agent.operation.duration` (`s`), partitioned by registered operation/outcome.
Do not export IDs or arbitrary provider parameters as metric dimensions.

## Manager startup and navigation

| Name | Kind | Start -> meaningful end / occurrence | Required distinctions |
|------|------|-------------------------------------|-----------------------|
| `agent.manager.launch` | Q | Native launcher invocation -> interactive frame and current authoritative view ready | Root E2E; failed/no-frame launches remain in the cohort |
| `agent.manager.first_frame` | Q | Native invocation -> independently observed interactive frame | Headed/terminal observer method; Textual refresh alone is not headed paint |
| `agent.manager.frame_observed` | E | Actual renderer/observer checkpoint | `observation_method`; pending-binding chrome vs operational Picker |
| `agent.manager.machines.load` | Q | Initial request or refresh -> authoritative machine list applied/rendered | Empty valid roster vs failure; count and cold/warm mode |
| `agent.manager.worktrees.load` | Q | Requested view generation -> matching authoritative worktrees rendered | `trigger=initial|machine_switch|refresh`, locality, provider kind, row count |
| `agent.manager.cache_presented` | E | Cached rows first displayed | Not authoritative load success |
| `agent.manager.machine.selected` | E | User selects a machine pivot | Starts a new view generation, not a label containing a machine name |
| `agent.manager.pivot.load` | Q | Provider-pivot selection -> matching usable provider view rendered | Registered provider kind/ID; data readiness and empty/error outcome |
| `agent.manager.actions.open` | Q | Actions gesture -> visible usable menu for the originating row/dialog | Cached usable menu distinct from verified menu |
| `agent.manager.actions.verify` | Q | Background refinement requested -> accepted matching dialog revision applied | Closed/superseded dialog is not a different dialog's success |
| `agent.manager.action.selected` | E | User invokes an available action | Bounded action ID; do not count opening as execution success |
| `agent.manager.result.discarded` | E | Stale/wrong-generation result is rejected | Bounded reason; no stale result credited to the current request |

The existing `FrameHealthReporter`, first-refresh and pending-binding callbacks
are source observations. They do not prove OS first instruction, human-visible
paint or production authority. Unknown boundaries stay unavailable.

## Worktree operations and callback composition

| Name | Kind | Boundary | Required distinctions |
|------|------|----------|-----------------------|
| `agent.worktrees.create` | Q | Command admission -> requested creation result, including every preparation/update phase | All-inclusive E2E; allocation success is not Copilot readiness |
| `agent.worktrees.create.prepare` | Q | Source preparation begins -> prepared source admitted | Child phase, including any nested update work |
| `agent.worktrees.create.allocate` | Q | Allocation begins -> Git worktree and identity committed | Child phase; no exclusion arithmetic |
| `agent.worktrees.create.pair` | Q | Paired allocation begins -> required sibling state committed | Only when applicable; no invented phase for unpaired operations |
| `agent.worktrees.update` | Q | Owner starts update work -> its actual terminal receipt | Existing update owner/journal; no new parallel updater |
| `agent.worktrees.resume` | Q | Resume request -> existing owner's verified requested session-ready receipt | Command return/launch acceptance vs ready; no missing receipt inferred |
| `agent.worktrees.open_live` | Q | Open request -> intended live terminal/session attached and acknowledged | Already-open no-op; wrong/dead target is not success |
| `agent.worktrees.stop` | Q | Stop request -> owner verifies requested execution stopped | Refusal/ambiguous outcome vs stopped; no kill authority from telemetry |
| `agent.worktrees.reclaim` | Q | Reclaim request -> ownership/state cleanup reaches the requested verified result | Retained obligations/refusal are not reclaimed resources |
| `agent.worktrees.plugins.reconcile` | Q | Reconcile admission -> selected roster committed/verified | No-op, install/update work and partial/failure outcomes |
| `agent.worktrees.instructions.inject` | Q | Actual guidance preparation -> delivery owner's receipt | Generated, delivered and host-admitted scopes remain distinct |
| `agent.extension.call` | Q | Extension callback dispatch -> result returned and owned cleanup complete | `component=agent-worktrees|agent-bridge`, callback ID, outcome |
| `agent.hook.call` | Q | One hook wrapper invoked -> output/decision parsed and descendants cleaned up | Plugin, hook event, cold/warm, no-op vs work; includes wrapper cost |
| `agent.hook.dispatch` | Q | Host starts applicable roster -> all required results/cleanup complete | One composed measurement, not sum of provider spans |
| `agent.hook.invoked` | E | Wrapper entry is actually observed | Occurrence counter; not evidence of completion or user engagement |

Creation uses the ordinary parent/child model: one **all-inclusive end-to-end**
span, with preparation, update, allocation, pairing, instruction and any requested
launch phases reported separately where they actually occur. Keep actual nested
timestamps and parentage; do not subtract update time, invent an update-excluded
span or sum overlapping phases to reconstruct total duration. Source fetch/
preparation is not automatically classified as runtime update.

## Bridge and remote startup

| Name | Kind | Boundary | Required distinctions |
|------|------|----------|-----------------------|
| `agent.bridge.launch` | Q | Caller admission -> target session actually input-ready | Logical request vs attempts; full discovery/setup/connect cost |
| `agent.bridge.transport.connect` | Q | Connection attempt -> authenticated usable selected transport | Local/SSH/CodeSpace/container; endpoint change, refusal, timeout |
| `agent.bridge.workspace.prepare` | Q | Target preparation requested -> required checkout/environment ready | Workspace, session-host copy, plugin/runtime stages as children |
| `agent.bridge.plugins.prepare` | Q | Required roster preparation -> target admission/verification complete | First-touch vs already-present; component counts, not paths |
| `agent.bridge.runtime.prepare` | Q | Required runtime preparation -> verified runtime usable | Bounded runtime component (`worktrees|bridge|session_host`), not argv |
| `agent.bridge.session_host.start` | Q | Host launch requested -> validated host-ready receipt | Host-created vs ready; wrong/stale state cannot qualify |
| `agent.bridge.copilot.start` | Q | Copilot launch/init -> protocol/session initialized and input-ready | Do not equate process existence or generic `running` state with readiness |
| `agent.bridge.input.send` | Q | Input submitted -> target acceptance receipt or rejection | Accepted vs uncertain vs merely written; no prompt contents/replay |
| `agent.bridge.turn` | Q | Accepted turn -> actual terminal turn result | Separate from send latency; cancellation/failure not erased |
| `agent.bridge.state.read` | Q | Snapshot request -> valid state decoded | Snapshot read vs streaming/wait; stale/missing state explicit |
| `agent.bridge.state.wait` | Q | Wait requested -> requested condition reached or terminal wait outcome | Unsettled timeout is not success even if process exits zero |
| `agent.bridge.session.not_found` | E | Authoritative target lookup returns not-found | `operation`, probe vs required target; not transport failure by inference |
| `agent.bridge.transport.disconnected` | E | Actual carrier/host disconnect observed | Intentional stop/shutdown vs loss/unknown; active operation impact |
| `agent.bridge.reconnect` | Q | Reconnect attempt -> exact retained session restored/failed | Restoration receipt; do not count retry initiation as recovery |
| `agent.bridge.progress.received` | E | Caller receives an actual target update | Aggregate/count normally; no per-tick export flood; no local-timer fake contact |

Record communication-silence and absolute operation/process budgets separately.
Progress may renew the former, never the latter. They are diagnostics, not
permission to replay a prompt, allocate another worker or kill a shared service.

## Dispatch and queueing

| Name | Kind | Boundary | Required distinctions |
|------|------|----------|-----------------------|
| `agent.dispatch.emitter.start` | Q | Producer launch -> explicit emitter-ready receipt | If no readiness protocol exists, unavailable; child-created is an event |
| `agent.dispatch.emitter.run` | Q | Tick begins -> validated output batch/terminal error | Empty valid batch is success; startup and generation not conflated |
| `agent.dispatch.evaluator.start` | Q | Evaluator launch -> explicit ready receipt | No fake startup boundary for a one-shot callable |
| `agent.dispatch.evaluator.run` | Q | Evaluation begins -> validated decision/application result | Evaluation completion is not task completion |
| `agent.dispatch.reservation.spawn` | Q | Reservation admitted -> owner records startup success/refusal/failure | Distinct from task claim/actual execution; stable keyed replay |
| `agent.dispatch.steer.confirm` | Q | Steer committed -> worker consumes/acknowledges that exact steer | Submission is not confirmation; withdrawn/expired outcomes |
| `agent.dispatch.task.insert` | Q | API request -> authoritative creation commit/result | Idempotent existing task vs new task; uncertain acceptance explicit |
| `agent.dispatch.task.update` | Q | Mutation request -> authoritative revision accepted/result | Bounded mutation kind; conflict/refusal and commit timing |
| `agent.dispatch.task.queue_wait` | Q | Ready-queue episode admitted -> actual execution claim | Persisted authority timestamps; no waiting process required |
| `agent.dispatch.task.start_wait` | Q | Execution claim -> authoritative `task.started` | Reservation, claim and execution start stay distinct |
| `agent.dispatch.task.state_changed` | E | Accepted lifecycle transition | Reuse existing lifecycle seam, not speculative client state |

Report first ready-queue wait, sum of ready-queue episodes, and submitted-to-first
`task.started` separately. "Total queue time" is the sum of ready episodes;
proposed/draft, suspended, active and reservation-to-start time are separate,
not silently included/excluded under one number. Open episodes are censored
observations with age, not completed zero-duration samples. Use owner queue
depth/oldest-ready-age gauges to expose tasks that never finish waiting.

## Session measurements

These are source-qualified lifecycle observations, not new transcript scraping.
Consume agent-logger accounting and metadata owners; unknown values are absent
with a bounded availability/reason code.

| Name | Kind | Measures and exact scope |
|------|------|--------------------------|
| `agent.session.start_snapshot` | E | Per `set.kind=instructions|skill_descriptions|agent_descriptions`: UTF-8 bytes, contributing file count, source count, item count and admission scope |
| `agent.session.context_triggered` | E | Context at native rollover/handoff trigger: used tokens, effective token limit, used percent, observed time and trigger mechanism |
| `agent.session.completed` | E | One session's terminal outcome, root accepted/completed turn counts, native AI-credit value/unit/source when available |
| `agent.session.accounting_updated` | E | Later source-qualified accounting revision for that session; supersedes a snapshot rather than adding its cumulative total |
| `agent.worktree.finalized` | E | Worktree conclusion plus separately scoped session-chain totals/coverage; never re-add exported session totals |

Instruction bytes describe the selected instruction text, not selector filenames.
Description bytes exclude skill/agent bodies. If useful, report description-only
bytes separately from formatted name+description metadata bytes. File count
counts contributing files; source/item counts cover inline/non-file entries.
Deduplicate aliases by the actual selected identity. `host_admitted`,
`delivered` and `discovered_upper_bound` are distinct series. The current scanner's
inventory is not proof that bytes entered the model.

Native rollover and successor handoff are distinct mechanisms. Record context at
the trigger, not after rollover. Without a supported host callback or exact
usage basis, report unavailable; do not estimate percent from transcript size.
Native host gaps are dependencies, not permission to instrument private host internals.

AI credits require an actual provider credit meter and its documented unit.
Tokens, request multipliers, money and provider nano-AIU are not automatically
AI credits. Keep raw supported meters separately; no guessed conversion.
Turn counts mean root accepted/completed prompted turns, not tool calls, model
calls or every assistant event; the adapter must prove that definition from its
native source, or label a differing source definition and not merge the series.
Preserve root/child/successor scope, late records, source deduplication and coverage.

### Parameter vocabulary for startup/accounting

These are custom keys in the normalized `params` bag, mapped to `ce.*` in OTel.
They are permitted only on their relevant catalog event, not blanket permission
to attach all fields to every hook. Values are finite; counts are nonnegative
integers. Omit unavailable numeric values and include the availability/reason.

| Parameter | Type / unit / vocabulary |
|-----------|--------------------------|
| `set.kind` | `instructions|skill_descriptions|agent_descriptions` |
| `set.bytes`, `set.description_bytes`, `set.metadata_bytes` | Integer UTF-8 bytes (`By`); distinct definitions, never added together |
| `set.file_count`, `set.source_count`, `set.item_count` | Integer counts of contributing files, sources and entries |
| `admission.scope` | `host_admitted|delivered|discovered_upper_bound` |
| `measurement.availability` | `observed|partial|unavailable|estimated` |
| `measurement.reason` | Registered code such as `host_unsupported|source_missing|source_conflict|incomplete|not_applicable` |
| `context.used_tokens`, `context.limit_tokens` | Native integer token observations at the trigger |
| `context.used_percent` | Number in 0-100, computed from the same usage observation |
| `continuity.mechanism` | `native_rollover|successor_handoff|compatibility_handoff` |
| `usage.aic` | Native AI-credit number, only when its meter/unit is supported |
| `usage.unit`, `usage.source`, `usage.revision` | Registered meter unit, source kind and integer accounting revision |
| `turns.accepted`, `turns.completed` | Integer prompted-turn counts with proven source semantics |
| `session.scope` | `root_session|child_session|successor_session|worktree_chain` |

Opaque lineage IDs stay correlation-only. Large native integer meters must
preserve precision (for example, an explicitly typed decimal-string projection)
rather than round through a JSON/JavaScript number; they cannot be silently
placed in `usage.aic`. Per-operation result codes and additional attribute
allowlists are a required owner review item before runtime hooks ship.

## Additional recommended coverage

Agent-recommended, not an expansion of already authorized implementation:

- UI input acknowledgement/frame gaps; cache-hit and stale-result rates.
- Queue depth, oldest ready age, worker startup retries and ambiguous acceptance.
- Communication gaps, reconnect recovery, configuration/discovery/IPC/lock wait.
- Existing owner-supplied CPU, spawn/enumeration/recompute counts and serving
  revision visibility; absence never means zero.
- Telemetry enabled/sampled/dropped/invalid/export-failed counts and bounded
  delivery overhead, so missing data is not reported as healthy tooling.

Priorities and source owners are in the
[instrumentation plan](../efforts/2026/10/22%20telemetry-measurement-contract/instrumentation-plan.md).
