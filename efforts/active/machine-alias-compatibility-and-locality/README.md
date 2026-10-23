# Machine Alias Compatibility and Locality

- **Slug:** `machine-alias-compatibility-and-locality`
- **Repo:** copilot-extensions
- **Branch(es):** Existing identity-repair publication; separate reviewed effort-capture change
- **Created:** 2026-10-15
- **Status:** Active
- **Vision:** `visions/plugins/agent-worktrees/README.md` (durable repository/worktree identity); `visions/agent-fabric/README.md` (shared identity authority)
- **Umbrella issue:** #5674
- **Related issues:** #5972 · #6170 · #6692 · #6739 · #6945 · #7057 · #7097

## Guiding Intent

Restore correct worktree creation, launch, and resume when physical host names,
preferred names, historical aliases, and Windows/WSL logical identities differ.
One shared machine map resolves those spellings wherever a machine identity is
consumed. Compatibility preserves historical records and their separately
tracked environment; it does not collapse native and guest execution authority.

The completion gate is released, installed behavior that resolves every existing
worktree on the adopting host's Windows and WSL environments, not a passing
example, a source commit, or a merged individual pull request.

**Current status (2026-10-22):** natural qualification `39912447859` successfully
released the runtime ownership alignment #8010 and tag-aware allocation repair
#8110 after reviewed helper adoption #8121 and Source mirror #8127. Independent
provider and artifact readback verifies the exact candidate, qualified/Main tree,
source tag and immutable artifact receipts within the unchanged timing bound.
The subsequent publication-scope repair #8283 qualified in natural
`40013348876`: Manager bootstrap no longer compares unrelated Worktrees helper
bytes. Actual native recovery clears that refusal and activates Manager
`0.6.105-dev1`, but its real update request completes with exit 1 and an empty
diagnostic log; the selected Worktrees runtime remains unchanged. A reproduced
native repair-child standard-stream fix #8289 qualified in natural
`40017462768`. The next supported bootstrap stops before construction at another
stale hash gate: its installed Manager launcher exactly matches the current
producer but is absent from the historical hash list. Registered Manager
admission is being aligned with its existing provider/package/path authority
and construction-time change detection, rather than expanding that list.
Complete native suite and normal-entry acceptance remain
open; guest interrupted-request recovery still needs the separately confirmed
disruptive restart. #7743's supported installed recovery remains ahead of
local-first identity implementation. The
[current journal](journal/2026/10.22%20progress.md) preserves the release,
activation, request-outcome and installed-suite distinctions.

**Physical-machine clarification:** machine name, OS hostname and execution
environment are separate dimensions. A compact host/environment address is
appropriate only where an interface needs one execution identifier; it must not
replace the physical machine name in deployment policy or structured context.
The [Phase 3d contract](physical-machine-environment-boundary.md) applies this
clarification without rewriting historical identifiers.

**Local-first clarification:** registering, loading and launching the harness on
the current machine requires no machine manifest entries. Machine maps provide
optional customization, not basic local execution authority. The
[local-identity amendment](manifest-independent-local-identity.md) supersedes
the gated producer's registry-required assumption before ordinary activation.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Effort capture, existing repair PR stewardship, release and authorized rollout | Managed worktree `weathered-rivet` |
| Identity implementation owner | Shared mapping, receiving-project compatibility and Manager locality | Existing managed source `chugging-bolt`; bounded delegate with exclusive edit ownership |
| Windows validation | Native configuration, payload execution and installed launch/resume proof | Adopting host's configured native transport |
| WSL validation | Guest configuration, historical records and installed launch/resume proof | Adopting host's configured guest transport |
| Runtime readiness owner | Empty-mux current-roster and liveness prerequisite | Managed source `grimy-cylinder` |
| Guest installation identity repair | Bounded #6945 proposal, shared identity repair and entry-path acceptance | Managed source `tinkling-toolbox` |
| Canonical native label history | Bounded #7057 convergence on the canonical native repair; owns source PRs, not live application | Managed source `chugging-ratchet` |
| Gated producer contributor | Phase 3d producers/replay/equivalence plus explicitly assigned Windows repair and installed acceptance; ordinary-default activation remains review-gated | Managed source `chugging-wrench` |

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
- **Containing rollout follow-up:** The #6945 owner takes only the declared
  Phase 3b slice; the original repair, release qualification and wider
  transport-convergence owners retain their responsibilities. The independent
  #6947 installer and #6948 preview repairs do not become new ownership here.
