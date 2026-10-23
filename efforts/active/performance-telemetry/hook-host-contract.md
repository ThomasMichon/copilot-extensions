# Windows command-hook host contract and qualification design

**Status: source plan, not runtime qualification.** Reviewed publication of this
document does not authorize an experiment, change registrations, or satisfy a
runtime acceptance checkbox. The performance campaign coordinator retains
integration and execution admission under #7935. The ten clauses transferred
from #8036 by #8086 remain open in [the root](README.md); that issue's
administrative closure is not a performance pass.

## Scope and evidence boundary

This bounded slice identifies the supported host contract and the smallest next
observation, not another mention-guard optimization or an audit of other hooks.
Manager launch/new/resume remains the campaign's first priority. Excluded:
parser/GraphQL #3677, maintenance #7788, native mux #8031, independently owned
Manager/entry/producer implementation, telemetry modules, collectors, deployment,
live registrations and installed payload edits. No broker, daemon, additional
reporter, deadline increase or weakened scope/body decision is proposed.

This refines the existing process-telemetry vision's attributed process boundary
and [performance measurement](../../../docs/patterns/performance-measurement.md)
qualification obligation; it does not extend standing intent. Preserve
standalone behavior and the
[Windows process-owner pattern](../../../docs/patterns/windows-background-process-launch.md):
visibility, containment, cleanup and timing are separate assertions.

### Read-only receipts (2026-10-23 UTC)

* Source inspected at `08573ca215255b398de90e6e5742387a1f3d66f5` on the
  contribution branch. No inference from the release branch's older draft.
