# Account-Aware Operations

- **Slug:** `account-aware-operations`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-08-29
- **Status:** Draft
- **Vision:** agent-fabric `no-account-per-agent`,
  `guidance-emitted-at-point-of-action`, and `project-addressed-not-cwd-bound`

## Guiding Intent

Make every repository-facing operation select identity from the target
repository and operation instead of mutable ambient process state. Concurrent
agents can then use different authorized accounts without racing a global
switch or leaking one operation's credentials into another.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| identity contract host | Owns account selection and execution contracts | isolated worktree |
| command owners | Adopt scoped execution in repository-facing commands | independent slice PRs |
| validation host | Exercises concurrent and failure-path scenarios | clean-room scenario |

## Coordination

- **Finite #3296 inference-identity intake (proposed):** Phase 6 is the
  separate Copilot inference-identity lane; it does not change Phases 1-4's
  repository-operation authentication contract or conflate the two identities.
  Worktrees retains repo-keyed resolution and launcher integration;
  agent-machines retains machine-default policy and restoration. The existing
  trusted-container claimant keeps that bounded launch/isolation slice and
  its explicit exclusions. Other launcher, credential, source and native
  owners remain unchanged. Preserve Draft status and the temporary-token
  implementation restriction: this amendment appoints no executor, transfers
  no claim and starts no authentication or live inference operation.
  #3296 remains OPEN during the proposal and until a separate complete
  receiving/custody judgment; ordinary document review is not claim release.

The proposed #4756 fork-consent lane below is distinct from #5791 credential
dispatch. It directly receives the WHOLE durable target/account/remote,
existence-before-creation and confirmation outcome, not only its residual
edge. Earlier campaign whole-OPEN recommendations remain historical until
normal reviewed complete receiving, current whole ROOT/source/custody readback
and a separate source-specific judgment; they are not an original requirement
to retain duplicate tracking forever. Preserve this effort's Draft status,
temporary coordination token, identity/command/source custodians and
implementation restriction. No credential lookup, fork operation or remote
rewrite follows from planned receiving; #5904 and #1043 stay independent.

The finite #4199 push-race intake below remains with the existing identity,
command and actual push-consumer owners. Keep the WHOLE #4199 outcome
independently OPEN until actual implementation OR exact complete authorized
receiving is accepted; partial #5791 same-account/missing-token coverage is
insufficient. This proposed direct receipt changes no Draft status,
temporary-token implementation restriction or executor assignment and
authorizes no credential operation. Planned receiving, current readback and
a separate source-specific judgment remain distinct from technical delivery.

- **Topology:** contract-first host with independent command-adoption slices.
- **Host (owns PRs):** identity contract host.
- **Delegates:** command owners adopt the shared resolver without redefining it.
- **Handoff:** each slice lands with contract tests and explicit unsupported
  behavior.
- **Public coordination token:** the reviewed plan PR until a dedicated issue
  is authorized; implementation does not begin under the temporary token.
- **Finite #810 intake:** existing identity-contract and scoped-wrapper command
  custodians retain the Windows argument-fidelity slice below. Preserve its
  native-route OR point-of-use-guidance alternative, source/resource/native
  duties and current ownership. This tracking amendment does not start
  implementation under the temporary token, appoint an owner, change
  authentication or require a new generic acknowledgement.

