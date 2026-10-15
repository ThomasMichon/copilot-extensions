# Machine Alias Compatibility and Locality

- **Slug:** `machine-alias-compatibility-and-locality`
- **Repo:** copilot-extensions
- **Branch(es):** Existing identity-repair publication; separate reviewed effort-capture change
- **Created:** 2026-10-15
- **Status:** Active
- **Vision:** `visions/plugins/agent-worktrees/README.md` (durable repository/worktree identity); `visions/agent-fabric/README.md` (shared identity authority)
- **Umbrella issue:** #5674
- **Related issues:** #5972 · #6170 · #6692

## Guiding Intent

Restore correct worktree creation, launch, and resume when physical host names,
preferred names, historical aliases, and Windows/WSL logical identities differ.
One shared machine map resolves those spellings wherever a machine identity is
consumed. Compatibility preserves historical records and their separately
tracked environment; it does not collapse native and guest execution authority.

The completion gate is released, installed behavior that resolves every existing
worktree on the adopting host's Windows and WSL environments, not a passing
example, a source commit, or a merged individual pull request.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Effort capture, existing repair PR stewardship, release and authorized rollout | Managed worktree `weathered-rivet` |
| Identity implementation owner | Shared mapping, receiving-project compatibility and Manager locality | Existing managed source `chugging-bolt`; bounded delegate with exclusive edit ownership |
| Windows validation | Native configuration, payload execution and installed launch/resume proof | Adopting host's configured native transport |
| WSL validation | Guest configuration, historical records and installed launch/resume proof | Adopting host's configured guest transport |

## Coordination

- **Topology:** One existing source repair, serial review/publication, and a
  separate effort-capture PR. No replacement identity-repair PR.
- **Host (owns PRs):** Coordinator; implementation delegates do not publish.
- **Delegates:** Exclusive source ownership per bounded slice; no concurrent
  Git mutations in the implementation checkout.
- **Handoff:** This README and its first unresolved Plan/Validation Plan item
  govern continuation. Carry exact publication approvals and held resources.
- **Existing owners:** `machine-transport-convergence` retains its wider
  consumer migration campaign. This effort owns the compatibility/locality
  prerequisite, not its Dispatch, SSH, Index or Logger slices.
- **Separate work:** Attribution-only blank update comments are tracked by
  #6815 and explicitly deferred until this repair is deployed.

## Context

The original identity repair was already underway when this effort was
requested. Its history was referenced by another campaign, but this
compatibility-and-deployment objective had no dedicated canonical effort or
source binding. This capture is retroactive: it does not imply that earlier
implementation was preceded by this effort's review.

The shared registry already distinguishes execution spaces and can project
legacy Windows/WSL hosts into independently addressable entries. Remaining
adapters must use that authority rather than introduce competing interpretations
of case, hostname, display metadata or suffixes.

The reported Manager symptom is a preferred-name tab that is highlighted but
shows no worktrees and a failed status when the OS hostname differs. SSH
loopback is a hypothesis, not a diagnosis: source selection and the literal
loader error must distinguish a wrong remote route from a listing failure.

## Request

**Summary of the settled operator request:** accept historical and
regression-era aliases in every relevant identity slot; keep physical-host
grouping and separately tracked execution environment; restore creation and
resume on Windows and WSL; drive the repair through review, release, deployment
and machine-wide verification. Check Manager self-locality when hostname and
preferred name differ.

The operator explicitly requested the effort retroactively and approved this
slug. Public-safe request excerpts, with identifiers redacted but technical
constraints retained, are in [inception-transcript.md](inception-transcript.md).

## Plan

### Phase 1 - Capture ownership and the compatibility contract
- [x] Resolve prior campaign ownership and preserve the original repair source.
- [x] Recover the original committed source without editing deployed code or
  silently changing publication leases.
- [x] Land the exact-head recovery prerequisite #6692 through reviewed source;
  its implementation merged as #6703 and reached a release.
- [x] Land this retrospective effort and remaining-work plan through review
  in #6821.
- [x] Bind the implementation source to the declared effort and slice after
  the canonical document is available in its repository.

### Phase 2 - Shared alias map and all identity slots
- [x] Complete the single shared mapping seam for keys, aliases, hostnames,
  display names, case variants, legacy `-win`/`-wsl` and parenthesized
  environment labels.
- [x] Wire configuration/detection, JSON and interactive launch selectors,
  persisted machine/platform records, owner/claim refs, handoff, liveness,
  settlement and Manager source selection to that seam.
- [x] Preserve stored identity strings and explicit execution-space authority;
  contextual compatibility must not make a foreign native space local.