* [#8036 release comment](https://github.com/ThomasMichon/copilot-extensions/issues/8036#issuecomment-6355971059),
  [#8049 performance packet](https://github.com/ThomasMichon/copilot-extensions/pull/8049)
  and [the immutable transferred clauses](https://github.com/ThomasMichon/copilot-extensions/blob/7653a19411947e5ec0f059f374b5e1377d3a9518/efforts/active/performance-telemetry/README.md)
  were read, not re-executed.
* Public executable discovery: `Get-Command copilot -CommandType Application`;
  `copilot --version` returned **1.0.103**. `copilot help hooks` returned
  **Unknown help topic: hooks**; that is not evidence that hooks are unsupported.
  `copilot --help`, `copilot help monitoring`, and `copilot help logging` were
  read. The session documentation helper could not find its **1.0.101** README;
  its interactive help is not proof that this session runs the same executable
  as the separately resolved 1.0.103 command. No runtime was restarted/switched.
* Non-benchmark environment reads: `$PSVersionTable.PSVersion` = **7.4.21**,
  `[Environment]::OSVersion.Version` = **10.0.26300.0**. Application discovery
  found a WindowsApps `python3` alias, then a non-WindowsApps `python`;
  that selected interpreter's `--version` returned **Python 3.9.5**.
  These are discovery observations, not a captured host-spawn argv.
  Exact machine paths remain local evidence, not portable configuration.
* Authoritative [GitHub hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference)
  sections **Command hooks**, **preToolUse**, **Matcher filtering** and
  **Exit codes for command hooks**, plus
  [Using hooks](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/use-hooks)
  and the official
  [CLI changelog](https://github.com/github/copilot-cli/blob/main/changelog.md)
  were read. These are dated live documentation observations, not an immutable
  host implementation specification.

## Supported contract versus missing evidence

| Boundary | Evidence and classification | Consequence |
|---|---|---|
| Shell-free launch | **Documented available in Copilot CLI:** `type: command`, `exec` executable name/path and string-array `args`, without shell interpretation. `exec` cannot coexist with `bash`, `powershell` or `command`. `cwd`, `env`, `timeoutSec` remain documented fields. | Shell startup is **not a universal current host requirement**. Acceptance by this exact local build is **unverified**, not absent. Cloud agent is a different contract. |
| Current plugin entry | Source `plugins/copilot-extensions-harness/hooks.json:5-10` still selects the PowerShell wrapper, with `timeoutSec: 15`. It requires `COPILOT_PLUGIN_ROOT`, searches `python3`, `python`, `py` as Applications, rejects WindowsApps paths and invokes the selected `.Source` with the joined script path. | Retain this exact baseline. Do not replace its selection with bare `python`, add `py -3`, or assert that `exec` implements its fallback policy. |
| Interpreter/provenance | Changelog **1.0.26** documents plugin-root environment variables; current reference documents literal direct arguments, cwd and env expansion. | No documented automatic Python discovery policy equivalent to the wrapper. Expansion of variables inside `exec`/`args`, relative executable resolution, and exact Windows quoting/Unicode/space behavior need a capability witness or host-owner clarification. Pin already-resolved absolute fixture paths for a probe; never commit machine paths or invent interpolation. |
| Input/output | Reference documents `preToolUse` input `sessionId`, `timestamp`, `cwd`, `toolName`, `toolArgs`; the how-to demonstrates stdin JSON. Output is parsed from stdout, after progress lines are removed. Invalid/empty remaining JSON means no output. A deny must retain its reason. | Validate identical stdin, cwd, environment/provenance, literal argv and decision parsing on shell and direct entries. Do not use progress output as an instrumentation channel. |
| Pre-spawn elapsed/remaining time | **Absent documented support:** no monotonic dispatch-start, remaining composed deadline or elapsed-before-spawn field in the published command-hook payload/config. `timestamp` is epoch time, not a documented deadline origin. | Host pre-spawn budget remains **UNKNOWN**. Subtracting child wall time from `timestamp`, or inventing an environment variable, is not a contract. A supported owner interface is a dependency. |
| Composition/deadline | Reference says multiple same-event hooks execute in order; per-entry timeout defaults to 30s. Source uses 15s. Neither specifies one shared two-second dispatch deadline. | The campaign ceiling includes all entries, startup and cleanup. It is not the sum or minimum of watchdogs. No timeout setting changes here. |
| Timeout and decisions | Reference explicitly says command-hook non-timeout errors deny `preToolUse`, but **timeouts always fail open**, including policy hooks. Changelog **1.0.67** records timeout continuation. | Setting `timeoutSec: 2` would not prove the budget and could bypass a body denial. It is not an acceptable shortcut. |
| Child-tree completion | Reference says a timed-out command is killed; **no documented descendant join bound, cleanup-complete receipt, creation flags, or cleanup-inclusive end timestamp** was found. | Host cleanup latency and containment are **UNKNOWN**. Root exit and a warning do not prove descendants are reaped. Plugin Git cleanup does not establish host-tree cleanup. |
| Native observation | **Supported:** `copilot help monitoring` describes optional native OTel/file export; logging supports `--log-dir`/`--log-level`. Changelog **1.0.12** says hook executions became span events rather than child spans; **1.0.98** records lost child-exit-signal completion repair. | Do not claim native hook telemetry is absent. Its current event schema, start/end coverage, monotonic clock and cleanup inclusion are **unverified**; public monitoring help lists tool/agent/sandbox metrics, not a qualification-ready hook deadline/descendant contract. |
| Instrumentation implementation | No adapter, controlled lane or host change is implemented by this document. | **Unavailable implementation in this slice** is distinct from an unsupported host feature. Source reading cannot certify windows, load, timing, CPU or process counts. |

The older [cold-spawn pattern](../../../docs/patterns/cold-spawn-latency-budget.md)
records CLI 1.0.87-0 observations and says shell wrapping is mandatory. That
assertion conflicts with the current official `exec`/`args` reference and must
not be used as current-host proof. Its historical measurements remain observations,
not an unavoidable floor. This scoped sidecar records the discrepancy without
rewriting another owner's shared pattern or launching an optimization.

## What is measured already, and what is not

#8006 merged `15a1de3227500534e98d28758902f8c4762fbe81`; #8049 merged
`67aa451179484ad459e93b3d0850318a45528ff9`. Credit the relevance/shared-decision
deadline/cleanup controls and deferred subprocess import.

#8049 reports alternating native wrapper samples, **n=3 per cell**: candidate
maxima **1.846s irrelevant / 1.989s relevant-safe / 1.922s deny**. Its actual
blocked-Git and slow-body wrappers were **3.312s / 2.789s**, one each.
The shared host was noisy; the 72 regressions establish finite semantic evidence,
not tails. Empty-shell median/max **1.102/1.252s** and empty-Python
**0.274/0.675s** are neither additive disjoint spans nor proven minimum costs.

**Measured unavoidable host floor: UNKNOWN.** An observed over-budget case
proves an unmet case, not that every supported implementation must exceed two
seconds. Direct-exec support further prevents concluding a mandatory shell tax.
Retain the honest-floor alternative: if controlled evidence eventually proves a
host-owned constraint, report its scope, uncertainty, unresolved cases and owner;
do not label that report a two-second pass or waive the acceptance gate.

## Smallest next experiment — requires separate permission

No experiment below ran for this plan. The coordinator first chooses whether to
authorize a **capability-only smoke**, seek host-owner clarification, or defer
until Manager work is qualified. A merge of this plan is not that decision.

1. **Admit a private, owned local fixture and one host invocation.** Obtain
   explicit approval for source fixture hook registration, starting an isolated
   host, harmless synthetic tool input, native diagnostic output, and exact-PID
   cleanup. Never use an existing session, live user/repository registration or
   installed plugin copy. Keep ordinary policy hooks and trust protections;
   if an isolated effective roster cannot be obtained without disabling required
   policy or mutating shared state, stop. Record the complete effective roster
   and cardinality. A `--plugin-dir` alone is not isolation from other sources.
2. **Capability smoke before timing.** Use the existing captured-child owner
   primitive with `CREATE_NO_WINDOW`, redirected streams and caller-owned
   containment; no detached child or new broker. First prove visible-window and
   foreground invariance with an owned inert child. Then admit the isolated host.
   Give one inert fixture command hook an absolute `exec` and literal `args`;
   record received argv/stdin/cwd and plugin-root provenance, return one valid
   decision object, and test space/Unicode paths without shell expansion.
   No guessed `copilot hook run` command or private host import. The exact
   supported way to trigger the fixture's tool event must be selected and
   approved before launch; model instructions alone are not deterministic input.
   A failed capability/roster/containment assertion stops after this smoke.
3. **Inspect the existing native signal, not a second reporter.** If separately
   authorized, use the native file exporter described by
   `copilot help monitoring` with a fixture-owned absolute output path and
   message-content capture off. Explicitly select file export; do not inherit an
   OTLP endpoint or configure a collector. Confirm whether hook events expose
   host dispatch start, spawn, decision consumption and cleanup completion.
   Record missing fields and sampling/export errors. Debug logs may diagnose
   failures, but have no promised timing schema. Do not turn them into a
   guessed monotonic API. If the required host boundaries are missing, stop:
   whole-dispatch qualification remains blocked, even if direct exec works.
4. **Only after boundary admission, propose the bounded qualification run.**
   A separately reviewed runner reuses the current native/process ownership and
   reporting seams. It runs serially, no load generator, no concurrent matrix,
   no unrelated process control, and only in a coordinator-approved quiet window.
   Start with one sample per case, stop on missing identity, changed decisions,
   timeout, residual child, visible window/focus change or incomplete trace.
   A synthetic blocked Git/slow body must be locally owned, bounded and
   network-inert; fake publication commands must be incapable of a real write,
   even if the guard fails. A case failure is evidence, not grounds to silently
   drop that case or keep repeating under an unhealthy host.
5. **Qualify, not extrapolate.** Preserve exact baseline source and candidate
   source, host executable/version, shell, interpreter, source plugin root,
   workload/remotes/body cardinality and sandbox/roster. Keep cold, warm and
   first-touch cells separate; alternate matched A/B without summing overlaps.
   Record host-dispatch start through decision consumption **and last owned
   descendant exit**. Use pinned creation identities/handles, not process names
   or polling that misses short-lived children. Sampling gaps and unobservable
   descendants are unavailable, never zero. Cleanup must include timeout and
   cancellation paths, measured separately from mere root return.

A direct-exec probe is the smallest supported test of shell avoidance; it is
**not** permission to ship a replacement interpreter resolver. If fixture
equivalence passes but production plugin-root expansion/discovery remains
unproven, keep the production wrapper unchanged and return to its owner.

### Sampling and acceptance plan

Predeclare sample caps and a stop rule with the coordinator before the timing
stage. Preserve every original sample/error, expected/completed count,
failure/drop count, workload/roster cardinality, source/installed/serving identity,
and descendant CPU/spawns with measurement source and coverage. Use one
monotonic end-to-end duration; overlapping explanatory spans are not summed.

Report p50/p95/p99 using a named method (proposed: nearest rank
`ceil(p*n)`, one-based), plus max and n for **each** case/cache cell.
The initial smoke and n=3 history are insufficient tail qualification.
Even n=100 makes nearest-rank p99 essentially the maximum, not a robust tail
or worst-case guarantee. For orientation, 299 independent zero-exceedance
samples would only put the one-sided 95% binomial upper bound on the exceedance
rate below 1%; independence/stationarity cannot be assumed on a shared host,
and this is not a proposed automatic 299-run workload or proof of a hard ceiling.
The owner must approve sufficient representative samples or report
**insufficient/unavailable**, not manufacture p99 confidence.

Measure observer overhead disabled/enabled/failed-or-full-sink through the
existing owner seam; preserve errors/drops and absolute/relative overhead.
No synchronous export or per-hook diagnostic child. A complete single-hook
experiment cannot certify all enabled composed hooks or Manager performance.
Release promotion and authorized installed-serving validation are separate:
source merge, released payload, installed payload and actually serving host each
need their own identity. This plan requests no rollout.

## Dependencies and return decision

* **CLI host owner:** supported pre-spawn/composed remaining-budget semantics,
  hook event timing schema and descendant-cleanup completion/containment
  contract. Public product feedback venue is
  [github/copilot-cli](https://github.com/github/copilot-cli#-feedback-and-participation).
  No exact matching upstream issue is established here. Do not open or redirect
  an issue there without the campaign coordinator's routing decision; retain
  tracking under #7935 and this effort meanwhile.
* **Plugin entry owner:** preserve discovery/provenance/stdin/cwd/decision
  parity if a later direct-exec change is proposed. New implementation needs a
  fresh bounded claim, source-plan review and semantic tests.
* **Process/native measurement owner:** approve a controlled local lane,
  no-window containment, exact child identities, exit/CPU evidence and observer
  overhead using existing seams. No ownership transfer from other campaigns.
* **Campaign coordinator:** independently review this source plan, authorize
  or decline the single capability smoke, decide external routing if the native
  signal lacks required fields, and retain all strict runtime gates.

## Exact retained acceptance (frozen provenance, not a second live checklist)

The following ten unchecked clauses are copied verbatim from #8086's merge.
Their live owner/status remains the root; publication of this sidecar checks
off none of them. This snapshot prevents narrowing the original alternative
while developing a smaller experiment.

- [ ] Credit #8006's relevance/shared-decision-budget/cleanup controls and #8049's
  deferred subprocess import. Preserve repository/plugin provenance, publication/body
  validation, original decision semantics and normal standalone behavior; a narrower
  scope probe, fail-open body or increased timeout is not an optimization.
- [ ] Measure the ACTUAL Windows preToolUse hooks.json wrapper from host invocation
  through process return and descendant cleanup. Attribute shell startup, interpreter
  selection/import, Git, decision, body I/O and cleanup without summing overlapping
  spans. Source inspection, Python helper duration, unit count and a configured timeout
  are not whole-dispatch observations.
- [ ] Preserve normally-instant1-2second intent and the original honest host/shell-floor
  condition. If an unavoidable floor prevents the target within this plugin, report it
  with the unmet cases and responsible host boundary; a floor report is not a passing
  two-second result or an invented waiver of the retained qualification.
- [ ] Obtain the actual hook host's pre-spawn elapsed/remaining-budget and
  descendant-cleanup accounting contract plus an explicitly owned controlled
  representative native lane. Name unavailable capabilities and owner dependencies; do
  not invent an API or infer permission from this intake.
- [ ] Consider shell-startup avoidance only through an actually supported host-owned
  direct-argv/interpreter-resolution contract that preserves plugin/source provenance,
  stdin, cwd, environment, exact argv and decision parsing. No plugin-local resident
  daemon, unreviewed shared bootstrap/launcher change or second reporter is authorized.
- [ ] Preserve #3677 parser/GraphQL independence, other-hook exclusions,
  source/installed distinction and existing rollout/host/process owners. Any future host
  implementation needs its own bounded eligible claim; the released source lane is not a
  new broad claim.

- [ ] Keep matched baseline/candidate shell/interpreter/source/workload identities,
  cold/warm/first-touch controls and complete original samples/errors. The existing n=3
  observations and shared-host noise are insufficient tail qualification; unavailable
  measurements remain unavailable.
- [ ] Qualify irrelevant, relevant-safe and deny outcomes plus actual blocked-Git and
  slow-body wrapper cases, including descendant cleanup and unchanged scope/body
  decisions. Retain the reported3.312s/2.789s over-budget observations as unresolved,
  not replaced by candidate fast-case maxima or72 unit passes.
- [ ] Report actual end-to-end p50/p95/p99/max with sample/quantile sufficiency,
  expected/completed counts, roster/cardinality, source/installed identities and
  attributable descendant CPU/spawns. No double-counted spans, missing-child-as-zero
  result or unmeasured host-floor claim.
- [ ] Prove normally-instant/ceiling behavior at the actual supported host boundary OR
  explicitly preserve the measured unavoidable floor, unresolved strict cases and
  missing host dependency without claiming qualification. Any budget/contract revision
  requires its existing reviewed owner process, not a silent local weakening.