- **Finite #5791 intake:** retain the existing identity-contract/scoped-command
  owners, original bounded-repair continuation, claims and activation gates.
  Phase 2 below is its single canonical phase; Phases 1 and 4 are interfaces,
  not competing owners. Its original plus inherited #330/#2755 contract is
  deduplicated against the existing tracker and recorded in the
  [dated intake ledger](../migration-intake/ledger.md#finite-account-execution-intake-2026-11-13).
  Acceptance requires normal reviewed merge of this direct Plan amendment;
  a ledger proposal alone is not acceptance. Draft status and the
  temporary-token implementation restriction remain unchanged; no new
  tracker, provider abstraction, executor appointment or authentication
  operation is authorized.

- **Finite #12466 intake:** Phase 4 is the single canonical phase for
  provider-appropriate update-failure guidance. Existing scoped-command and
  update-authentication custodians retain their work; this receives tracking,
  not their source claim. The [append-only intake record](../migration-intake/ledger.md#finite-provider-appropriate-update-guidance-intake-2026-11-14-utc)
  retains the existing issue rather than creating another tracker. Normal
  reviewed direct Plan acceptance does not activate this Draft effort, waive
  the temporary-token implementation restriction or authorize an auth ceremony.

- **Finite #69 intake (proposed):** P69-C records the contract and owner
  boundary; P69-I and V69 retain the actual account-to-provider-to-credential
  implementation, integration and behavior validation as unchecked work.
  This is one account-aware family, not a second plan or executor assignment.
  Credit the delivered GitHub-only source Phase 1 as historical evidence:
  its explicit-account, remote-owner-derived, then-none sequence is not a
  current selection requirement. Current policy is exact repository
  `account:` override, explicit owner account map, preferred/default account,
  then none/ambient. Never infer a login from a repository namespace or a
  sibling's account. Non-GitHub repositories may use their explicit override
  but do not inherit a default GitHub account. Retain the related-repository
  account surface/override and `repos`/`related` output under that current policy.
  The credited command set is `create-pr`, `push-changes`, `pr-merge`,
  `pr-ready`, `pr-status`, `pr-watch`, `pr-complete`, `set-pr` and `finalize`.
  Preserve token injection, explicit-token override and per-invocation Git
  `extraheader` with no global switch. The historical `extraheader` versus
  credential-helper-after-switch alternative remains context, reconciled to
  that delivered no-global-switch route, not a reopened decision. Existing
  Phase 4 concurrency/global-auth validation remains applicable; no new
  switch/restore or concurrency program is introduced. Original Q3 remains
  the follow-on question whether ad-hoc non-agent-worktrees CLI calls should
  share the resolver, not an OR alternative or a new P69 requirement.
  Keep the original `accounts.yaml` and/or moving credential ownership into
  `agent-vault` alternative unresolved. Worktrees, Vault/trust and venue-parity
  retain their respective custody. Draft status and the temporary-token
  implementation restriction remain; this intake establishes no execution,
  credential movement or resource transfer.

## Context

Repository tools often inherit whichever account happens to be active in a
global CLI configuration. That assumption is unsafe when concurrent sessions
address repositories owned by different accounts. A portable solution needs a
repository-to-account binding, a race-free way to mint scoped process
credentials, and fail-loud behavior when the requested identity is unavailable.
This effort owns repository/account selection and child-process scoping;
venue-specific credential transport and launch authentication remain with
[`venue-parity`](../venue-parity/README.md).

## Request

Define and adopt a general-purpose account-aware execution contract for
repository operations, including concurrent use, diagnostics, and safe
degradation without global identity mutation.

The finite Phase 6 intake preserves #3296's independently stated request:
"Per-repo/per-machine enforcement of Copilot CLI's own inference identity
(distinct from gh account_map)". Its requested repo override and machine
default, supported launch enforcement OR actionable warning, independent
Git identity and prerequisite question are retained below. Private deployment
examples are deliberately not reproduced. This is a source-contract intake,
not an operator amendment or a grant to use the original speculative
credential-extraction mechanism.

## Plan

### Phase 1 - Define identity resolution

- [ ] Specify repository identity inputs, normalized repository keys,
  configuration precedence, and explicit no-binding behavior.
- [ ] Separate identity selection from credential retrieval and command
  execution so providers remain replaceable and testable.
- [ ] Return structured provenance and errors without returning credentials.

- [ ] **P69-C:** Specify the account-to-auth-provider abstraction for #69's
  deferred Phase 2 non-CLI identity scope. Preserve the unresolved alternative
  "an `accounts.yaml` and/or moving credential ownership into `agent-vault`";
  do not force exclusive-or or choose placement for its existing owners.
  Keep agent-worktrees responsible for repository-to-account selection and
  the trust/Vault layer responsible for account-to-credential ownership, with
  no duplicate credential owner. This contract row alone is not delivery;
  this effort's Phase 1 does not reopen #69's credited source Phase 1.

### Phase 2 - Add scoped command execution

- [ ] **AA4199-P:** Correct the actual ordinary AND pinned push consumers so
  their authentication decisions do not depend on a cached observation of
  the global active account. Preserve legitimate same-account credential-
  helper, scope and rotation behavior; do not blindly force a token, mutate
  global selection or persist credentials. This is the distinct push
  check/use race, not #5791's broader missing/rejected-credential cases.
- [ ] **AA4199-LV:** At the actual ordinary and pinned push-consumer decision,
  use a synthetic controlled account-selection change between observation
  and authentication use. Verify no cached-global-state dependence while
  retaining the legitimate same-account helper/scope/rotation path. A safe
  sibling wrapper or #5791 coverage is not proof; do not use live accounts,
  authentication or network pushes.

- [ ] Resolve the intended account before launching a repository-facing
  command and inject credentials only into that child process.
- [ ] Reject mismatched or unavailable identities before mutation.
- [ ] Preserve interactive fallback only when explicitly requested by a human,
  never as an agent-side success-shaped fallback.

- [ ] **P69-I:** Implement and integrate the selected account-to-provider and
  provider-to-credential resolution so #69's deferred non-CLI identity scope
  fits the existing repository/account model. Retain the source-named
  Entra/GCM for Azure DevOps and Gitea PAT examples, not a new exhaustive
  support floor. The account-aware contract and scoped-command path must
  consume provider resolution while preserving repository-to-account
  selection and a single credential owner. Existing Worktrees,
  agent-vault/trust and venue-parity owners retain source, credential and
  transport/launch-auth custody. This unchecked Plan row appoints no executor
  and authorizes no credential operation, provider write or ownership transfer.

#### Direct retained #5791 intended-account execution

**Agent-recommended tracking amendment.** Phase 2 is the single canonical
home for this finite original and inherited #330/#2755 family. The Phase 1
identity contract and Phase 4 recovery work remain interfaces. Preserve
the delivered #8921 explicit missing-token correction and #3050 short-name
lookup; neither is a fresh missing implementation or whole-family proof.

- [ ] **P5791-M:** Retain explicit repository-override OR applicable
  account-map mint-failure refusal BEFORE arbitrary gh execution AND before
  Git or PR/fork entrypoints can substitute ambient authentication, with
  actionable selected-account identity-unavailable diagnostics. Retain the
  bounded repair's 60-second account-token lookup and no inherited-token/
  global-account substitution across those entrypoints. Preserve successful
  process-local injection and ordinary unconfigured/derived-owner ambient
  behavior; an organization-name heuristic is not an explicit account pin.
  Credit #8921's delivered no-launch correction rather than repeating the
  obsolete assertion that explicit pins still fall through.
- [ ] **P5791-S:** Retain EVERY inherited #330 stale/rejected-credential
  obligation: recover ONLY within the SAME intended identity OR refuse with
  an actionable error naming the account needing reauthentication, without
  secret exposure or ambient retry. Exercise stale-token/401 PR-provider
  execution alongside missing-token dispatcher refusal; preserve the fixed
  active-account path, successful scoped injection and derived/unconfigured
  controls. A one-attempt HTTP-401 assertion alone does not establish the
  named-account diagnostic.
- [ ] **P5791-R:** Retain EVERY inherited #2755 obligation: registered short
  name and canonical qualified slug select the SAME exact entry/account
  with explicit-repository override, applicable account map, then supported
  derived/unconfigured precedence. Never borrow a sibling override.
  Distinguish missing/ambiguous canonical binding, unavailable mint and
  rejected scoped credentials with honest non-secret results and aligned
  cross-repository guidance; correct lookup is not credential validity.
- [ ] **P5791-C:** Preserve the complete actual-dispatcher compatibility
  matrix: short/qualified equality, exact-repository versus sibling override,
  genuine bare-owner compatibility, non-GitHub registration WITH/WITHOUT its
  supported explicit override, unknown/ambiguous binding, ambient-present
  mint failure and successful scoped execution. Keep no-global-auth-switch,
  operation identity distinct from Copilot inference identity and the
  separate opt-in idle-machine/explicit-risk inference correction intact.
  Retain every original/inherited checkbox, marker, comment and existing
  ownership/activation condition; no real provider write, new provider
  abstraction or native/all-host programme is required by this contract.

#### Local Validation - #5791

- [ ] **V5791-M:** Use synthetic explicit override AND account-map mint
  failures with ambient reader credentials present to prove no gh subprocess
  and no Git or PR/fork operation using replacement ambient credentials,
  with an actionable identity-unavailable result at each entrypoint.
  Preserve the existing 60-second token-lookup bound and pair the actual
  scoped-gh, Git and PR/fork controls with successful scoped injection,
  same-active-account and derived/unconfigured behavior. Credit the
  delivered repair rather than requesting another implementation.
- [ ] **V5791-S:** Resolve the actual PR-provider stale/rejected-token/401
  path: SAME intended-identity recovery OR named-account actionable refusal,
  no ambient substitution and no secrets. Preserve missing-token dispatcher
  controls and distinguish existing one-attempt proof from the remaining
  diagnostic qualification.
- [ ] **V5791-R:** Resolve every exact-target, precedence and compatibility
  case in P5791-R/C through the supported dispatcher, including genuine
  bare-owner and non-GitHub WITH/WITHOUT override cases. Unknown/ambiguous
  binding, unavailable mint and rejected credentials remain distinct
  non-secret diagnostics; no live provider write is needed.
- [ ] **V5791-C:** Preserve process-local injection, unchanged machine-global
  authentication, all original/inherited controls and the separate inference
  identity capability. Keep source/release/installed evidence distinct,
  credit #8921/#3050 and retain all actual owners, claims, activation and
  temporary-token gates without weakening #810's separate alternative.

#### Local Validation - #69

- [ ] **V69:** Validate actual P69-I account-to-provider-to-credential resolver
  behavior and its integration after the P69-C contract/placement decision
  clears its existing owning review. Use synthetic, non-secret fixtures for
  the original non-CLI scope, distinguishing account selection, provider
  resolution, credential ownership and consumption. Preserve the source-named
  examples without an exhaustive provider-support floor; prove no duplicate
  owner, secret return/leak, ambient/global-account substitution or regression
  of the still-applicable credited Phase 1 behavior. Honor the current explicit
  repository/owner-map/default/none policy above, not historical namespace-as-
  login inference; retain explicit non-GitHub overrides. Contract prose alone
  is insufficient.
  This intake authorizes no real credentials, auth ceremony, provider write,
  native experiment or launch-auth change.

### Phase 3 - Adopt across repository workflows

- [ ] Use the shared contract for tracker, pull-request, repository API, and
  hosted-workspace operations that currently depend on ambient identity.
- [ ] Keep account-scoped resources explicit when no repository key exists.
- [ ] Emit point-of-action guidance that names the target and selected account
  without exposing tokens.

### Phase 4 - Harden concurrency and recovery

- [ ] Prove simultaneous operations for different accounts do not mutate or
  observe one another's authentication state.
- [ ] Add diagnostics for missing bindings, expired credentials, provider
  failures, and target/account mismatches.
- [ ] Document migration from ambient selection and a reversible rollback path.

#### Direct retained #12466 provider-appropriate update guidance

**Agent-recommended tracking amendment.** This is the finite update failure
diagnostic, not another identity resolver or a replacement for #5791/#810.

- [ ] **P12466:** Select recovery guidance from the actual remote/provider
  and authentication policy. Preserve valid GitHub and GitHub Enterprise
  guidance, but do not unconditionally prescribe `gh auth login` for an
  unrelated HTTPS provider. Distinguish probe timeout/unavailability from
  credential rejection; unknown providers receive a neutral diagnostic
  without an authentication ceremony. Preserve configured probe bounds,
  non-secret diagnostics and the original operation/update outcome.

#### Local Validation - #12466

- [ ] **V12466:** Through the actual update-authentication guidance entry,
  use synthetic GitHub, GitHub Enterprise, unrelated HTTPS and unknown
  provider cases, with rejected credentials, timeout and unavailable-probe
  controls. Prove provider/policy-appropriate advice without changing the
  original status/update result or configured timeout. No real login, token
  change, provider write or installed update is needed for this finite check.

### Phase 5 - Reconcile deferred backlog

- [ ] Accept account-scoping candidates only through
  [`migration-intake`](../migration-intake/README.md)'s deduplication and
  ownership gate.
- [ ] Revalidate accepted technical scope against the current identity/scoped-
  execution contract; return obsolete or unsafe candidates for explicit
  disposition.
- [ ] Place each accepted public tracker item in exactly one existing phase,
  extending this plan before implementation when necessary.
- [ ] PR-guidance overlays and relationship metadata: support review-guidance
  overlays and cross-PR relationship metadata once live caller-identity and
  network resolution (Phase 1-2 of this plan) exist to attribute them
  correctly.
- [ ] Keep examples synthetic and repository-neutral.

### Proposed finite #4756 durable fork-consent and publish lane

This is the complete original and refined consent/publish contract, not
additional #5791 credential-dispatch coverage. Credit the existing durable
atomic catalog, account isolation, actual/displayed-owner separation,
automatic remote repair and visible persistence errors at their actual scope.
Their presence does not establish missing-target reauthorization.

- [ ] **FC4756-P1:** Persist the confirmed fork owner/repository and approved
  remote per actual operator/account plus normalized repository in private
  user/machine state, never the shareable harness. Reuse that deliberate choice
  across future worktrees and new PR branches without repeated confirmation
  when the target, authority and approved mapping remain valid. Preserve
  actual-login versus displayed-owner isolation and the delivered catalog.
- [ ] **FC4756-P2:** Establish existence and identity of the recorded target
  BEFORE silent reuse can reach effectful create-or-read POST. First use,
  changed actor/target or a deleted/renamed/missing/404 confirmed target
  requires fresh deliberate confirmation; a successful POST is not proof of
  prior existence. Unknown or unavailable verification must not silently
  authorize creation or substitute another account/target. Preserve existing
  legitimate known-target reuse and scoped authority.
- [ ] **FC4756-P3:** Keep the local fork remote exactly aligned with the
  confirmed target, repairing missing/stale wiring automatically through the
  existing serialized publish path rather than re-asking merely because a
  later worktree lacks the remote. Preserve atomic catalog persistence,
  concurrency and visible persistence/remote failures; saving a catalog or
  repairing a remote does not itself satisfy reauthorization.
- [ ] **FC4756-LV1:** With synthetic account/repository catalogs and future
  worktrees/new branches, prove valid confirmed reuse, private persistence,
  actual/displayed-owner and account isolation without global selection,
  repeated confirmation or loss of the delivered baseline.
- [ ] **FC4756-LV2:** At the actual publish decision BEFORE POST, distinguish
  recorded existing from deleted/renamed/missing/404 targets, first use,
  changed actor/target, wrong authority and unavailable/uncertain verification.
  Prove required deliberate confirmation/refusal precedes creation; a mocked
  successful ensure-fork response cannot substitute for prior-existence proof.
- [ ] **FC4756-LV3:** Exercise correct/missing/stale remote mapping,
  future-worktree reuse, concurrent catalog publication, persistence and remote
  failure while preserving exact approved target and scoped authority.
  Qualify the entire P1..3 union, not only a missing-credential leaf.
  No real auth query, fork lookup/create or remote rewrite is authorized here.

### Finite Windows scoped-command argument fidelity from #810

**Agent-recommended tracking amendment.** This finite contract enters the
existing Phase 5 intake and Phase 2 command-execution scope; it does not
reopen account-resolution foundations or change the Draft status.

- [ ] **P810-1:** Preserve literal Windows PowerShell arguments through the
  actual scoped-gh entry: use a native literal-argument route OR explicit
  point-of-use quoting guidance if script invocation semantics remain.
  Preserve comma-joined `--json`/`--label`, spaces/quoting, combined
  spaced-search/comma-json arguments and the actual exit. Forwarding parsed
  `@args` cannot reconstruct comma grouping already lost by the script entry.
- [ ] **P810-2:** Preserve repository/account-scoped invocation, unchanged
  machine-global active account and synthetic public examples. Reuse existing
  command/identity owners; neither both alternatives, a new resolver, an
  optional-warning mandate nor a headless-window redesign is required.

### Bug sweep — linked open bugs (2026-09-24)

_Correlated via a facility-driven sweep of open `bug`-labeled issues against active efforts (VEI + direct review). Not yet triaged into a numbered phase — listed here as upcoming work for whoever picks this effort back up._

- [ ] **#545** agent-worktrees: <project> --version mis-routes to the bare Picker seam instead of the CLI
  - `<project> --version` mis-routing is exactly this effort's `project-addressed-not-cwd-bound` scope.

### Phase 6 - Independent Copilot inference identity from #3296

**Agent-recommended tracking amendment.** This is the complete independent
repo/machine inference-identity program, not another gh account wrapper.
It advances the Worktrees vision's
[per-repo account identity](../../../visions/plugins/agent-worktrees/README.md#per-repo-account-identity-resolution)
without changing that standing intent. The original issue's proposed
credential-extraction/log-parsing mechanism was a feasibility question, not
an accepted implementation or authority to access credentials.

- [ ] **CI3296-P1:** Resolve the intended Copilot inference identity from an
  optional exact repository `copilot_account` override, then the
  machine-level `default_copilot_account`. Use stable repository identity,
  including repositories hosted outside GitHub; never substitute an owner
  namespace, gh `account_map`, active gh account or sibling repo preference.
  Keep the existing Git/gh mapping and its process-scoped credentials
  unchanged. With no inference preference, preserve ordinary ambient
  behavior and report that it is unpinned rather than verified.
- [ ] **CI3296-P2:** Preserve the durable default-off identity-switch policy
  independently of an account preference. Compare intended versus actually
  effective inference identity through a supported source, keeping unknown,
  unavailable and verified results distinct. Automatic correction of a shared
  machine-wide identity store is permitted only under the existing opt-in
  and otherwise-idle Copilot condition; unknown concurrent-session state is
  not proof of idleness. Otherwise warn clearly without blocking launch or
  changing another session, unless the operator explicitly accepts that
  shared-session risk. Do not silently enable switching on update or adoption.
- [ ] **CI3296-P3:** Inventory and reconcile the actual supported local
  Manager/direct, Windows/POSIX, remote/venue and repository-context-change
  launch boundaries through their existing owners. Preserve exact selected
  repository/account, effective identity reporting, argument/exit behavior
  and explicit unsupported outcomes. A Windows prototype call, successful
  credential injection or gh result is not proof of effective inference at
  another boundary. Worktrees owns resolution/integration; agent-machines
  owns declarative machine-default policy and restore/reconcile behavior,
  not a second resolver or credential store.
- [ ] **CI3296-P4:** Reuse only supported authentication/account-selection
  interfaces. Resolve the original prerequisite question about selecting an
  already-cached account versus a vendor-supported login/switch interface;
  if unsupported, retain actionable mismatch guidance instead of inventing
  credential-store extraction or parsing private token formats. Preserve
  cold/stale/unavailable/expired-source distinctions and explicit
  host-Copilot versus legacy launch modes. Configured strict-source/account
  failure must refuse that credential route, never silently fall back to
  ambient or legacy credentials; this is distinct from P2's nonblocking
  warning when a shared-store correction would disturb another session.
- [ ] **CI3296-P5:** Preserve the existing trusted-container claimant's
  bounded token-resolution/native child-environment isolation scope and
  exclusions: no vendor source-parser, auth-store, expiry/refresh-schema or
  restricted-proxy redesign is received implicitly. Keep process-only
  credential lifetime, no secret disclosure/persistence and independent Git
  authentication. Credit #3344/#3498's prototype/default-off policy,
  #6415's four trusted builders and explicit strict/legacy isolation, and
  #6725's local preference editing at their reported source scope. None
  proves complete machine-policy restoration, live OAuth/inference,
  all-launcher identity or deployment. Publish owner-appropriate configuration,
  diagnostics and migration guidance, with separately authorized
  source/release/installed qualification rather than a shared-store rollout.

#### Local Validation - #3296

- [ ] **CI3296-V1:** With synthetic non-secret repository/machine settings,
  prove exact-repo override/default/no-preference precedence for GitHub and
  non-GitHub targets, alias-equivalent identity and sibling isolation.
  Exercise intended/effective mismatch, unknown/unavailable identity,
  default-off, opted-in idle, concurrent/unknown-session warning and explicit
  risk-acceptance paths. Prove ordinary launch stays nonblocking when only
  unsafe shared-store correction is refused; unchanged Git/gh identity and
  other sessions remain controls.
- [ ] **CI3296-V2:** Qualify each actually supported P3 boundary through its
  real owning launcher/configuration consumer, including machine-default
  restore/reconcile and context changes. Use controlled identity sources and
  fixture-owned state before separately authorized native/live checks.
  Preserve literal arguments, exit and independent Git authentication.
  Mark unavailable/unsupported boundaries honestly; successful Windows or
  container injection does not stand in for effective inference elsewhere.
- [ ] **CI3296-V3:** Exercise configured strict-source/account failures,
  cold/stale/expired/unavailable providers, explicit legacy selection,
  incompatible/missing vendor capability and successful scoped launch.
  Prove no strict-to-ambient/legacy fallback, shared-store surprise, secret
  leakage or second credential owner. Keep isolation, supported mechanism,
  actual effective inference, containing release and authorized installed
  evidence distinct. No credential lookup, login, account switch, model
  request, container operation or runtime mutation is authorized by this plan.

## Validation Plan

The existing machine-global-account invariants below continue to govern
Git/gh repository operations. Phase 6 separately qualifies the inference
store's already-explicit opt-in/idle-machine OR accepted-risk correction
and nonblocking-warning alternative; it does not relax operation scoping.

- [ ] **CI3296-G:** Resolve EVERY CI3296-P1..5/V1..3 clause at its actual
  supported source/consumer and separately authorized native/installed tier.
  Preserve repo-keyed inference versus Git/gh independence, machine-policy
  ownership, default-off/shared-session warning safety, strict/legacy
  compatibility, unknown versus verified identity and the bounded claimant.
  Keep all incumbent Plan/Validation states, Draft and temporary-token
  implementation restriction. Reviewed receiving is not technical completion,
  source-claim release, live authentication, deployment or authority transfer.

- [ ] **FC4756-G:** Complete EVERY FC4756-P1..3/LV1..3 clause at its original
  source/consumer tier: durable private target/account/remote consent and reuse,
  existence/reauthorization before effectful POST, and automatic exact remote
  repair with serialized persistence and honest failures. Preserve current
  source credit, owners, Draft and temporary-token implementation restriction,
  #5791 separation and independent #5904/#1043 boundaries. Reviewed planned
  receiving, any later redundant-tracking judgment and actual technical
  delivery remain separate; no new ACK or credential authority is introduced.

- [ ] **AA4199-G:** Qualify the ordinary AND pinned push-consumer correction
  with controlled selection-change and same-account helper/scope/rotation
  compatibility controls. Keep the complete narrow race separate from
  #5791 coverage and preserve current identity/credential owners. Report
  source, release and installed evidence separately; no global-account
  mutation, forced-token policy or native test is supplied by this plan.

- [ ] Parallel child processes targeting repositories with different bindings
  consistently use the intended identities.
- [ ] The machine-global active account remains unchanged before, during, and
  after each operation.
- [ ] Missing, unauthorized, and mismatched identities fail before mutation
  with actionable diagnostics and no credential disclosure.
- [ ] Repository aliases and equivalent remote URL forms resolve to one stable
  binding.
- [ ] Windows and POSIX command paths preserve equivalent scoping and exit
  semantics.
- [ ] **V810-1:** Qualify the chosen supported Windows entry/guidance with
  literal comma lists, quoted and variable controls, spaces, combined
  arguments and native exit. An available shim or post-parse forwarding
  alone is not the actual argument-matrix witness.
- [ ] **V810-2:** Resolve every direct #810 Plan clause while preserving
  intended scoped-auth/repository behavior and the unchanged global account.
  Record source, release and actual native/installed proof separately; no
  unrelated account-provider or every-host completion gate is added.

- [ ] **G5791:** Resolve EVERY P5791-M/S/R/C and V5791-M/S/R/C clause,
  inherited #330/#2755 obligation and continuing #5791 custody. Preserve
  same-intended-identity recovery OR actionable refusal, complete exact-target
  compatibility, delivered source credit, no global switch and operation/
  inference separation. Reviewed Phase 2 acceptance and the dated intake
  record do not activate this Draft effort, satisfy technical acceptance,
  authorize implementation or release source/resource custody.

- [ ] **G12466:** Resolve P12466 and V12466 at the same reviewed source
  head: provider/policy-appropriate GitHub/Enterprise and neutral unknown/
  unrelated-provider advice, honest timeout/unavailable versus rejected-
  credential classification, unchanged configured bounds and original
  operation/update result. Retain the existing owners, Draft and
  temporary-token restriction, #5791/#810 acceptance and separate source,
  release and installed evidence. Planned receiving is not a technical fix.

- [ ] **G69:** Retain P69-C, P69-I and V69 as distinct unchecked obligations.
  Reviewed Plan acceptance records the deferred work; it does not complete
  integration or behavior validation. Technical acceptance requires the
  existing owners' review of any boundary/placement change, actual resolver
  integration and V69's synthetic behavior evidence. Keep source, release and
  install/live evidence distinct where applicable. This intake does not
  activate the Draft effort, appoint an executor, move credential ownership,
  authorize provider/credential effects or release source/resource custody.

## Proposal

Establish repository-scoped identity resolution as a shared primitive, then
adopt it incrementally across commands that currently inherit ambient account
state.

## Journal

### 2026-11-20 - Complete independent inference-identity receiving proposed

Added five unchecked Phase 6 Plan, three local Validation and one actual
global Validation rows for #3296, through the existing Phase 5 intake.
The complete repo/default/ambient policy, machine restoration, actual launcher
matrix, safe shared-store warning behavior and strict/legacy source contract
remain independent of Git/gh operation identity. Existing source milestones
retain their reported scope; the trusted-container claim and its exclusions
remain with their owner. Draft, temporary coordination, original intent,
every incumbent check state and all source/resource/native duties remain.
No implementation, credential operation, inference trial or issue disposition
is performed by this planning amendment.

### 2026-11-17 - Whole durable fork-consent receiving proposed

Added three unchecked Plan, three local Validation and one actual global
Validation rows for the complete #4756 contract. Durable known-target reuse
and remote repair retain credit; prior target existence and fresh missing/
changed-target confirmation before POST remain actual technical obligations.
The historical campaign whole-OPEN recommendation is prospectively reconciled
only after complete reviewed receiving/readback and separate judgment, not
by partial #5791 coverage. Draft, temporary token, every incumbent check,
original owner/resource boundary and separate #5904/#1043 work remain.
No implementation, credential operation, provider effect or tracker change.

### 2026-11-14 - #69 account-to-provider receiving proposal

- Proposed P69-C contract and P69-I technical integration, V69 behavior
  validation and G69's separate technical acceptance. All four new rows remain
  unchecked. Coordination directly retains delivered source Phase 1 credit,
  its complete command set, literal alternatives and existing owners.
  This is planned tracking, not implementation, issue disposition, execution
  assignment or custody transfer.

### 2026-11-14 - Finite provider-appropriate update-guidance intake

- Added Phase 4 P12466, local V12466 and actual global G12466 through the
  existing Phase 5 intake, preserving the existing tracker and source
  custodians. Existing structured refusal, credential isolation and timeout
  controls are positive source credit, not proof the reported branch is fixed.
- Direct reviewed Plan delivery is documentary acceptance, not activation,
  implementation appointment, a login/test/update or a source/resource transfer.
  Draft status, the temporary-token restriction and all prior rows remain.

### 2026-11-13 - Proposed finite intended-account execution intake

- Added direct Phase 2 M/S/R/C Plan, paired local Validation, actual global
  Validation and continuing #5791 custody after the Phase 5 intake
  deduplication/ownership/current-fit assessment. The append-only migration
  ledger records this one canonical phase and existing tracker, not another
  umbrella or new issue.
- Credit #8921's explicit missing-token correction and #3050's lookup while
  retaining named-account stale-401 refusal and the full inherited #330/#2755
  compatibility matrix. The bounded repair's gh, Git and PR/fork refusal
  entrypoints and 60-second token lookup remain explicit synthetic
  acceptance, not new implementation requests. Original owners, every
  incumbent byte/check state, first unresolved identity-contract gate,
  Draft status, #810 alternatives
  and temporary-token implementation restriction remain unchanged.
- Normal reviewed Plan merge establishes documentary intake; complete current
  receiving readback and separate source judgment remain. No authentication
  query, token operation, provider write, test, implementation or rollout ran.

### 2026-10-28 - Finite Windows argument-fidelity intake

- Added direct #810 Plan and actual global Validation with continuing
  coordination, retaining native literal arguments OR explicit point-of-use
  quoting guidance. Existing scoped invocation is credit, not a fresh native
  argument-matrix result.
- Status, original Request, participants, prior rows and the temporary-token
  implementation restriction remain unchanged. Normal reviewed delivery and
  current readback precede any separate source judgment; this amendment runs
  no wrapper, authentication probe, native experiment or implementation.

### 2026-08-29 - Kickoff

- Defined the campaign boundary around repository-derived identity, scoped
  child execution, concurrent safety, and fail-loud recovery.
