# Repository PR Fleet Sweep

- **Slug:** `repository-pr-fleet-sweep`
- **Repo:** copilot-extensions
- **Branch(es):** Independent plan and implementation PRs from one source worktree
- **Created:** 2026-10-25
- **Status:** Active
- **Vision:** `visions/plugins/agent-worktrees/pull-requests/README.md` (provider-neutral operations and foreign-repo addressing); `visions/agent-fabric/README.md` (legible cross-machine responsibility)
- **Umbrella issue:** #8802
- **Sub-issues:** #7039 (bounded publication-performance prerequisite)

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
- [x] Land this proposed effort through normal review and required CI before
  implementing the command.

### Phase 2 - One-shot repository sweep

- [x] Add one discoverable native command, `pr-sweep <repo>`,
  without changing existing local `claims find pr` behavior. Keep a standalone
  local-cell mode when no optional fleet capability is configured.
- [x] Resolve the target repository's registered provider and scoped identity.
  Obtain a complete paginated open-PR census through the existing PR-domain
  provider boundary; preserve PR identity, creation time, author and head where
  available. Unsupported providers and incomplete reads are explicit failures,
  not successful empty inventories.
- [x] Resolve configured machine/environment cells through existing topology.
  Apply exact include/exclude selections before any execution; enumerate skipped,
  unavailable and unsupported cells explicitly. A selected unconfigured name is
  a usage error. Keep native and WSL/POSIX stores distinct.
- [x] Invoke supported, non-provisioning read operations using the selected
  cell's attributable command owner. Batch per-cell work rather than opening
  one SSH connection per worktree. Keep original project/root identity,
  direct candidates, transitive paths, unresolved edges and retained non-open
  PR claims distinct.
- [x] Join claims to provider-open PRs without dropping unclaimed open PRs or
  falsely treating local tracking state as provider truth. Preserve independent
  observation timestamps and explicit partial coverage. Optional author
  filtering affects presentation/metrics, not the underlying all-author census.
- [x] Provide compact human and versioned JSON output from the same result,
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
- [ ] Resolve the measured publication-performance blocker under #7039 without
  bypassing a check or increasing the30-second deadline. Preserve complete
  published-object novelty and all existing size/path/type/refusal contracts;
  verify native publication, not just a subprocess timeout.
- [ ] Record source/release state honestly and close the source effort.
  Runtime installation or fleet rollout needs separate explicit authorization;
  retain any outstanding adoption responsibility with a named tracked owner.

## Validation Plan

- [x] Read the public-safe request back against the operator's words, retaining
  the repository-targeted and all-machines intent.
- [x] Provider tests cover more than one full page, a later-page failure,
  malformed data, exact repository/authority and scoped-account isolation.
  No fixed limit, partial result or unreadable registry is called complete.
- [x] Joining tests cover unknown/open/reopened PRs, stale local states,
  duplicate claims, multiple projects, transitive original roots, cycles,
  missing records, cross-cell edges and retained non-open claims.
- [x] Selection tests prove excludes/held cells execute nothing, invalid
  selectors fail before effects, and unsupported/offline/failed cells remain
  explicit. Local-only operation needs no fleet-manager, SSH or daemon.
- [x] Human/JSON tests verify the same counts, oldest PR, timestamps, provenance
  and partial-coverage meaning; optional author filters preserve the complete
  all-author source inventory.
- [x] Invoke the real CLI against synthetic process/provider fixtures and prove
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

The proposal merged in #8807 after exact-head Copilot approval with no findings
and successful required CI. Implementation is authorized within this reviewed
scope. No runtime rollout is authorized.

## Journal

### 2026-10-25 - Kickoff

Confirmed the effort name, checked existing mechanisms and current public
coordination, and reserved #8802 under the assigned public source handle.
The first PR proposes scope and validation only; implementation waits for its
reviewed merge.

### 2026-10-25 - Reviewed plan admitted

The plan's exact head received approval with no open issues and green required
checks; #8807 actually merged through the managed Maintainer path. The native
terminal record was reconciled, then guarded sync pulled the source worktree
onto the reviewed plan. The initial sync could not confirm the deleted PR ref;
it rebased nothing, and normal terminal reconciliation resolved that condition.
Begin Phase 2 without changing existing runtime installations or fleet holds.

### 2026-10-25 - Implementation and Windows consumer proof

