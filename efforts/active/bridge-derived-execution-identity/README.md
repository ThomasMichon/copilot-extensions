# Bridge Derived Execution Identity

- **Slug:** `bridge-derived-execution-identity`
- **Repo:** copilot-extensions
- **Branch(es):** Independent plan, implementation and completion PRs targeting `dev`
- **Created:** 2026-10-16
- **Status:** Active
- **Vision:** [Agent Fabric](../../../visions/agent-fabric/README.md), “Derive, don't duplicate”; standalone graceful composition
- **Umbrella issue:** #7069
- **Sub-issues:** #7090 (validation fix-forward)

## Guiding Intent

Derive bridge execution-space identity from existing authoritative source
worktree/project and machine configuration, not another manually maintained
bridge identity setting. Preserve independent execution-space authority.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source owner | Plan, implementation, validation and PR stewardship | Managed worktree `hinged-clasp` |

## Coordination

- **Topology:** Serial plan, implementation and completion PRs.
- **Host (owns PRs):** Source owner.
- **Delegates:** None.
- **Handoff:** The reviewed effort and issue carry source responsibility.
  Deployment remains an independently authorized operation, not this effort.
- **Adjacent owners:** #6987 (read-only locality), #5839 (remote ownership),
  #7057 (explicit owner-label migration) retain their scopes. This effort
  neither migrates owner records nor supersedes their implementations.

## Context

`build_resolver` passes `ServiceConfig.local_execution_space` into topology
locality checks. `_find_covering_agent` then constructs a nested `AgentResolver`
without that selector, repeating detection and failing for independent
registered spaces. Requiring the operator to duplicate an existing project
machine binding in bridge configuration is not the intended repair.

This closes Agent Fabric's derive-don't-duplicate intent. The design follows
the patterns' à-la-carte independence, optional composition, fail-loud errors,
cross-platform parity and installation provenance invariants. A source checkout
is context for existing machine authority, not a new runtime code dependency.

## Request

> We want to ensure this is dynamically computed from the source worktree and machine config, and not made into yet-another-thing that needs to be written manually

## Plan

### Phase 1 — Reviewed source contract
- [x] Claim the deduplicated issue and land this plan-only PR before implementation.
- [x] Trace existing configuration, invocation and execution-space authority
  helpers; choose the smallest supported derivation contract.

### Phase 2 — Derived identity and propagation
- [x] Capture originating source context before daemon detach/cwd changes;
  recompute and validate against authoritative project/machine configuration.
- [x] Carry one resolved execution identity through every nested resolver caller.
- [x] Support start, restart and adoption from a source worktree without manually
  writing `local_execution_space`.
- [x] Preserve standalone bridge operation without requiring agent-worktrees.
  Reject missing/ambiguous bindings explicitly when authority cannot be derived.
- [x] Preserve native/guest and separately registered same-platform spaces;
  never grant ownership from hostname, display alias, SSH profile or unverified
  ambient environment.
- [x] Define legacy selector compatibility without overriding contradictory
  authority; update the directly affected configuration documentation and changefile.

### Phase 3 — Validate, land and close
- [x] Complete targeted then required contained plugin gates, lint and install-contract validation.
- [ ] Complete REVIEW.md self-check, documentation-impact review and public privacy scan.
- [ ] Land the implementation through normal review/check/role gates; re-read this
  effort, resolve its checklists and land the Done/archive delta.
- [ ] Release effort focus and finalize the source worktree; report release status
  without installing, updating or restarting any shared runtime.

## Validation Plan

- [x] Regression for the original discovered-agent `_find_covering_agent`
  startup path, exercising the nested resolver.
- [x] Real source-worktree → authoritative identity → daemon context/config →
  nested resolver contract with no manual bridge selector.
- [x] Native/guest and same-platform independent spaces remain distinct; missing,
  ambiguous, platform-mismatched and contradictory bindings fail closed.
- [x] Standalone no-sibling and deliberate legacy-selector compatibility cases.
- [x] Fresh isolated configuration/startup check without touching a shared daemon.
- [x] Targeted tests followed by required contained plugin suites, ruff F/E9 and
  `check-install-contract`; stage new files before wrapper snapshots.
- [x] Record exercised validation tiers and explicit reasons for unavailable
  clean-room/external lanes; no deployment implied by unit success.
- [x] _(agent-recommended)_ Re-read changed configuration docs and inspect every
  resolver constructor to catch sibling propagation omissions.

## Proposal

