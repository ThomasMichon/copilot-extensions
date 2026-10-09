# Machine Transport Convergence

- **Slug:** `machine-transport-convergence`
- **Repo:** copilot-extensions
- **Branch(es):** Independent, serially landed per-slice PRs against `dev`
- **Created:** 2026-10-08
- **Status:** Active
- **Vision:** `visions/agent-fabric/README.md`, derive-don't-duplicate and graceful composition
- **Sub-issues:** #5737 · #5738 · #5740 · #5741

## Guiding Intent

Every named-machine operation should share identity matching and transport
selection rather than independently interpreting aliases, casing, or locality.
Preserve provider-specific launch protocols and standalone installation.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Campaign owner | Plan, integration, validation, and serial PR stewardship | Managed upstream worktrees |
| Evidence delegates | Read-only consumer-boundary assessments | Bounded task calls; reports integrated by campaign owner |

## Coordination

- **Topology:** Reviewed plan first, then independent per-slice PRs.
- **Host (owns PRs):** Campaign owner.
- **Delegates:** Evidence only; no shared-worktree git or edit authority.
- **Handoff:** Resume the first unresolved item from this canonical README.
- **Concurrent work:** #5689 introduces shared host/WSL execution identity for
  agent-machines and agent-worktrees. Do not supersede its still-open change.
  It must land before the Dispatch and agent-machines integration slices; it
  must not wait for those slices or for the independent Bridge/publication
  repair. The original PR owner retains implementation and merge stewardship.
  The negotiated direction retires the duplicate `machine-identity` library:
  shared matching, locality and host/WSL qualification live in `machine-transport`,
  with consumer-specific discovery and diagnostics in thin adapters.

## Context

Phase 1 landed in #5687 after #5634: `libs/machine-transport` now owns normalized
registry entries, alias matching, canonical local-machine checks, SSH target
selection, and generic POSIX login-shell wrapping for agent-worktrees and
worktree-manager. Consumer-specific topology path discovery remains outside it.

Initial read-only evidence identifies these boundaries:

- Bridge duplicates topology parsing and identity lookup. Its ACP argv,
  breadcrumbs, forwarding, authentication, and Windows quoting remain local.
- Dispatch lowercases a requested machine into an SSH target; configured machine
  aliases and remote shell selection need the shared resolution boundary.
  Endpoint discovery, forwarding, health checks, and retries remain local.
- Codespaces' `_ssh_namespace()` assembles options for its dynamic GitHub venue
  provider. It does not resolve a `machines.yaml` machine and must not be forced
  into that registry.
- Agent-machines overlaps canonical key/alias/hostname/display-name matching,
  but also returns package-gate identities and diagnostics. #5689 is already
  changing that contract; the two shared libraries must not become duplicate
  identity authorities.

## Request

Public-safe operator request:

> "We'll want to make sure that across all agent-* commands which perform
> operations on machines, we uniformly use the same flow... we can produce a
> reusable resolver module that everything can share, and individually validate
> it... One place to resolve aliases, casing, etc., used everywhere."

Continuation:

> "Since we have context, let's tackle the broader objective"

Dispatch integration decision:

> "Use optional shared resolver CLI with standalone alias compatibility"

The operator selected a read-only agent-worktrees transport-resolution command
backed by the shared library. Dispatch consumes it when available and preserves
raw SSH-alias operation only when the optional provider is unavailable.

Publication-blocker decision:

> "Fix the tooling blocker, then continue"

## Plan

### Phase 1 - Review the campaign boundary
- [x] Land this proposal through automated review before implementation.
- [x] Coordinate with #5689 on shared identity ownership and avoid overlapping edits.

### Phase 2 - Named-machine consumers
- [ ] Repair the owned-PR publication blocker #5796, then resume the original
  consumer work. _(operator-approved prerequisite)_
- [ ] Resolve #5737 by migrating Bridge registry and named-machine resolution;
  preserve ACP launch construction and compatibility metadata.
- [ ] Resolve #5738 by migrating Dispatch named-machine SSH resolution; preserve
  endpoint/tunnel behavior and explicit diagnostics.
- [ ] Resolve #5741 by integrating the reviewed host/WSL identity contract with
  transport identity, or record a justified distinct contract.

### Phase 3 - Provider boundary and completion
- [x] Resolve #5740 with a documented dynamic-venue boundary, or migrate only
  independently proven named-machine overlap.
- [ ] Inventory other agent-* named-machine operation paths for remaining
  duplicated identity/transport resolution. _(agent-recommended verification
  of the requested all-consumer objective)_
- [ ] Land any remaining proven overlap, or transfer a specifically bounded
  item to a named tracked objective with explicit rationale.
- [ ] Complete deployment evidence, journal outcomes, and mark the effort Done
  only after every implementation and validation item is resolved.

## Validation Plan

