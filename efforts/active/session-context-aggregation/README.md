# Session Context Aggregation

- **Slug:** `session-context-aggregation`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase PRs; the architecture and migration
  contract land before any producer disables its direct context hook
- **Created:** 2026-08-28
- **Status:** Draft
- **Vision:** closes
  [`visions/plugin-services/installation-cells`](../../../visions/plugin-services/installation-cells/README.md)
  §Features/`attributable-agent-capabilities` and
  §Behaviors/`same-cell-composition`, `provenance-carried-end-to-end`, and
  `ownership-is-legible`; uses the explicit-interoperability boundary under
  §Non-Goals/`no-cross-marketplace-federation`
- **Umbrella issue:** [#1325](https://github.com/ThomasMichon/copilot-extensions/issues/1325)
- **Related issues:** [#1234](https://github.com/ThomasMichon/copilot-extensions/issues/1234) ·
  [#1103](https://github.com/ThomasMichon/copilot-extensions/issues/1103) ·
  [#1096](https://github.com/ThomasMichon/copilot-extensions/issues/1096)

## Guiding Intent

Deliver every suite-migrated plugin contributor's attributable session context
as one deterministic, bounded aggregate, without depending on the host to
preserve several different `additionalContext` results.

The coordinator is also the suite's durable composition layer after the host
bug is fixed: plugins own their rules and command definitions, while
`zz-context-injection` owns the shared declaration schema, active-stack
discovery, ordering, provenance, budget admission, and compact rendering of
repeated structures such as command glossaries.

The workaround must remain safe during partial rollout. A migrated producer
retains its standalone direct path and additionally publishes a pure context
contributor. If the host provides a supported, testable way to guarantee one
configured aggregator runs after every competing context hook, that final hook
re-runs the active contributors and its complete aggregate wins the host's
last-result behavior. If no such ordering guarantee exists, the rollout falls
back to the session broker design in which every migrated hook returns the same
cached aggregate bytes. In either mode, uncertainty restores existing direct
behavior rather than disabling a producer.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Architecture driver | Resolver, contributor contract, rollout protocol, and reviewed sequencing | Isolated planning and implementation worktrees |
| Windows validation lane | Native PowerShell resolution, invocation, timeout, encoding, and clean-room behavior | Windows worktree and disposable clean-room runs |
| POSIX validation lane | Dependency-light shell resolution, invocation, timeout, and parity | Linux/WSL worktree and disposable clean-room runs |

## Coordination

- **Topology:** independent per-phase PRs sequenced by this effort.
- **Host (owns PRs):** architecture driver.
- **Delegates:** Windows and POSIX lanes may own validation or parity slices
  after recording them in the Journal.
- **Handoff:** each PR must be independently safe under old aggregator/new
  producer and new aggregator/old producer version skew. No producer removes or
  replaces its direct context path before the brokered fallback is proven.

## Context

Copilot CLI currently executes every configured `sessionStart` hook but
preserves only one non-empty `additionalContext` result. The surviving producer
varies with completion timing. This makes independently correct command
catalogs, policy kernels, contribution boundaries, and session guidance
disappear nondeterministically.

The suite currently has 14 plugins with `sessionStart` registrations and 42
individual hooks. Not all are context producers: bootstrap, registration, and
other side-effect hooks may remain direct and return `{}`. This effort concerns
only hooks that emit agent context.

Reusable foundations already exist:

- `agent-plugin-activation` resolves attributable active plugin payloads across
  user-global and registered-project scopes with tri-state authority.
- `plugin-resolve` parses repository plugin settings and local marketplace
  conventions.
- the marketplace-installation-cell pattern defines payload ownership,
  same-cell composition, explicit cross-cell interoperability, and
  payload-local invocation.
- current producer scripts already emit the constrained `{}` or
  `{"additionalContext":"..."}` shape.

The existing activation resolver answers a machine-wide reconciliation
question, not yet the exact session question. The aggregator needs a
dependency-light resolver for the effective user plus current-repository plugin
stack, including installed remote-marketplace payloads, directory marketplaces,
and staged plugin roots supplied by the host.

The hook input includes `sessionId`, which permits all migrated hooks to address
one session-local aggregation result. The design must not duplicate the
aggregator-selection or contributor-admission decision in every producer. The
selected aggregator's broker makes that decision and returns either the shared
aggregate or an explicit fallback disposition.

The detailed investigation and proposed architecture are in
[`design.md`](design.md).

## Request

> So, a proposed workaround is that we define some extra plugin in
> copilot-extensions whose only purpose is to have a single sessionStart hook.
> That hook will look to the current repo, resolve the complete stack of plugins
> active for the CWD (same resolver flow as agent-bridge, which we will need to
> vendor or make ps1/sh versions of), and then enumerate every plugin looking for
> some declarative, poprietary "hook", which specifies an equivalent script to
> use to emit instructions. Then, all relevant plugins which use
> additionalContext change to this plugin's requirements. Worth an investigation
> and effort.

## Plan

### Phase 0 - Architecture and inventory

- [ ] Inventory every suite-owned `sessionStart` hook and classify it as context
  contribution, side effect, bootstrap/readiness, or mixed behavior.
- [ ] Specify the single-aggregator authority model, contributor schema,
  session rendezvous, deterministic ordering, aggregate byte budget, diagnostics,
  and failure semantics.
- [ ] Characterize which hook result the affected host versions retain and
  record the version range. Do not rely on completion timing as a correctness
  mechanism.
- [ ] Determine whether affected host versions expose a supported,
  cross-platform, source-qualified way to guarantee one plugin hook executes
  after all other plugin hooks. Distinguish a documented contract from
  incidental alphabetical, installation, marketplace, or settings-object order.
- [ ] If a guaranteed-last seam exists, specify how exactly one
  `context-injection` authority claims it and how review tooling rejects a
  second claimant. Otherwise retain the byte-identical session broker as the
  required compatibility architecture.
- [ ] Reconcile the design with context-injection, a-la-carte independence,
  marketplace installation cells, and the absence of host-enforced transitive
  plugin dependencies.
- [ ] Name the payload-only coordinator `context-injection` and specify the
  authoring rule that only it may directly emit session-start context when it
  is active; producer hooks must use its broker or retain their standalone
  direct fallback.
- [ ] Land the reviewed design before creating the aggregator plugin or changing
  any producer's activation behavior.

### Phase 1 - Session-exact plugin resolution

- [ ] Extract or extend a pure reference resolver that computes the effective
  plugin set for one session cwd: user settings plus that repository's settings,
  with local overrides and exact source-qualified enablement.
- [ ] Resolve installed remote-marketplace payloads, directory marketplaces,
  and staged payloads without PATH lookup, marketplace wildcard scans, network
  fetches, or activation of unrelated runtimes.
- [ ] Provide dependency-light PowerShell and POSIX implementations generated
  from one contract and validated against shared fixtures.
- [ ] Fail safely on ambiguous marketplace identity, missing payloads, malformed
  settings, path escapes, duplicate identities, and uncertain authority.

### Phase 2 - Aggregator and contributor contract

- [ ] Add a payload-only aggregator plugin with exactly one context-emitting
  `sessionStart` hook and no installer-owned runtime.
- [ ] Define a versioned payload-relative contributor manifest with stable
  contributor ids, platform commands, ordering class, timeout, byte allowance,
  applicability, and fail-open policy.
- [ ] Invoke only contributors declared by exact active payloads; accept only
  `{}` or one string `additionalContext` field; isolate stderr and reject noisy,
  oversized, malformed, or timed-out output.
- [ ] Emit one deterministic aggregate with attributable owner boundaries and
  bounded diagnostics; one contributor failure must not suppress valid siblings.
- [ ] Expose one payload-local broker command used by the aggregator hook and
  every migrated producer hook. For a given `sessionId`, every successful caller
  receives byte-identical aggregate JSON.
- [ ] Build a fresh child environment for each contributor: replace plugin-root
  variables with the contributor's validated payload root, strip the
  aggregator's payload/data identity, and pass only validated cell context.
- [ ] Enforce an aggregate wall-clock deadline below the registered host-hook
  timeout, with bounded parallelism or admission based on declared worst-case
  cost.
- [ ] Provide a bounded status/doctor surface that reports selected authority,
  active/inactive reason, admitted contributors, partial-migration competitors,
  budget, deadline, and last session result without exposing context content.
- [ ] Add a shadow/audit mode that resolves and invokes contributors without
  emitting context, so rollout can compare the aggregate with existing direct
  producers.

### Phase 3 - Version-skew-safe broker adoption

- [ ] Define an explicit aggregator authority selection that permits at most one
  source-qualified aggregator for a session, is selected only by user-local
  policy, and treats multiple candidates as ambiguous.
- [ ] Add a generated, dependency-light producer wrapper that resolves only the
  configured aggregator authority and invokes that payload's broker. Do not
  vendor a second contributor-admission implementation into each producer.
- [ ] Make the broker return the byte-identical session aggregate only when the
  active set, trust, staged-plugin visibility, contributor consent, declared
  total bytes, and declared wall-clock cost are all authoritative and admissible.
  Otherwise it directs the producer wrapper to run its original context script.
- [ ] Prove all rollout combinations: no aggregator/old producer,
  aggregator/old producer, no aggregator/new producer, compatible
  aggregator/new producer, incompatible aggregator/new producer, and ambiguous
  aggregators.
- [ ] For a guaranteed-last implementation, prove migrated producers keep their
  ordinary direct emissions and that the final aggregator deterministically
  supersedes them with the complete aggregate; prove the ordinary direct path
  still operates when the aggregator is absent or disabled.
- [ ] Pilot one runtime command-catalog producer and one payload-only policy
  producer before broad conversion.

### Phase 4 - Suite migration

- [ ] Convert every suite-owned context producer to the contributor contract
  while leaving non-context `sessionStart` side effects direct.
- [ ] Update runtime-agent-plugin and context-injection patterns,
  `customizing-copilot:authoring-skills`, marketplace guards, and
  producer-coverage tests with the single-emitter rule and the required
  standalone fallback.
- [ ] Extend `customizing-copilot:reviewing-customizations` to inventory the
  declared session-context role without executing hooks and report a blocking
  finding when a configured stack can emit different non-empty session-start
  context results rather than one byte-identical brokered aggregate.
- [ ] Make consumer enablement explicit because current Copilot plugin manifests
  do not enforce transitive dependency installation.
- [ ] Replace direct producer commands only after the brokered wrapper and its
  standalone fallback are deployed and clean-room validated for that producer.
- [ ] Update `a-la-carte-independence` and
  `marketplace-installation-cells` for the optional coordinator, cross-cell
  contributor consent, and child-environment reconstruction rules.

### Phase 5 - Repository hooks and host convergence

- [ ] Decide whether repository-owned dynamic context can opt into a trusted,
  declarative repository contribution seam or remains an explicit limitation
  handled by static instructions and the upstream runtime fix.
- [ ] Verify behavior after the host restores native multi-hook aggregation:
  either retain the aggregator as deterministic composition/budget enforcement
  or provide an ownership-checked retirement path.
- [ ] Document diagnostics, rollback, partial-upgrade recovery, and how to
  identify the contributor that consumed or lost context budget.

### Phase 6 - Reconcile deferred backlog

- [ ] Accept context-aggregation candidates only through
  [`migration-intake`](../migration-intake/README.md)'s deduplication and
  ownership gate.
- [ ] Revalidate accepted technical scope against the current contributor,
  budget, and compatibility contracts; return obsolete or unsafe candidates
  for explicit disposition.
- [ ] Place each accepted public tracker item in exactly one existing phase,
  extending this plan before implementation when necessary.
- [ ] Keep fixtures synthetic and independent of installed consumer sets.

## Validation Plan

- [ ] Reproduce the host defect with two direct producers and prove only one
  context result survives.
- [ ] Enable the aggregator with two migrated contributors and prove both arrive
  through one `additionalContext` response on Windows and Linux/WSL.
- [ ] Prove old and new producer versions never create a context-loss window
  during aggregator-first, producer-first, interrupted, and rolled-back updates.
- [ ] Prove one malformed, hanging, crashing, or oversized contributor does not
  block session startup or suppress healthy contributors.
- [ ] Prove ordering and aggregate bytes are identical across PowerShell and
  POSIX fixtures, including Unicode and newline normalization.
- [ ] Prove compatibility with Windows PowerShell 5.1 and PowerShell 7, and
  define POSIX behavior without requiring `jq` or Python.
- [ ] Prove source-qualified identity and containment with two marketplaces that
  ship the same plugin and contributor ids.
- [ ] Prove staged `--plugin-dir`, installed remote marketplaces, directory
  marketplaces, user-global enablement, repository enablement, local overrides,
  and disabled plugins resolve correctly.
- [ ] Prove the brokered producer wrapper falls back to direct emission when the aggregator is
  absent, ambiguous, incompatible, missing its payload, or cannot authoritatively
  resolve the host's effective plugin set.
- [ ] Prove every broker caller for one `sessionId` returns byte-identical JSON,
  including concurrent callers, leader failure, stale cache, and retry.
- [ ] Prove a contributor receives its own validated plugin-root identity rather
  than the aggregator's environment.
- [ ] Prove aggregation fails back to direct producers for ACP/staged-plugin
  launches without an authoritative staged inventory and for untrusted
  repository-scoped settings.
- [ ] Prove aggregate admission fails back to direct producers when declared
  maximum bytes or execution cost cannot fit the configured budget/deadline.
- [ ] Prove bootstrap and side-effect hooks still execute directly and are not
  duplicated by the aggregator.
- [ ] Add a clean-room scenario proving all migrated producers return the same
  aggregate and that a remaining legacy producer is reported as a degraded,
  still-lossy partial-migration state rather than claimed safe.
- [ ] Run the customization coherence scan and the repository's plugin,
  marketplace-isolation, generated-file, and documentation consistency guards.

## Proposal

See [`design.md`](design.md).

## Journal

### 2026-08-28 - Kickoff

- Filed [#1325](https://github.com/ThomasMichon/copilot-extensions/issues/1325)
  after finding no existing issue for a suite-owned aggregator.
- Confirmed that #1234 is still reproducible on Copilot CLI 1.0.82-1 and can
  block a sidecar skill even when its payload and runtime are healthy.
- Found that `agent-plugin-activation` and `plugin-resolve` provide reusable
  ownership and settings foundations, but the aggregator still needs an exact
  current-session resolver and dependency-light PowerShell/POSIX parity.
- Identified the version-skew-safe deferral handshake as the mechanism that
  initially appeared to avoid the synchronized flag day rejected during the
  effort-driven-session loops work.
- Kept repository-local hook composition as an explicit investigation boundary:
  a plugin-only aggregator removes suite-internal races but cannot by itself
  prevent an unrelated repository hook from competing with the aggregate.
- Plan review found that duplicated deferral decisions could still create a
  stand-down-then-drop window. Revised the direction to one aggregator-owned
  broker keyed by hook `sessionId`: every migrated hook receives the same
  aggregate bytes, while an unavailable or non-authoritative broker sends the
  producer through its standalone direct path.
- Added hard design gates for ACP and staged-plugin visibility, folder trust,
  child payload environment reconstruction, cross-cell contributor consent,
  aggregate admission, wall-clock deadlines, and the still-lossy nature of
  partial migration.
- Confirmed the official hooks reference orders hook source classes with plugin
  hooks last, but does not define ordering among plugins or a plugin hook
  priority field. The current plugin manifest also has no host-enforced
  dependency field; dependency support remains an upstream feature request.
- Revised the proposal to prefer a simpler guaranteed-last mode if an
  executable cross-platform/version matrix proves a supported ordering seam:
  producers keep their direct emissions as backup, while the final
  `context-injection` hook re-runs pure contributors and supersedes them with
  the aggregate. The byte-identical broker remains the compatibility design
  when no such guarantee can be established.
- Live Copilot CLI 1.0.82-1 probes with three synthetic `--plugin-dir` payloads
  showed hook execution follows argument order and the final non-empty result
  wins. Three trusted repository-settings probes with different
  `enabledPlugins` insertion orders all executed by plugin name
  (`a-first`, `m-middle`, `z-last`), including across two marketplaces; the
  lexically final plugin won.
- Verified the fail-open seam on the same host: when the final plugin emitted
  `{}`, the preceding non-empty context remained model-visible. A late
  aggregator can therefore stand down without erasing direct producer output.
- Confirmed repository folder trust is exact-path rather than inherited from a
  trusted parent worktree: an ignored nested test repository's settings were
  not loaded. The aggregator must independently reject untrusted repository
  settings rather than assuming parent trust.
- Began the rollout scaffold as `zz-context-injection`: exact
  source-qualified authority, lexical-final verification, complete-declaration
  gate, pure contributor execution, intentional byte/time admission, and conservative
  stand-down. The scaffold remains inert on existing stacks until producer
  declarations land.
- Corrected the initial budget premise: the official 10 KB join cap is stated
  for `postToolUse`, not `sessionStart`. Copilot CLI 1.0.82-1 delivered a
  synthetic 20 KB startup context including an end marker. The coordinator now
  uses an intentional 64 KB product budget beneath the host's 10 MiB hook-output
  guard; compaction remains valuable for context efficiency, not host
  correctness.
- Confirmed the coordinator should remain useful beyond the compatibility
  workaround as the suite-wide efficient composition system for plugin-owned
  rules and command glossaries. Producers remain authoritative for content;
  the coordinator deduplicates shared framing and renders one attributable,
  budgeted aggregate.
