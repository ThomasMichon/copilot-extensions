# Trusted Linux CI Offload

- **Slug:** `trusted-linux-ci-offload`
- **Repo:** copilot-extensions
- **Branch(es):** independent managed plan, implementation and control-activation PRs
- **Created:** 2026-10-16
- **Status:** Active; pure existing-row backend selector/tests implemented; CI integration after #7164 and qualification/activation pending
- **Vision:** `visions/test-portfolio/README.md`: bounded feedback loops,
  shared-host admission, unique value or consolidation, reductions preserve assurance
- **Umbrella issue:** #7122
- **Related campaigns:** #6237; #3336; #1303

## Guiding Intent

Enable practical, explicitly bounded self-hosted Linux validation to relieve
hosted Actions allocation pressure. Replace equivalent hosted Linux execution
for eligible same-repository collaborator PRs rather than adding a second
bridge suite. Preserve the actual test portfolio, native Windows/macOS lanes,
external-contribution coverage and required aggregate checks. Hosted execution
remains the default without an active, verified opt-in.

This is vision-closing: execution placement and duplicate-work removal advance
bounded feedback and unique-value coverage without reducing assurance. It does
not expand the required portfolio or move privileged release work onto private
runners. It follows the pattern principles of right-sized surfaces,
cross-platform parity and explicit failures.

### Implemented slice

The current implementation is the pure
[`select_backend(row, context)`](../../../tools/ci_plugin_partitions.py)
extension in the existing row helper, with dedicated cases in the
[existing test module](../../../tools/test_ci_plugin_partitions.py).
It returns a backend kind and explicit reason before execution, without
changing row identity, inventory, partitions, CLI output or test commands.
It does not allocate workers, wire CI, execute recipes or establish readiness.
There is no second selector registry.

CI integration remains with the existing owner after #7164. Full routing,
executor delivery, qualification, activation and real-job acceptance are open.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| drowsy-ledger | Public plan, routing implementation, tests and PR stewardship | #7122 and managed codename attribution |
| Integration owner | Sequence plan acceptance and authorize implementation | #7122 coordination |
| Repository owner | Authorize default-branch workflow control and activation | Owner-reviewed control PR |
| Capacity operator | Own runner admission, isolation, slots and opt-in | Separately authorized runner administration |
| Automated reviewer | Review plan and implementation independently | Repository Copilot review flow |

## Coordination

- **Topology:** plan first; reviewed implementation targets `dev`; default-branch
  control activation is a separate, explicitly authorized owner operation.
- **Host (owns PRs):** drowsy-ledger owns public plan and implementation PRs.
- **Delegates:** none. Runner provisioning and host changes are not this
  participant's implementation scope.
- **Handoff:** report concrete changes, gates and blockers to the integration
  owner; retain the same public effort and issue.
- **Related ownership:** #6237 retains workflow-control policy, #3336 retains
  release ownership and #1303 retains portfolio rationalization. This effort
  changes neither those owners nor their independent completion gates.
- **File seam:** leave `.github/workflows/ci.yml` untouched while #6237's owner
  carries its dev-only job-admission/containment work. Resolve overlap explicitly
  before any CI edit. The dedicated review setup, narrow route guard and review
  preservation remain #7122's subject; coordinate shared helper files before
  implementation.
- **Stage gate:** the integration owner authorized publication of the
  planning-only proposal. Merge the reviewed plan before implementation, and
  obtain a further implementation authorization. Proposal publication does not
  authorize the implementing change or any deployment.
- **Activation gate:** neither plan approval nor a `dev` merge authorizes runner
  changes, repository variables, workflow pins, release resume or direct-main
  edits. Preserve the accepted collaborator boundary and separately qualify
  capacity before enabling routing.
- **Current slice:** the existing helper's pure PR-backend decision and its
  dedicated tests only. The CI owner retains integration after #7164, including
  coupled workflow, worker-contract and release-admission changes. No executor,
  runner allocation, selector activation or CI wiring is implemented here.

## Context

The current `.github/workflows/trusted-ci.yml` is a separate path-filtered
`pull_request_target` lane. A hosted authorization job checks both the PR author
and event sender for current write/maintain/admin permission and same-repository
origin. The only self-hosted job uses `copilot-extensions-ci`, checks out the
immutable PR head without checkout credentials and runs the bridge suite.
`TRUSTED_SELF_HOSTED_CI == 'enabled'` is its activation latch.

The ordinary `ci.yml` still runs its hosted bridge suite, including the separate
shared SSH proxy contracts. Thus enabling the existing trusted lane adds work;
it does not offload the equivalent hosted work. Other Linux contracts include
standalone consumers, runner admission, Fleet partitions and Worktrees
collection/guards. Their selectors and timeout exclusions are meaningful
coverage, not disposable matrix overhead.

