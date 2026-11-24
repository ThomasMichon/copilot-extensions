# Discoverable Command Errors

## Goal

> We need to ensure that every time there is an error from any binstub command, we capture and log it. I shouldn't have to scrape console output and paste it into another agent to ask it to diagnose; it should be easy for a subsequent agent to identify and find.

- **Slug:** `discoverable-command-errors`
- **Repo:** copilot-extensions
- **Branch(es):** independent, sequential reviewed source slices against `dev`
- **Created:** 2026-11-23
- **Status:** Active
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
| Portable command implementer | Shared evidence and portable entry adapters, with tests and source PRs | Driver's separately owned source allocation after the plan merges |
| Adopter command integrator | Repository-owned generator adapters and qualification | Driver's separately owned adopter allocation |

## Coordination

- **Topology:** sequential reviewed plan and implementation PRs; no shared edit ownership.
- **Host (owns PRs):** Command diagnostics driver.
- **Delegates:** researchers return cited evidence; neither edits source nor
  publishes. After plan merge, the portable command implementer owns its separate
  source allocation and implementation PRs; the driver owns integration and
  canonical effort reconciliation.
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

The realization below is **agent-recommended**. The plan gate cleared in
#14345 before executable changes.

### Phase 1 - Reconcile coverage and review the contract

- [x] Dedupe public tracking and claim the command-boundary slice (#14336).
- [x] Inventory every supported generated/manual binstub family and its owner,
  including payload, project, application and adopter-tool entries.
- [x] Define pre-runtime, parser, application, native-child and structured
  error coverage; retain incomplete/unknown outcomes explicitly.
- [x] Review the finite inventory, privacy/stream/lifetime contract and
  validation matrix before implementing.

### Phase 2 - Extend existing evidence and entry owners

- [x] Land the shared Python evidence contract and generated entry adapter
  through existing persistence/generator owners, without a collector (#14352).
- [ ] Extend that contract to the remaining native/bootstrap and entry owners.
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

The [command contract](command-contract.md) preserves existing command and
process owners. Shared Python evidence and generated entry adaptation landed
in #14352. The [finite source inventory](source-inventory.md) still has open
native, project, legacy, application, Node, development and adopter families.
Neither universal error coverage nor a release or live rollout is claimed.

## Journal

### 2026-11-24 - Shared Python slice merged

- Merged #14352 as `db1cfeec248f` after exact-head clean review and all required
  CI passed; the reviewed source was `e10b102aecaf`.
- Delivered private retained evidence, bounded recent-error reads, explicit
  producer reporting and same-interpreter module/script entry adaptation.
  Exception values remain deliberately unretained when no producer-safe cause
  exists; unobserved final results remain unknown rather than guessed.
- Added native fork qualification: a child reported while another parent
  thread held the recorder lock, with both process reports discoverable.
- Assessed startup cost with ten interleaved pairs after cache warmup. Median
  paired additions were about 105 ms on Linux and 431 ms on Windows for a
  successful small module and absent store. Shared-host tails were noisy;
  these measurements are not a performance-budget pass or installed proof.
- The next slice is the native error-emitter contract and canonical locator
  needed by bootstrap/entry adapters and adopter integration. All remaining
  families, automatic parser/reported-error coverage, release and installed
  qualification stay open.

### 2026-11-24 - Reconcile new development entrypoints

- Added the repository-development wrappers introduced by #14371 to the
  [finite inventory](source-inventory.md), with explicit native-prerequisite,
  delegated-execution and compatibility qualification still open.
- They are repository-owned rather than Invocation-generated. The original
  manifest count is a baseline inventory, not evidence that these newer
  commands are instrumented.

### 2026-11-23 - Independent POSIX source qualification

- Built the self-contained evidence archive from pinned source `980408541534`
  with the standard library only and ran it in fixture-private state.
- Seven native controls passed: read-only discovery with absent storage; binary
  stdin/stdout/stderr and exit 23 fidelity; fresh-session exception discovery
  without the private input canary or script path; six concurrent explicit
  zero-exit reports; inherited PTY and entry PID; baseline SIGTERM result; and
  a private evidence directory with nonsymlinked log files.
- The explicit-report fixture used the archive's internal SDK namespace, not
  an installed application dependency. Automatic parser/reported-error
  integration, early native adapters and installed proof remain open.
- An initial fixture import assumed an external SDK package; its literal
  `ModuleNotFoundError` was corrected in the fixture, not worked around in
  production source.

### 2026-11-23 - Reviewed plan and implementation handoff

- Merged #14345 after current-head Copilot approval and required green checks.
- Reconciled the driver onto the reviewed plan; executable coverage, release,
  adopter integration and installed qualification remain open.
- Assigned the portable implementer a separately owned source allocation for
  shared evidence and entry adapters. The driver retains canonical effort and
  integration ownership; no shared checkout mutations are delegated.
- Implement the shared contract and generated payload entry first, then close
  the remaining inventoried native, project, application and Node families
  through finite reviewed slices. A partial slice is not universal coverage.

### 2026-11-23 - Goal capture

- Captured the operator's complete current objective without private identifiers.
- Verified compatible target-owned effort adoption and resolved the source
  worktree through the existing project lifecycle.
- Started two non-overlapping read-only evidence assignments for portable
  binstub owners and adopter-owned tool stubs.
