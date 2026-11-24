# Discoverable Command Errors

## Goal

> We need to ensure that every time there is an error from any binstub command, we capture and log it. I shouldn't have to scrape console output and paste it into another agent to ask it to diagnose; it should be easy for a subsequent agent to identify and find.

- **Slug:** `discoverable-command-errors`
- **Repo:** copilot-extensions
- **Branch(es):** independent, sequential reviewed source slices against `dev`
- **Created:** 2026-11-23
- **Status:** Draft
- **Vision:** `machine-fleet` / `convention-discoverable-diagnostic-logs`
- **Umbrella issue:** #14336; diagnostic integration remains with #8377

## Guiding Intent

Errors must survive the terminal that displayed them. Every supported binstub
entry must retain discoverable, machine-local diagnostic evidence, including
failures before the application parser or runtime starts. A subsequent agent
must be able to find the failed command and its actual error without asking
the operator to reproduce or paste console output.

The implementation must preserve existing command behavior and privacy.
Diagnostic evidence never authorizes replay, service repair or resource
cleanup. Missing completion evidence is unknown, not successful execution.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Command diagnostics driver | Contract, implementation, review and integration | Managed source worktree `nimble-gauge` |
| Portable boundary researcher | Read-only upstream generator and ingress inventory | Driver's bounded evidence assignment |
| Adopter boundary researcher | Read-only repository-tool stub inventory | Driver's bounded evidence assignment |

## Coordination

- **Topology:** sequential reviewed plan and implementation PRs; no shared edit ownership.
- **Host (owns PRs):** Command diagnostics driver.
- **Delegates:** researchers return cited evidence; neither edits source nor publishes.
- **Handoff:** retain this Goal, original authorization, inventory, validation
  gates, open PRs and existing owner boundaries. A merged slice is not completion.

## Context

The existing [diagnostic logging plan](../linux-role-fleet/logging-conventions.md)
and #8377 own convention-based discovery, attribution and native capture
integration. #8370 owns installer-attempt diagnostics; #6812 owns progress
presentation. #8375 owns launch-attempt intent and outcome correlation.
This effort closes the command-entry gap without replacing those authorities
or claiming their unrelated acceptance gates.

The [command evidence contract](command-contract.md) specifies proposed failure
coverage, privacy, compatibility, bounded work and subsequent-session discovery.
The [source inventory](source-inventory.md) identifies current portable producers,
existing adapters, adopter integration and remaining declaration checks.

The initial read-only audit found real nonzero launches, unresolved acquisition
effects, generic error-only observations and timestamp-poor diagnostics.
Those are motivating failure classes, not an asserted common cause or
permission to retry any retained operation.

## Request

The current operator request is preserved verbatim in Goal. It assigns automatic
capture and subsequent-agent discovery, not another console-scraping procedure
or a repair of each error observed in the preceding audit.

## Scope Approvals

- The 2026-11-23 operator request above assigns durable binstub error capture
  and discoverability, including the required shared implementation and adopter
  integrations through their existing repository review gates.
- This is not permission to stop/restart shared services, change system-wide
  settings, collect credentials/private prompts, or replay failed operations.
  Running acceptance and rollout retain their existing effect gates.
- Existing diagnostic, bootstrap, interpreter, process-lifetime, command-routing
  and installation owners remain responsible for their resources and contracts.

## Plan

The realization below is **agent-recommended**, subject to the reviewed plan
gate before executable changes.

### Phase 1 - Reconcile coverage and review the contract

- [x] Dedupe public tracking and claim the command-boundary slice (#14336).
- [ ] Inventory every supported generated/manual binstub family and its owner,
  including payload, project, application and adopter-tool entries.
- [x] Define pre-runtime, parser, application, native-child and structured
  error coverage; retain incomplete/unknown outcomes explicitly.
- [ ] Review the finite inventory, privacy/stream/lifetime contract and
  validation matrix before implementing.

### Phase 2 - Extend existing evidence and entry owners

- [ ] Implement a common durable diagnostic contract through existing logging,
  bootstrap and generator owners, with no new collector daemon.
- [ ] Capture actual error context and the primary result before terminal
  disappearance; retain correlation across nested command boundaries.
- [ ] Cover every inventoried entry family, including failures before Python,
  and errors reported without a nonzero process exit.
- [ ] Preserve TTY/stdin semantics, streaming, clean protocol stdout,
  argument/exit fidelity, cancellation and native process containment.
- [ ] Expose bounded, read-only discovery/recent-error inspection that works
  from another session and makes missing/degraded evidence explicit.
- [ ] Compose existing diagnostic retention and protected/incomplete-attempt
  custody; do not expire operational records through a log policy.

### Phase 3 - Qualify, publish and integrate

- [ ] Validate the complete boundary matrix on native Windows and POSIX,
  including fresh/missing runtime prerequisites and subsequent-session reads.
- [ ] Land source through current-head review and required CI; verify the
  containing release separately.
- [ ] Integrate adopter-owned stubs through their normal reviewed generators.
- [ ] Qualify authorized installed command failures and discovery on the
  supported environments without disturbing existing sessions/services.
- [ ] Reconcile every Plan and Validation item, then archive through review.

## Validation Plan

- [ ] Bootstrap/interpreter resolution, malformed arguments, caught and uncaught
  application failures, nonzero native exits and error-level/zero-exit reports.
- [ ] Exact command result, stderr/stdout streaming, JSON/IPC cleanliness,
  Unicode, large output, concurrent invocations and nested correlation.
- [ ] Interactive TTY and stdin preservation; cancellation, retained descendants,
  Windows Job cleanup and existing headless/lifetime guarantees.
- [ ] No credential, environment, stdin or private-prompt capture; private
  permissions, unsafe-path refusal and bounded sanitized diagnostic context.
- [ ] Unwritable/missing/full storage, interrupted writes and missing completion:
  preserve the original error and explicitly report unavailable evidence.
- [ ] Bounded discovery from a fresh agent/session, without remembered paths,
  daemon provisioning, mutation or access to the prior console.
- [ ] Generator conformance covers the complete inventory, with an explicit
  disposition for every source rather than a sample-only success claim.
- [ ] Required unit/contract checks, relevant native qualification, reviewed
  source, actual release and separately authorized installed readback.

## Proposal

The [proposed command contract](command-contract.md) preserves existing command
and process owners. The [finite source inventory](source-inventory.md) is mapped
through shared producers, with the remaining native/Node declaration check
explicitly open. No executable change or live rollout is claimed.

## Journal

### 2026-11-23 - Goal capture

- Captured the operator's complete current objective without private identifiers.
- Verified compatible target-owned effort adoption and resolved the source
  worktree through the existing project lifecycle.
- Started two non-overlapping read-only evidence assignments for portable
  binstub owners and adopter-owned tool stubs.
