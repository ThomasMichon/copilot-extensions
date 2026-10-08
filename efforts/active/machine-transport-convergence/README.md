# Machine Transport Convergence

- **Slug:** `machine-transport-convergence`
- **Repo:** copilot-extensions
- **Branch(es):** Independent, serially landed per-slice PRs against `dev`
- **Created:** 2026-10-08
- **Status:** Draft
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
  Coordinate shared-library ownership and integrate its landed contracts before
  editing the overlapping agent-machines identity path.

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

## Plan

### Phase 1 - Review the campaign boundary
- [ ] Land this proposal through automated review before implementation.
- [ ] Coordinate with #5689 on shared identity ownership and avoid overlapping edits.

### Phase 2 - Named-machine consumers
- [ ] Resolve #5737 by migrating Bridge registry and named-machine resolution;
  preserve ACP launch construction and compatibility metadata.
- [ ] Resolve #5738 by migrating Dispatch named-machine SSH resolution; preserve
  endpoint/tunnel behavior and explicit diagnostics.
- [ ] Resolve #5741 by integrating the reviewed host/WSL identity contract with
  transport identity, or record a justified distinct contract.

### Phase 3 - Provider boundary and completion
- [ ] Resolve #5740 with a documented dynamic-venue boundary, or migrate only
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
