# Process Slot Ownership

- **Slug:** `process-slot-ownership`
- **Repo:** copilot-extensions
- **Branch(es):** serial per-slice PRs targeting `dev`
- **Created:** 2026-10-10
- **Status:** Active
- **Vision:** `visions/agent-fabric/README.md` - claimed-resource-not-reclaimed
  and reclaim-idle-process; `visions/plugin-services/README.md` -
  work-coalescing-singleton
- **Umbrella issue:** #3963
- **Related:** #5559 (operational process registry), #5579/#5664
  (separately claimed lean session lifecycle)

## Guiding Intent

An outstanding reader retains its role slot until the actual holder finishes.
Caller timeout, cancellation, or a resource-budget breach does not establish
that the holder is gone. Coalesce equivalent work, suppress repeated admission
when it overruns, expose incomplete observations, and preserve ownership-safe
cleanup. Do not replace a slow holder with an overlapping attempt merely
because its result is no longer eligible for publication.

This is the canonical generic implementation plan for #3963. It reuses the
existing [process-slot ownership pattern](../../../docs/patterns/process-slot-ownership.md)
and [work-coalescing singleton pattern](../../../docs/patterns/work-coalescing-singleton.md).

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Process ownership driver | Inventory, bounded implementation, serial PRs, validation | #3963 and this effort |
| Process registry driver | Independently owns shared operational membership/query service | #5559 |
| Lean lifecycle driver | Independently owns handoff wake and wait-only launch reduction | #5579/#5664 |

## Coordination

- **Topology:** independent per-slice worktrees, serial PRs targeting `dev`.
- **Host/PR owner:** Process ownership driver owns this effort's continuation
  PRs; the two sibling drivers own only their separately tracked efforts.
- **Slice ownership:** announce one continuation slice at a time on #3963
  before editing; do not overlap another driver's claimed slice.
- **Handoff:** the successor reads this Plan, validation, journal, and any
  open PR obligation before starting the next slice.
- One writer per implementation slice; use #3963 to announce the slice.
- Do not build another process registry or take over the separately claimed
  launch/handoff work.
- Shared membership, role/owner queries, and inventory presentation belong to
  [agent-process-self-report-registry](../agent-process-self-report-registry/README.md).
  This effort owns admission and reader lifetime, not that broker's design.
- A successor resumes this Plan and its Validation Plan, not the latest
  completed PR alone.

## Context

Existing mechanisms already satisfy substantial portions of the contract:

| Existing seam | Evidence and remaining boundary |
|---------------|---------------------------------|
| OS-exclusive daemon role leases | `libs/single-instance-lease`; reuse rather than introduce a rival lease |
| Worktree discovery crawls | `WorktreeDiscoveryCache.crawl` in `plugins/agent-bridge/src/agent_bridge/routes/worktrees.py` serializes with `_crawl_lock` |
| Live/archived single-target probes | The same cache retains and shields per-worktree tasks until completion |
| Namespace roster readers | `plugins/agent-bridge/src/agent_bridge/agent_registry_cache.py` retains `_inflight` tasks and provider references; caller join budgets do not cancel shared work |
| Registry presentation | #5559's existing reviewed proposal owns current process membership and owner-scoped queries |

The namespace cache's cancellation-resistant paths require specific scrutiny:
`force_fail_uninitialized_namespaces` and `_cancel_inflight_nowait` can remove
tracking before the old task has actually completed. Invalidating publication
is necessary, but is not evidence that the underlying reader terminated.
Characterize this with an event-gated reader before changing it. A provider
replacement is a separate identity boundary, not permission to lose the old
holder from shutdown accounting.

### Remaining reader inventory

This is a bounded source inventory, not proof that every reader has passed
native lifetime or budget acceptance. Paths below are relative to `plugins/`.
Existing cost limits are not substitutes for measured process-tree CPU budgets.

