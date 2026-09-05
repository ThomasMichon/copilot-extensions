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
concise context contribution. `delegation-guidance` remains the owner of routing
strategy and eligibility; it may consume budget-guidance output when both are
enabled. `context-injection` remains the composition authority. External
accounting systems remain the owners of longitudinal ledgers and detailed cost
attribution.

## Request

> Add a shareable harness primitive that can obtain current AI-usage allowance
> and consumption through pluggable adapters, calculate sustainable run-rate and
> reset projection, inject concise budget-aware model-routing guidance, and
> expose the same posture as machine-readable status without depending on a
> private service.

## Plan

### Phase 0 - Reviewed intent and architecture

- [x] Search existing issues, visions, and efforts; file and claim #2137 without
  duplicating #2014.
- [x] Extend the harness-guidance vision with budget-aware routing and explicit
  adapter/failure semantics.
- [x] Assign ownership: `budget-guidance` owns posture; `delegation-guidance`
  owns eligibility and routing policy; `context-injection` owns composition;
  external accounting owns longitudinal history.
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
- [ ] Add a bounded external-command adapter contract for account surfaces or
  organization-specific readers that cannot live in the public plugin.
- [ ] Make adapter order and field authority configurable without allowing
  repository data to override operator-owned credentials or safety policy.
- [ ] Ensure no adapter failure becomes a zero-consumption success.

### Phase 3 - Plugin, status, and context delivery

- [x] Add the independently installable `budget-guidance` plugin using the
  repository's runtime-plugin and configuration patterns.
- [x] Expose one machine-readable status result for hooks, dashboards, scripts,
  and routing consumers.
- [ ] Contribute a concise, attributable session-start cue through the existing
  context-injection contract and its static fallback pattern.
- [ ] Keep injected output inside a declared byte/token budget and avoid loading
  the detailed adapter/config reference into every session.
- [x] Preserve source, capture time, freshness, projection, and error status in
  both human and machine-readable output.

### Phase 4 - Routing composition

- [ ] Let delegation-guidance consume budget posture when available without a
  hard dependency on budget-guidance.
- [ ] Refine only demonstrated eligible choices; never qualify candidates,
  bypass holds, or weaken trial admission.
- [ ] Preserve explicit operator model/profile choices as authoritative.
- [ ] Return legible budget-based recommendation, caution, escalation, and
  no-fit reasons without changing direct-versus-delegate decisions.
- [ ] Keep standalone budget-guidance useful when delegation-guidance is absent.

### Phase 5 - Validation, publication, and adoption

- [ ] Add focused tests for arithmetic, source precedence, stale/unavailable
  adapters, empty provider reports, reset boundaries, config precedence,
  injection size, and routing composition.
- [ ] Validate Bash and PowerShell paths plus clean-room installation when
  practical.
- [ ] Keep marketplace, manifest, package, and runtime versions aligned.
- [ ] Publish through the repository's normal review and self-merge flow.
- [ ] Deploy through the unified update path and verify source/runtime identity.
- [ ] Record evidence, close #2137, and archive this effort.

## Validation Plan

- [x] The posture contract is provider-neutral and contains no real account,
  allowance, organization, host, or private service.
- [x] Manual/static configuration works with no network or external service.
- [ ] Provider and external adapters retain source, capture time, freshness, and
  explicit availability/error status.
- [ ] Missing, stale, unauthorized, empty, or contradictory readings are never
  represented as zero consumption or full remaining allowance.
- [x] Arithmetic handles partial days, reset boundaries, overspend, and zero
  remaining time deterministically.
- [x] Field-level precedence composes partial sources without allowing a lower
  authority to overwrite a higher-authority field silently.
- [ ] Machine-readable status and injected guidance derive from the same
  resolved posture.
- [ ] Context output stays within its declared budget and carries stable owner
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
4. **Optional routing integration:** delegation-guidance reads posture status and
   adds budget-based reasons after eligibility resolution; either plugin remains
   usable alone.

The plugin stores at most bounded current/cache state needed for freshness and
resilience. It does not become a historical billing ledger, model benchmark,
promotion authority, task queue, or dashboard.

## Journal

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