- **Explicit owner migration:** The #7057 participant owns only Phase 3c and its
  validation. Existing campaign, release and installation owners remain intact.
  The coordinator owns any separately authorized local application; source
  implementation must not touch real records, topology, runtimes or services.
- **Current source transition:** After completing the first regression-admission
  cohort under the release effort's Phase 9b, `chugging-wrench` resumes the
  declared gated producer contribution in Phase 3d. Release, installed rollout,
  original compatibility repair and other participants retain their obligations.

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

**2026-10-16 continuation:** the operator confirmed a real launch after the
released compatibility repair, then requested fixes for the discovered
follow-ups. The designated worktree distro should be the host's default WSL.
Guest addresses should follow a compact host/guest convention without another
distribution suffix. Renaming the actual distro to match a logical guest name
was suggested as an option, not ordered. The exact public-safe request is
preserved in the request record.

**Explicit owner-migration follow-up (public-safe verbatim excerpts; private
identifiers and operational evidence REDACTED):**
> "Implement upstream but also do locally to unblock since main promotion is held right now"
>
> "implement and land through dev PR a supported explicit provenance-checked local owner-machine migration for agent-worktrees, with report-first dry-run, exact old and new machine labels, safe authoritative locked writes and backup/audit, JSON, focused regression tests, docs + mandatory changefile."
>
> "Preserve IDs/paths (old name prefixes are historical identity not rename targets), sessions, head, titles, flags, lifecycle, PRs, claims semantics."
>
> "Account for embedded owner_refs/pair_refs/controller/resource references or refuse explicitly rather than leaving incoherent edges."

Private topology, host labels, checkout paths and downstream context are
redacted, not implementation inputs. One-time local application is explicitly
authorized but separately coordinator-owned, not a release promotion or rollout.

**Canonical integration decision (public-safe verbatim excerpts):**
> "converge"
>
> "use merged command locally and converge remaining upstream work on it."
>
> "Avoid gratuitous conservative refusals that make known-local paired/controlled records impossible: assess whether preserved old references remain semantically valid same-machine aliases, and only refuse when you can show unsafe/incoherent authority"
>
> "The actual user gate remains all this machine's worktrees show canonical owner, not merely landing a feature."

Private operational evidence and identifiers remain redacted. The settled
direction is one canonical command, preserving valid same-machine alias
relationships; misplaced-record integrity is a distinct capability.

**Physical machine and environment clarification (verbatim, private host names
replaced with generic examples):**

> We need to deal with the situation properly:
>
> Machines:
>   - example-host:
>     - hostname: Example-Host
>     - environments:
>       - windows
>       - wsl
>
> The machine name is "example-host".
> For systems which need just a single identifier for machine+environment,
> "example-host-wsl" may be used to represent WSL.

The operator also reported that several WSL worktrees briefly could not resolve
from Worktree Manager after a local update, then could not reproduce the
failure. That observation remains unconfirmed: diagnose read-only and preserve
its uncertainty rather than claiming that a naming change fixed it. Duplicate
environment qualifiers in historical IDs are a naming-generation defect in
this host's case, not permission to rename existing IDs or paths.

**New-record schema choice (operator-selected response):**

> tuple-first

The selected form option makes the new record's machine the physical host,
with environment and one-term execution-owner identity separate. This is a
source-schema choice, not approval to migrate existing records or deploy.

**One-term addressing intent (verbatim):**

> An intent is to be able to refer to machines' WSL environments via `<machine>-wsl` for speed, though, when a one-term reference is useful/needed

