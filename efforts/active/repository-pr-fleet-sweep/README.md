# Repository PR Fleet Sweep

- **Slug:** `repository-pr-fleet-sweep`
- **Repo:** copilot-extensions
- **Branch(es):** Independent plan and implementation PRs from one source worktree
- **Created:** 2026-10-25
- **Status:** Draft
- **Vision:** `visions/plugins/agent-worktrees/pull-requests/README.md` (provider-neutral operations and foreign-repo addressing); `visions/agent-fabric/README.md` (legible cross-machine responsibility)
- **Umbrella issue:** #8802
- **Sub-issues:** None

## Guiding Intent

Provide a simple read-only, one-shot way to find open PRs targeting a named
repository and their responsible worktrees across configured machine cells.
Compose the existing repository, provider, topology and claim authorities;
do not create another fleet registry, forge client, service or supervisor.

The complete provider-open inventory and the coverage of worktree observations
are separate facts. An open PR with no known claimant remains visible, and a
failed, excluded or unsupported cell never becomes an empty successful result.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Sweep owner | Plan, implementation, validation and PR completion | Public source handle `shiny-anvil`; issue #8802 |

## Coordination

- **Topology:** One source worktree; reviewed plan before implementation.
- **Host (owns PRs):** Sweep owner.
- **Delegates:** None assigned.
- **Handoff:** Preserve this effort's remaining Plan/Validation items and the
  public issue; another owner accepts responsibility explicitly.
- Existing PR-service, shared subscription, fleet lifecycle and claim
  settlement work remains with its existing owners. This effort neither
  takes those slices nor requires their rollout.

## Context

`agent-worktrees claims find pr --repo ...` scans the current cell's registered
projects. Its project envelope distinguishes unreadable projects/records,
but the command does not enumerate remote cells or provider-open PRs that
have no local tracking record. `fleet` owns the existing machine/environment
selection, and supported claim traversal supplies roots and transitive paths.

The GitHub provider's existing `list_open_pulls` operation currently reports
unsupported. Complete pagination must be implemented at the provider owner,
not approximated with remembered claim numbers, a fixed `gh pr list` limit
or a second client in a reporting script.

This closes an observation/reach gap in the existing visions, without changing
their intent. The relevant patterns are a-la-carte operation, one attributable
command owner, platform parity, explicit failures, economical evidence and
bounded process lifetime.

## Request

Operator request, verbatim:

> We should make sure you have simple scripts to run to compute this information at this point. Might be good to have these tools available in copilot-extensions itself, to sweep for open PRs targeting a specific repo across all user machines.

The operator confirmed the effort name `repository-pr-fleet-sweep`.

The following design and validation details are **agent-recommended**, not
additional operator requirements. They keep the implementation portable,
read-only and useful without any particular downstream report or topology.

## Plan

### Phase 1 - Reviewed scope and contract

- [x] Inspect existing local PR-claim, fleet-selection and provider-listing
  mechanisms; find and claim the public coordination issue.
- [ ] Land this proposed effort through normal review and required CI before
  implementing the command.

### Phase 2 - One-shot repository sweep

- [ ] Add one discoverable native command, provisionally `pr-sweep <repo>`,
  without changing existing local `claims find pr` behavior. Keep a standalone
  local-cell mode when no optional fleet capability is configured.
- [ ] Resolve the target repository's registered provider and scoped identity.
  Obtain a complete paginated open-PR census through the existing PR-domain
  provider boundary; preserve PR identity, creation time, author and head where
  available. Unsupported providers and incomplete reads are explicit failures,
  not successful empty inventories.
- [ ] Resolve configured machine/environment cells through existing topology.
  Apply exact include/exclude selections before any execution; enumerate skipped,
  unavailable and unsupported cells explicitly. A selected unconfigured name is
  a usage error. Keep native and WSL/POSIX stores distinct.
- [ ] Invoke supported, non-provisioning read operations using the selected
  cell's attributable command owner. Batch per-cell work rather than opening
  one SSH connection per worktree. Keep original project/root identity,
  direct candidates, transitive paths, unresolved edges and retained non-open
  PR claims distinct.
- [ ] Join claims to provider-open PRs without dropping unclaimed open PRs or
  falsely treating local tracking state as provider truth. Preserve independent
  observation timestamps and explicit partial coverage. Optional author
  filtering affects presentation/metrics, not the underlying all-author census.
- [ ] Provide compact human and versioned JSON output from the same result,
  including count/oldest information, source freshness and actionable coverage
  errors. Do not derive merge eligibility, acceptance or claim clearance.

### Phase 3 - Validate, document and complete

- [ ] Add focused provider, joining, selection and command-contract tests, plus
  real process-boundary checks for Windows/POSIX invocation and cancellation.
  Preserve unrelated owner processes and all production deadlines.
- [ ] Exercise the one-shot command in an isolated synthetic local setup and
  an already-admitted remote setup when available. Excluded/held targets receive
  no probe. Record unavailable validation lanes rather than provisioning them.
- [ ] Update the authoritative agent-worktrees CLI/claim documentation and
  discoverability pointer. Add the required plugin changefile(s), publish the
  implementation and drive review/CI to a verified merge.
- [ ] Record source/release state honestly and close the source effort.
  Runtime installation or fleet rollout needs separate explicit authorization;
  retain any outstanding adoption responsibility with a named tracked owner.

## Validation Plan

- [x] Read the public-safe request back against the operator's words, retaining
  the repository-targeted and all-machines intent.
- [ ] Provider tests cover more than one full page, a later-page failure,
  malformed data, exact repository/authority and scoped-account isolation.
  No fixed limit, partial result or unreadable registry is called complete.
- [ ] Joining tests cover unknown/open/reopened PRs, stale local states,
  duplicate claims, multiple projects, transitive original roots, cycles,
  missing records, cross-cell edges and retained non-open claims.
- [ ] Selection tests prove excludes/held cells execute nothing, invalid
  selectors fail before effects, and unsupported/offline/failed cells remain
  explicit. Local-only operation needs no fleet-manager, SSH or daemon.
- [ ] Human/JSON tests verify the same counts, oldest PR, timestamps, provenance
  and partial-coverage meaning; optional author filters preserve the complete
  all-author source inventory.
- [ ] Invoke the real CLI against synthetic process/provider fixtures and prove
  no mutation, first-use provisioning, authentication bootstrap, background
  worker, service, claim settlement or global account switch occurs.
- [ ] Run the focused suites through the repository test runner, touched-source
  lint and applicable install-contract checks. Do not enlarge required CI with
  an exhaustive fleet or external-provider matrix.
- [ ] Record native Windows and POSIX/remote checks, including bounded failure
  and descendant cleanup. Unavailable lanes remain unverified with a reason;
  mocked tests alone are not native transport qualification.
- [ ] Complete documentation-impact review, actual PR review and required CI;
  verify the implementation PR merged before declaring source completion.

## Proposal

Planning only. No implementation or runtime rollout has begun.

## Journal

### 2026-10-25 - Kickoff

Confirmed the effort name, checked existing mechanisms and current public
coordination, and reserved #8802 under the assigned public source handle.
The first PR proposes scope and validation only; implementation waits for its
reviewed merge.
