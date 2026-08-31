# Session Context Aggregation

- **Slug:** `session-context-aggregation`
- **Repo:** copilot-extensions
- **Branch(es):** one coherent engine-v2 producer-migration campaign, preserving
  standalone fallback throughout
- **Created:** 2026-08-28
- **Status:** Active
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
`context-injection` owns the shared declaration schema, active-stack
discovery, ordering, provenance, budget admission, and compact rendering of
repeated structures such as command glossaries.

The workaround must remain safe during partial rollout. A migrated producer
retains its standalone direct path and additionally publishes a pure context
contributor. Trusted plugin-owned `.context-injection/config.yaml` adoption
selects the exact direct marketplace authority
`context-injection@copilot-extensions` and binds its engine schema and version;
host settings only enable the plugin.
Before exact authority proof, a producer emits its contributor directly. After
proof, producers join the shared `(sessionId, canonical cwd)` rendezvous but
emit `{}`; only the authority emits the cached aggregate. This makes execution
order irrelevant. Authority uncertainty restores existing direct behavior
rather than disabling a producer, while post-proof failures remain one shared
cached `{}` result.

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

The initial marketplace-owned inventory had 16 plugins with `sessionStart`
registrations, 43 individual hooks, and 21 pure context contributors. Four
plugins are context-only, eleven are mixed context plus restart-safe-idempotent
side effects, and `context-injection` is the aggregate authority. Splitting two
agent-worktrees mixed hooks into one pure contributor leaves 42 direct hooks in
the migrated stack. Side-effect hooks remain direct and return `{}`; only pure
contributors run through the authority.

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
> some declarative, proprietary "hook", which specifies an equivalent script to
> use to emit instructions. Then, all relevant plugins which use
> additionalContext change to this plugin's requirements. Worth an investigation
> and effort.

## Plan

### Phase 0 - Architecture and inventory

- [x] Inventory every suite-owned `sessionStart` hook and classify it as context
  contribution, side effect, bootstrap/readiness, or mixed behavior.
- [x] Specify the single-aggregator authority model, contributor schema,
  session rendezvous, deterministic ordering, aggregate byte budget, diagnostics,
  and failure semantics.
- [x] Characterize which hook result the affected host versions retain and
  record the version range. Do not rely on completion timing as a correctness
  mechanism.
- [x] Do not depend on a cross-plugin execution order. Use one source-qualified
  direct marketplace authority whose producer-empty rendezvous protocol makes
  alphabetical, installation, marketplace, and settings-object order
  irrelevant.
- [x] Specify how exactly one `context-injection@copilot-extensions` authority
  is adopted and how review tooling rejects a second claimant, an incompatible
  engine, or any context producer that is not authority-aware.
- [x] Reconcile the design with context-injection, a-la-carte independence,
  marketplace installation cells, and the absence of host-enforced transitive
  plugin dependencies.
- [x] Name the payload-only coordinator `context-injection` and specify the
  authoring rule that only it may directly emit session-start context when it
  is active; producer hooks must use its broker or retain their standalone
  direct fallback.
- [x] Land the reviewed design before creating the aggregator plugin or changing
  any producer's activation behavior.

### Phase 1 - Session-exact plugin resolution

- [x] Extract or extend a pure reference resolver that computes the effective
  plugin set for one session cwd: user settings plus that repository's settings,
  with local overrides and exact source-qualified enablement.
- [x] Resolve staged payloads from raw ACP process ancestry without shell
  evaluation, PATH lookup, marketplace wildcard scans, network fetches, or
  activation of unrelated runtimes.
- [x] Complete installed remote-marketplace and directory-marketplace parity
  across the dependency-light platform implementations.
- [ ] Provide dependency-light PowerShell and POSIX implementations generated
  from one contract and validated against shared fixtures.
- [x] Fail safely on ambiguous marketplace identity, missing payloads, malformed
  settings, path escapes, duplicate identities, and uncertain authority.

### Phase 2 - Aggregator and contributor contract

- [x] Add a payload-only aggregator plugin with exactly one context-emitting
  `sessionStart` hook and no installer-owned runtime.
- [x] Define a versioned payload-relative contributor manifest with stable
  contributor ids, platform commands, ordering class, timeout, byte allowance,
  applicability, and fail-open policy.