Tuple-first storage must retain that concise execution address; it does not
require tuple-shaped commands. The [new-record contract](physical-machine-environment-boundary.md#tuple-first-new-record-contract)
keeps the physical tuple and one-term shorthand distinct and equivalent at the
appropriate interfaces.

**2026-10-20 cross-plugin boundary choice:** the operator selected
`shared-pure-codec`, the form option "Extract a shared, dependency-free codec;
preserve the existing Worktrees import path." The
[bounded integration plan](physical-machine-environment-boundary.md#shared-raw-json-codec-integration)
implements that choice without enabling tuple producers or installing runtimes.

**2026-10-20 producer implementation decision (operator-selected form):**

The operator selected `gated-producer-implementation`, the form option:

> Resume full tuple producer implementation; keep defaults and rollout gated on replay, equivalence and installed-reader acceptance

The form explicitly preserved existing records, IDs and references and
authorized no live rollout or migration. Full source producer implementation
is resumed; ordinary producer defaults are not thereby enabled. The subsequent
regression-led release-test request is the driver's current bounded priority,
not abandonment of this producer objective.

**2026-10-21 local-first clarification (verbatim):**

> control-plane repositories should not need to declare *any* machines in their maps in order to be able to register, load, and launch the harness on the current machine. The machine manifest is purely for enabling *additional customizations*, such as friendly-name mapping, per-machine service loadouts, ssh roles, dispatch assignments, etc.

The [manifest-independent amendment](manifest-independent-local-identity.md)
captures this requirement. Requiring a machine-map entry or a cross-project map
binding for basic local operation is not the selected policy.

**2026-10-21 Windows self-repair priority (public-safe verbatim excerpts):**

> This was caused by updates to worktree-manager itself. These need to be fixed upstream.

> We also need to ensure that `<repo> update` fast-paths to invoking the remote path, with no python deps, venvs, and other things in-between the binstub and triggering the fresh installer. This ensures we can always self-repair from a fresh install.

> That must be because SSH terminates as Admin on Windows; we'll need to take care in that situation to avoid leaving admin-only folders in user-space

The original native failure report names private local paths; its technical
failure is retained here as a same-account, non-administrator retention-lock
access failure. #7742 and #7743 carry the generic upstream repairs. The earlier
explicit deployment/migration/restart authorization remains in force for
necessary supported repair, not arbitrary permission or record rewrites.

**2026-10-22 muxed continuation and promotion priority (verbatim):**

> Let's update the main effort with current status, and I'd like to pick this up and drive it from a properly-muxed worktree. Can you package up everything, and then create a muxed worktree agent with a seeded prompt continuing your mandate? Its first initial task will be to unblock promotion by fixing whatever happened here.

This explicitly assigns the promotion-blocker repair to the continuation
driver; passive waiting is no longer its next action. Reuse the bound effort
worktree and preserve its held resources rather than creating a duplicate
effort or discarding the prior source. The assignment changes the first slice,
not the parent completion gate or the authority of the existing release
controller.

**Promotion allocation choice (operator-selected form):** `tag-aware`:

> Preserve existing version scheme; skip occupied incompatible version tags, reuse only exact matching payloads (recommended)

The bounded repair accounts for aliases reserved by an unsuccessful Main
landing. It does not authorize tag replacement or weaker publication checks;
reviewed helper adoption and natural qualification remain with the existing
control path.

**Installed Manager observation (operator clarification, verbatim):**

> The name should not matter, though: the machine metadata does.

The operator reports that an existing guest worktree with a duplicated
environment qualifier in its historical name is missing from the expected
Manager machine tab. Stored machine/platform/environment metadata and canonical
identity resolution govern discovery and grouping, not its ID or suffix.
Preserve that worktree's identity, path, relationships and live session. Actual
installed tab visibility remains an explicit acceptance case; bridge, mux or
canonical-head presence is not equivalent proof. Prospective naming quality is
separate and does not establish the cause of the missing row.

The subsequent read-only diagnosis in #5674 identifies project scope for this
particular witness: the unchanged record is present in its owning project's
installed current roster and Picker normalization, while the open Manager
instances enumerate a different project. That does not establish a machine or
name exclusion, and does not authorize related-project aggregation, a rename
or a metadata rewrite. The broader installed acceptance matrix remains open.

**Operator-directed continuation boundary (public-safe summary):** the agent
home must be an agent-capable harness; this repository's checkout remains
subordinate source work. Preserve the existing source, effort, relationships,
claims, queues and promotion progress during the coordinated transfer. No
finalization, record rewrite, automatic claim release or new agent launch from
this source repository is part of the wrap-up.

## Plan

### Phase 0 - Empty-mux readiness prerequisite
_(Agent-recommended prerequisite discovered while checking the requested live-data gate.)_
- [x] Confirm the existing #6739 no-server classification defect using a
  read-only probe, without killing sessions or starting a dummy server.
- [x] Handle recognized healthy empty tmux results consistently in monitor and
  session-liveness enumeration; retain unknown evidence for genuine failures.
- [x] Pass focused, real missing-socket and native-safe probe contracts; land
  the readiness prerequisite through ordinary review and required CI in #6856.
- [x] Verify current-roster readiness after the authorized released update.
  Actual installed Manager source reads received current publications in all
  six guest projects and both native projects.

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
- [x] Publish the current integrated candidate through the normal supported
  flow with verified replay provenance. Require exact-head recovery approval
  only if the publication tool actually requires a recovery operation.
- [x] Obtain an eligible current-head review and passing required CI; merge
  the original identity repair in #5689.
- [x] Follow the release pipeline and verify the promoted snapshot contains
  the repair, rather than inferring release from commit ancestry.
  Natural qualification `39648224191` records the exact promoted Main tree
  containing the compatibility, readiness and producer-reader repairs.
  The release effort retains its wider qualification obligations.
- [x] Install the released repair through the unified update flow in both
  adopting environments; verify the selected running versions.

### Phase 3b - Entry-path-independent guest installation identity
- [x] Review the [#6945 guest-identity proposal](guest-execution-identity.md)
  before introducing new registration or ownership behavior; #6993 merged
  after exact-head approval and passing required checks.
- [x] Establish one authoritative guest identity across direct, SSH and
  supervised entry paths, preserving independent installation and execution
  spaces; do not derive the caller from an incumbent ownership record.
  The shared source contract landed in #7009; installed entry-path acceptance
  remains part of the final Phase 3b gate below.
- [x] Represent the designated/default WSL selection and compact host/guest
  addressing through existing identity owners, without adding suffix chains
  or a competing topology registry.
  Source #7009 separates guest/default diagnostics from logical-address
  authority; no OS default, distro name or existing record was changed.
- [ ] Pass the proposal's same-guest, foreign-guest, default-drift and
  cross-runner validation, then land, qualify, release and perform only
  authorized installation/default-selection changes.

### Phase 3c - Canonical native label repair and historical records
_(Operator-selected convergence; detailed mechanism is agent-recommended.)_
- [x] Review and merge this plan before implementation; #7059 merged with
  exact-head approval and passing required CI.
- [x] Converge on the merged #7048 `doctor --repair-machine-record` surface;
  remove the competing migration API. Keep report-first JSON and explicit apply;
  optional exact target assertion cannot select another execution space.
- [x] Preserve native observed-host and existing linked-Git proof. For removed
  checkouts require an explicit canonical native key, canonical receiving
  worktree/knowledge registration, readable Git anchor and exact ID/platform/
  checkout namespace. Existing non-Git directories never borrow history proof.
- [x] Preserve relationship bytes, rather than transfer ownership. Prove that
  old qualified aliases still resolve to the same receiving record before and
  after normalization, including known-local paired/controlled records.
- [x] Reuse authoritative mutation/publication and tracking locks, validate
  unchanged input under lock, retain original-byte backups and an audit report;
  report partial/error outcomes honestly and make repeated apply idempotent.
- [x] Pass the converged focused regressions, lint, install-contract and
  metadata/documentation gates; retain the agent-worktrees changefile.
  Native/history repair, legacy handoff, authority/doctor/registry/locality,
  cache/serialization and exact CI producer-clone regressions have bounded
  contained coverage without increasing the normal test deadlines.
- [ ] Land the source candidate through final-head review and required dev CI.
- [x] Blocked; transferred to `#7097`: targeted authoritative handling of
  misplaced duplicates/orphans without lifecycle destruction or a weakened
  receiving-project fence.
- [ ] Coordinator verifies all native records and resident roster show canonical
  ownership, with wrong-store blockers resolved or explicitly transferred. A
  direct exact-record write alone is not a resident projection refresh.
- [ ] Transfer the retained clean source and canonical CLI commands to the
  coordinator. No runtime deployment, service change, real-record repair or
  release promotion by this participant; the containing user gate remains open.

### Phase 3d - Physical machine and environment boundaries
_(Agent-recommended realization of the operator's explicit model clarification.)_
- [x] Unblock the containing promotion's source-closure publication failure
      under #3336, through a reviewed fix and natural qualified release.
      This is the operator-prioritized prerequisite to #7743's complete native
      and guest installed acceptance, not permission to rewrite immutable tags,
      weaken closure checks or introduce a second Main writer.
      Natural `39912447859` independently verifies the exact source containing
      #8010 and #8110, qualification/Main tree, immutable publication and source tag.
- [x] Review the [structured identity contract](physical-machine-environment-boundary.md)
      before changing implementation.
- [x] Reuse the shared map's physical-machine/environment selection; identify
      compact execution-ID boundaries explicitly, without another registry or
      suffix-derived authority.
- [x] Review the [tuple-first new-record contract](physical-machine-environment-boundary.md#tuple-first-new-record-contract)
      and one-term WSL reference equivalence before changing durable creation
      semantics.
- [x] Prepare Worktrees' versioned tracking, reference and observational
      consumers in #7414, keeping ordinary creation producers legacy.
- [x] Review and execute the [shared raw-JSON codec integration](physical-machine-environment-boundary.md#shared-raw-json-codec-integration)
      before enabling producers; retain independent plugin installation.
- [ ] Review and execute the [gated producer source plan](physical-machine-environment-boundary.md#gated-producer-implementation),
      preserving retained allocations before any ordinary-default activation.
- [ ] Review and execute the [manifest-independent local-identity amendment](manifest-independent-local-identity.md);
      machine maps customize rather than admit ordinary local use.
      The intent and corrective plan passed review in #7773; implementation and
      installed acceptance remain open.
- [ ] Make runtime admission, structured machine guidance and prospective
      worktree naming/metadata consume the correct dimension. Keep native and
      guest execution authority distinct and use the same policy in update and
      launch reconciliation.
- [ ] Preserve existing IDs, paths, refs, records, queued work and live sessions;
      retain supported legacy lookup and allocation replay. No automatic
      relabel, rename, policy widening or data migration.
- [ ] Pass bounded in-process decision regressions and required Source CI, then
      qualify and release the implementation through normal promotion.
- [ ] Verify the installed Windows/WSL deployment and all-worktree resolution
      matrix; record or explicitly transfer any reproducible Manager regression.

### Phase 4 - Installed acceptance and closure
- [x] Enumerate all registered projects and existing Windows/WSL worktrees;
  report removed checkouts separately from identity-resolution failures.
- [ ] Prove installed creation and resume for historical and current identity
  forms, including hostname/preferred-name differences.
- [x] Verify the current Manager source is in-process, its rows remain visible,
  and its logical sibling uses the correct separate transport.
  Actual selected Manager packages loaded each local source without SSH,
  preserved the real current publication's row IDs through normalization,
  and selected the configured sibling transport independently under the
  administrative validation entry. The subsequent ordinary native startup
  failure was separately resolved by #7750 and #7772, released in natural
  qualification `39725960073` and installed through the supported updater.
  The operator confirmed ordinary, non-elevated PowerShell startup of Manager
  `0.6.67-dev1`; the administrative probe alone was not counted as that proof.
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
- [x] Run bounded affected tests, offline staged-payload checks and native
  Windows lanes; distinguish conditional skips from executed proof.
- [x] Pass applicable lint, install-contract, module ceilings, publication
  safety, documentation-impact review and required provider checks.
- [ ] Verify released and installed behavior in both environments, preserving
  queued prompts, live sessions, historical strings and unrelated records.
- [ ] Satisfy the [guest-identity validation matrix](guest-execution-identity.md#validation-plan)
  before declaring the #6945 containing rollout gap closed.
- [x] For Phase 3c, prove read-only dry-run, exact selection and JSON, local
  existing/missing checkouts, unchanged non-owner fields, repeated apply,
  changed-record/lock failures and original-byte backup/audit recovery.
- [x] For converged Phase 3c, retain foreign project/platform/namespace and
  ambiguous identity refusals while preserving valid same-machine alias links.
  Prove existing linked-Git and removed-record receiving-registration paths,
  including knowledge-only repositories, with no competing public API.
- [x] For Phase 3c, distinguish exact-record truth from stale resident/list
  projections; no source-only invocation claims to upgrade or patch the daemon.
- [ ] For Phase 3d, verify physical name/hostname/environment/execution-ID
  separation, update/launch gate agreement, new single-environment naming,
  unchanged legacy lookup and native/guest refusal boundaries using the
  [validation matrix](physical-machine-environment-boundary.md#validation-plan).
- [x] Classify the operator-reported retained guest worktree using its installed
  owning-project roster and actual Picker data-source path: it is present with
  unchanged guest metadata; the open Manager instances enumerate another
  project. Preserve the ID/path/relationships and do not infer a locality defect,
  related-project aggregation requirement or rename remedy. This source-path
  diagnosis is not completion of the broader installed creation/resume matrix.

## Proposal

Derive consumer behavior from the existing shared identity and registry owners.
Separate physical-host grouping, logical execution identity and SSH transport.
Use execution context and verified receiving-side records where historical
labels are insufficient; do not repair a naming mismatch by self-targeted SSH,
automatic record relabeling, or weakening ownership fences. Explicit native
label repair is a separately reviewed, operator-selected record scope, not
transport routing or a foreign ownership transfer.

The additional regression matrix is implementation detail derived from the
operator's all-alias/all-worktree acceptance requirement, not authorization for
unrelated service changes or a second machine registry.

## Journal

See the [dated journal index](journal.md) for the preserved implementation,
release, deployment and decision history. The current Plan and Validation Plan
above remain the completion gate.
