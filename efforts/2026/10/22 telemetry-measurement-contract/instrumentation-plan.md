# Future instrumentation plan

Back to [effort](README.md). This is the reviewed design deliverable, **not
execution admission**. Runtime work remains with existing production owners and
requires their reviewed slices, tests and rollout authorization.

## Priority and evidence

The first two columns below are an **agent-recommended order**, based on a
bounded recent issue sample and selected source bodies, not complaint prevalence
or measured severity. The complete operator-requested catalog remains in scope.
Closed fixes remain regression examples, not proof a current source still fails.

| Order | Measurements | Existing evidence / owner boundary |
|-------|--------------|------------------------------------|
| P0 | Manager launch/no-frame/first interactive frame, authoritative rows, Actions | #7938/#7939; existing Picker budgets and Manager observation owner |
| P0 | Individual hook/extension calls and complete applicable roster latency | #7935; wrapper startup, IPC/fallback and descendant cleanup all count |
| P0 | Bridge launch phases, target progress, readiness and Dispatch reservation/startup | #8051/#5215; silence and absolute budget are separate; no fake ready receipt |
| P1 | All-inclusive worktree create with phase/update children, resume/open/stop/reclaim; reconcile/injection | Existing Worktrees phase progress and launch/activity owners; #7961 discovery is a shared cost |
| P1 | Dispatch CRUD, queue/claim/start episodes and steer consumption | Queue state authority, reservation/attempt identity and persisted steer `taken_at` |
| P1 | Disconnect/not-found/reconnect, state snapshot/wait | #7977 unsettled exit and retained Bridge recovery owners |
| P1 | Session startup sets, trigger-time context, credits and turn accounting | Logger usage evidence/#6819 and actual host admission/usage callbacks |
| P2 | Additional frame/input, queue-age, CPU/spawn/recompute, telemetry loss metrics | Agent-recommended diagnostics; existing owner-supplied counters only |

## Source integration map

Evidence baseline: `0e6a1da286c17d5e62d9d0f12631ae617fa9bff8`. These are
candidate owner integration seams, not claims that the proposed hooks exist.

| Owner / source | Reuse / required addition |
|----------------|--------------------------|
| `libs/process-telemetry/src/process_telemetry/measurement.py` and `otlp.py` | Existing typed spans, monotonic duration and real optional SDK export; evolve adapters/schema explicitly for QoS, named logs and metrics |
| `worktree-manager/.../production_picker/picker_tui/frame_health.py` | Existing 128-event queue, first-refresh/gap reporter and writer lifecycle; no second reporter |
| `production_picker/runner.py`, `picker_tui/engine_loading.py` | Pending-binding/first-refresh/current-view source boundaries; source refresh is weaker than native headed paint |
| `picker_tui/engine_worktree_actions.py::_open_submenu` | Gesture/menu and background reconciliation; bind completion to the originating dialog/generation |
| `agent_worktrees/worktree_ops_cli.py::cmd_create`, `worktree_creation.py::_create_worktree_core` | Inclusive creation and separate config/preparation/update/allocation phases; preserve create vs interactive readiness |
| Worktrees launch, reconciliation and injection owners | Add start/end around actual existing phases and owner receipts; no instrumentation changes admission, cleanup or state authority |
| `agent_bridge/client.py`, `session_host/launcher.py`, `session_host/codespace_transport.py` | Transport retry, host-ready and workspace/plugin preparation are distinct |
| `agent_bridge/acp_client.py`, `events.py`, session routes | Reuse lifecycle reductions; add true ready/accept/read/turn/disconnect observations, not blanket `running`/HTTP success |
| `agent_dispatch/queue_storage.py`, `queue_lifecycle.py`, `queue_spawn_reservations.py`, `queue_steering.py` | Commit timestamps, claim/start episodes, idempotent reservation and steer consumption; never infer confirmation from submission |
| `agent_dispatch/producers/emitter.py`, `evaluator.py`, supervisor | Per-tick/per-pass observations; explicit startup readiness protocol required where none exists |
| `customizing-copilot/.../context_budget.py` | Existing metadata inventory/upper bounds only; no proof of host admission or exact description-only context |
| `agent-logger/docs/usage-evidence.md`, context-handoff/native host source | Reuse source-qualified accounting and observed usage; missing native completion/admission/credits remain dependencies |

Abbreviated paths identify owners; the catalog's semantic boundary is normative.
Resolve exact current functions with those owners before a runtime PR, because
source snapshots and process boundaries evolve.

## Ordered implementation slices - future, not this effort's completion gate

1. Extend the existing typed projection and optional adapter with versioned QoS
   and named-log mapping, bounded enums and independently recorded metrics.
   Preserve legacy validation/export behavior through explicit adapters.
2. Wire Manager source observations and native launch/paint/input observers
   through their already assigned owners; retain method/authority availability.
3. Wire host roster and per-provider hook/extension boundaries. The callback
   itself returns evidence through existing owner IPC/results, not a new writer.
4. Wire Worktrees and Bridge logical operations/child phases and attempts,
   actual readiness receipts and communication-budget accounting.
5. Wire Dispatch state-owner queue episodes, task commits, worker startup and
   steer acknowledgement; distinguish open/censored waits.
6. Add supported metadata/continuity/accounting observations through their native
   owners. Publish unavailable capabilities rather than install a parallel
   session scraper or convert estimated tokens into credits.

Every slice needs explicit ownership, catalog result/attribute allowlists,
controlled-clock and failure tests, and its own source/native acceptance.

## Validation required before claiming coverage

- Schema positives/negatives and semantic fixtures: all result buckets, absent
  observations, true no-op, retries, cancellation and uncertain acceptance.
- Root/child clock and overlap controls, including inclusive creation with
  independent update/phase spans, NTP correction, cross-process domains and
  requeue/suspension intervals; no subtraction exception.
- Failing-before-first-frame, blocked bootstrap, stale rows, closed/reopened menus,
  wrong generations and actual native input/paint evidence.
- Cold/warm/first-touch launch, plugin/setup stages, host-created-but-not-ready,
  prompt accepted-but-not-completed, disconnect and unsupported ready protocol.
- Emission/metrics replay, trace sampling independent of reliability counters,
  overflow/export outage, censored queues and missing terminal receipts.
- Exact UTF-8/source/file/item measurements with admission scope; credits and
  turns from identified native sources, late accounting revisions and no
  source/session/finalization double counting.
- Real SDK receiver readback for spans, named logs and metrics separately; no
  automatic collector, endpoint, provisioning or installed rollout.

Source tests alone do not prove native headed paint, production authority,
host model admission, full roster latency, complete billing or installed-serving
adoption. Those retained gates stay with their existing owners.