The normal-CI trust guard permits the legacy bridge route; the dedicated review
setup has its own separate contract. Other normal-CI custom/dynamic/reusable
routes remain denied. The guard and existing PR trigger must not be disabled
to enable a new route. This pure selector change does not alter those guards.

The existing `PR gate (required check)` depends on all required CI jobs but
accepts `skipped` indiscriminately. Routing introduces a new distinction:
skipping a displaced hosted job is legitimate only if its selected replacement
actually succeeds. The promotion snapshot aggregate has different rules and
must retain its existing hosted qualification graph.

Read `docs/pipelines.md`, `TESTING.md`, and the
[main-owned workflow control design](../main-owned-workflow-control/design.md)
before changing ownership or required contexts. Main is the GitHub default;
ordinary contributions target dev. The release controller is paused at planning
intake. A dev merge cannot activate a default-branch trusted controller.

## Request

Public-safe operator intent, with execution-venue details omitted:

> enable practical bounded local self-hosted Linux capacity to relieve hosted
> queue, preserve native Windows/macOS and external PR coverage.

> Design a SAFE cohesive routing change that REPLACES equivalent hosted Linux
> work for eligible trusted same-repo collaborator PRs (both author AND sender
> current write/maintain/admin), not just duplicates existing bridge lane. Fail
> closed on unknown auth or inactive opt-in; preserve required aggregate checks
> and actual test completeness. Bound work per host, avoid excessive matrix
> fanout. Default hosted behavior unchanged. No spoofing ubuntu-latest labels or
> granting forks private runner access.

> Current trusted CI only permits secret-free immutable PR-head bridge test,
> and check-trusted-ci.py forbids any other custom/dynamic routes; evolve guard
> tests intentionally.

Additional operator constraint, summarized without deployment inventory:
Linux capacity may use different distributions and isolation methods. Route by
an approved custom label and demonstrated capability, not an assumed Ubuntu
baseline or a copied service definition. An online runner does not establish
dependency, action or containment compatibility.

The operator subsequently requested uv-managed Python 3.12 for the trusted
Linux lane rather than depending on setup-python's hosted-distribution
archives. Existing immutable-workflow qualification remains separate from
qualification of that proposed portable toolchain.

Trust-model clarification:

> Existing reviewed trust model explicitly treats current write/maintain/admin
> collaborators as DURABLE execution principals, with strict all-external
> approval policy + protected workflow governance and a documented prohibition
> on manually approving an external workflow targeting private hardware.

The host gate's lack of cryptographic job binding is an explicitly accepted
existing limitation. Preserve that boundary; additional caller/callee
attestation is not a prerequisite unless a concrete new routing-induced
violation is identified.

Subsequent operator direction, summarized for public planning: prioritize the
official GitHub Copilot Code Review runner integration, not a CLI reviewer or
unofficial replacement. Use ARC-managed Ubuntu x64 ephemeral capacity with a
single review slot and operator-enforced deny-LAN, allowlisted egress isolation.
Fork/external review workloads are accepted only inside that isolated review
envelope; this does not widen the persistent validator lane's collaborator
admission. Deployment remains operator-owned. See
[the proposed ARC review slice](arc-code-review.md).

The remaining design below is **agent-recommended**, not an authorization to
change host policy or bypass the release pause.

## Plan

### Phase 1 - Reviewed routing and admission contract

- [x] Draft the canonical public effort and index; read the draft back against
  operator intent and identify the proposed graph, changed files and blockers.
- [x] Review this draft against #7122 and the operator constraints; obtain
  authorization to publish the plan.
- [x] Confirm routing preserves the existing durable-collaborator trust model,
  protected workflow governance and all-external approval policy. Identify any
  concrete new boundary widening before making additional hardening mandatory.
- [x] Land a plan-only PR and record its actual merge: #7125 merged as
  `d4925bc2394d5b1c1c5bc1176cc7f4334b53f367` under the operator's exact-head
  bootstrap exception. Normal full review/CI did not pass; original protection
  settings were restored and verified immediately after merge.
- [x] Obtain separate implementation authorization and identify source/destination
  admission ownership. Subsequent operator direction pauses broad validator
  implementation while the official Code Review priority is planned.

### Phase 1a - Official ARC Code Review proposal

- [x] Assess existing review setup, authorization and default-branch control;
  capture the official supported ARC-only integration and exact guard migration
  in [arc-code-review.md](arc-code-review.md).
- [x] Obtain the integration owner's implementing-source publication clearance;
  keep initial setup hosted-safe and require a separate custom-selector READY
  decision.
- [x] Coordinate the disjoint guard/test edits with #6237's existing owner.
  Preserve its held publication-boundary tests and notify exact additive
  post-CI test hunks before any such edits; use dedicated fixtures here.
