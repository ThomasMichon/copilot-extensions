# Bridge Derived Execution Identity

- **Slug:** `bridge-derived-execution-identity`
- **Repo:** copilot-extensions
- **Branch(es):** Independent plan, implementation and completion PRs targeting `dev`
- **Created:** 2026-10-16
- **Status:** Draft
- **Vision:** [Agent Fabric](../../../visions/agent-fabric/README.md), “Derive, don't duplicate”; standalone graceful composition
- **Umbrella issue:** #7069
- **Sub-issues:** None

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
- [ ] Claim the deduplicated issue and land this plan-only PR before implementation.
- [ ] Trace existing configuration, invocation and execution-space authority
  helpers; choose the smallest supported derivation contract.

### Phase 2 — Derived identity and propagation
- [ ] Capture originating source context before daemon detach/cwd changes;
  recompute and validate against authoritative project/machine configuration.
- [ ] Carry one resolved execution identity through every nested resolver caller.
- [ ] Support start, restart and adoption from a source worktree without manually
  writing `local_execution_space`.
- [ ] Preserve standalone bridge operation without requiring agent-worktrees.
  Reject missing/ambiguous bindings explicitly when authority cannot be derived.
- [ ] Preserve native/guest and separately registered same-platform spaces;
  never grant ownership from hostname, display alias, SSH profile or unverified
  ambient environment.
- [ ] Define legacy selector compatibility without overriding contradictory
  authority; update the directly affected configuration documentation and changefile.

### Phase 3 — Validate, land and close
- [ ] Complete targeted then required contained plugin gates, lint and install-contract validation.
- [ ] Complete REVIEW.md self-check, documentation-impact review and public privacy scan.
- [ ] Land the implementation through normal review/check/role gates; re-read this
  effort, resolve its checklists and land the Done/archive delta.
- [ ] Release effort focus and finalize the source worktree; report release status
  without installing, updating or restarting any shared runtime.

## Validation Plan

- [ ] Regression for the original discovered-agent `_find_covering_agent`
  startup path, exercising the nested resolver.
- [ ] Real source-worktree → authoritative identity → daemon context/config →
  nested resolver contract with no manual bridge selector.
- [ ] Native/guest and same-platform independent spaces remain distinct; missing,
  ambiguous, platform-mismatched and contradictory bindings fail closed.
- [ ] Standalone no-sibling and deliberate legacy-selector compatibility cases.
- [ ] Fresh isolated configuration/startup check without touching a shared daemon.
- [ ] Targeted tests followed by required contained plugin suites, ruff F/E9 and
  `check-install-contract`; stage new files before wrapper snapshots.
- [ ] Record exercised validation tiers and explicit reasons for unavailable
  clean-room/external lanes; no deployment implied by unit success.
- [ ] _(agent-recommended)_ Re-read changed configuration docs and inspect every
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