- [ ] Per migrated consumer, run existing relevant contracts before edits and
  targeted regressions plus the full affected suite after edits.
- [ ] Prove key/alias/hostname/case matching, known-local direct operation,
  missing/invalid registry diagnostics, ambiguous identities, and remote shell
  selection without changing provider namespaces or ACP semantics.
- [ ] Run shared-library regressions for changed shared contracts; validate
  standalone dependency staging, release materialization, and pip-only fallback
  paths on both supported platforms.
- [ ] Exercise a lightweight real subprocess/SSH-command construction contract
  for each changed transport path, not only mocked resolver returns.
- [ ] Run applicable fresh-install clean-room and live venue checks when
  available; record explicit reasons for any unavailable tier.
- [ ] Pass touched-code lint, install-contract and relevant repository guards;
  add changefiles, complete documentation-impact review, and satisfy automated
  review plus required CI before each merge.
- [ ] Verify merged provider state, refresh installed consumers through the
  unified update flow after promotion, reconcile harness projections, and
  finalize only settled worktrees.

## Proposal

Consolidate identity and transport decisions, not unrelated launch protocols.
Keep path discovery and caller-specific errors at the consumer edge. Preserve
existing public interfaces through thin adapters where needed. Reject a second
identity authority: reconcile `machine-transport` with #5689's
`machine-identity` before changing the overlapping consumer.

## Journal

### 2026-10-08 - Planning
- Resumed the Phase 2 objective and claimed #5737, #5738, #5740, and #5741.
- Integrated four bounded, read-only consumer assessments.
- Found concurrent #5689; recorded the dependency before implementation.
- Operator confirmed the effort slug `machine-transport-convergence`.

### 2026-10-08 - Reviewed proposal and Bridge implementation
- Proposal #5753 merged into `dev`; cooperation with #5689 was recorded before
  implementation. Its machine-identity, agent-machines and agent-worktrees
  implementation paths remain untouched.
- Prepared the #5737 Bridge slice for campaign-owner integration/publication,
  not issue completion. Shared raw-data parsing preserves Bridge's missing
  SSH-alias/key and shell/bash defaults, unnamed environments and extended
  metadata. Shared matching adds case-insensitive machine alias, hostname and
  display-name resolution; ambiguous identities/SSH aliases now fail explicitly.
  SSH-alias environment binding and conflicts, platform-specific local
  loopback, default environment preference and ACP launch shapes remain
  consumer-owned.
- Both installer platforms refresh the shared dependency. Windows standalone
  snapshots stage it and rewrite its nested login-shell pointer; an actual
  isolated PowerShell snapshot test asserts that contract. Bridge requires `uv`
  on both platforms and has no pip-only fallback to modify.
- Focused validation:
  `python tools/run-plugin-tests.py agent-bridge -k 'topology or bare_name_resolution or test_agent_registry or test_transport or machine_transport_convergence or install_stale_cache_guard or installer_powershell51' --plugin-timeout 900 --subsuite-timeout 300`
  — **356 passed, 1 skipped**.
- Full validation:
  `python tools/run-plugin-tests.py agent-bridge --plugin-timeout 2400 --subsuite-timeout 600`
  — **3429 passed, 66 skipped**, all eight contained sub-suites green.
  The first attempt's 300-second file-group budget expired on Windows;
  the same group completed in 365.61 seconds within the bounded retry.
- Shared source validation:
  `.test-venvs/win32/agent-bridge/Scripts/python.exe -m pytest libs/machine-transport/tests -q --basetemp=.test-state/machine-transport --timeout=30`
  with `PYTHONPATH` pointing to this checkout's `libs/machine-transport/src`
  and `libs/remote-login-shell/src` — **73 passed**. The initial missing
  scratch-parent setup error was corrected before this successful run.
- Touched Python `ruff check --select F,E9`, `check-install-contract.py`
  (**13 plugins**), `check-module-size.py`, `check-vendored-libs-sync.py`
  (**5 shared libraries**) and staged/unstaged `git diff --check` passed.
  A patch changefile covers Bridge and both existing shared-library consumers.
- Lightweight integration exercises the real resolver-to-ACP-command
  construction, including auth metadata and breadcrumb-bearing command
  equivalence. Full fresh-box clean-room and live SSH tiers were not exercised:
  no disposable target or live venue was assigned, and this slice does not
  authorize host installation/service changes.
- Documentation impact: updated the shared-library API/compatibility contract
  and Bridge's machine-configuration guide. This realizes the reviewed
  derive-don't-duplicate intent without changing the vision, installation
  independence or daemon lifecycle contract. Self-reviewed against `REVIEW.md`.
  No commit, PR, push, merge or deployment was performed; #5737 remains open
  until parent-owned publication and merge.