- [x] Record the separate Main admission and manifest/setup epochs in the
  public merge history; no combined policy/data epoch is represented.
- [x] Main admission-path change: #7153 merged as
  `bc328d8d425155616bc91a35c616692dc4c81342`.
- [x] Main manifest/hosted-setup change: #7154 merged as
  `c1c0fea7ba3beea33b4010be009489696e51e4be`, parent
  `bc328d8d425155616bc91a35c616692dc4c81342`. That commit contains the
  four-path manifest and a literal `ubuntu-latest` setup definition.
- [x] Land the ARC proposal: #7133 merged as
  `58dd5c890415a65a656acbd8bf4b4931bda97a11` through the operator-authorized
  exact-head planning exception, with both original protections restored and
  audited. This is not a claim that full agentic review or required CI passed.
- [x] Land hosted-safe setup staging through dev: #7135 merged as
  `2db8a9c072fb191de2b6adef053ecfa8bc50c11a`. The five-minute limit is
  step-only; no job timeout or custom runner selection is configured.
- [x] Land the disjoint dedicated-review guard and fixtures through dev:
  #7151 merged as `0f0f039ca40bf9db4b3ba4ae832e08e46c85113c` after
  current-head approval and all required Dev checks passed; qualified
  default-branch delivery is a separate remaining gate.
- [ ] Implement and qualify only the approved official ARC slice after its
  gates clear; keep broad validators paused until separately resumed.

### Phase 2 - Inert public implementation

The implemented boundary is pure per-row selection in the existing helper and
dedicated tests. It changes no inventory, default CLI output, job identity or
test command. It is not a worker-allocation or readiness receipt.

- [x] Define the existing-row selector boundary, with CI integration retained
  by the existing owner after #7164.
- [x] Implement and test `select_backend(row, context)` in
  `tools/ci_plugin_partitions.py`, with dedicated cases in
  `tools/test_ci_plugin_partitions.py`; preserve all original functions/tests,
  partition inventory and CLI behavior.
- [ ] Integrate ordinary PR smoke/heavy routing after #7164 through the existing
  CI owner, preserving current row/job identities and execution semantics.
- [ ] Implement the owner-coordinated routing and bounded execution for the
  selected existing recipes; keep live routing unchanged until its gates clear.
- [ ] Add behavioral authorization, graph, partition and mutation regressions;
  intentionally replace obsolete exact-shape guard expectations.
- [ ] Update directly affected pipeline/testing docs and preserve all native,
  external, push, manual and promotion behavior.
- [ ] Publish through the managed dev PR flow and steward to an actual merge
  after current-head review and required checks. Journal merged/pending release,
  not live.

### Phase 3 - Separately authorized control activation

- [ ] Identify a permitted owner-reviewed control-only path to main while
  promotion is paused, or wait for an authorized normal release. No helper,
  test or documentation source is smuggled into a workflow-only exception.
- [ ] Verify that the exact controller and helper versions are present together
  on the default branch; record the trusted workflow Git blob SHA and immutable
  reusable-workflow commit pin.
- [ ] Capacity operator proves admission, containment and one slot per host;
  repository owner separately authorizes pins and the opt-in latch.
- [ ] Prove the canaries and rollback conditions below before ordinary use.
  Re-read this effort after each merge; resolve or transfer every validation
  item before declaring Done.

## Validation Plan

- [ ] Authorization: executable fixtures cover same-repository positive
  write/maintain/admin cases for both identities; fork, read/triage, one denied
  identity, missing/deleted identity, 404, API errors and malformed responses.
  Unknown authority never selects a private runner.
- [ ] Freshness: changed PR head/base, retarget, stale event, rerun identity,
  actor/sender distinction and revoked permission cannot reuse an old trusted
  decision in a newly authorized invocation. Document the existing point-in-time
  authorization and operator revocation procedure; do not claim continuous
  permission revalidation for already-admitted jobs.
- [ ] Trust boundary: the reviewed callee refuses fork/untrusted events,
  caller-supplied trust booleans and unsupported invocation shapes before its
  self-hosted job. Workflow mutations that weaken authorization, change the
  custom route or substitute the callee/ref fail the structural/governance
  guards. Those guards are not credited as cryptographic job admission.
- [ ] External approval: retain the strict all-external policy and prohibition
  on manually approving an external workflow targeting private hardware;
  manual run approval alone never satisfies same-repository author/sender checks.
- [ ] Routing: opt-in absent/disabled and unknown authorization retain hosted
  execution; eligible events execute each selected Linux recipe once, not both
  hosted and self-hosted. External/fork runs retain hosted coverage and their
  existing approval policy. Push/manual/promotion routing remains hosted.