- [x] Address the known review findings and integrate the shared-registry
  contract in the existing identity repair, with regressions for each bug
  class. Fresh current-head review remains a Phase 3 gate.
- [x] Ensure physical sibling grouping is independent of roster adjacency and
  that keyboard traversal follows the displayed order.

### Phase 3 - Review, release and authorized deployment
- [ ] Publish the current integrated candidate through the normal supported
  flow with verified replay provenance. Require exact-head recovery approval
  only if the publication tool actually requires a recovery operation.
- [ ] Obtain an eligible current-head review and passing required CI; merge.
- [ ] Follow the release pipeline and verify the promoted snapshot contains
  the repair, rather than inferring release from commit ancestry.
- [ ] Install the released repair through the unified update flow in both
  adopting environments; verify the selected running versions.

### Phase 4 - Installed acceptance and closure
- [ ] Enumerate all registered projects and existing Windows/WSL worktrees;
  report removed checkouts separately from identity-resolution failures.
- [ ] Prove installed creation and resume for historical and current identity
  forms, including hostname/preferred-name differences.
- [ ] Verify the current Manager source is in-process, its rows remain visible,
  and its logical sibling uses the correct separate transport.
- [ ] Resolve or explicitly transfer every remaining Plan/Validation Plan item,
  capture durable behavior documentation, and archive only then.

## Validation Plan

- [x] Cross the alias forms with Windows/WSL contexts and every Phase 2 slot,
  covering both historical host/platform tuples and logical-key records.
- [x] Prove exact registered-key and configured transport-alias precedence;
  reject ambiguous mappings, conflicting context and foreign-space authority.
- [x] Exercise preferred-key, raw-hostname and alias Manager source selection:
  the actual local source must have `local=True` and no SSH launch argv.
- [x] Cover physical grouping, nonadjacent siblings, keyboard order, unchanged
  source IDs and separate SSH targets.
- [x] Verify receiving-project record ID, actual platform and absolute
  namespace proof for legacy ownership, including anchor actors, post-cleanup
  ledgers, cross-project aliases and real offer/accept/settlement flows.
- [ ] Run bounded affected tests, offline staged-payload checks and native
  Windows lanes; distinguish conditional skips from executed proof.
- [ ] Pass applicable lint, install-contract, module ceilings, publication
  safety, documentation-impact review and required provider checks.
- [ ] Verify released and installed behavior in both environments, preserving
  queued prompts, live sessions, historical strings and unrelated records.

## Proposal

Derive consumer behavior from the existing shared identity and registry owners.
Separate physical-host grouping, logical execution identity and SSH transport.
Use execution context and verified receiving-side records where historical
labels are insufficient; do not repair a naming mismatch by self-targeted SSH,
record relabeling, or weakening ownership fences.

The additional regression matrix is implementation detail derived from the
operator's all-alias/all-worktree acceptance requirement, not authorization for
unrelated service changes or a second machine registry.

## Journal

### 2026-10-15 - Producer handoff and actual-caller locality corrections
- Migrated the sole remaining caller of the removed machine-only handoff
  helper to complete-record locality. Producer acceptance validates the actual
  accepting execution identity, supports historical stored labels and preserves
  accepted retries without reopening or requiring a removed source.
- Claimant liveness retains the actual caller configuration while the record
  resolver independently loads receiving-project authority. A reference cannot
  substitute its project's machine for the caller, and unavailable caller
  configuration remains unconfirmed rather than evidence of death.
- Real producer reserve/bind/offer/accept/retry regressions preserve metadata
  and historical labels; foreign machine/platform cases leave every ledger
  unchanged. Actual claimant/prune regressions cover different caller/receiver
  hosts on all three platforms and legitimate cross-project aliases.
- The bounded producer, claimant, handoff and alias portfolio passed 644 tests;
  the focused recovery/caller checks passed 32 overlapping tests. Ruff, install
  contracts, module ceilings and whitespace checks passed. Existing WSL
  claimant fixtures remain explicit, and producer fixtures explicitly select
  their Linux context for native-host parity.
- The alias-grid assertion now decodes the existing shell-safe PowerShell
  command before inspecting its arguments, rather than expecting plaintext.
  Native Windows validation and publication remain coordinator-owned.

### 2026-10-15 - Context safety and complete remote option policy
- Normal supported publication succeeded for `28bdc336`, `7d878` and `26a1a2be`
  with intact replay proof after integration. No publication override is
  currently needed; a prior sync-preparation refusal is not a publication gate.
- Automatic Machines discovery can use an observed physical hostname that is
  also an explicit Windows key to select its registered WSL guest. Manual
  native-key/profile selections retain conflicting-context rejection.