### 2026-10-08 - Bridge review and next-slice decision
- Automated review found that registry assembly still used key-only coverage
  checks. Coverage now reuses the same resolver, including SSH-environment
  binding, so a differently named explicit local project suppresses its
  auto-discovered duplicate under alias, hostname, display-name or SSH-alias
  spellings. Ambiguous/conflicting hosts warn rather than suppressing evidence.
- Merge-path regressions and related coverage contracts: **59 passed, 1 skipped**;
  touched-code lint and module-size gate passed.
- Operator chose the optional shared-resolver CLI for Dispatch, preserving
  standalone SSH aliases without introducing a duplicate topology configuration.
- Additional read-only inventory identified agent-ssh's WSL registry emitter as
  remaining local-machine matching overlap. Its transport-specific projection
  remains outside the shared resolver; the dtssh alias default and mesh-status
  projection require boundary checks, not indiscriminate migration.

### 2026-10-08 - Provider boundary landed; publication repair
- #5834 merged into `dev` and #5740 was explicitly closed. Codespace discovery
  and `_ssh_namespace()` are a dynamic venue/options contract, not a static
  machine resolver. The clarification preserves existing behavior without a
  redundant dependency; all provider checks and automated review passed.
- Bridge's subsequent review corrections reject ambiguous local hostnames and
  decouple parser defaults from field normalization. Shared regressions:
  **76 passed**; Bridge coverage: **60 passed, 1 skipped**.
- Supported rebase reconciliation exposed #5796: publication rejects a rebased
  owned PR despite an exact remote-head lease. The operator authorized a
  separate source repair, which remains in review as #5825. Bridge publication
  is blocked on that repair; no guard bypass or deployed-code edit was used.
- The repair's actual blocked-branch lineage verified read-only, including the
  explicit conflict continuation. Real-Git tests cover leases, lost-work
  refusals and hooks; review added raw-byte patch/message preservation and an
  opt-in exhaustive lane. Default smoke measured **6 passed in 70.81 seconds**
  on Windows. A fresh consoleless-parent observation exposed an unguarded core
  Git spawn; the corrected path completed two cycles with zero newly visible
  windows and foreground transitions. Combined changed contracts:
  **127 passed, 15 skipped**.
- Dispatch's optional resolver CLI remains unimplemented until ambiguity and
  native/WSL execution locality are reconciled with #5689. Its still-open review
  findings are not superseded by this campaign.
- Further evidence shows the mesh parser also feeds operational refresh:
  #5375 already tracks agent-ssh's self-SSH probe loop. Include that path in the
  remaining inventory rather than treating the mesh parser as read-only only.

### 2026-10-08 - Dependency order, shared identity and source repair
- The #5689 owner had paused in draft because rebasing exposed the duplicate
  identity library and they believed Phase 2 had to land first. Explicit
  prerequisite comments corrected the order before implementation resumed.
  Negotiation as **ThomasMichon (MSFT)** with **ThomasMichon (Home)** agreed
  direction (1): retire the standalone duplicate and compose the host/WSL
  execution contract with the existing `machine-transport` identity primitives.
  No separate #5741 design PR blocks #5689. Actual WSL evidence, native-host
  precedence and the distinct SSH namespace remain required.
- Remaining inventory includes Worktrees' picker/handoff helpers in
  `resolve_machine_cli.py`, which still repeat first-match identity logic, and
  agent-ssh's WSL emitter, which uses key-prefix hostname heuristics. These
  are not silently counted as completed migrations.
- #5382 was confirmed merged into `dev` as
  `d841b53f8fd882cacc6c91bccf7d36ad9cccc332`; #5375 was explicitly closed.
  Its physical-host self-probe exclusion is a landed bug fix, not proof that
  every agent-ssh identity consumer has migrated to the shared authority.
  Promotion/deployment remain separate. Its originating worktree has
  uncommitted changes and is retained, not discarded or finalized.
- #5825 review corrections fingerprint credential-bearing push destinations,
  disable replacement objects in proof and strict ancestry checks, and replace
  patch-ID authorization with per-commit three-way tree reconstruction.
  Repeated-region tests distinguish legal upstream line shifts from relocated
  edits and false already-applied matches. Default proof contracts:
  **10 passed, 16 skipped**; explicit conflict/cache contracts: **3 passed**.
  The actual blocked Bridge lineage still verifies with one conflict replay.
  The full updated matrix and current-head review remain pending.
- A later runtime slot was marked installed while its shared transport package
  had only bytecode cache. The operator authorized official recovery; the
  installer refused another live build lease. Waiting for that builder restored
  CLI execution without clearing ownership, editing a runtime or forcing a
  service restart. This does not establish overall monitor/deployment health.
- Operator priority is to settle owned #5382 and #5825 next. The former is
  confirmed merged; the latter remains the publication prerequisite.
  This effort stays Active and its remaining implementation/deployment gates
  remain open.