| Reader seam | Existing admission/ownership evidence | Remaining evidence |
|-------------|---------------------------------------|--------------------|
| `agent-worktrees/src/agent_worktrees/list_cli.py:417-505`, `list_cache.py:77-130` | Project/status/args-keyed result cache; cache-only path avoids enrichment; ordinary cache miss preserves inline computation | Cross-process cache-miss single-flight and CPU/wall admission are not established by result caching |
| `agent-worktrees/src/agent_worktrees/list_cli.py:386-413`, `status_monitor_cli.py:746-765` | Resident monitor refreshes demanded list shapes rather than every possible project | Bound per-shape work and measure aggregate role cost |
| `agent-worktrees/src/agent_worktrees/worktree_status_cache.py:34-96` | 180s status TTL and four refreshes per sweep cap costly recomputation | Time/CPU overruns and exact-owner release need independent proof |
| `agent-bridge/src/agent_bridge/routes/worktrees.py:158-242`, `routes/worktree_probe.py:188-286` | Crawl lock; shielded per-ID probe holders; per-agent fanout; list/classify timeouts | Prove native worker/tree completion, not just cancellation of probe tasks |
| `agent-bridge/src/agent_bridge/agent_registry_namespace.py:110-190` | CLI namespace result TTL; thread-offloaded subprocess with a 90s default timeout; incomplete results are distinct from empty | Cancellation of the awaiter does not prove thread/subprocess completion; retain actual worker ownership |
| `agent-bridge/src/agent_bridge/agent_registry_discovery.py:47-129` | Provider report generation fencing and transactional reconciliation | Bound synchronous scan admission and preserve real worker lifetime independently of publication |
| `agent-dispatch/src/agent_dispatch/client.py:44-66`, `client_worktree_status.py:8-37` | 10s HTTP timeout and batched status relay reads | Remote reader-holder accounting and fanout/CPU circuits remain unverified |
| `agent-dispatch/src/agent_dispatch/registrar_discovery.py:450-548,614-703` | Deterministic declaration discovery; indeterminate reads and duplicate identities are explicit | Concurrent-crawl coalescing and resource budgets remain unverified |

Prioritize the CLI namespace worker boundary next: a cancelled coroutine can
finish while its `to_thread` subprocess continues. Any correction must retain
that actual holder and preserve bounded caller waits. Do not introduce a second
registry or convert unknown remote ownership into permission to reclaim.

## Request

The operator selected process ownership as the continuing work and confirmed
the canonical effort slug `process-slot-ownership`.

The original public scope is #3963: owner-tethered, single-occupancy background
process slots across agent-worktrees, agent-bridge, and agent-dispatch.
The execution decomposition and acceptance details below are
**agent-recommended refinements**, not new operator requirements.

## Plan

### Historical phase mapping

Existing pattern/source references keep their original meanings. The
continuation slices below do not renumber or erase that historical sequence.
Published mechanisms are prior art; full acceptance remains independently
verified by this effort's Validation Plan.

| Historical phase | Meaning and current disposition |
|------------------|---------------------------------|
| Phase 0 | Spawn-site audit and shared primitive design; current caller inventory is reconciled in Continuation A |
| Phase 1 | Launcher/mux slot and tether; pane-lifetime work landed in #712 |
| Phase 2 | Status-query debounce; discovery coalescing landed in #714 and remains prior art |
| Phase 3 | Status-daemon and stdio ownership tethers; preserve rather than rewrite these contracts |
| Phase 4 | Generation tether and backstop unification; the graceful-daemon-cutover pattern owns the published mechanism |
| Phase 5 | Slot observability and pattern documentation; health descriptors/pattern are published, broader inventory presentation belongs to #5559 |
| Phase 6 | Reader budgets, circuit-first admission, and ownership-safe cleanup; the continuing implementation scope below |

### Continuation A - Consolidate current contracts and evidence