Implemented the one-shot command, complete scoped GitHub provider inventory,
topology selection, cached original-root traversal and pure inventory join.
The established read-only claims namespace carries remote observations;
older clients reject its new option without reaching a launching fallback.
Bulk-completion listing remains unsupported. Human output escapes terminal
controls, JSON retains source values, and malformed response encoding is
isolated to its failed cell.

The final six-file isolated Windows packet passed143 tests with two native
POSIX skips. It exercised actual CLI/claims-adapter processes, quoted catalog
paths, no mutation/provisioning and owned descendant cleanup. A misspelled
test selector initially refused before test execution; the corrected full
packet passed without removing a case or changing any deadline.
The earlier native stdout-capture failure drove the known read-only adapter
design rather than an extra remote preflight process.

Candidate provider functions also completed real scoped inventories without
any cell probes; synthetic real-CLI proof remains separate from that live read.
No candidate was installed and no excluded route was probed. This changes no
installer or first-provisioning path; disposable synthetic first-use CLI state
was exercised instead of provisioning another host. Native POSIX and the
manual Ubuntu packet remain pending on the published candidate.
The optional lane expands neither scheduled portfolios nor required PR CI.

Documentation impact: agent-worktrees owns the detailed command/output/
failure contract in `docs/pr-sweep.md`, with README/CLI-reference pointers.
The root testing guide documents the manual native packet. Existing provider,
claim and topology authorities remain unchanged; no new daemon or lifecycle
architecture is introduced. Final install-contract/lint, actual review,
required CI, Linux proof, merge and source-effort closeout remain open.

### 2026-10-25 - Actual CI, native Linux and review corrections

Initial required CI rejected the claims dispatcher exceeding its unchanged
shrink-only ceiling. The first real Ubuntu portfolio also caught a POSIX
fixture asserting status3 although the script exits2. Moved validation into
the existing claims-find owner and corrected that assertion. The second
native Ubuntu run passed143 cases/two Windows skips on its exact candidate.
The next candidate, with the first review's five correctness repairs, passed
152 cases/two POSIX skips on Windows and its exact-head Ubuntu portfolio.
Required CI passed on that candidate, but its review was not yet current.

The full review also identified legacy WSL record locality, unknown-author
oldest reporting, alias-less declared profiles and configured machine aliases.
These were handled at their existing owners: record-scoped receiving-project
resolution, unknown scoped oldest, an opt-in unfiltered topology read preserving
the ordinary loader default, and topology-qualified alias comparison.
Focused regressions use real topology parsing and historical record fixtures,
not just mocked selected-cell strings.

An expanded Windows packet timed out once in the real-CLI case; that same
two-case process selection subsequently passed without raising deadlines.
The next unchanged expanded packet completed all cases and exposed two
actual alias-selection errors: a foreign-environment profile and an unknown
environment were passed to strict lookup. The fix filters conflicting
profiles before lookup and preserves unknown environments as unavailable.
The final corrected packet, updated native Linux run, full review and merge
still require verification; no previous pass is substituted for them.

### 2026-10-25 - Authorized bounded publication prerequisite

The corrected expanded Windows packet passed201 tests/four platform skips,
with source lint and all13 install contracts passing. Managed publication of
that correction then reached the unchanged30-second deadline on four attempts.
Provider reads confirmed the head did not advance. A bounded trace showed
successful authentication and pre-push entry, with complete object-reachability
checks still active at the deadline; a standalone successful guard invocation
took46 seconds, while a later profile was faster. No specific service or
credential failure is inferred from variable local latency.

The operator explicitly authorized fixing publication performance without
bypassing checks or raising deadlines. Reused #7039 rather than creating a
competing guard objective; #8803's separate target-probe owner is untouched.
The bounded correction batches commit-reference resolution and overlaps a
conservative incoming-object walk with the full published-reachability proof.
It retains the full known-object subtraction required for rebase/deletion/reuse
cases; negative traversal alone is not sufficient novelty proof.
This is a below-altitude implementation optimization of existing guard
semantics, not a new publication authority, cache or lifecycle.
Existing and new batch/refusal regressions plus actual native publication
remain the acceptance gate. No hook, safety check or deadline is waived.

