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
| Command diagnostics driver | Contract, shared publication, Node integration and delivery | Managed source worktree `nimble-gauge` |
| Portable boundary researcher | Read-only upstream generator and ingress inventory | Driver's bounded evidence assignment |
| Adopter boundary researcher | Read-only repository-tool stub inventory | Driver's bounded evidence assignment |
| Portable command implementer | Delivered shared Python/native providers; remaining work returned to driver | Prior separately owned source allocation |
| Adopter command integrator | Delivered reviewed generator integration; producer-specific gaps remain | Retained adopter allocation |

## Coordination

- **Topology:** sequential reviewed plan and implementation PRs; no shared edit ownership.
- **Host (owns PRs):** Command diagnostics driver.
- **Delegates:** researchers returned cited evidence without publication.
  Portable providers landed from their separate allocation; remaining shared
  publication and portable caller work now belongs to the driver. The adopter
  integrator retains its separate allocation. The driver owns canonical effort
  reconciliation and the parent completion gate.
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
- [x] Land Python-free native emitters and the build-time asset locator (#14393).
- [x] Land explicit native-only resource publication without a fake Python entry (#14410).
- [x] Review the bounded standalone error-path adapter contract before Node adoption (#14412).
- [ ] Adopt the shared contract in remaining native/bootstrap and entry owners.
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
- [x] Integrate the adopter-owned stubs through their reviewed generators;
  remaining producer-specific and installed qualification stays open.
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
in #14352; native providers and their asset locator landed in #14393.
Both provider slices are source-released. The native-only publisher (#14410)
and bounded archive owner (#14416) are also available on protected `main`,
verified against their effective file changes at `4fc6c993b63f`.
Their qualified automatic Python and Node callers can now proceed through
ordinary source review and publication. The standalone Node error-path
exception was reviewed in #14412; synchronous Python callback persistence is
not treated as conformant.
Bridge's native required-context entry owner landed in #14456 after native
Windows/Linux qualification, current-head review and required CI. Receiving
runtime adoption landed in #14467 and selects the same-interpreter archive
for ordinary module dispatch; other entry owners and automatic reported-error
adapters remain separate work. POSIX still transfers observation through `exec`
without a new supervising process.
The [finite source inventory](source-inventory.md) still has open
native, project, legacy, application, Node, development and adopter families.
Neither universal error coverage nor installation or live adoption is claimed.

## Journal

- [2026-11-24 - Native publication and standalone error-boundary review](journal/2026/11.24%20native-publication.md)
- [2026-11-24 - Provider delivery, release and caller adoption](journal/2026/11.24%20provider-delivery.md)
- [2026-11-23 - Goal capture, reviewed plan and source qualification](journal/2026/11.23%20goal-and-plan.md)