- [x] Identify existing leases, crawl/probe coalescing, and namespace readers.
- [x] Separate operational registry presentation (#5559) and lean launch
      lifecycle (#5579/#5664) from reader admission; do not duplicate them.
- [x] Land this plan through review before implementation (#6091).
- [x] Inventory remaining status/list/discovery caller seams and record
      already-satisfied versus unverified requirements.

### Continuation B - Preserve outstanding reader ownership (Phase 6)

- [x] Reproduce cancellation-resistant same-provider namespace admission:
      bound the caller wait without starting a second underlying reader.
- [x] Retain exact task/provider ownership through timeout and cancellation;
      fence late publication independently from slot release.
- [x] Account for superseded-provider tasks until actual completion or
      bounded shutdown; replacement must not erase shutdown ownership.
- [x] Land the smallest correction with deterministic async regressions,
      documentation, and an agent-bridge changefile.

### Continuation C - Reader budgets and circuit-first admission (Phase 6)

- [x] Implement the operator-approved bounded
      [CLI wall-admission circuit](reader-admission.md), preserving fallback
      and native holder ownership; CPU and process-tree gates stay separate.
- [x] Implement the operator-approved
      [owned CPU observation boundary](reader-cpu-observation.md) through
      optional shared procutil metrics; reader wiring and enforcement remain
      separate gates.
- [x] Wire the [CLI reader observation lifecycle](reader-cpu-wiring.md)
      and existing telemetry seam without CPU enforcement or new termination.
- [ ] Add [sample provenance and collector cost](reader-sample-accounting.md)
      for measurement-only role-cost calibration before numeric enforcement.
- [ ] Define role keys, expected wall/CPU cost, fan-out limits, and provenance.
- [ ] Open admission circuits on repeated budget overruns without reclaiming
      live progressing holders; successful bounded probes close them.
- [ ] Define cooperative cancellation and exact identity/no-progress proof
      before any process-tree reclamation. No time-only kill policy.
- [ ] Expose role accounting through existing observability/registry seams;
      distinguish unknown from dead and stale from current.

### Continuation D - Release and validate adoption

- [ ] Complete the validation matrix, release through normal promotion, and
      verify authorized consumer behavior.
- [ ] Resolve or transfer all remaining scope and journal merged outcomes.

## Validation Plan

- [ ] Across agent-worktrees, agent-bridge, and agent-dispatch, each role slot
      has exactly one logical owner session; concurrent claims cannot gain
      a second live holder.
- [ ] Confirmed owner-session end releases its slot, permits a new session
      to claim it, and cannot release a successor's slot through delayed
      cleanup. Unknown ownership/liveness is not an affirmative release.
- [ ] N same-role concurrent requests perform one underlying read.
- [ ] A timed-out/cancelled waiter cannot release or cancel another waiter's
      shared holder; a cancellation-resistant holder cannot multiply.
- [ ] Failed publication cannot overwrite a newer provider/generation.
- [ ] Provider replacement preserves old-holder accounting without serving
      old-provider results as authoritative.
- [ ] Wall/CPU overrun suppresses new admission, not live progress.
- [ ] Exact-holder cooperative cancellation and confirmed no-progress
      cleanup release the slot and complete process tree; ambiguous liveness
      never authorizes reclamation.
- [ ] Circuit recovery is bounded, preserves inline-fallback contracts, and
      does not create a competing registry or resident helper.
- [ ] Shutdown remains bounded and observes all owned tasks.
- [ ] Native platform tests preserve PID/start-time identity, deliberate
      detach, lock release, and crash backstops.
- [ ] Bounded targeted tests and applicable isolated scenarios pass; release
      and running-state evidence are recorded separately from merge evidence.

## Proposal

Retain holder accounting independently from publication eligibility. First
characterize the namespace cache's cancellation-resistant paths with
deterministic event-gated readers, then make the smallest lifetime correction.
Later budget/circuit design must preserve existing inline fallback contracts
and leave shared registry presentation to #5559.

## Journal

### 2026-10-10 - Canonical continuation proposal

Confirmed the slug and mapped current prior art before implementation.
Existing coalescing and registry/lifecycle efforts prevent a greenfield
rewrite. Continuation B's first slice is deliberately a deterministic reader-lifetime
regression, not a live reaper, service restart, or fleet-wide rollout.

### 2026-10-10 - Reader holder correction prepared

The reviewed plan landed in #6091. The first implementation retains
same-provider task/token admission after watchdog or warm-up cancellation
timeouts, fences late publication independently, and tracks superseded
providers until completion for shutdown accounting.

Event-gated regressions cover exact-holder reuse, actual late-result rejection,
watchdog recovery only after holder completion, and superseded-holder shutdown.
The discovery report fixture now targets discovery directly rather than
counting unrelated synchronous list-agent reconciliation; the hung-discovery
fixture awaits the replacement task before claiming its worker executed.
The roster-cache contract passed (49 passed, one skipped), touched F/E9 lint
passed, and the install-contract guard reported zero violations.
This is not native process-tree cleanup, budget/circuit implementation, or
release/adoption proof; those remain open acceptance gates.

### 2026-10-10 - Scheduled provider identity verified

Review identified a swap-before-task-start race: a task captured under provider
A could read replacement B when first scheduled, admitting two B reads.
The scan now verifies its captured provider identity before invalidation or
calling the resolver. There is no await between that check and the resolver's
own provider capture. A deterministic replacement-before-yield regression
proves A performs no read, B performs exactly one, and only B's rows publish.
The full roster-cache contract passes (50 passed, one skipped); touched lint
and the install contract remain clean.

### 2026-10-12 - Reconciliation and shutdown publication fence

Reconciled the effort index with newer upstream entries without reverting
their status. Review then identified that a cancellation-resistant holder
could finish after bounded shutdown and publish into the stopped cache.
Publication now rejects results while stopping; retained ownership remains
observable until task completion. An event-gated shutdown regression proves
the late result cannot change rows or state. Provider replacement before
scan start is logged as normal discarded work, not a failed scan warning,
and the replacement shutdown fixture uses an explicit start handshake.
The targeted roster contract passes (51 passed, one skipped); native worker
exit and budget/circuit gates remain unresolved.

### 2026-10-12 - Coroutine holder correction landed

Merged #6129 as `57eaa07e2` after current-head approval and green checks.
Continuation B's namespace coroutine ownership correction is complete.
The native CLI worker can still outlive cancellation of its `to_thread`
awaiter; that distinct worker boundary is the next bounded correction.
Continuation C and release/adoption/native validation remain open. Merge
does not establish promotion or running-state adoption.

### 2026-10-12 - Native CLI enumeration holder prepared

CLI namespace enumeration now coalesces through a provider-local task that
shields the full thread/subprocess operation from caller cancellation.
The initial admitted timeout bounds that subprocess; later callers keep
their outer wait budgets without replacing its execution policy.
A native Python provider fixture records one PID launch, stays blocked until
explicit graceful release, and returns the same result to eight joiners after
the first caller cancels. The namespace/roster selection passed 105 tests,
one skipped. This proves direct native enumeration admission, not descendant
tree cleanup, CPU circuits, cross-platform adoption, or runtime release.

### 2026-10-12 - Native ownership linked to cache supervision

Review identified that a provider-local shielded task needed an explicit
cache lifecycle handoff when the outer cache scan ended. Resolvers now expose
pending enumeration; cache scan cleanup retains its exact native holder.
Bounded cache shutdown observes those tasks without cancelling their native
work, including after provider replacement, and retains timed-out holders
until completion. Two native process regressions demonstrate shutdown and
replacement accounting. The combined contracts pass 107 tests, one skipped.

### 2026-10-12 - Native enumeration correction landed

Merged #6400 as `913479b04` after exact-head approval with zero open findings
and green checks. Native enumeration now retains one provider-local holder
through cancelled callers and hands remaining ownership to bounded cache
shutdown supervision, including after provider replacement. The umbrella
remains open: process-tree identity/reclamation, role CPU/wall circuits,
cross-platform acceptance, promotion and authorized adoption are not complete.

### 2026-10-12 - Wall-admission policy approved

The operator directed continued upstream execution independent of the private
journal review. Initial CLI circuit policy was explicitly approved: two
consecutive native timeout overruns, 60-second cooldown, and one bounded
recovery probe. The detailed slice preserves existing fallback, execution
timeouts and ownership; CPU and native tree reclamation are not implied.

### 2026-10-12 - Wall-admission implementation prepared

The policy and complete transition matrix landed in #6428. Provider-local
admission now records native execution timeout separately from caller
cancellation, preserves fallback during suppression, and admits one bounded
native recovery probe. Deterministic tests cover consecutive streaks,
suppression, failed probes restarting cooldown, and native-only recovery.
Existing process-backed holder and roster contracts remain in the targeted
selection. CPU measurement and process-tree cleanup are still separate gates.

Review caught an absence-contract edge: an open circuit must not turn a
provider's missing executable into namespace incompleteness. Suppressed
no-fallback requests now re-check executable presence and retain legitimate
empty absence without launching work or treating it as native recovery.
Both binstub and explicit-command absence regressions pass; the combined
selection now passes 117 tests, one skipped.

### 2026-10-12 - Wall admission landed

Merged #6446 as `e28aaab8f` after exact-head approval and green checks.
The provider absence compatibility edge is covered and clarified in the
pattern. The CLI wall-admission slice and its focused validation are complete;
the parent role CPU/wall accounting, wider caller admission, identity-safe
native tree cleanup and cross-platform release/adoption gates remain open.

### 2026-10-13 - Owned CPU observation boundary selected

The operator selected psutil-backed observation of only reader-owned PIDs
and descendants through shared procutil. The next reviewed slice preserves
stdlib-only default consumers via optional metrics and explicitly represents
partial coverage and unavailable samples. Numeric CPU budgets and any
process termination policy have not been approved by this selection.

### 2026-10-13 - Optional CPU observation implementation prepared

The observation contract landed in #6481. Shared procutil now offers an
optional psutil-backed, read-only sampler without importing that dependency
through default helpers. Root and descendant identities are revalidated with
fresh process objects; observed cumulative CPU maxima are lower bounds, not
exhaustive tree totals. Discovery attempts and retained identities are capped,
sampling is single-flight and cached for one second, and denied/missing/reused
identities and counter regressions remain explicit.

The complete procutil suite passed 78 tests on Windows, including a native
root/child CPU fixture and graceful completion. Shared pytest-root cleanup
initially encountered an unrelated access-denied path; a fresh task-local
base directory avoided touching that shared path and validation passed.
Full library lint, install-contract and exact source mirror checks passed.
Linux proof, reader lifecycle wiring, cumulative role export, enforced CPU
budgets and process-tree reclamation remain open; this library slice does not
claim those outcomes. All 13 physical/pointer consumers carry release entries.

Sampling uses a running native future rather than treating a cancelled
asyncio task as proof that the observation thread ended. A loop-shutdown
regression proves a later loop joins the original holder without another
native sample. No new resident process or termination surface is introduced.

The initial shared-library CI setup did not install optional metrics. Native
CPU validation explicitly requires that extra; ordinary imports and the
stdlib-only test lane remain runnable without it. The fixture passes its
checkout source path to child interpreters, so bare pytest and editable-package
validation exercise the same source. Cold default validation passed 77 tests
with one explicitly skipped native-metrics fixture; metrics-enabled validation
passed all 78. This does not substitute for the outstanding Linux native proof.

The shared-library CI setup now explicitly installs the metrics backend so
its native fixture executes rather than skips in the canonical Linux lane.
Stdlib-only collection still reports the optional test as skipped when that
backend is absent; production default consumers do not gain a runtime dependency.
Linux execution evidence will be recorded only after the current CI run passes.

### 2026-10-13 - CPU observation library landed; reader wiring next

Merged #6496 as `3f5e9e550` after exact-head approval with zero findings and
green checks. Canonical Linux CI installed psutil and passed 72 procutil tests
with only the six existing Windows-native exclusions; Windows passed all 78.
The optional shared-library observation boundary is complete, not the wider
CPU budget or process-tree contract. The next slice connects owned CLI spawn
receipts and the existing telemetry seam while keeping sampling holders
accounted for independently from command completion.

### 2026-10-13 - CLI reader CPU lifecycle prepared

The reviewed wiring contract landed in #6514. Agent Bridge now opts into the
metrics extra and observes only native namespace enumeration. The owned
command adapter captures identity before communicate/reap, preserving direct
child execution deadlines and output/fallback semantics. Native Windows
FILETIME receipts match the metrics backend exactly, including a virtual
environment launcher's distinct interpreter descendant.

Sampling is provider-local, one-second completion-based cadence with 100ms
joins. Native sampling futures remain owned across command completion,
cancelled scan awaiters and provider replacement; bounded cache shutdown
observes them without cancellation. A stuck sample suppresses later sampling
instead of multiplying threads. Existing telemetry exports bounded lower-bound
CPU observations and explicit uncertainty without arguments, output or paths.

The focused Windows selection passed 133 tests, including native CPU export
and two console-child cycles from a windowless parent with zero new visible
windows or foreground transitions. The initial native fixture incorrectly
equated a virtual environment launcher with its interpreter; the corrected
fixture pins the owned root independently and verifies the observed descendant.
A cadence regression also showed that starting the next bridge interval at
sample admission could hit procutil's completion-based cache; cadence now
starts at sample completion. Linux native proof remains pending the PR's
canonical CI run. Numeric CPU limits, cumulative role accounting, sampler
overhead and process-tree reclamation remain separate open items.

The implementation PR's guard caught the roster cache crossing the module
cap. Native command/sample ownership is now a focused helper rather than
another cache responsibility. Validation also caught a sample that exceeded
its 100ms join but completed during command execution: the observer now
retains its shielded delivery waiter and consumes the completed observation
at the bounded final join, without confusing waiter lifetime with the
separately retained raw future. A deterministic delayed-sample regression
and the complete focused Windows selection pass (134 tests).

Review identified Python 3.10's distinct asyncio timeout class and unrelated
desktop activity in the native launch regression. Observation and shutdown
now classify both timeout classes consistently; a distinct-class regression
proves ordinary join exhaustion remains pending rather than sampling failure.
Windows visibility/focus counters are scoped to the owned parent/descendant
PID set, not other applications. The focused selection passes 135 tests.

Further review identified a late native completion after its join deadline
and detached asyncio tasks whose eventual exceptions were not consumed.
The provider now records sample completion through a native-future callback,
with a conservative delivery-task fallback when the native future has already
finished. A completion receipt blocks new sampling until its timestamp is
known, then enforces the full one-second interval even across a later reader.
Command and sampling task callbacks consume and explicitly log late failures
without arguments or captured output; normal awaiters still receive errors.
Late-completion cadence and cancelled-observer failure regressions pass, as
does the complete focused selection (137 tests).

Wall telemetry now uses the command worker's own completion-written duration,
not the asyncio task's done state. A cancelled command task can leave its
thread alive, so that case reports unknown duration rather than invented zero.
The dedicated thread-backed cancellation regression and the complete focused
selection pass (138 tests).

The approved implementation's canonical CI then reached an integration
fixture that still mocked the old subprocess seam. Both registry list-only
fixtures now use the owned adapter, so deadline forwarding and non-zero
failure exercise the intended boundary rather than accidental launch failure.
The expanded namespace/provider selection passes 346 tests. The complete
bounded Windows Agent Bridge portfolio passes all nine sub-suites: 4,239
passed, 94 skipped. The non-blocking documentation observation was corrected
as well: uncertainty retains an earlier valid CPU lower bound; `null` applies
only when none was observed. Current-head review and Linux CI must pass again
before landing this correction.

### 2026-10-13 - Reader CPU wiring landed; calibration next

Merged #6524 as `76eca4833` after exact-head approval with zero open findings
and actual CI success on `5f32439f9`. The apparent earlier checks-success
state contained only review workflows while code CI was still queued; merge
waited for the complete code workflow and its required aggregate gate.

Canonical Linux CI installed psutil 7.2.2 and passed all nine Agent Bridge
sub-suites (4,287 passed, 46 skipped), including native CPU export. Windows
passed all nine sub-suites (4,239 passed, 94 skipped). The reader wiring
contract is complete, not numeric CPU enforcement or release/adoption.

The next agent-recommended increment measures collector cost and gives each
native sample bounded completion provenance so cached reuse and repeated
observations cannot inflate accounting. It remains measurement-only;
cumulative role reduction, expected cost/fan-out policy, enforced budgets and
identity-safe process-tree reclamation are separate open gates.

### 2026-10-13 - Sample accounting plan landed; implementation prepared

Merged #6534 as `0fdda518f` after exact-head approval with zero findings and
actual CI success. The reviewed measurement-only contract now has an
implementation: bounded per-sampler/sequence provenance, current/last native
accounting, separate collector wall/thread CPU, and explicit clock uncertainty.
Cache reuse carries the same cost; backend exceptions still reach awaiters.
Bridge exports late accounting through the existing content-free sink, including
failed or cancelled waits, without losing native holder ownership.

The physical procutil mirror and all 13 consumer changefile entries are
synchronized; default helper consumers neither import psutil nor sample.
Windows library validation passed 99 tests, including a direct native-thread
counter witness. All nine Agent Bridge sub-suites passed (4,245 passed, 94
skipped), including native accounting export. Linux CI and exact-head review
remain required before merge. Numeric policy, role reduction, reclamation and
authorized adoption remain unresolved parent gates; this implementation does
not close them.

### 2026-10-13 - Worker-start cache failure semantics corrected

Review identified the same cache-invalidation omission in the canonical sampler
and its physical mirror. A worker-start failure after an unavailable or regressed
cache clock must invalidate `_last_at`, as a backend failure already does, so
later clock recovery cannot resurrect an older successful observation.
Both paths now do so; four recovery regressions cover both clock conditions
and RuntimeError/OSError start failures while preserving the original exception.

The full Windows procutil suite passed 103 tests after this correction, and the
focused reader/admission/telemetry/vendoring selection passed 42. The previous
full nine-sub-suite Windows and Linux runs are baseline evidence only; fresh
exact-head review and code CI remain required before landing the correction.

The existing consumer native fixture now also verifies the actual sampling-thread
counter delta in exported telemetry, ensuring canonical Linux CI exercises that
direct witness without another subprocess fixture. Its focused Windows
reader/admission/telemetry/vendoring selection passed 42 tests.
