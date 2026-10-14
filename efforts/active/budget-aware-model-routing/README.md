# Budget-Aware Model Routing

- **Slug:** `budget-aware-model-routing`
- **Repo:** copilot-extensions
- **Branch(es):** serial per-slice worktrees and pull requests
- **Created:** 2026-09-05
- **Status:** Active
- **Vision:** [`visions/harness-guidance`](../../../visions/harness-guidance/README.md)
  - `budget-aware-model-routing`
  - `adapter-based-budget-posture`
  - `concise-current-budget-guidance`
  - `budget-refines-but-never-qualifies`
  - `unavailable-is-not-zero`
  - `one-posture-many-surfaces`
- **Umbrella issue:** [#2137](https://github.com/ThomasMichon/copilot-extensions/issues/2137)
- **Foundation:** [#2014](https://github.com/ThomasMichon/copilot-extensions/issues/2014)
  (evidence-calibrated eligibility, routing, and outcome provenance)

## Guiding Intent

Give a harness a portable, current view of finite AI-usage budget posture before
it chooses among otherwise-eligible model classes. Keep allowance, usage, reset
horizon, freshness, and errors attributable; calculate sustainable pace and
projection deterministically; and expose one resolved result to both concise
session guidance and machine-readable consumers.

Compose with evidence-calibrated model routing rather than rebuilding it.
Budget posture may refine qualified choices, but it never qualifies an unproven
model, lowers a product gate, or overrides an explicit operator choice.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Public effort host | Owns issue, plan, serial PRs, and final synthesis | managed worktree |
| Budget-contract implementer | Owns posture schema, arithmetic, and adapters | bounded implementation slice |
| Routing integrator | Composes posture with existing eligibility output | bounded implementation slice |
| Independent reviewer | Reviews contracts, failure semantics, and public safety | repository review workflow |

## Coordination

- **Topology:** serial per-slice PRs; one open implementation PR at a time.
- **Host (owns PRs):** public effort host.
- **Delegates:** implementation and review scopes remain disjoint; no delegate
  owns publication, promotion, or completion.
- **Handoff:** every merged slice records its result and next boundary here
  before another slice begins.

## Context

The evidence-calibrated routing effort (#2014) already owns task
classification, model eligibility, demonstrated/candidate/held/failed states,
selection reasons, trial admission, and outcome provenance. Its design
deliberately keeps provider billing and longitudinal accounting external.

Issue #2137 identifies a sibling delta: a finite account budget can be consumed
too quickly even when every individual model choice is evidence-qualified and
locally economical. Provider billing APIs may expose detailed usage while an
account entitlement or reset horizon comes from a separate source. A portable
harness therefore needs one small posture contract over multiple partial
adapters, with explicit source, freshness, and failure semantics.

The intended implementation is a new independently usable `budget-guidance`
plugin. It owns budget posture, arithmetic, adapters, current status, and a
concise context contribution. The delegation module in `agent-conduct-guidance`
(formerly `delegation-guidance`) owns routing strategy and eligibility; it may
consume budget-guidance output when both are enabled.
The checked-in pointer plus exact-session guidance file owns current
delivery; native host composition may later carry direct plugin output after
supported-version proof. External accounting systems remain the owners of
longitudinal ledgers and detailed cost attribution.

## Request

> Add a shareable harness primitive that can obtain current AI-usage allowance
> and consumption through pluggable adapters, calculate sustainable run-rate and
> reset projection, inject concise budget-aware model-routing guidance, and
> expose the same posture as machine-readable status without depending on a
> private service.

### Follow-up — agent-owned quota and advisory pacing

**Public-safe summary of operator intent:** add an on-demand skill and helper
that reads Copilot allowance, consumption, and next reset for the requesting
agent's account. Use run-rate projections to advise autonomous background-worker
and parallel-fleet concurrency. Warn about projected overages at session launch,
but generally do not block operator-directed work on harness pacing grounds.
Cooperative sleeps and suspensions are sparing options, not automatic remedies.
Monthly entitlement can change; one period's increased allowance must not become
a permanent default.

**Agent-recommended constraints:** prefer supported, in-session SDK surfaces;
retain runtime/version, account provenance, quantity kind, source acquisition
status, and reset-source diagnostics. Do not call a new client's current-account
quota the requesting session's quota without verifying the identity. Keep
credentials and unrelated account metadata out of helper output. An SDK read
timestamp is not proof that the provider metadata was refreshed.

## Plan

### Phase 0 - Reviewed intent and architecture

- [x] Search existing issues, visions, and efforts; file and claim #2137 without
  duplicating #2014.
- [x] Extend the harness-guidance vision with budget-aware routing and explicit
  adapter/failure semantics.
- [x] Assign ownership: `budget-guidance` owns posture; `delegation-guidance`
  owns eligibility and routing policy; each plugin owns its exact-session
  guidance file; external accounting owns longitudinal history.
- [x] Land this vision and effort through the repository review gate before
  plugin implementation.

### Phase 1 - Budget posture contract and arithmetic

- [x] Define a versioned inert configuration and reading contract for allowance,
  consumption, reset instant, capture time, source, freshness, optional trailing
  rates, and explicit availability/error state.
- [x] Define field-level source precedence so partial allowance and usage
  readings can compose without silently replacing one another.
- [x] Calculate remaining allowance, overspend, time remaining, sustainable
  daily rate, effective configured ceiling, projected position at reset, and
  warning bands.
- [x] Handle partial days, reset boundaries, zero time remaining, stale
  readings, contradictory readings, and consumption beyond allowance.
- [x] Keep configuration inert, strictly parsed, and default-off.

### Phase 2 - Portable adapters

- [x] Add a manual/static adapter that can provide a complete posture without
  network access or another service.
- [ ] Add a provider API adapter for available personal or organization
  AI-usage reports, preserving empty, unauthorized, and unsupported results.
- [x] Add a bounded external-command adapter contract for account surfaces or
  organization-specific readers that cannot live in the public plugin.
- [x] Make adapter order and field authority configurable without allowing
  repository data to override operator-owned credentials or safety policy.
- [ ] Ensure no adapter failure becomes a zero-consumption success.

### Phase 3 - Plugin, status, and context delivery

- [x] Add the independently installable `budget-guidance` plugin using the
  repository's runtime-plugin and configuration patterns.
- [x] Expose one machine-readable status result for hooks, dashboards, scripts,
  and routing consumers.
- [x] Contribute a concise, attributable session-start cue through the
  checked-in pointer plus exact-session guidance-file pattern.
- [x] Keep injected output inside a declared byte/token budget and avoid loading
  the detailed adapter/config reference into every session.
- [x] Preserve source, capture time, freshness, projection, and error status in
  both human and machine-readable output.

### Phase 4 - Routing composition

- [ ] Let the delegation module in agent-conduct-guidance consume budget posture
  when available without a hard dependency on budget-guidance.
- [ ] Refine only demonstrated eligible choices; never qualify candidates,
  bypass holds, or weaken trial admission.
- [ ] Preserve explicit operator model/profile choices as authoritative.
- [ ] Return legible budget-based recommendation, caution, escalation, and
  no-fit reasons without changing direct-versus-delegate decisions.
- [ ] Keep standalone budget-guidance useful when the delegation module is absent.

### Phase 5 - Validation, publication, and adoption

- [ ] Add focused tests for arithmetic, source precedence, stale/unavailable
  adapters, empty provider reports, reset boundaries, config precedence,
  injection size, and routing composition.
- [ ] Validate Bash and PowerShell paths plus clean-room installation when
  practical.
- [ ] Keep marketplace, manifest, package, and runtime versions aligned.
- [ ] Publish through the repository's normal review and self-merge flow.
- [ ] Deploy through the unified update path and verify source/runtime identity.

### Phase 6 - Agent-owned quota helper and pacing advice

- [x] Add a reusable helper and discoverable on-demand skill for Copilot runtime
  quota acquisition on behalf of the requesting session.
- [ ] Preserve exact session/account attribution; report missing session context,
  mismatched account, unsupported RPCs, and cached provider metadata explicitly.
- [ ] Read raw account next-reset metadata separately from normalized category
  snapshots. Reject missing, zero-epoch, or past reset values for current
  forecasting; diagnose disagreements instead of silently selecting a date.
- [x] Distinguish token-based credits from premium-request quantities and
  unlimited entitlements before interpreting numeric values.
- [x] Calculate sustainable remaining pace and projection from attributable
  same-period rate evidence. Label assumed period starts and cycle-average
  estimates; do not manufacture measured daily/weekly rates from one snapshot.
- [x] Emit advice for autonomous worker/fleet aggressiveness and overrun warnings,
  without blocking operator-directed work or mutating worker state.
- [x] Document explicit, sparing cooperative pacing separately from read-only
  quota acquisition. Do not add automatic sleeps or suspension control in this
  slice.
- [ ] Add regressions for changing allowance, selected-account mismatch,
  malformed/missing/stale reset, unit ambiguity, cached metadata, and advice-only
  behavior.

### Phase 7 - Completion

- [ ] Resolve every Plan and Validation Plan item, including quota-helper and
  pacing validation; record evidence, close #2137, and archive this effort.

## Validation Plan

- [x] Native Windows external-reader lifecycle validation observes a real
  windowless parent, two console-descendant cycles, no visible windows/focus or
  Default Terminal acquisitions, and complete timeout cleanup; batch/stream
  grant refusals run in the same native lane.
- [x] Deferred to `ThomasMichon/copilot-extensions#6644`: complete native Windows
  session-cue acceptance and diagnose the subsequent reader-mode deadline
  failure. The earlier external-reader receipt remains source-specific; the
  new hook is not claimed desktop-verified.
- [ ] The quota skill/helper identifies the requesting session's account rather
  than silently reading a fresh runtime's global default.
- [x] Acquisition time and provider-observation freshness are distinct; cached
  metadata is not advertised as a confirmed fresh provider reading.
- [x] Monthly allocations are read per period, not carried forward as constants.
- [x] Advice neither blocks operator work nor sleeps, suspends, or changes
  autonomous workers.

- [x] The posture contract is provider-neutral and contains no real account,
  allowance, organization, host, or private service.
- [x] Manual/static configuration works with no network or external service.
- [ ] Provider and external adapters retain source, capture time, freshness, and
  explicit availability/error status.
  External-command coverage landed in #6450; provider acquisition remains open.
- [ ] Missing, stale, unauthorized, empty, or contradictory readings are never
  represented as zero consumption or full remaining allowance.
- [x] Arithmetic handles partial days, reset boundaries, overspend, and zero
  remaining time deterministically.
- [x] Field-level precedence composes partial sources without allowing a lower
  authority to overwrite a higher-authority field silently.
- [x] Machine-readable status and injected guidance derive from the same
  resolved posture.
- [x] Context output stays within its declared budget and carries stable owner
  attribution.
- [ ] Budget posture cannot qualify an unproven model, clear a hold, arm a
  trial, or override an explicit operator choice.
- [ ] Product correctness, safety, review, publication, and deployment gates
  remain unchanged.
- [x] The plugin has no dependency on a private hostname, service, account, or
  downstream repository.
- [x] Detailed longitudinal accounting and billing-event storage remain outside
  the plugin.
- [ ] Focused plugin tests, guards, docs consistency, install contracts, and
  cross-platform behavior pass without requiring the repository-wide exhaustive
  portfolio.

## Proposal

Build `budget-guidance` as a small runtime plugin with four layers:

1. **Posture core:** strict configuration/reading models plus pure deterministic
   arithmetic and field-level source resolution.
2. **Adapters:** manual/static, provider API, and bounded external-command
   readers that return partial attributable readings rather than mutating global
   state.
3. **Presentation:** one status command and one concise context contributor
   derived from the same resolved posture.
4. **Optional routing integration:** agent-conduct-guidance's delegation module
   reads posture status and adds budget-based reasons after eligibility
   resolution; either plugin remains usable alone.

The plugin stores at most bounded current/cache state needed for freshness and
resilience. It does not become a historical billing ledger, model benchmark,
promotion authority, task queue, or dashboard.

## Journal

### 2026-10-14 - Exact-session delivery merged; routing boundary next

- PR #6646 merged as `d29ee82c3dd46a5fe9ad36235903fb0d4f4fad46`
  after current-head Copilot approval and the required PR gate,
  source-attribution, and workflow-lockdown checks passed.
- Closed Phase 3's implementation/publication boundary. Review corrections arm
  the POSIX deadline before payload path resolution/import, test all five
  directory levels with synthetic reparse attributes, and include asynchronous
  PowerShell stdin delivery within the writer's single deadline.
- The bounded Linux session-delivery lane passed 46 cases with three explicit
  platform/tool skips and verified cleanup. Earlier full-plugin and source
  guards remain recorded; no skipped PowerShell/native execution is credited
  as a pass.
- Native acceptance remains transferred to #6644. That objective also owns the
  reviewed witness-only window/focus attribution defect: unrelated desktop
  processes must not be counted as fixture descendants. The source receipt
  for the earlier external-reader launch remains source-specific.
- Re-read the complete Plan and Validation Plan after merge. The effort stays
  Active; provider acquisition/account proof, routing, release, and authorized
  rollout remain open.
- Next: Phase 4 routing composition through the current delegation owner,
  `agent-conduct-guidance`, not the deprecated `delegation-guidance` placeholder.
  Keep eligibility and explicit operator choices authoritative, consume the same
  resolved posture optionally, and do not infer per-task prices or new model
  eligibility from account totals.

### 2026-10-13 - Windows acceptance transferred to a tracked objective

- At the operator's direction, transferred the new session-cue desktop proof
  and unresolved reader-mode deadline failure to
  [#6644](https://github.com/ThomasMichon/copilot-extensions/issues/6644).
  Continue the portable session-delivery slice through normal review and CI;
  deferral does not credit a native pass or relax its acceptance assertions.
- The bounded native run reported seven passes and two failures with verified
  supervisor cleanup. Reader acquisitions reached the unchanged deadline with
  no established cause. The cue collector incorrectly read configuration as
  process evidence; it now reads only the two literal process-record files.
- Resolved virtual-environment launchers to the direct base console interpreter
  and added direct writer-parent checks, so outer Job cleanup cannot hide a
  redirector child. Forwarding-only test timing output survives failed-run
  cleanup without changing production reader limits.
- The exact-source follow-up could not reach the former interactive validation
  session. No replacement session, service repair, snapshot patch, or
  pass-chasing retry was performed. Native proof remains owned by #6644.
- Linux implementation and source guards remain verified. Current-head review,
  provider/account proof, routing, release, and authorized rollout remain open.
  The effort stays Active.

### 2026-10-13 - Bounded exact-session posture delivery

- Implemented Phase 3's launch-time cue through an output-free writer and two
  reviewed projection declarations: a minimal file pointer and a separate static
  advisory boundary. The owning payload loads its pure posture source without
  provisioning a runtime, refreshing a provider, or granting any reader.
- Reused status configuration and posture resolution, including attributable
  sources, capture times, freshness, contradictions, and explicit failures.
  Configuration reads are regular-file-only and bounded; opaque adapter errors
  and detailed arrays stay out of the computed cue.
- Bounded the entire UTF-8 cue to 4096 bytes and the suite's 1024-token heuristic.
  Oversize evidence becomes explicit unavailable data with all values withheld.
  Fresh and resumed writes atomically replace only the requesting session's
  file and refuse directory symlinks/reparse points.
- Independent design critique found no blocking flaws. It prompted an internal
  five-second POSIX process deadline in addition to the outer hook bound, plus
  explicit native-fixture provenance. The PowerShell writer already suppresses
  console creation, drains both output streams concurrently, and has its own
  bounded timeout/cleanup path.
- Added an opt-in native session-cue mode to the existing Windows desktop
  witness. It retains the external-reader lane's limits/assertions and adds two
  cue cycles plus production-wrapper timeout cleanup. Exact-source native
  acceptance for the new launch path is deferred to #6644; earlier
  external-reader evidence is not used as proof of this hook.
- Publication, provider/account proof, routing, release, and authorized rollout
  remain open. The effort stays Active.

### 2026-10-13 - External-reader contract merged

- PR #6450 merged as `35738f6e095776c824789c37929a8680688688df` after
  exact-head Copilot approval with zero open findings and both required checks
  passing. The earlier merge attempt correctly refused the still-pending PR
  gate; no readiness bypass was used.
- Closed the external-command implementation and publication boundary, with
  invocation-owned grants, bounded output/deadlines, explicit failures, and
  retained source/capture attribution. Unrepresentable Decimal JSON is
  contained at external and file-input parsing boundaries.
- Added runtime-only acquisition provenance so direct library and CLI
  evaluations reject captures later than their evaluation instant. Default CLI
  evaluation is sampled after acquisition; explicit historical evaluation
  remains authoritative and manual/static semantics are unchanged.
- POSIX cleanup now uses an isolated supervisor outside an atomically spawned
  reader group, retaining the reader root with non-reaping `waitid(WNOWAIT)`.
  Explicit control requires group teardown, root reap, a private cleanup
  acknowledgment, and successful host completion. Neither a pipe close nor
  ordinary zero exit alone proves cleanup.
- The bounded full Linux suite passed 270 cases with seven platform/opt-in
  skips; the fast lane passed 19 cases with four skips. The real installer
  fallback matrix is outside the guard lane. Native Windows launch/Job evidence
  remains credited to the exact recorded source; the subsequent parsing and
  POSIX changes did not alter that launch path or its native witness.
- The effort remains Active. Provider/account identity, native macOS lifecycle
  evidence, routing, release, and authorized rollout remain open.
- Next: Phase 3's bounded session-start posture cue and exact-session delivery.
  Reuse the same pure posture without granting hooks executable authority,
  introducing a cross-plugin aggregator, or loading detailed references.

### 2026-10-13 - External-reader native acceptance and safe group ownership

- Verified the native Windows lane on exact source `1b8da8b66`: six passed,
  zero failed/skipped, with an interactive desktop and supervisor cleanup.
  Two successful console-descendant cycles and timeout cleanup retain the
  original acquisition limits and acceptance assertions. Five grant cases cover
  trailing-name batch aliases and executable streams.
- Independently matched the executed native archive to the authoritative Git
  archive with its recorded prefix, plus the reader and witness file hashes.
  Earlier source snapshots failed their first acquisition; the cause remains
  unproven. This receipt credits the literal current-source witness only,
  not a relaxed retry or an inferred historical repair.
- Hardened POSIX cleanup against ignored `SIGCHLD` and foreign waiters: the
  isolated group owner stays alive until control-pipe EOF and terminates its
  own live group. The caller never signals a numeric PID/group after ownership
  may be lost; unexpected owner exit refuses cleanup confirmation.
- Publication review and required checks are still pending for the latest
  corrections. Provider/account identity, native macOS lifecycle evidence,
  session delivery, routing, release, and authorized rollout remain open.

### 2026-10-13 - Bounded external-reader implementation

- Implemented the Phase 2 external-command contract: configuration holds only
  adapter ID, source, and field authority; execution requires an invocation-owned
  operator grant. Acquired readings reuse the existing resolver and arithmetic.
- Kept output/deadline limits, unavailable/error states, source and capture
  validation, diagnostic redaction, and owned process cleanup explicit. Review
  regressions cover option-presence validation, host-interpreter refusal, missing
  configuration, and preservation of primary failures alongside cleanup errors.
- The full contained plugin suite reports 253 passed and four platform skips.
  Linux-native lifecycle evidence is recorded; Windows and macOS native lifecycle
  validation remain open, with unsupported capabilities refusing execution.
- Implementation is complete for this contract, not for the provider API
  adapter, publication gate, live-account identity, release, or authorized rollout.
  The effort remains Active; the merge receipt will be recorded after landing.
- Next: continue provider acquisition and requesting-account proof, then current
  session guidance and routing integration. Do not grant repository data command
  authority or treat captures as live provider refresh evidence.

### 2026-10-12 - Same-period cycle-average pacing

- PR #6417 merged as `8b21c9788bab1fa492a678239b66c9bae4da34a2` after
  current-head Copilot approval with zero open findings and successful required
  checks. The implemented pacing slice is landed; the effort remains Active
  and release, authorized rollout, and live-account proof are not claimed.
- Qualified the selected provider's user-scoped AI-credit monthly usage against
  its authoritative account consumption, reset, observation ordering, and
  freshness. Missing cycle starts and incompatible evidence withhold the rate;
  no previous allocation or calendar-derived period supplies a default.
- Reused posture arithmetic for explicitly labeled cycle-average estimates,
  without presenting one snapshot as a measured daily/weekly rate or settlement
  watermark. Recommendations remain advice-only and cannot infer a worker count
  from account totals, change workers, or block operator work.
- Preserved filesystem error causes for quota captures while retaining opaque
  JSON-parse diagnostic redaction.
- Added missing/null monthly-enum regressions so absent rate evidence preserves
  valid account balances, plus a real missing-capture-path regression that
  preserves lookup causes while retaining regular-file rejection.
- Live provider acquisition and independent selected-account proof, raw legacy
  reset reconciliation, provider/external adapters, launch delivery, routing,
  cross-platform validation, and release/rollout remain open.
- Next: resume Phase 2 through a bounded external-reader contract. Keep
  invocation authority operator-owned, deadlines and output bounded, and
  missing/unauthorized/error evidence distinct from numeric success.

### 2026-10-12 - Provider observation and reset evidence

- PR #6406 merged as `d80fb34e5cee33a75ac1c5fbebfb49689e9f891e` after
  current-head Copilot approval with zero open findings and successful required
  checks. This completes the provider-evidence normalization slice, not live
  provider acquisition, release, rollout, or the broader effort.
- Added explicit provider/quota selection over the incumbent SDK projection.
  Provider acquisition, access, capacity, observation kind, quantity authority,
  and units remain independent; unsupported or ambiguous evidence does not
  fall back to compatibility counters.
- Compared raw provider budget reset metadata against compatibility snapshots.
  Missing, estimated, past, synthetic, or conflicting dates remain diagnostic
  states rather than inferred billing-cycle boundaries.
- Qualified incumbent account observations reuse existing posture arithmetic
  for sustainable pace. Capture files stay unverified and cannot enable that
  calculation. Same-period rate evidence, forecast, routing, and launch delivery
  still remain open.
- Review exposed an unlimited-entitlement freshness bypass. The shared
  observation validation now covers finite and unlimited evidence alike; eight
  regressions fail before the fix, and the corrected plugin suite passes.
- Next: preserve capture filesystem failure diagnostics, then add attributable
  same-period cycle-average estimates and advice-only pacing. Keep measured
  daily/weekly rates, unknown cycle starts, and live-provider proof distinct.

### 2026-10-12 - Session-owned quota acquisition slice

- PR #6384 merged as `f693b4dae60638906dfd0a8c59a003b5edb7c222` after an
  exact-head Copilot approval with zero open findings and successful required
  checks. This lands the acquisition contract and skill, not a live provider
  forecast or deployment. The effort remains Active.
- Added an on-demand skill, bounded incumbent-session SDK quota helper, and
  allowlisted capture-inspection CLI. The helper refuses mismatched requesting
  sessions before RPC and never creates, refreshes, resumes, or stops a client.
- Distinguished cached session projection from provider observation freshness.
  Runtime quota values retain unit, unlimited entitlement, reset, runtime, and
  session attribution diagnostics; fresh read timestamps do not become fresh
  provider evidence.
- Deliberately left forecasting and concurrency advice unavailable for these
  cached readings. Raw account reset reconciliation, independently attributable
  provider freshness, same-period rate evidence, and integration remain open.
  This is a partial Phase 6 slice, not effort completion.
- Independent review identified that cached counters must not override an
  explicit provider quota denial. Added boolean validation and withheld all
  budget values on denial, including conflicting unlimited entitlements; the
  new regressions fail against the original normalization before passing with
  the correction.
- Next: evaluate provider-owned observation and reset evidence separately from
  compatibility snapshots, retaining explicit partial/unsupported results and
  the read-only pacing boundary.

### 2026-10-10 - Quota helper planning and publication reliability

- Added session-attributed quota-helper and advisory pacing intent with variable
  monthly entitlement and an explicit warning-not-blocking boundary.
- Removed a publication blocker caused by ignored artifacts of retired vendor
  trees. Shared discovery now preserves malformed source-bearing copies and
  excludes artifact-only remnants across synchronization and publication gates.
- Reduced automatic review-request amplification. The operator's later
  simplification supersedes the intermediate polling design: wait five minutes,
  inspect live review activity, and nudge once only if needed. There is no
  post-nudge polling or seat inference. Repository tokens perform reads; the
  licensed account credential performs only the write. Existing native-review
  trust policy is unchanged; provider reset/backoff errors remain explicit.

### 2026-09-05 - Kickoff

- Public issue #2137 claimed the new delta after confirming #2014 already owns
  evidence-calibrated model eligibility, routing policy, and outcome provenance.
- The proposed vision extension makes finite budget posture a first-class input
  while preserving `budget-refines-but-never-qualifies` and
  `unavailable-is-not-zero`.
- The architecture selects a new independently usable `budget-guidance` plugin
  and composes it with existing delegation-guidance and context-injection
  ownership instead of duplicating either.

### 2026-09-05 - First runtime and posture slice

- Added the independently installable `budget-guidance` runtime CLI with
  generated payload-local invocation, versioned installers, bootstrap
  reconciliation, and a static command catalog. Context contribution remains
  deferred.
- Added strict version 1 configuration, reading, and posture schemas; inert
  static adapters; deterministic field authority; attributable contradictions;
  and explicit unavailable, error, stale, and contradictory states.
- Added pure balance, horizon, sustainable-rate, daily-ceiling, projection, and
  warning-band calculations plus JSON and human status from one posture.
- Generalized payload-invocation owner validation to support lowercase runtime
  plugin ids outside the core `agent-*` installation-cell family.
- Focused tests and repository contract guards pass for this slice. Provider,
  external-command, context-delivery, and routing-composition work remains open.

### 2026-09-05 - Independent review corrections

- Anchored reset horizon and projection to posture evaluation time rather than
  the newest source capture time, and made an elapsed budget period stale until
  a future rollover contract supplies the next period.
- Replaced expiry-instant addition with age comparison so arbitrarily large
  validated freshness durations and near-maximum timestamps cannot overflow.
- Added posture and CLI regressions for elapsed reset periods, huge freshness,
  and near-maximum timestamps.

### 2026-09-05 - Numeric and bootstrap hardening

- Bounded budget quantities to a provider-neutral `1e18` domain so extreme
  JSON exponents are rejected before Decimal arithmetic.
- Converted timezone-normalization overflow at datetime boundaries into strict
  modeled configuration errors.
- Made both installers acquire or resolve standalone uv before requiring an
  ambient Python, request an explicit compatible interpreter from uv, and use
  system Python only as fallback.

### 2026-09-05 - Release-candidate hardening

- Bounded freshness and authority integers before conversion so extreme JSON
  exponents fail quickly as modeled configuration errors.
- Replaced float timestamp sorting with stable direct datetime ordering,
  preserving microsecond recency through year 9999.
- Added a shell-native generated command-catalog mode and Python-independent
  POSIX first-session stamp path, while retaining PowerShell catalog parity.

### 2026-09-05 - Installation contract closure

- Made POSIX stamp copy the exact owning payload into the same per-version
  snapshot model as PowerShell and removed cross-marketplace wildcard fallback.
- Made the Windows compatibility wrapper serialize first-use provisioning on
  the runtime-root lock and re-resolve after lock acquisition, preventing
  duplicate concurrent installs.

### 2026-09-05 - Python-less update reconciliation

- Made POSIX session bootstrap compare deployed and payload versions with
  bounded shell-native parsing, so an older valid runtime is reconciled even
  when no ambient Python command exists.
- Added a dev4-to-dev5 Python-less update regression and confirmed the
  PowerShell bootstrap already performs native JSON version comparison.

### 2026-09-05 - POSIX self-stage option parity

- Preserved parsed action, custom install root, and force state across POSIX
  self-staging through dedicated environment values without command evaluation.
- Added installed-payload regressions proving a custom root is retained and a
  forwarded force request rebuilds only that root; confirmed PowerShell already
  forwards its bound parameters safely.

### 2026-09-05 - PowerShell self-stage quoting

- Wrapped staged PowerShell `-File` launches in an encoded invocation with
  literal script and argument values, avoiding `Start-Process -ArgumentList`
  re-tokenization for paths containing spaces.
- Added an executable installed-payload regression with spaced profile,
  marketplace, and custom runtime paths, covering stamp, normal install, and
  force forwarding.