- [x] Invoke only contributors declared by exact active payloads; accept only
  `{}` or one string `additionalContext` field; isolate stderr and reject noisy,
  oversized, malformed, or timed-out output.
- [x] Emit one deterministic aggregate with attributable owner boundaries and
  bounded diagnostics; one contributor failure must not suppress valid siblings.
- [x] Expose one payload-local broker command used by the aggregator hook and
  every migrated producer hook. For a given `sessionId`, every successful caller
  receives byte-identical aggregate JSON.
- [x] Build a fresh child environment for each contributor: replace plugin-root
  variables with the contributor's validated payload root, strip the
  aggregator's payload/data identity, and pass only validated cell context.
- [x] Enforce an aggregate wall-clock deadline below the registered host-hook
  timeout, with bounded parallelism or admission based on declared worst-case
  cost.
- [ ] Provide a bounded status/doctor surface that reports selected authority,
  active/inactive reason, admitted contributors, partial-migration competitors,
  budget, deadline, and last session result without exposing context content.
- [ ] Add a shadow/audit mode that resolves and invokes contributors without
  emitting context, so rollout can compare the aggregate with existing direct
  producers.

### Phase 3 - Version-skew-safe broker adoption

- [x] Define repository adoption of the exact source-qualified
  `context-injection@copilot-extensions` authority and bind its engine schema
  and version in trusted `.context-injection/config.yaml`, without allowing
  host settings or another source to replace it.
- [x] Add a generated, dependency-light producer wrapper that resolves only the
  configured aggregator authority and invokes that payload's broker. Do not
  vendor a second contributor-admission implementation into each producer.
- [x] Make the broker publish one deterministic pair-key result only when the
  active set, trust, staged-plugin visibility, contributor consent, declared
  total bytes, and declared wall-clock cost are all authoritative and
  admissible. Before proof, direct the producer wrapper to its original context
  script; after proof, every producer emits `{}`.
- [x] Prove all rollout combinations: no aggregator/old producer,
  aggregator/old producer, no aggregator/new producer, compatible
  aggregator/new producer, incompatible aggregator/new producer, and ambiguous
  aggregators.
- [x] Prove authority-first, producer-first, and concurrent execution all yield
  exactly one non-empty result with identical authority bytes; prove the
  ordinary direct path still operates when authority proof is unavailable.
- [x] Pilot one runtime command-catalog producer and one payload-only policy
  producer before broad conversion.

### Phase 4 - Suite migration

- [x] Convert every suite-owned context producer to the contributor contract
  while leaving non-context `sessionStart` side effects direct.
- [x] Update runtime-agent-plugin and context-injection patterns,
  `customizing-copilot:authoring-skills`, marketplace guards, and
  producer-coverage tests with the single-emitter rule and the required
  standalone fallback.
- [x] Extend `customizing-copilot:reviewing-customizations` to inventory the
  declared session-context role without executing hooks and report a blocking
  finding when a configured stack can emit different non-empty session-start
  context results rather than one byte-identical brokered aggregate.
- [x] Make consumer enablement explicit because current Copilot plugin manifests
  do not enforce transitive dependency installation.
- [x] Replace direct producer commands only after the brokered wrapper and its
  standalone fallback are deployed and clean-room validated for that producer.
- [x] Update `a-la-carte-independence` and
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

- [x] Reproduce the host defect with two direct producers and prove only one
  context result survives.
- [ ] Enable the aggregator with two migrated contributors and prove both arrive
  through one `additionalContext` response on Windows and Linux/WSL.
- [x] Prove old and new producer versions never create a context-loss window
  during aggregator-first, producer-first, interrupted, and rolled-back updates.
- [x] Prove one malformed, hanging, crashing, or oversized contributor does not
  block session startup or suppress healthy contributors.
- [ ] Prove ordering and aggregate bytes are identical across PowerShell and
  POSIX fixtures, including Unicode and newline normalization.
- [ ] Prove compatibility with Windows PowerShell 5.1 and PowerShell 7, and
  define POSIX behavior without requiring `jq` or Python.
- [ ] Prove source-qualified identity and containment with two marketplaces that
  ship the same plugin and contributor ids.
- [x] Prove staged `--plugin-dir`, repository source qualification, disabled
  staged payloads, enabled-but-unstaged payloads, duplicate roots/identities,
  source ambiguity, and pre-proof direct fallback resolve correctly.