Reuse the existing authoritative project configuration and suite-owned invocation
or shared-library seams rather than introducing a second standing identity knob.
Preserve source context across daemon lifecycle boundaries as derivation input,
not as authority in its own right. Validate the exact integration and precedence
with implementation tests; standalone compatibility remains first-class.

## Journal

### 2026-10-16 — Kickoff
- Clean isolated source worktree; confirmed duplicate selector expectation and
  the nested resolver's dropped identity. Scoped public identity verified.
- Claimed #7069 after narrow dedup. Adjacent ownership fences retained.
- Request read back against the approved exact quote; unchanged. No private
  deployment context included. Implementation awaits the plan review gate.

### 2026-10-17 — Reviewed contract
- Plan #7070 merged after Copilot approval with no findings and all CI passing.
- Reuse Worktrees' `get machine`: its existing current-execution-space fence
  validates canonical configured machine and platform. Invoke it through bridge's
  same-installation peer launcher, not imports or a new ambient identity variable.
- Capture source context before detach/chdir; adoption retains the stable source
  repository for supervised starts. Context is a locator, never authority.
- Keep standalone unscoped topology and legacy selectors usable without a sibling;
  a discovered authoritative contradiction remains an error.

### 2026-10-17 — Implementation and focused validation
- Captured source context is non-persistent process data or an adoption-created
  stable repository locator, never a stored derived key. Originating project
  authority takes precedence; neutral/supervised starts require profile consensus.
- Legacy selectors work without a sibling but cannot override a discovered
  authoritative contradiction or bound-project refusal. Invalid identity/topology
  configuration no longer becomes successful default configuration.
- The original nested construction fails the new regression with the exact
  missing-selector error; forwarding the derived key fixes it. Targeted runs
  passed 242 tests, then 127 including added real linked-Git and routing-env checks.
- A read-only native consumer check exercised the actual same-installation
  Worktrees command from a registered source worktree, its canonical configured
  machine authority, derived bridge identity and nested coverage resolver. A
  second same-platform space remained remote; no selector/config/runtime was written.
- Fresh isolated configuration, adoption and pre-chdir/startup fixtures cover
  first-touch behavior. No disposable external host/guest rollout was performed:
  this Windows source work owns no deployment lane or rollout authorization.
  Guest/Linux cases are contract tests, not claims of native guest rollout.
- Ruff F/E9, install-contract (13 plugins), documentation consistency and version
  consistency passed. Full contained Bridge suite is in progress; its final
  aggregate and implementation review remain outstanding.
- The native portfolio exposed #7090: Python 3.14 reports late inner exceptions
  from abandoned shield wrappers even when the task failure is consumed. Replaced
  the reader's sample/command joins with non-cancelling waits, retaining ownership
  and deadlines; validation covers cancellation during either join.

### 2026-10-17 — Native portfolio complete
- Covered all 242 Bridge test files through the contained runner. The first
  75 files passed in three groups; the remainder passed in file-bounded runs.
  The completed-slot admission matrix exceeded the unchanged 300-second group
  budget, so every test function was run separately at the same limits, with
  no omissions or increased item budgets. Normal platform/explicit-tier skips
  remain visible; no host-state opt-in was used.
- Fixed both real failures encountered: #7090's owned-reader join handling,
  and the synthetic HTTP/2 redirect peer's premature reply before request-body
  consumption. Its existing no-forward assertion remains, with an additional
  positive body-consumption assertion. The full native peer module and registry
  selection passed together (201 tests); reader/identity selection passed 67.
- The final native broker fixture now starts from its own neutral cwd rather
  than accidentally probing the caller's Git checkout through a mocked process
  factory. Final portfolio files passed 201 tests with 20 platform skips.
- Cross-plugin guards passed 9 tests. Rechecked ruff F/E9, install contract,
  documentation/version consistency, whitespace and source-diff privacy.
- No new daemon, lifecycle owner, endpoint or termination path was introduced.
  Existing service/installer activation, passive cutover, singleton, drain and
  production-lifetime mechanisms remain authoritative.
- The publication guard identified the model source's missing contract-registry
  fingerprint refresh. Updated its LF-normalized hash and explicitly classified
  the topology-only locator addition as non-semantic for HTTP; no wire generation
  or frozen wire fixture changes are warranted.
- Local-agent/provider-only configurations have no machine inventory and therefore
  select no registered execution authority, even with a residual legacy selector.
  Nonempty topology selections remain strict. Added the actual local-only startup
  regression. Completed sample timeout exceptions are observation failures,
  distinct from a still-pending bounded join; both paths have direct coverage.
- Captured source context is applied after configuration migration returns,
  independent of replacement-model behavior. Startup tests cover both identity
  and copied-model migrations. Derived-key validation now attributes failures
  to the source-project binding rather than suggesting a manual bridge setting.
