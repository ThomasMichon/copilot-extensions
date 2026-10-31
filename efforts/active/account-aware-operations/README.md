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

## Proposal

Establish repository-scoped identity resolution as a shared primitive, then
adopt it incrementally across commands that currently inherit ambient account
state.

## Journal

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