- [x] Complete installed remote-marketplace, directory-marketplace, user-global
  enablement, and local-override parity.
- [x] Prove the brokered producer wrapper falls back to direct emission when the aggregator is
  absent, ambiguous, incompatible, missing its payload, or cannot authoritatively
  resolve the host's effective plugin set.
- [x] Prove every broker caller for one `sessionId` returns byte-identical JSON,
  including concurrent callers, leader failure, stale cache, and retry.
- [x] Prove a contributor receives its own validated plugin-root identity rather
  than the aggregator's environment.
- [x] Prove aggregation fails back to direct producers for ACP/staged-plugin
  launches without an authoritative staged inventory and for untrusted
  repository-scoped settings.
- [x] Prove aggregate admission fails back to direct producers when declared
  maximum bytes or execution cost cannot fit the configured budget/deadline.
- [x] Prove bootstrap and side-effect hooks still execute directly and are not
  duplicated by the aggregator.
- [ ] Add a clean-room scenario proving all migrated producers return the same
  aggregate and that a remaining legacy producer is reported as a degraded,
  still-lossy partial-migration state rather than claimed safe.
- [x] Add a synthetic clean-room completeness witness that installs the
  unpublished direct authority, two authority-aware canary producers, and a
  restart-safe-idempotent/context-none side-effect hook through a supported
  local marketplace; prove authority-first, producer-first, concurrent,
  two-session, and two-CWD broker permutations before Tier E.
- [x] Establish model-visible completeness through fresh agent-bridge ACP
  sessions: two variant-A sessions and one variant-B session each returned both
  expected canaries once, used no tools, and left one idempotent side-effect
  marker under independent clean-room judgment.
- [x] Run the customization coherence scan and the repository's plugin,
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
  broker keyed by `(sessionId, canonical cwd)`: every migrated producer joins
  the same rendezvous but emits `{}` after proof, while an unavailable or
  non-authoritative broker sends the producer through its standalone direct
  path.
- Added hard design gates for ACP and staged-plugin visibility, folder trust,
  child payload environment reconstruction, cross-cell contributor consent,
  aggregate admission, wall-clock deadlines, and the still-lossy nature of
  partial migration.
- Confirmed the official hooks reference orders hook source classes with plugin
  hooks last, but does not define ordering among plugins or a plugin hook
  priority field. The current plugin manifest also has no host-enforced
  dependency field; dependency support remains an upstream feature request.
- Rejected hook order as an ownership mechanism. The direct
  `context-injection@copilot-extensions` authority discovers and runs every
  pure contributor itself. Proven producers join its pair-key rendezvous and
  emit `{}`, so authority-first, producer-first, and concurrent execution have
  the same single non-empty result.
- Live Copilot CLI 1.0.82-1 probes with synthetic `--plugin-dir` and trusted
  repository-settings payloads confirmed that result selection depends on
  implementation-specific ordering. Argument order, plugin names,
  marketplaces, and `enabledPlugins` insertion order are not supported
  ownership contracts.
- Verified the fail-open seam on the same host: an empty context result did not
  erase a preceding non-empty result. The direct-authority protocol records
  this output-composition behavior as a host-version compatibility precondition
  rather than inferring ownership from execution order.
- Confirmed repository folder trust is exact-path rather than inherited from a
  trusted parent worktree: an ignored nested test repository's settings were
  not loaded. The aggregator must independently reject untrusted repository
  settings rather than assuming parent trust.
- Began the rollout scaffold as `context-injection`: exact source-qualified
  direct authority, compatible-engine proof, complete-declaration gate, pure
  contributor execution, pair-key rendezvous, intentional byte/time admission,
  and conservative direct fallback. The scaffold remains inert on existing
  stacks until producer declarations land.
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

### 2026-08-31 - Agent-bridge completeness witness

- Added the organization-neutral `context-injection-eval` clean-room scenario.
  A disposable local marketplace carries the unpublished authority, two
  high-entropy synthetic canary producers per CWD, and one complete-declared
  `restart-safe-idempotent` / `context: none` side-effect-only hook.
- Kept ACP loading on the supported installed local-marketplace path. The
  scenario uses no `--plugin-dir` staging exception; a separate
  `payload_fingerprint_dirs` field lets the runner hash live local-marketplace
  payloads without changing the launch path.
