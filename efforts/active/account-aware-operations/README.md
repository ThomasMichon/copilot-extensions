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

## Proposal

Establish repository-scoped identity resolution as a shared primitive, then
adopt it incrementally across commands that currently inherit ambient account
state.

## Journal

### 2026-08-29 - Kickoff

- Defined the campaign boundary around repository-derived identity, scoped
  child execution, concurrent safety, and fail-loud recovery.
