# Maintenance-Safe Module Opt-In

- **Slug:** `maintenance-safe-module-optin`
- **Repo:** copilot-extensions
- **Branch(es):** sequential proposal, implementation and completion PRs against `dev`
- **Created:** 2026-10-18
- **Status:** Draft
- **Vision:** installer `legible-and-consent-driven`; machine-fleet existing service authorities
- **Umbrella issue:** #7213

## Guiding Intent

Allow an adopter to deliberately include individually trusted repo-local modules
in the existing maintenance-safe restore without weakening its default-off
boundary or treating arbitrary commands as inherently safe.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Implementation owner | Proposal, implementation, validation and PR stewardship | #7213 |

## Coordination

- **Topology:** One writer, sequential PRs against `dev`.
- **Host (owns PRs):** Implementation owner.
- **Delegates:** None.
- **Handoff:** Preserve this effort and #7213 if a concrete blocker prevents completion.

## Context

Resources already declare an optional `maintenance_safe` boolean. Modules have
no corresponding opt-in, so blanket maintenance-safe restore skips every module.
The current implementation explicitly anticipates a future per-module opt-in.
This closes a narrow consent-controlled reconciliation delta, consistent with
the installer vision's `legible-and-consent-driven` behavior and the
machine-fleet vision's existing reconciliation authority. No new service,
scheduler, dependency or authority is introduced.

The patterns' right-sized surface, cross-platform parity and fail-loud principles
apply. Existing resource semantics and trusted source admission remain unchanged.

## Request

Public-safe capture of the requested capability:

> Add deliberate validated per-module `maintenance_safe: true` support to existing
> `restore --maintenance-safe`. Omitted/false and malformed flags must NOT gain
> unattended execution. Existing resource behavior, normal restore, explicit
> selection semantics, dry-run guarantees, platform/machine gates, environment
> isolation, failure propagation, conflict handling, and trusted source boundaries
> must remain intact. No new scheduler, daemon, arbitrary implicit opt-in, or
> automatic opt-in of existing modules.

This is declaration-controlled selected trusted code, not a claim that arbitrary
scripts are inherently safe. Runtime rollout and adopter configuration changes
are outside this implementation objective.

## Plan

### Phase 1 — Reviewed contract
- [ ] Land this proposal through the normal PR review gate before implementation.

### Phase 2 — Implementation
- [ ] Validate the optional module flag as a strict boolean; explicit null and
  other nonbooleans are errors.
- [ ] Execute only opted-in applicable modules during blanket maintenance-safe
  restore, reusing existing execution and dry-run paths; preserve explicit selection.
- [ ] Update owning schema/documentation/skill surfaces and add a plugin changefile.
- [ ] Add focused regression coverage and land the implementation through review.

### Phase 3 — Completion
- [ ] Record merged implementation and validation outcomes; close the implementation
  issue and effort, retaining automatic promotion and authorized rollout as
  separate release/adopter responsibilities.

## Validation Plan

- [ ] Cover omitted, false, true, null and other malformed module flags.
- [ ] Cover normal restore, dry-run with/without preview support, mixed safe/unsafe
  modules and explicit selection without executing unselected commands.
- [ ] Preserve machine/platform gates, conflicts, failure propagation and
  invocation/environment behavior with focused and full agent-machines suites.
- [ ] Stage new tests before contained execution; run the documented bounded
  runner, relevant lint, documentation guards and install-contract gate.
- [ ] Verify generic public artifacts, passing required checks and real Copilot
  review before each merge. Do not claim release or rollout from a `dev` merge.

## Proposal

The flag is optional and defaults off. Only the actual boolean `true` admits a
module to blanket maintenance-safe execution. Parsing rejects every explicitly
declared nonboolean. The existing executor retains command construction, source,
machine/platform, dry-run, timeout and error behavior. Explicitly named modules
retain the operator-directed exception. No existing module gains an opt-in.

## Journal

### 2026-10-18 — Proposal
- Claimed #7213; inspected module exclusion and resource flag prior art.
- Reconciled consent-driven behavior and existing reconciliation authority.
- Implementation has not started; proposal review is the next gate.