- Tier P passed authority-first, producer-first, concurrent, two-session, and
  two-CWD permutations. Counts-only evidence records two expected contributors,
  both token hashes once per authority output, distinct session/CWD identity
  hashes, and one PASS verdict.
- Authorization succeeded for three fresh Copilot CLI 1.0.82 sessions driven
  through agent-bridge: two in variant A and one in variant B. Every literal
  response was the empty-token form, no transcript used tools or read files,
  and the side-effect hook produced zero markers.
- The clean-room judge independently scored both A and B packets
  `FAIL / scenario-transport-gap`. The first break is installed local-plugin
  loading or `sessionStart` delivery through the stock ACP transport, not
  authentication and not the deterministic broker. No model-visible
  completeness claim is made.
- Preserved the counts-only Tier-P evidence, Tier-E evidence, transcripts,
  post-check reports, and judge verdicts in the machine-local clean-room results
  directory outside the repository. No raw ambient context was copied into the
  structured evidence.

### 2026-08-31 - Staged activation correction and successful rerun

- Superseded the earlier `FAIL / scenario-transport-gap` interpretation. The
  scenario omitted the explicit `--plugin-dir` activation arguments required by
  ACP and copilot-extensions plugin hooks, so that artifact is an invalid
  scenario false negative, not a product or agent-bridge transport verdict. Its
  machine-local evidence remains preserved only as historical evidence.
- Replaced the engine's staged-launch stand-down with an authoritative raw-argv
  inventory resolver. It canonicalizes and deduplicates contained plugin roots,
  source-qualifies manifests against repository enablement, ignores
  enabled-but-unstaged payloads, requires the staged authority, and restores
  producer-local direct output before proof on every ambiguity or path failure.
- Corrected the eval fixture after proving that every explicit `--plugin-dir`
  payload is host-loaded even when its repository identity is false. The final
  manifest stages only four stable roots: authority, variant-selected alpha,
  variant-selected beta, and the side-effect-only plugin.
- Tier P passed staged authority-first, producer-first, concurrent, two-session,
  and two-CWD permutations. Focused engine, scanner, declaration, scenario, and
  runner contract gates also passed.
- Independent clean-room judges passed both final packets. Across two fresh
  variant-A sessions and one fresh variant-B session, each transcript contained
  both expected canaries exactly once in the strict response, used no tools,
  invented no canaries, and produced exactly one well-formed idempotent
  side-effect marker per session.
- Preserved final counts-only evidence and both judge verdicts in machine-local
  clean-room result directories outside the repository. Tracked files contain
  no raw canaries.

### 2026-08-31 - Complete marketplace-owned producer migration

- Inventoried the complete marketplace-owned stack: initially 16 plugins, 43
  `sessionStart` hooks, and 21 pure contributors; the migrated stack has 42
  direct hooks after splitting agent-worktrees' mixed behavior. Classified four
  plugins as context-only, eleven as mixed context plus
  restart-safe-idempotent side effects, and `context-injection` as the
  aggregate authority.
- Added byte-identical Bash and PowerShell engine-v2 producer wrappers to all
  15 contributing plugins. Each wrapper preserves payload-relative identity and
  standalone direct fallback, then emits `{}` after exact authority proof and
  pair-key rendezvous.
- Split agent-worktrees' context from its direct registration, nudge, and
  marketplace-reconciliation mutations. Those hooks now expose explicit
  context-free modes; the aggregator invokes only the pure read-only
  contributor commands.
- Added a marketplace-derived synchronization tool and complete-stack tests
  that reject undeclared hooks, legacy direct context emitters, wrapper drift,
  insufficient host timeouts, contributor identity mismatches, and side-effect
  re-entry.
- Updated the context-injection, runtime-plugin, a-la-carte, installation-cell,
  authoring, review, and architecture guidance to make the single-emitter and
  standalone-fallback contracts explicit.
- Corrected adoption ownership before publication: host settings now only
  enable the plugin, while exact authority and engine selection live in trusted
  `.context-injection/config.yaml`. Runtime, scanner, clean-room fixtures, and
  tests reject the retired settings key plus malformed, unknown, escaping, or
  incompatible v1 configuration.
