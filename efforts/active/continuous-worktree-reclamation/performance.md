# Reclamation Performance Plan

Part of [Continuous Worktree Reclamation](README.md). All mechanisms and
thresholds here are **agent-recommended, pre-implementation proposals**.

## Evidence and limits

An exploratory recovery trace contained 1,732 successful `finalize` invocations:
median 37.78 seconds and empirical p95 78.50 seconds. Twenty-six refusals had
median 40.05 seconds and empirical p95 43.91 seconds. Percentiles use sorted
sample index `floor(0.95 * (n - 1))`. This is anonymous
aggregate operational evidence, not a portable benchmark: invocation mixes,
repository sizes, network state and machine contention were not controlled.
It demonstrates the cost of repeating an end-to-end command, not its cause.

Source tracing identifies opportunities, not measured attribution:

| Candidate | Existing boundary | Proposed experiment / constraint |
|-----------|-------------------|----------------------------------|
| Remove per-worktree interpreter/shim startup | Manual driver calls `finalize` separately; resident has an owning process | Invoke the shared lifecycle operation with explicit context. Count Python/PowerShell/CLI launches. Retain canonical process containment for required children. |
| Coalesce upstream refresh | `finalize` refreshes the repository; finished-session sweep already uses `fetch=False` | Share repository-scoped refresh evidence; fetch at most once per repo per due refresh generation, outside lifecycle/record locks. Offline or failed refresh holds unproven work. Do not add a fetch to every GC candidate. |
| Share discovery | Collectors scan sessions/mux and derive active paths | Reuse an attributable batch snapshot for nomination; perform fresh bounded candidate checks at removal. Compare enumeration counts and total work. |
| Avoid rescanning unchanged records | Registry/tracking/status owners already observe changes | Prioritize affected candidates, retain a bounded rescan/cursor for missed events, and invalidate on branch/content/claim/session/policy changes. No duplicate safety ledger. |
| Bound contention and fairness | `_RecordLock`, repo `FinalizeLock` and canonical disposal guards | Measure wait/hold time and reason codes. Defer a busy unit and advance another repo. Do not delete lock files, shorten safety windows blindly, or hold global locks during network/slow owner probes. |
| Reduce repeated bookkeeping | Removal, trust/settings reconciliation and archival have existing owners | Measure first; batch only independently idempotent non-authoritative housekeeping. Preserve exact per-worktree receipts and failure visibility. |

Parallel read-only discovery may help, but is not the first fix. Destructive
concurrency stays bounded and serialized by the existing authority/lock scope.
Parallelism must not race same-repository removal, reciprocal pairs or a daemon
cutover, and may not turn a large backlog into a process/I/O storm.

## Controlled measurement

1. Build identical disposable Git fixtures with 10 and 100 actual worktrees
   across several repos; use 1,000-record mixed-state fixtures for cheap discovery
   scaling. A larger real-checkout lane is scheduled/manual, not routine PR CI.
2. Measure cold and warm runs separately. Record OS, runtime/source revision,
   storage, fixture file counts/sizes, repository count, policy/grace and remote
   fixture latency. Keep equivalent policy, data and liveness in both paths.
3. Attribute elapsed time to discovery/classification, network refresh, process
   startup, Git verification/removal, lock wait/hold, durable bookkeeping and
   deferred work. Record subprocess/scan/fetch counts as well as wall time.
4. Compare the existing supported scoped-cleanup path, an equivalent per-worktree
   CLI loop and the proposed resident path. A legacy invocation mix is not a
   substitute for the supported-cleanup baseline.
5. Run multiple repetitions; publish medians, p95, variance and all failure/hold
   counts. Explain noise and changed fixtures; do not select only the best run.
6. Run each timed fixture through a safety oracle: classify the same initial
   states, compare actual removed/retained identities, and inject changes at the
   mutation boundary. Warm-cache speed is invalid if it removes more than the
   canonical policy authorizes.

## Acceptance gates

These are proposed goals to ratify after Phase 2 baseline, not achieved results.
Any revision needs recorded measurements and review before implementation is
judged complete; do not quietly relax a missed target.

| Gate | Proposed measurable criterion |
|------|-------------------------------|
| No repeated control-plane startup | Zero Python/PowerShell/CLI wrapper launches per candidate in a warm resident batch; count necessary Git/provider children separately. |
| Shared refresh | At most one coalesced upstream fetch per repository and refresh generation, none inside removal/record locks. Unknown upstream proof remains a hold. |
| Bounded discovery | At most one full session/process/mux enumeration per batch, plus explicit per-candidate fresh probes. An unchanged foreground status read triggers no GC fleet scan. |
| Throughput | Target at least 5x warm removal throughput versus an equivalent per-worktree CLI loop on the 100-checkout fixture, and no regression versus supported bulk cleanup. Report absolute rates and safety parity, not the speedup alone. |
| Normal-load stability | After measuring service rate, test arrivals below that rate at a declared representative load; eligible backlog and oldest eligible age stabilize. Report retirement/retention time separately from service wait. |
| Burst recovery | Inject 10x that declared normal arrival rate for a bounded interval; after arrivals return to normal, eligible backlog drains with fair progress for every repository. Predicted and observed drain time must agree within a predeclared tolerance. |
| Foreground responsiveness | Target no more than 10% p95 regression in concurrent status/read and admitted lifecycle request latency versus GC-disabled control, on the same fixture and host. Report absolute latency too. |
| Holds and long operations | One deliberately held lock cannot prevent progress elsewhere. Demonstrate bounded admission waits and drain/cancel behavior; do not interrupt a destructive unit and assume its outcome. |
| Resource bounds | Record CPU, memory, child-process concurrency and work per pass; enforce implementation budgets selected from the baseline. No unbounded scan, queue growth or automatic high-concurrency catch-up. |

## Safety and operational proof

- A warm candidate cache cannot bypass a newly dirty file, resume, new claim,
  changed project/cell, branch drift or missing pair proof.
- Slow/unreachable remote ownership and failed network refresh are explicit
  holds. Timeouts are not proof of death, completion or disposability.
- The actual disposal transaction participates in resident drain/cutover.
  Bound destructive subprocess trees and reconcile an interrupted receipt
  before retry; do not change production timeouts solely to make a benchmark pass.
- Record backlog/eligible-age and allocation/reclamation rates. Alert on sustained
  overload; allocator admission/backpressure requires its own reviewed policy.
  Do not automatically resume a disabled producer or tighten retention.

Return to the [effort](README.md).