- [ ] Completeness: compare a machine-readable recipe inventory before/after;
  all selected test files, markers, collection, guards, shared-library extras,
  standalone consumers and platform lanes remain accounted for. Partition
  union equals expected coverage and intersections are empty. Preserve the
  immutable tested tree as well: current hosted PR checkout includes the
  synthetic merge, unlike the existing head-only trusted bridge baseline.
  Pin and verify the equivalent head/base merge input rather than silently
  replacing integration coverage with head-only tests.
- [ ] Aggregate: selected-success, failure, timeout, cancellation, missing
  output, empty/duplicate partition and unexpected skip fixtures exercise the
  actual gate. Only planned out-of-scope or displaced jobs may be skipped;
  missing selected execution fails `PR gate (required check)`.
- [ ] Guard mutations: wrong workflow source/destination, broad custom labels,
  arbitrary dynamic routes, unpinned actions/calls, credential inheritance,
  mutable checkout and condition/needs/timeout bypasses fail closed. Run the
  trusted guard together with its updated tests and related CI scope tests.
- [ ] Bounded execution: at most two selected serial test recipes per PR run,
  one actual private worker and unchanged per-recipe
  deadlines. Exercise concurrent PRs, admission contention and cleanup after
  failure/cancellation without raising existing test budgets.
- [ ] Native/hosted regression: retain actual Windows/macOS jobs, external
  hosted lane and promotion snapshot failure propagation; Linux success is
  not credited for a native platform contract.
- [ ] Capability qualification: exercise the actual Python/tool setup actions,
  dependency preparation, required shells/native tools, process cleanup and
  resource ceilings inside each admitted runner's real execution envelope.
  Missing or unproven capabilities remain ineligible; distribution names,
  online status and container-engine presence are not readiness evidence.
- [ ] Portable Python: prove pinned setup-uv plus managed Python 3.12 without
  setup-python or a compatible system Python. Assert the outer interpreter and
  fresh/reused per-plugin venv interpreter, dev extras, local path sources and
  runner dependencies. Preserve seasoning, admission and containment; a missing
  native library remains an explicit capability failure, not a skipped test.
- [ ] Publication: audit the draft and final public diff, managed attribution,
  PR body and commit messages for private identifiers. Stage new files before
  isolation snapshots. All pytest execution uses the bounded test supervisor
  around the target repository's runner.
- [ ] Live acceptance: after authorized default-branch activation, observe a
  positive trusted PR, an external/untrusted hosted PR, opt-in-off fallback and
  a controlled selected-shard failure at the exact workflow version. Compare
  hosted jobs/runner minutes and queue time with equivalent baseline events;
  do not infer Copilot service capacity from Actions queue relief.
- [ ] Rollback: disabling opt-in affects new decisions and restores hosted
  execution; already-selected runs fail visibly or finish under their recorded
  decision, never silently pass on skipped work. Operators own draining any
  admitted jobs and changing runner state. Observe bounded slot utilization
  before completion.

## Proposal

### Existing-row decision and pending CI integration

The pure selector evaluates one existing row and caller-supplied context.
Only ordinary PR `smoke`/Dispatch and `worktrees-smoke`/Worktrees recipes are
eligible. Unknown authority, partial/shadow coverage, mismatched or stale
readiness, or insufficient Windows reserve yields a reasoned hosted decision
before execution. Source (including source-main), candidate and legacy modes
remain hosted.
After execution starts, reselection is refused; the caller must propagate
the actual result rather than retrying on hosted.

The protected caller remains responsible for current same-repository
author/sender trust, immutable integration-head/recipe/provider identities,
complete recipe evidence and fresh resource observations. A backend decision
is not an authorization, allocation or qualification receipt. The code defines
no provider registry, runner-label mapping or default resource thresholds.

Future CI wiring belongs to the existing owner after #7164. It must use the
same rows and job identities, retain the existing aggregate/cancellation
contracts, and require successful complete execution of the selected branch.
It must not count two skipped branches as coverage. Planning and API credentials
stay hosted; credential-free execution must use the exact tested integration
tree. These are open integration obligations, not implemented mechanisms.

Preserve Worktrees collection/guards and Dispatch's existing PR selection,
changed tests, smoke floor, guards and full fallback. Non-PR partitions,
other plugins, native Windows/macOS and privileged release recipes are not
offloaded by this slice. No second caller, registry, executor module or CI job
is added. Existing contained-runner and per-recipe budgets remain unchanged.

### Label and capability qualification

The approved custom label denotes a tested execution contract, not a substitute
for `ubuntu-latest`. Never attach hosted labels to private runners, accept an
arbitrary label from PR discovery, or assume every Linux distribution supports
the same prebuilt Python and native dependencies. Admit only the reviewed label
and capability profile; an absent, unknown or unqualified profile retains hosted
routing with an explicit diagnostic.

