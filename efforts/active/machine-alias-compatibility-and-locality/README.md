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
- [ ] Land this retrospective effort and remaining-work plan through review.
- [ ] Bind the implementation source to the declared effort and slice after
  the canonical document is available in its repository.

### Phase 2 - Shared alias map and all identity slots
- [ ] Complete the single shared mapping seam for keys, aliases, hostnames,
  display names, case variants, legacy `-win`/`-wsl` and parenthesized
  environment labels.
- [ ] Wire configuration/detection, JSON and interactive launch selectors,
  persisted machine/platform records, owner/claim refs, handoff, liveness,
  settlement and Manager source selection to that seam.
- [ ] Preserve stored identity strings and explicit execution-space authority;
  contextual compatibility must not make a foreign native space local.
- [ ] Resolve the remaining review findings and upstream integration in the
  existing identity repair, with regressions for each bug class.
- [ ] Ensure physical sibling grouping is independent of roster adjacency and
  that keyboard traversal follows the displayed order.

### Phase 3 - Review, release and authorized deployment
- [ ] Publish the integrated source through the supported, exact-head flow;
  obtain new operator confirmation whenever a recovery plan changes.
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

- [ ] Cross the alias forms with Windows/WSL contexts and every Phase 2 slot,
  covering both historical host/platform tuples and logical-key records.
- [ ] Prove exact registered-key and configured transport-alias precedence;
  reject ambiguous mappings, conflicting context and foreign-space authority.
- [ ] Exercise preferred-key, raw-hostname and alias Manager source selection:
  the actual local source must have `local=True` and no SSH launch argv.
- [ ] Cover physical grouping, nonadjacent siblings, keyboard order, unchanged
  source IDs and separate SSH targets.
- [ ] Verify receiving-project record ID, actual platform and absolute
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

### 2026-10-15 - Retroactive capture and widened acceptance
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
