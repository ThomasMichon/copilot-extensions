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

## Plan

### Phase 1 - Define identity resolution

- [ ] Specify repository identity inputs, normalized repository keys,
  configuration precedence, and explicit no-binding behavior.
- [ ] Separate identity selection from credential retrieval and command
  execution so providers remain replaceable and testable.
- [ ] Return structured provenance and errors without returning credentials.

### Phase 2 - Add scoped command execution

- [ ] Resolve the intended account before launching a repository-facing
  command and inject credentials only into that child process.
- [ ] Reject mismatched or unavailable identities before mutation.
- [ ] Preserve interactive fallback only when explicitly requested by a human,
  never as an agent-side success-shaped fallback.

#### Direct retained #5791 intended-account execution

**Agent-recommended tracking amendment.** Phase 2 is the single canonical
home for this finite original and inherited #330/#2755 family. The Phase 1
identity contract and Phase 4 recovery work remain interfaces. Preserve
the delivered #8921 explicit missing-token correction and #3050 short-name
lookup; neither is a fresh missing implementation or whole-family proof.

- [ ] **P5791-M:** Retain explicit repository-override OR applicable
  account-map mint-failure refusal BEFORE arbitrary gh execution, with
  actionable selected-account identity-unavailable diagnostics and no
  inherited-token/global-account substitution. Preserve successful
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
  and an actionable identity-unavailable result. Pair those with successful
  scoped injection, same-active-account and derived/unconfigured controls.
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

## Validation Plan

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

## Proposal

Establish repository-scoped identity resolution as a shared primitive, then
adopt it incrementally across commands that currently inherit ambient account
state.

## Journal

### 2026-11-13 - Proposed finite intended-account execution intake

- Added direct Phase 2 M/S/R/C Plan, paired local Validation, actual global
  Validation and continuing #5791 custody after the Phase 5 intake
  deduplication/ownership/current-fit assessment. The append-only migration
  ledger records this one canonical phase and existing tracker, not another
  umbrella or new issue.
- Credit #8921's explicit missing-token correction and #3050's lookup while
  retaining named-account stale-401 refusal and the full inherited #330/#2755
  compatibility matrix. Original owners, every incumbent byte/check state,
  first unresolved identity-contract gate, Draft status, #810 alternatives
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
