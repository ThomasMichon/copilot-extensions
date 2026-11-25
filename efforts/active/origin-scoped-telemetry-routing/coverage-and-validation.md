# Producer coverage and validation packet

Back to the [canonical effort](README.md) and
[architecture proposal](architecture.md).

**Status:** planning baseline and recommended evidence, not fresh execution or
installed qualification. Source baseline:
`88dfa72e5bd87136842c5ddc6708f1dc6825b5ba`.

## Source inventory

The sweep covered production sources in `plugins`, `libs`, `worktree-manager`,
`tools`, `services`, `fleet-manager`, `install` and `bundle`. Tests, generated
dependency materialization and names that merely resemble telemetry were not
counted as production hooks. Dynamic external factories and third-party SDK
internals remain outside this static absence claim.

| Existing source | Current route | Planned disposition / existing owner |
|-----------------|---------------|--------------------------------------|
| Bridge session/conversation/tool lifecycle and health | Closed `bridge.lifecycle.v1` command projection | Converge the existing adapter on shared policy and admitted driving origin |
| Dispatch task lifecycle | Closed `dispatch.lifecycle.v1` command projection | Converge stored-task admission and preserve immutable accepted origin |
| Dispatch producer fences/taskless observations | Command projection only with separate service binding | Explicit service-purpose policy; never borrow a caller's asserted task lane |
| Bridge inbox delivery | Existing reduced observations to the legacy bounded owner; outside command v1 | Separate reviewed source decision with inbox owner; no raw forwarding |
| Bridge represented/restored activity | Missing initiating admission suppresses bound export | Retain honest unavailable state; enroll only if a real admitted origin exists |
| Bridge watchdog/application diagnostics; Dispatch emitter/application diagnostics | Independent files or bounded stderr | Local-only by default; no arbitrary log pipeline |
| Worktrees launch checkpoints | Opt-in local JSONL | Reviewed operation/launch projection through the existing launch owner |
| Worktrees/Manager/generated installer update progress | `update_telemetry` run JSONL, status and human output | Add scoped projection at that owner, preserving durable run and output meaning |
| Manager native frame health | Opt-in bounded local writer | Owner-reviewed typed production projection; local diagnostics remain independent |
| Manager source observation traces/logs/metrics | Optional SDK export to explicit literal loopback OTLP endpoints | Preserve #7935/#5900; distinguish source-only qualification from production enrollment |
| Machines health/service and Dispatch hourly reports | Typed collection/reduction/report output | Separate service-purpose decision under #10873 and current report owners |
| Native Manager probe and explicit OTLP canary | Finite caller-selected validation | Validation-only, never automatic installed collection |
| Other production apps/services/tools | No additional direct SDK exporter or command registration found | Record actual absence/unknown coverage; new hooks use the common seam, not a new app-specific setup contract |

Evidence entry points:

- [Shared command reader](../../../libs/process-telemetry/src/process_telemetry/command_config.py)
  and [source switch](../../../libs/process-telemetry/src/process_telemetry/command.py).
- [Bridge admission](../../../plugins/agent-bridge/src/agent_bridge/consumer_telemetry.py)
  and [Dispatch admission](../../../plugins/agent-dispatch/src/agent_dispatch/consumer_telemetry.py).
- [Worktrees launch checkpoints](../../../plugins/agent-worktrees/src/agent_worktrees/launch_trace.py)
  and [shared update records](../../../libs/update-telemetry/src/update_telemetry/run.py).
- [Manager source observation](../../../worktree-manager/src/worktree_manager/performance_observation.py)
  and [native/source distinction](../../../worktree-manager/docs/source-observation.md).
- [OTLP trace exporter](../../../libs/process-telemetry/src/process_telemetry/otlp.py),
  [structured signals](../../../libs/process-telemetry/src/process_telemetry/otlp_signals.py)
  and [canary](../../../libs/process-telemetry/src/process_telemetry/canary.py).
- [Machine service reports](../../../plugins/agent-machines/src/agent_machines/service_reports.py).

## Validation packet

These are recommended acceptance controls. Add focused regressions that fail
without the selected behavior; do not run a full native fleet matrix in every
PR. All fixture repositories, identities and destinations are synthetic.

| ID | Boundary | Required evidence |
|----|----------|-------------------|
| C1 | Configured harness / standalone | One declaration reaches capable consumers; absent optional knowledge/peer retains standalone no-export behavior without provisioning |
| C2 | Composition | Real configuration reader resolves harness, knowledge, machine and exact-repository+machine choices with source provenance and defined keyed/list/null behavior |
| C3 | Invalid and disabled policy | Exclusion/opt-out wins; malformed explicit binding or unknown required policy does not become an ambient fallback |
| C4 | Capability and skew | Selected old/new provider, receiver and rollback cases preserve required scope; an old reader is never credited with a policy it cannot enforce |
| O1 | Direct harness | Canonical harness repository, registered worktree and subdirectory operations reach the eligible sink |
| O2 | Delegated service-of | Child/queued/service/remote work in a different target CWD retains the originating harness through actual admission |
| O3 | Unrelated work | A different repository, similarly named folder or unrelated native Copilot session produces no record in that harness sink |
| O4 | Unknown or invalid origin | Missing context, invalid relationship and caller-provided event/target labels cannot enroll a route |
| O5 | Concurrent harnesses | Interleaved accepted operations keep their own sinks, exclusions and lifetimes; no last-caller retargeting |
| O6 | Taskless service activity | Unbound shared-daemon/health observations stay local; an explicitly admitted service-purpose case is separate and inspectable |
| O7 | Machine usage | Two logical-machine configurations exercise different usage/exclusion/opt-out decisions without changing shared source defaults |
| R1 | Real producers | Ordinary Bridge/Dispatch/Worktrees/Manager operations reach independent receiver readback, not a direct helper-only emission |
| R2 | Complete intake | Native observer, registered report/fallback and registration/self-observation cases use the same eligibility semantics before sink-bound enqueue |
| R3 | Receiver custody | Reused owner-managed child, bounded buffers and shutdown, disabled/failed/nonreading receivers preserve product results and output |
| R4 | Prior integrations | Legacy callable/spool consumers, local update/status output and independent diagnostics retain their admitted behavior |
| D1 | Closed meaning and privacy | Unknown sources/fields refuse; typed units and trace meaning survive; raw content/paths/identities and fabricated session/span IDs do not appear |
| D2 | Delivery evidence | Exact source observations join receiver-persisted records; pipe writes, HTTP acknowledgement, upload state and backend query remain separate |
| D3 | Windows and POSIX | Existing contained/native owners prove platform process/transport behavior and bounded cleanup; unavailable lanes are recorded, not passed |
| D4 | Selected release/adoption | Actual published provider capabilities and ordinary installed flows qualify the named cohort; source merge or library import alone is insufficient |

## Promotion and rollout boundaries

Source validation, source review, consumable release, selected installed
adoption and backend evidence are distinct gates. Existing native/collector
reservations and lifetime/credential owners remain authoritative.

Do not install an SDK, start a collector, restart a shared service, activate
background collection or select a fleet merely to satisfy this document.
Implementation workers obtain the applicable effect/target authority before
using such a lane. A backend persistence claim requires separately authorized
backend readback; receiver persistence alone must not be overstated.