The profile must cover the pinned setup actions, supported Python version and
toolchain, required shell/native libraries, contained subprocess behavior,
workspace isolation and operator-approved CPU/memory limits. Qualify these
inside the actual dedicated-user or isolated execution envelope, not merely on
the surrounding host. Container-engine availability alone proves neither
isolation nor toolchain compatibility. An existing runner installation or
service unit is not a portable provisioning template across host layouts.

The capacity operator owns the execution envelope and qualification signal.
Public routing consumes that bounded capability contract without identifying
hosts, assuming all eventual slots are ready, or changing machine-wide service
configuration. One qualified slot may precede the two-slot target; at most two
queued shards do not authorize two concurrent jobs on one host. If a recipe
cannot run faithfully within the approved profile, retain hosted routing for
the selected set rather than silently omitting it or broadening the host's
permissions. Any deliberately narrower offload must identify its retained
hosted recipes in the coverage inventory before activation.

### Portable trusted-lane Python

Use the pinned setup-uv action followed by explicitly managed Python 3.12 in
the proposed trusted Linux executor. Do not require setup-python there:
[its self-hosted guidance](https://github.com/actions/setup-python/blob/main/docs/advanced-usage.md#using-setup-python-with-a-self-hosted-runner)
warns that its distributions match hosted environments and may not work on
other distributions. Keep existing hosted/native platform setup unchanged.

An illustrative recipe is
`UV_PYTHON=3.12 UV_PYTHON_PREFERENCE=only-managed uv run --no-project --python 3.12 python tools/run-plugin-tests.py <plugin>`.
The approved uv version and resolved Python patch belong to the capability
record. Preparation may explicitly install that managed interpreter before
network-restricted execution; it does not grant the later tests new network
authority. Setup-uv's Node runtime, uv's standalone binary and the managed
interpreter still require qualification in the actual runner envelope.

`--no-project` prevents the outer invocation from implicitly syncing an unrelated
root project. Keep `run-plugin-tests.py` as the dependency/containment owner:
it creates each plugin's venv, installs its dev extras, resolves local path
sources and installs the runner's declared dependencies through the seasoned
installer. Its current `uv venv` call does not specify an interpreter, so the
managed-Python selection must reach that nested call as well as the outer
process. Merely starting the wrapper on Python 3.12 is not sufficient proof.
Verify fresh and cached environment identity; do not reuse a venv built with an
incompatible interpreter or bypass the existing environment lease.

Shared SSH contracts continue under the prepared bridge venv, not a separate
dependency-free outer Python. Worktrees collection and guards retain their
normal plugin environments. Local validation wraps these recipes in the
bounded test supervisor. None of this changes the existing immutable workflow
used for baseline qualification or claims portability from an Ubuntu-only
baseline result.

The hosted replacement and trusted call are mutually exclusive outputs of the
same routing decision. The aggregate requires the selected branch to succeed
and verifies expected coverage; it cannot accept both branches skipped. No
automatic retry on hosted infrastructure after partial trusted execution:
report the failure and require a fresh recorded routing decision.

### Accepted trust boundary and proportionate safeguards

An exact default-branch `trusted-ci.yml` blob and fork-approval policy do
**not** bind an assigned job to that workflow. A malicious or stale PR workflow
can request `copilot-extensions-ci` directly before its guard fails. Labels are
not authentication, and structural CI checks are not a pre-execution access
control. This limitation predates offload and is accepted within the reviewed
durable-collaborator model; this effort does not claim to eliminate it.

Preserve current write/maintain/admin collaborators as durable execution
principals, protected workflow governance, the strict all-external approval
policy and the prohibition on manually approving an external workflow targeting
private hardware. Keep authorization code base-controlled through the approved,
immutable callee rather than trusting a PR-edited helper. The callee itself
checks same-repository origin and both current author and event-sender
permissions before allowing any self-hosted execution. A caller cannot grant
authority through an input, custom label, run approval or success-shaped output.
Accept only the reviewed CI PR invocation; reject dispatch, push and unsupported
caller contexts instead of inheriting a broader workflow-call event surface.

The proposed routing changes execution volume and the reviewed caller graph,
not the admitted principal set: no fork, read/triage user or untrusted sender
becomes eligible. No unavoidable new authority widening has been identified.
Implementation must test the actual reusable-workflow context and token
permissions; any inability to preserve those checks is a concrete blocker,
not permission to replace them with caller assertions.

Bind a decision to repository, PR number, head/base revisions, actual triggering
sender and run/attempt. Revalidate the real PR and opt-in before dispatch, and
perform fresh permission checks for each newly authorized invocation. A rerun
actor is not automatically the original event sender. Stale controller pins or
changed default-branch workflow blobs are ineligible until explicitly reconciled.
Authorization remains point-in-time: already-admitted or queued jobs do not gain
a new continuous-revocation guarantee. Operators retain their existing
withdrawal/drain procedure when authority must be revoked after admission.

Provider-enforced workflow restrictions or cryptographic job attestation are
optional, separately scoped hardening, not an activation prerequisite here.
Reconsider them only against an identified new violation with a proportional
remedy. Do not add an OIDC/admission protocol or assume runner-group capabilities
merely to restate an accepted existing limitation.

### Expected source changes and control ownership

| Surface | Implemented or pending responsibility |
|---------|----------------|
| `tools/ci_plugin_partitions.py` | Implemented pure `select_backend(row, context)`; existing inventory, partitions, CLI output and commands unchanged |
| `tools/test_ci_plugin_partitions.py` | Implemented dedicated decision cases while retaining all original tests |
| `.github/workflows/ci.yml` | Pending owner integration after #7164; no changes in this selector slice |
| Existing executor, route guards and integration tests | Future coordinated routing/execution work; no new registry or executor module proposed by this slice |
| Existing contained runner and coverage-selection helpers | Unchanged here; their current commands, selection semantics and budgets remain authoritative |
| This effort and `efforts/README.md` | Current implementation boundary, public repository history and open completion gates |

This selector introduces no executor reference or call site. Later CI wiring,
source pins and qualified execution remain coordinated integration work under
#6237; no Main delivery, helper-pin change or activation is inferred here.

## Journal

### 2026-10-16 - Local planning draft

- Read the current trusted workflow, CI graph, trust guard/tests, test-portfolio
  vision and main-owned control contract. Confirmed that the existing trusted
  bridge job duplicates hosted execution.
- Proposed one CI graph and a pinned reusable executor, limited initially to
  smoke and Worktrees collection/guards, with two serial-recipe shards.
- Identified job provenance and paused default-branch activation as explicit
  prerequisites. No runner, opt-in, pin, workflow or release state was changed.
- Read the complete draft back against the request: preserved both current
  identities, hosted default/fork coverage, unchanged native platforms, real
  aggregate completeness and separate publication/implementation authorization.
  Marked the initial offload scope and reusable-executor design as recommendations.
- Draft only; plan publication and implementation remain separately gated.

### 2026-10-16 - Capability qualification constraint

- Captured heterogeneous Linux execution envelopes as an operator constraint,
  without publishing host inventory or resource assignments.
- Added action/native-dependency and containment qualification before label
  admission. No Ubuntu baseline, copied service layout, existing slot readiness
  or hosted-label aliasing is assumed.
- Publication remains gated on the integration owner's prerequisite review and
  explicit authorization; implementation and activation remain unauthorized.

### 2026-10-16 - Portable interpreter direction

- Confirmed setup-python's upstream self-hosted distribution caveat and captured
  the operator's uv-managed Python 3.12 direction for the proposed trusted lane.
- Inspected the real plugin runner: nested venv creation does not explicitly
  select Python, while dependency installation already owns dev extras and
  local path sources. Added nested-selection and cached-interpreter validation
  rather than assuming the outer uv invocation controls every plugin venv.
- Existing immutable-workflow baseline qualification remains a separate
  operator-owned action; no workflow, toolchain or pin was changed here.

### 2026-10-16 - Accepted trust-model clarification

- Operator clarified the existing durable-collaborator, protected-workflow and
  strict external-approval boundary. The initial draft's mandatory
  cryptographic job-provenance prerequisite is superseded; no new principal
  admission is intended or currently required by the proposed graph.
- Retained pinned/base-controlled authorization, both current permission checks
  and explicit same-repository refusal. Replaced unsupported continuous
  revocation claims with fresh per-invocation authorization and the existing
  operator-owned withdrawal/drain boundary.
- Additional attestation remains outside-scope hardening unless implementation
  exposes a concrete new boundary violation. Default-branch release/control
  authorization and capacity qualification remain real activation gates.

### 2026-10-16 - Planning publication authorized

- Integration owner authorized the planning-only public PR independently of
  other proposal reviews. This supersedes the earlier local-only publication
  gate, not the separate implementation and activation gates.
- The proposal preserves the accepted durable-collaborator boundary and names
  immutable head/base integration coverage explicitly. No new authority
  widening is required by the proposed route; implementation must verify that
  against actual reusable-workflow event and permission behavior.
- Only the canonical effort and its index are proposed for publication.

### 2026-10-17 - Exact-head planning merge and review priority

- #7125 merged at 01:39 America/Los_Angeles as
  `d4925bc2394d5b1c1c5bc1176cc7f4334b53f367`, from verified planning head
  `b9b23d9c10bdef128400068380f83dff4cf2e7de`. The operator explicitly
  authorized a bounded PR-only bootstrap exception; the original independent
  ruleset and classic admin-enforcement settings were restored and live-audited
  before any further work. No release pause, unrelated PR or check status was
  edited.
- The actual Copilot review reported that full agentic review did not start
  before timeout. Its lightweight feedback noted an index/related-campaign
  ownership inconsistency; the local continuation now names #1303 alongside
  the existing workflow-control and release owners. This is not recorded as
  successful full review or green required CI.
- Re-read the canonical effort after managed PR completion advanced the worktree
  onto the merged dev commit. The parent #7122 remains open; implementation and
  live acceptance are not complete.
- Operator prioritized official ARC Code Review capacity. Added a planning-only
  ARC slice; no workflow, guard, runner label or infrastructure was changed.

### 2026-10-17 - ARC proposal publication and scoped compatibility direction

- Operator authorized the public planning amendment in parallel with other
  intent reviews; implementing ARC source publication remains separately gated.
- Captured explicit authorization for a minimum compatible, dev-first
  guard/admission bootstrap on main. The proposed transaction pins four
  guard/admission source and test files, activates its admission policy first,
  transfers only approved merged-dev blobs, then retires the bounded source
  exception. No unrelated plugin/runtime source or whole dev snapshot is in scope.
- The existing #6237 workflow-control owner retains its campaign. This slice
  coordinates the exact compatibility policy rather than declaring an
  unqualified helper-source blockage or taking over the release work.

### 2026-10-17 - ARC planning amendment merged

- #7133 merged at 02:14 America/Los_Angeles as
  `58dd5c890415a65a656acbd8bf4b4931bda97a11`, from verified planning head
  `bc74b885de29cef5039118ff717b81e3bc3264bf`. The operator-authorized
  PR-only protection transaction restored and live-audited both original
  settings before any continuation; no exception remains open.
- Posted the bounded compatibility coordination note on #6237 before any
  admission-policy implementation. It names the four-file
  dev-first transfer, retained owners and prohibition on unrelated release work.
- Re-read this canonical effort after managed completion advanced the worktree
  onto the actual merge. Phase 1a's proposal landing is complete; implementing
  source publication and live ARC readiness remain separately gated.
- No workflow, guard, credential, runner label or infrastructure implementation
  was published. Broad validator implementation remains paused; #7122 stays open.

### 2026-10-17 - Hosted-safe setup implementation started

- Integration owner cleared implementing-source publication, requiring literal
  hosted staging until a separate explicit ARC readiness decision.
- Added the dedicated official setup with one `copilot-setup-steps` job,
  `ubuntu-latest`, empty setup permissions and a bounded minimal setup command.
  No checkout, dependency setup, coding-agent fallback or custom label changed.
- Existing trusted-route guard passed and all 34 existing guard regressions
  passed under the bounded test supervisor, with verified cleanup.
- Shared-helper owner coordination remains outstanding; no admission helper or
  `ci.yml` was edited. The narrow route-guard and preservation changes follow
  only after that ownership seam is resolved.

### 2026-10-17 - Setup-step timeout and current-head review findings

- Scoped the five-minute timeout to the single setup command step. No
  job-level timeout is configured, so the setup file does not impose its own
  five-minute limit on the official review job.
- The previous head's required checks passed. Its lightweight review noted a
  historical-link portability issue and a stale proposal heading; the
  continuation uses the existing repo-local issue reference and labels the
  approved design accordingly.
- No Main control, shared helper, runner selector or credential was changed.

### 2026-10-17 - Hosted-safe implementation merged to dev

- #7135 merged at 16:59 America/Los_Angeles as
  `2db8a9c072fb191de2b6adef053ecfa8bc50c11a`, from exact head
  `438058d919fbb73cf784e2677a2abe50d17b3647`, after current-head Copilot
  approval and both ruleset checks passed. The normal managed merge remained
  blocked on the missing/pending PR aggregate; the operator-authorized
  transaction lifted only classic dev admin enforcement, restored it in
  `finally`, and live-audited both original protection snapshots. The check
  ruleset stayed active and unchanged throughout.
- Corrected the step-timeout scope using the official supported Actions step
  field. Exact YAML contract checks and all 34 existing trusted-route
  regressions passed under the bounded supervisor with verified cleanup.
- A description update omitted the source marker; restored it using managed
  attribution preparation, verified the live body, and confirmed the real
  source-attribution check passed before merge. No failure was replaced by a
  forged success status.
- Re-read the canonical effort after reconciling the already-merged squash,
  preserving intervening upstream work. The parent #7122 remains open.
- This is merged dev source, not default-branch activation or ARC readiness.
  Main is held pending the existing workflow-control owner's coordination;
  shared helper edits and the final static ARC selector remain gated.

### 2026-10-17 - Disjoint review guard and exact-Main overlay preparation

- Workflow-control owner cleared the dedicated setup guard/test seam and retained
  the held publication-boundary test file. This slice leaves that file,
  post-CI policy tests, CI, promoter and the Dev preservation manifest untouched.
- Added exact hosted/named-ARC setup-contract validation and independent
  mutation fixtures while retaining every original validator check. Dedicated
  fixtures execute the real inline Main admission body and unchanged generic
  preservation helper for old/prepared allowlists and unsafe transaction cases.
  All 90 focused guard cases and the 251-case coupled Dev policy portfolio
  passed under the bounded supervisor; fixture cleanup was verified.
- Prepared separate allowlist-only and later canonical-manifest/hosted-file
  overlays against `a2f59e9dae82738ba2537e9a58196cd5cca0e3c8`; no Main
  mutation occurred. The existing helper already supports the fourth path;
  no generic preservation implementation or helper pin was changed.
- Exact old-Main guard invocations pass. Its four-file guard/admission test
  portfolio reports 146 passed and the same two failures on the untouched
  baseline and on each overlay: stale contents-only permission expectations
  and an obsolete positive promotion fixture in `tools/test_post_ci_policy.py`.
  These remain #6237's compatibility/qualification decision, not a green proof
  or permission to replace its tests with unqualified Dev source.
- Current sequencing uses qualified Dev-to-Main guard delivery before final
  ARC selection, rather than expanding workflow-only policy to copy Python
  helpers. Hosted selection and the explicit physical READY gate remain intact.

### 2026-10-17 - Literal Main evidence and dedicated-contract review

- Refreshed both live API and fetched Main identities; both still resolve to
  `a2f59e9dae82738ba2537e9a58196cd5cca0e3c8`. Rebuilt fresh workflow-only
  overlays and captured literal failure sections, full logs and immutable
  input/output closures without porting tests or changing their assertions.
- All three exact old-source portfolios remain failed: 146 passed, 2 failed.
  Guard commands pass; the two failing node IDs and literal diagnostics are
  retained for #6237's classification. No green Main qualification, Main
  candidate publication or mutation is inferred.
- Dev review found that whitespace normalization could accept a setup command
  split across shell lines. The dedicated contract now compares parsed values
  directly and has an independent negative newline fixture. The coupled Dev
  portfolio passed 252 cases under the bounded supervisor.

### 2026-10-17 - Main admission path merged

- #7153 merged at 18:29 America/Los_Angeles as
  `bc328d8d425155616bc91a35c616692dc4c81342`, parent
  `a2f59e9dae82738ba2537e9a58196cd5cca0e3c8`, from reviewed head
  `a422deb07738d35bb32962b245160ff8c20c4fc5`. The one-line named allowlist
  addition was the only changed file; admission-workflow blob was
  `0cff158925120b6ee5c72b3d9b7d0b631c161500`.
- That commit did not change the manifest, helper or setup definition.

### 2026-10-17 - Dedicated review guard merged to dev

- #7151 merged at 18:37 America/Los_Angeles as
  `0f0f039ca40bf9db4b3ba4ae832e08e46c85113c`, from approved head
  `13e7e06b9ba2d60a0beb2f678ac561227537c647`. This records the Dev
  source landing, not qualification or physical readiness.

### 2026-10-17 - Main hosted setup preserved

- #7154 merged at 18:57 America/Los_Angeles as
  `c1c0fea7ba3beea33b4010be009489696e51e4be`, from reviewed head
  `a83e5005de7dbf81a0055ff7ec39920fda7cf2f1`, directly on the actual
  Stage 1 merge. Only the Main manifest and hosted setup definition changed.
- Commit contents: manifest `afab28d33694384f5167b7d268e0586be2b691c5`,
  setup workflow `add925d5cc80a3283cb7c612ace1b546c153770b`. All three
  existing declarations were retained; the fourth names the review setup.
  The definition in this commit selects literal `ubuntu-latest`, with a
  step-only five-minute timeout and no whole-review job cap. These entries
  record repository chronology, not current qualification or readiness.

### 2026-10-18 - Pure existing-row backend selection

- Implemented `select_backend(row, context)` in the existing
  `tools/ci_plugin_partitions.py` helper and dedicated cases in
  `tools/test_ci_plugin_partitions.py`. It returns only a route kind and
  explicit reason; it changes no existing row, inventory, partition, CLI
  output, job identity or test command, and adds no registry or caller.
- Selection is pre-execution only. Unknown/untrusted, partial/shadow,
  mismatched, stale or unready contexts remain hosted; reselection after
  execution starts is refused. The caller retains proof validation, resource
  admission and actual execution-result propagation.
- The targeted helper module passes 119 tests. The coupled guard, partition,
  coverage-selection and selection-file portfolio passes 298 tests with two
  existing opt-in integration skips. Original functions/tests and inventory
  remain unchanged.
- CI wiring and executor work remain owner integration after #7164.
  Qualification, activation and real-job acceptance remain open; the pure
  selector establishes none of them.