- Claimant liveness treats visible foreign-platform ledgers as unconfirmed on
  Linux as well as Windows/WSL. Actual record-backed prune regressions preserve
  both the resource shield and the unchanged owner/child ledgers, including
  anchor actors and terminal-looking foreign owners.
- JSON and interactive remote resolve now share one selector/option builder.
  Supported behavior flags and profile/parent/caller values are forwarded using
  the existing shell-safe command constructor. Remote ownership and raw Copilot
  arguments reject before a plan; structured seed admission rejects options its
  existing request cannot represent before SSH. Target-local request options
  cannot silently execute locally when a machine target was supplied.
- Bounded focused Worktrees validation passed 341 tests with one conditional
  skip; final complete guards passed 1,052 tests with 64 conditional skips.
  Machines discovery/identity validation passed 170 tests. Ruff, install
  contracts, module ceilings and whitespace checks passed.
- Native Windows execution, current-head provider review, release and installed
  acceptance remain coordinator-owned gates; no publication or deployment was
  performed by the implementation participant.

### 2026-10-15 - Integrate upstream session identity and preserve source acceptance
- The coordinator verified normal supported publication of the preceding source.
  A sync-preparation refusal was not a publication refusal; no tracking edits,
  reparenting or raw push were needed. The next publication remains coordinator-owned.
- Completed the helper-directed rebase onto the nine newer upstream commits.
  The rendering overlap retains observed hostname, canonical and friendly names,
  explicit execution identity and platform-selected deployment metadata through
  one renderer. Both upstream lifecycle and compatibility regressions remain.
- The effort add/add resolution preserves Phase 2 completion, source evidence
  and the operator's deployment authorization. The root effort index retains
  every upstream row.
- Final bounded integration checks passed: 596 Worktrees tests with nine
  conditional skips, 256 shared transport tests and 272 Manager tests. The
  context fixture now supplies a real registry entry to the actual metadata
  selector rather than mocking an obsolete lookup seam.
- Ruff, install contracts, module ceilings and whitespace checks passed.
  Native Windows, current-head review, release and installed acceptance remain
  open; this source integration does not close the effort.

### 2026-10-15 - Shared alias matrix and preferred-name source correction
- The source now maps historical names through the shared contextual registry
  across discovery/configuration, launch, stored records, qualified ownership
  references and Manager locality. Stored strings are preserved.
- Explicit regression matrices cover shared matching (96 cells), stored slots
  (96), JSON/non-JSON routing (96), record-backed local resume (48), Machines
  discovery (48), and Manager matching/grouping (48). Counts overlap the test
  portfolios and are not native-host or installed-state claims.
- Preferred-name validation adds 58 cases through source construction and
  engine-client row parsing. It confirmed a mismatch between source selection
  and the separate local-alias predicate; the latter now uses execution context.
  The reported live empty/failed tab still requires installed diagnosis.
- Bounded source portfolios passed: 256 shared-library tests; 708 Worktrees
  tests with two conditional skips, plus a 211-test follow-up; 168 Machines
  tests; 237 Manager/offline-packaging tests; two copied-payload tests; and four
  host installer adapter tests. The final preferred-name Manager portfolio
  passed 272 tests. These overlapping runs do not substitute for native Windows
  execution or the release/deployment completion gate.
- Parent settlement rejects a different same-platform caller, and nonadjacent
  physical siblings share display and keyboard traversal order without changing
  source identities or SSH targets.
- Normal supported publication with verified replay proof supersedes the
  earlier inferred recovery gate. Historical exact-head recovery approvals
  remain facts about those earlier operations, not a new approval requirement.

### 2026-10-15 - Retroactive capture and widened acceptance
- The effort capture merged in #6821 after current-head approval and required
  checks. The implementation and installed-acceptance gates remain open.
- The operator reaffirmed continuous execution until Windows/WSL worktrees
  resolve and authorized deployment on both environments and other adopting
  machines as needed. Normal target workflows and safety gates still apply.
- The operator requested this effort after observing preferred-name Manager
  tabs that appeared local but showed failed/empty data.
- Prior source work and repeated review corrections remain in the original
  identity repair; source validation is not claimed as live deployment.
- #6703 merged and its released guarded recovery operation was used with
  explicit exact-head approval. Source and prior published objects were
  retained; no raw tracking-record repair or unleased push was substituted.
- Source fixes cover host/environment selectors, receiving-project ownership,
  handoff and settlement edge cases, staged payloads and physical grouping.
  The broader historical-alias matrix and installed acceptance remain open.
- The canonical wider transport-convergence campaign is preserved as a
  separate owner. This effort does not claim its downstream consumer slices.