- A cancelled sampling holder is an observation failure, distinct from
  cancellation of the caller's wait (which still propagates). The updated
  identity/config/reader/startup/broker selection passed 335 tests; contract
  evidence and F/E9 lint remain clean.
- Invalid profile locators and captured origins identify their exact profile/path;
  reader failures and disagreeing authorities retain source provenance. Explicit
  source-query deduplication preserves argv values and avoids repeated reads of
  the same binding. Added duplicate-source and pre-write diagnostic regressions.
- Reconciled the source with current `dev`, preserving the independently merged
  SSH-profile authority change. Only the shared effort index conflicted; its
  transient status-summary edit is left for closure while this README remains
  the canonical current status.
- Legacy supervised profiles prefer their declared control-plane project over
  incidental shared-topology storage. An installed optional reader with no
  registered source project does not invalidate an existing standalone selector;
  a bound machine-authority refusal still blocks. These regressions and the
  current identity/startup/reader/SSH/native-peer selection passed 548 tests.
- An unversioned machine-name scalar cannot certify configured rather than
  detected identity, including a hostname that happens to equal a registry key.
  The final consumer uses a versioned read backed by the same configuration
  loader and registered-current-space predicate. Provenance is captured only
  in memory; no new identity setting, record migration or mutation-policy change
  is introduced. Older/unversioned or detected-only responses offer no derived
  authority. Explicit configured identities from unscoped source projects still
  require consumer-side execution-space qualification; scoped source registries
  retain their existing current-space fence.
- Final protocol producer/config/fence selection passed 384 tests; Bridge
  configured-binding/legacy/older-peer selection passed 351. The actual source
  CLI and Bridge consumer passed a real subprocess/cwd-change/nested-resolver
  check against existing explicit machine configuration, without a selector or
  configuration/runtime write. Full Worktrees collection passed all 416 files.
  Bridge guards passed 234 tests with 8 normal skips; contracts validate 2
  contracts/154 fixtures, and install/docs/version/F/E9 gates pass.
- Configuration-tier selection lives with machine identity; the configuration
  loader only captures ephemeral provenance and directly re-exports the existing
  shared platform detector. Its module remains below the unchanged size ceiling.
- Public source-content and implementation-commit attribution checks are clean.
  A separate publication-metadata authorization review remains outstanding; do
  not rewrite published history automatically or equate source-content review
  with completion of that broader gate.
- Full owner collection passed; the monolithic native owner guard run hit its
  unchanged 300-second group budget after progressing without reported assertion
  failures. Complete that guard portfolio in bounded selections before treating
  the required validation gate as satisfied; do not increase limits or omit cases.
- The exact collected owner guard portfolio completed: 2,047 selected nodes in
  93 files, all run in bounded selections at the unchanged limits, with normal
  platform skips preserved. No assertion failures or omitted selections.
- Review follow-up distinguishes actual remote-ACP validation failures in mixed
  identity/ACP configuration. Adopted source locators deliberately precede the
  legacy control-project fallback; documentation and a regression make both
  precedence branches explicit. The focused identity selection passed 46 tests.
- Reconciled independently merged native-resume and source-generation work.
  The sole conflict was the model fingerprint: retained the newer wire generation
  and fixtures, refreshed the merged model's LF-normalized hash, and confirmed
  the local-only locator delta. Contract validation now covers 224 fixtures.
  The post-reconcile identity/config/reader/startup/native-resume selection passed
  273 tests with 2 normal skips. Module-size, install, docs/version, F/E9 and
  whitespace gates passed again. No source owner protocol or mutation policy
  changed during reconciliation.
- A further review caught explicitly present falsy machine values being treated
  as absent provenance. Readonly certification now refuses empty/null/nonstring
  bindings at the selected configuration tier without changing ordinary loader
  fallback semantics. Regressions cover both tiers and an existing global
  fallback. Legacy external launchers retain their original arguments rather
  than receiving an unsupported private source-context flag.
- Recollected final owner guards after reconciliation: all 2,049 selected nodes
  across 93 files completed at unchanged budgets. The reviewed invalid-binding
  and independent-space selection passed 176 tests with 2 normal skips.
- Subsequent lifecycle review tightened source-directory validation so a missing
  descendant cannot silently become its parent repository, closed identity-reader
  stdin (including a real subprocess EOF regression), and distinguished unrelated
  Bridge configuration errors without allowing identity fallback. The focused
  identity/config/startup/broker selection passed 129 tests; F/E9 and module-size
  gates remain clean.