Profiling then isolated the remaining causal delay: the optional identifier
reader's nominal ten-second `subprocess.run` call took49.56 seconds while
descendants retained its pipes. The blob guard itself completed in roughly
three seconds after batching. The identifier reader now reuses the canonical
caller-owned finite-process primitive and tested batch-command transport,
without installation or library source changes. Native Windows checks prove
the unchanged deadline retires both a live root's and an already-exited root's
pipe-holding descendant; quoted batch paths and no-self-provisioning are tested.
Unsafe cleanup/output errors fail closed rather than becoming benign absence.
The first PowerShell batch wrapper exceeded the ten-second native budget;
the existing direct batch transport fixed it rather than increasing the cap.
The full identifier packet had55 passes and that one batch-wrapper failure;
the four final focused native/refusal cases now pass. The publication packet
had65 passes and one process-count assertion, subsequently corrected and
rerun against the new single reference batch.
The real whole-guard candidate check now completes in16.46 seconds.
Actual managed push remains the final publication-budget proof.

### 2026-10-25 - Reconciled candidate and concrete safety failures

Managed publication succeeded on the bounded-reader candidate; its exact-head
manual Ubuntu packet also passed. The next review found that concrete
output-limit and cleanup errors inherit from `SubprocessError` and were still
treated as optional absence. Only missing-command/OS errors and ordinary
timeouts now retain the optional policy; all other observation failures block
publication. Regressions raise the real canonical exception classes.
PR parsing also preserves terminal repository/number identity when owner or
repository names themselves contain `pull`, `pulls`, or numeric segments.

Rebased the owned implementation onto current Dev after preserving its commits
in a local backup. Both the independently added promotion-control portfolio
and this PR's optional sweep portfolio remain intact. The reconciled Windows
seven-file packet passed206 cases with four platform skips. The guard/workflow
packet passed191 cases with one platform skip and two failures: its sweep
assertion observed an intermediate edit, and an existing inventory omitted the
actual SSH-service selector/jobs. Both final inventory assertions now pass;
neither production workflow nor any deadline was weakened.
All13 install contracts and touched-source lint pass.

The manual Linux portfolio additionally runs the publication guards and a
real POSIX quoted-command/finite-timeout check. An initial local attempt used
an unsupported tools-component selector and a misspelled legacy-WSL test;
both refused before executing tests. Corrected commands use existing runners
and bounded pytest tooling, not a new runner or required CI matrix.

Documentation impact was reassessed against the complete rebased diff:
`pr-sweep.md` remains the command/output/error authority, with README and
CLI-reference pointers; `TESTING.md` describes the optional native guard
packet, and `CONTRIBUTING.md` describes the unchanged publication deadline and
fail-closed observation policy. This remains a finite read-only command and a
below-altitude publication implementation fix, with no daemon, installer,
new dependency or rollout. Final exact-head Linux, full review, required CI,
implementation merge and source-effort closeout remain open.

The next exact candidate passed its complete manual Ubuntu packet, including
both publication guards and native POSIX timeout/quoting. Full review then
identified two further parsing defects: a GitHub repository named `_git`
entered the Azure branch, and one malformed tracked URL aborted a whole cell.
The shared parser now recognizes only the actual Azure terminal suffix and
retains its organization authority. Candidate parse failures become explicit
root errors while valid candidates and held peers remain visible. Six added
regressions exercise real persisted collection as well as the pure parser;
the rebased Windows packet passes212 cases/four platform skips.
The optional workflow also preserves newly merged Logger/pane portfolios;
the sweep job is placed away from their shared insertion boundary.
Current-head review, required CI and Linux remain necessary after this fix.

The next review caught annotated-tag publication and mixed historical/current
machine-label traversal. Batched publication now checks pinned raw object
identity separately from commit peeling; actual annotated-tag candidates with
an existing tagged remote baseline pass or refuse oversized new blobs correctly.
The complete guard packet passes67 cases. Snapshot traversal reuses admitted
records and the existing record-scoped alias proof, preserves stored labels
and rejects foreign execution profiles; its expanded seven-file packet passes
217 cases/four platform skips.

A subsequent full review identified host-qualified account selection and
unambiguous legacy response hostnames. Inventory now reuses the existing
host/repository account resolver, without selecting a public-host account or
token for Enterprise. The selected host's credential must pass its account
identity check before census reads. Response hostnames use the canonical
strict topology resolver; ambiguous/foreign names remain rejected. The first
targeted packet exposed a wrong keyword on the compatibility wrapper; selecting
the actual canonical resolver fixed all seven failures. The final seven-file
isolated Windows packet passes223 cases/four platform skips, source lint and
all13 install contracts pass. Documentation was reassessed and updated for
these account/identity/error guarantees. No deadline, review or CI gate changed.
