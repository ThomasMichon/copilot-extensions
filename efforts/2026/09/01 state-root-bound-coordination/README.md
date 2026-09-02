# State-Root-Bound Coordination

- **Slug:** `state-root-bound-coordination`
- **Repo:** copilot-extensions
- **Branch(es):** reviewed plan PR, followed by serial implementation PRs
- **Created:** 2026-08-31
- **Status:** Done
- **Vision:** `visions/agent-fabric` - resource claims, resource leasing,
  resource accountability, and claimed-resource-not-reclaimed
- **Umbrella issue:** [#1513](https://github.com/ThomasMichon/copilot-extensions/issues/1513)
- **Sub-issues:** [#1517](https://github.com/ThomasMichon/copilot-extensions/issues/1517)
  (provider preflight);
  [#1518](https://github.com/ThomasMichon/copilot-extensions/issues/1518)
  (lease acquisition);
  [#1602](https://github.com/ThomasMichon/copilot-extensions/issues/1602)
  (claim-handoff acceptance gate, transferred)
- **Authorship:** AI-assisted; reviewed and directed by the repository owner.

## Guiding Intent

Make the resolved state root the authority for operator-specific coordination.
A stateless harness that requires an external state repository must not create
local claim-ledger entries, Git-ref leases, handoff reservations, or child
resources until its knowledge repository is bound and usable. Once bound, all
shared lease state must resolve through that private state identity rather than
the shared harness repository.

Keep providers independently installable. Optional integrations may ask
agent-worktrees for a machine-readable coordination preflight when an
agent-worktrees owner reference is present, but a provider must not require an
unrelated sibling merely to support its standalone mode.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Host worktree | Owns planning, implementation, integration, and PRs | Current agent-worktrees worktree |
| Research delegates | Inventory direct and provider-side claim producers | Read-only delegated exploration |
| Copilot reviewer | Reviews public PRs and reports non-blocking findings | GitHub pull-request review |

## Coordination

- **Topology:** one host worktree with serial plan and implementation PRs.
- **Host (owns PRs):** Host worktree.
- **Delegates:** Read-only inventory and review only; no independent edit
  branches.
- **Handoff:** The effort README remains the durable checkpoint between
  sessions.

## Context

The state-root resolver already rejects an unbound stateless harness for
effort, log, and vision writes. The Git lease resolver also redirects a bound
knowledge repository and refuses to fall back when that repository cannot be
resolved. Claim-producing paths do not yet share that prerequisite:

- `claims add` can mutate a valid worktree record without resolving the state
  root;
- `run` and automatic parent-child worktree ownership can launch or create a
  resource before discovering that coordination has no durable home;
- claim handoff offers can reserve local claims without validating the
  coordination identity;
- an explicit lease origin currently bypasses knowledge binding;
- agent-codespaces can begin provider work before its later claim or lease
  shell-outs encounter the missing binding.

Agent-containers' fleet lease is an independent provider-local admission
mechanism, not an agent-worktrees resource claim or Git-ref lease. Its
standalone behavior remains independent; agent-worktrees integration is gated
at the owner/run boundary instead.

Agent-dispatch owns inbound task claims under the delegation layer. This effort
governs outbound resource claims and leases under the ground layer; inbound
task-claim policy is outside its scope.

This effort extends agent-fabric intent with the durable coordination-identity
prerequisite that its claim and lease features previously presupposed. It is
adjacent to, but does not duplicate, `worktree-finality-and-obligations`: that
effort governs the lifecycle of claims after they exist, while this effort
governs whether a claim-producing operation is authorized to begin.

#1513 directly carries the shared readiness contract and ground-layer claim
gates in Phases 1-2. The sub-issues isolate the Git-lease and optional-provider
surfaces that can land as separate implementation slices.

## Request

Require claim- and lease-producing operations to resolve their canonical state
root before any ledger, Git-ref, subprocess, provider, or resource-creation
side effect. Reject an unbound required external state root with structured
`knowledge_binding_required` diagnostics and a bound-but-unresolved state root
with a distinct resolution diagnostic. Preserve self-hosted single-user
behavior, keep optional providers independently installable, and keep teardown
possible for resources whose ownership was acquired while the state root was
usable.

## Plan

### Phase 1 - Lock the coordination-readiness contract
- [x] Add one structured readiness result that distinguishes ready,
  `knowledge_binding_required`, and other state-root resolution failures.
- [x] Add failing tests for unbound and unresolved stateless configurations,
  bound knowledge repositories, and self-hosted repositories.
- [x] Prove rejected operations leave worktree records, handoff registries,
  Git refs, subprocesses, and provider calls untouched.

### Phase 2 - Gate agent-worktrees claim and lease producers
- [x] Gate `claims add`, including every supported kind and `--owner-ref`,
  before record mutation or cross-machine deferral.
- [x] Gate claim handoff offers before reservations are persisted.
- [x] Deferred to `#1602`: Gate claim handoff acceptance before consumer-side
  claims or resource ownership change; a rejected consumer leaves the source
  authoritative. The current runtime has no acceptance surface; the gate will
  land with that implementation rather than speculating one in advance.
- [x] Gate `run` and automatic parent-child worktree ownership before child
  launch, Git worktree creation, or reciprocal claim writes.
- [x] Gate lease acquisition before store resolution or network I/O. For a
  required external state root, an explicit origin is accepted only after
  binding succeeds and only when it matches the bound state repository.
- [x] Allow renewal and release of already-held leases when the caller supplies
  the original store origin (or a provider carries it in its existing lease
  receipt), even if current binding resolution later fails. Update remediation
  text so it never advertises an override that acquisition will reject.
- [x] Make a rejected post-creation `claims add` name the binding remediation;
  once binding is repaired, the same existing resource must be recordable
  without loss.
- [x] Keep read-only claim and lease inspection available with explicit
  readiness metadata.

### Phase 3 - Preflight optional provider integrations
- [x] Expose a stable, machine-readable agent-worktrees coordination preflight.
- [x] Make agent-codespaces invoke the preflight before provider work whenever
  an agent-worktrees owner reference participates; fail closed only for a
  definitive binding rejection and preserve standalone degradation otherwise.
- [x] Version the preflight response. Treat unknown-command, malformed,
  unversioned, or incompatible responses as an absent optional peer; reserve
  fail-closed behavior for an explicit compatible binding rejection.
- [x] Prove direct agent-containers fleet leasing remains an independent local
  provider mechanism while agent-worktrees-owned container creation is blocked
  at the run/owner boundary.

### Phase 4 - Publish and deploy
- [x] Update `docs/architecture.md` and add or extend the focused pattern
  documentation for the state-root coordination invariant and provider
  integration boundary.
- [x] Bump every changed plugin version and pass version/install contracts.
- [x] Publish, review, self-merge, deploy, and verify the installed runtime.
- [x] Close #1513 after the Validation Plan matrix below passes on an installed
  runtime.

## Validation Plan

Checked rows below are backed by focused source tests and merged CI unless they
explicitly say installed runtime. The checked `Deferred to` rows are resolved by
transfer under the effort lifecycle; they are not claims that the absent
acceptance feature was implemented.

- [x] Unbound stateless anchors and linked/system worktrees reject every
  supported `claims add` kind with nonzero structured
  `knowledge_binding_required` output.
- [x] `--owner-ref`, cross-project, and cross-machine paths cannot bypass the
  gate.
- [x] Rejected `run`, child creation, handoff offer, and lease operations
  perform no local mutation, subprocess launch, Git worktree creation, remote
  ref read, or remote ref write.
- [x] Deferred to `#1602`: Handoff acceptance by an unready consumer fails
  atomically and leaves the source authoritative.
- [x] An already-existing resource rejected before binding can be claimed
  successfully after binding is completed.
- [x] A bound knowledge repository enables claims and routes lease state
  through its configured origin and account context.
- [x] A lease acquired while the state root is usable remains renewable and
  releasable through its original explicit/carried store origin after that root
  becomes temporarily unresolvable.
- [x] A normal self-hosted repository remains backward compatible.
- [x] agent-codespaces stops before provider work on a definitive binding
  rejection and degrades safely when agent-worktrees is genuinely absent.
- [x] An agent-worktrees version older than the preflight contract degrades as
  an absent optional peer instead of blocking provider work.
- [x] A bound-but-unresolvable state root produces its distinct resolution
  diagnostic, not `knowledge_binding_required`, on the installed runtime.
- [x] Operator-initiated owner-less worktree/session creation remains available
  in an unbound harness so the operator can complete knowledge binding; only
  creation that would establish ownership or a claim is gated.
- [x] agent-containers provider-local lease behavior is unchanged.
- [x] agent-containers remains independently installable (covered by the final
  install-contract gate).
- [x] Focused suites, changed-plugin suites, lint, install-contract, generated
  payload, and version-consistency gates pass.

## Proposal

Add a reusable coordination-readiness function beside the existing state-root
resolver and expose it through a small JSON-first CLI preflight. Apply that
single policy before every agent-worktrees claim or Git-lease producer. Optional
providers consume the preflight only when they are participating in an
agent-worktrees-owned operation, preserving the suite's a-la-carte invariant.

## Journal

### 2026-08-31 - Kickoff
- Confirmed the defect on current main: `claims add` writes directly to the
  worktree ledger without resolving the required external state root.
- Mapped direct claim, run, child-worktree, handoff, Git-lease, and provider
  integration paths before opening source bodies broadly.
- Filed [#1513](https://github.com/ThomasMichon/copilot-extensions/issues/1513)
  as the public coordination token.
- Filed [#1517](https://github.com/ThomasMichon/copilot-extensions/issues/1517)
  for provider preflight and
  [#1518](https://github.com/ThomasMichon/copilot-extensions/issues/1518) for
  Git lease acquisition.
- Extended the agent-fabric vision in the proposal with the durable
  coordination-identity prerequisite and fail-open teardown boundary.
- Reconciled scope with `worktree-finality-and-obligations`: this effort owns
  pre-creation authorization; the existing effort owns post-creation lifecycle
  and finality.

### 2026-08-31 - Proposal reviewed
- Proposal PR [#1521](https://github.com/ThomasMichon/copilot-extensions/pull/1521)
  merged after review.
- Began the first serial implementation slice: the versioned readiness contract,
  direct `claims add` gating, and claim-handoff offer gating.
- Confirmed the current claim-handoff runtime has no acceptance command or API.
  Its planned readiness gate remains coupled to the future acceptance
  implementation; decline, cancel, and other teardown paths stay available.
- Review found that `--owner-ref` initially checked the provider's ambient
  project instead of the owning worktree's project. The gate now loads the
  same-machine owner's project configuration before any mutation; cross-machine
  deferral remains gated locally before returning a deferred result.
- Rebased across concurrent mainline changes and assigned monotonic
  agent-worktrees/catalog versions after the intervening releases.
- Focused coordination tests and guard tests pass. The full agent-worktrees
  portfolio repeatedly exceeded contained sub-suite budgets on this Windows
  host during unrelated Git-heavy tests; each timeout suspect passed alone.
  Required release-contract, payload-generation, version, and lint gates pass.
- Implementation PR [#1578](https://github.com/ThomasMichon/copilot-extensions/pull/1578)
  merged after CI and Copilot review. It published the v1 readiness contract,
  direct claim gate, owner-project resolution, and handoff-offer gate.
- Unified update refreshed the other registered runtimes, but this live Copilot
  process held the installed agent-worktrees payload directory open on Windows.
  The agent-worktrees payload/runtime deployment remains an explicit completion
  obligation for a successor session that can retry after the lock is released.
- Implemented the #1518 lease slice with a dedicated acquisition settings path:
  required external state is checked before store construction or network I/O,
  an explicit origin must identify the bound state repository, and the bound
  checkout continues to supply account-scoped authentication.
- Lease renew/release/inspect/list retain the maintenance settings path. Focused
  tests prove explicit and environment-carried original origins remain usable
  when current binding resolution fails, while new acquisition emits a
  versioned JSON rejection with a dedicated exit code.
- Lease PR [#1583](https://github.com/ThomasMichon/copilot-extensions/pull/1583)
  merged after CI and Copilot review.
- Implemented the #1517 provider slice across agent-codespaces and agent-bridge:
  the owning project is preflighted before new local ownership or CodeSpace
  transport, compatible binding rejection blocks with a distinct event/exit,
  and absent, old, malformed, or incompatible peers preserve standalone mode.
  Existing same-owner lease renewal remains available without a new-acquisition
  preflight.
- Provider PR [#1587](https://github.com/ThomasMichon/copilot-extensions/pull/1587)
  merged after both plugin suites passed CI; #1517 and #1518 are closed.
- Implemented the remaining direct creation gate: `run` now rejects before
  pending ownership or subprocess launch, and owner-linked worktree creation
  rejects before source preparation, directory creation, reciprocal claims, or
  Git worktree creation. Owner-less/system bootstrap creation remains available.
- Run/child gate PR [#1600](https://github.com/ThomasMichon/copilot-extensions/pull/1600)
  merged with an approval recommendation.
- Filed [#1602](https://github.com/ThomasMichon/copilot-extensions/issues/1602)
  to carry the acceptance gate with the future claim-handoff acceptance
  implementation; no acceptance surface exists in the current runtime.
- Published the reusable state-root coordination pattern and updated current
  architecture plus the à-la-carte optional-peer boundary.
- Verified agent-containers' provider-local lease path remains uncoupled from
  agent-worktrees readiness (`python tools/run-plugin-tests.py agent-containers
  -k lease`: `38` tests passed); the ownership gate applies only when an
  agent-worktrees owner/run boundary participates.

### 2026-09-01 - Completed
- Documentation PR [#1605](https://github.com/ThomasMichon/copilot-extensions/pull/1605)
  merged with an approval recommendation.
- Reloading extensions released the Windows plugin-directory lock. Unified
  update installed agent-worktrees `1.5.3-dev706`, agent-bridge
  `0.4.0-dev412`, and agent-codespaces `0.4.0-dev103`.
- Installed validation exercised the real `coordination-readiness` command, the
  installed package's unbound and bound-but-unresolved classifications, and a
  real agent-codespaces-to-agent-worktrees owner-project preflight.
- The same update reported a nonzero legacy `zz-context-injection` alias update
  after canonical payloads and relevant runtimes were current; tracked
  separately in [#1606](https://github.com/ThomasMichon/copilot-extensions/issues/1606).
- Closed #1513. Claim-handoff acceptance remains transferred to #1602.
