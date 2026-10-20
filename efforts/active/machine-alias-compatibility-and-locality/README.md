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

**Physical-machine clarification:** machine name, OS hostname and execution
environment are separate dimensions. A compact host/environment address is
appropriate only where an interface needs one execution identifier; it must not
replace the physical machine name in deployment policy or structured context.
The [Phase 3d contract](physical-machine-environment-boundary.md) applies this
clarification without rewriting historical identifiers.

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

## Plan

### Phase 0 - Empty-mux readiness prerequisite
_(Agent-recommended prerequisite discovered while checking the requested live-data gate.)_
- [x] Confirm the existing #6739 no-server classification defect using a
  read-only probe, without killing sessions or starting a dummy server.
- [x] Handle recognized healthy empty tmux results consistently in monitor and
  session-liveness enumeration; retain unknown evidence for genuine failures.
- [x] Pass focused, real missing-socket and native-safe probe contracts; land
  the readiness prerequisite through ordinary review and required CI in #6856.
- [ ] Verify current-roster readiness after the authorized released update.

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
- [ ] Follow the release pipeline and verify the promoted snapshot contains
  the repair, rather than inferring release from commit ancestry.
  The explicitly authorized workflow-only bootstrap #6867 has merged to
  `main`; #6848 still owns the containing promotion and release qualification.
  Bootstrap merge alone does not release the alias or readiness repairs.
- [ ] Install the released repair through the unified update flow in both
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
- [x] Review the [structured identity contract](physical-machine-environment-boundary.md)
      before changing implementation.
- [x] Reuse the shared map's physical-machine/environment selection; identify
      compact execution-ID boundaries explicitly, without another registry or
      suffix-derived authority.
- [ ] Review the [tuple-first new-record contract](physical-machine-environment-boundary.md#tuple-first-new-record-contract)
      and one-term WSL reference equivalence before changing durable creation
      semantics.
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

### 2026-10-19 - Guidance merged; tuple-first creation selected
- #7334 merged after exact-head approval and required Source checks. Guidance
  now distinguishes physical machine, observed hostname, selected environment
  and execution address; legacy text keeps OS descriptions and SSH aliases
  separate. `get machine` retains its one-term execution-ID contract.
- The operator selected tuple-first identity for new durable records, rather
  than keeping the old `machine` field overloaded and adding a parallel snapshot.
  Captured the schema decision and the explicit fast `<machine>-wsl` addressing
  intent in Request and the existing Phase 3d contract.
- The next implementation must audit one-term owner/ref consumers and retained
  allocation repair before changing new-record semantics. Existing records,
  IDs, paths, references, queues and live sessions remain untouched; reviewed
  source and separately authorized installed acceptance still gate completion.

### 2026-10-19 - Physical runtime admission merged; structured guidance next
- #7321 merged after exact-head Copilot approval, required Source checks and
  resolution of the implemented machine-local finding. The shared selection
  now drives update and launch physical admission while retaining exact
  execution selectors and independent native/guest authority.
- Review uncovered real configuration-boundary gaps. Deferred workers now carry
  explicit target configuration through both identity lookups, including real
  knowledge and machine-local registry sources. Linked checkouts preserve
  local registry precedence, borrow an anchor only after Git ownership proof,
  and retain launch-local settings and gates. Cross-project update uses the
  enabling project's configuration rather than unrelated resident state.
- Bounded disconnected-container affected validation passed 619 cases, with
  conditional skips reported separately. No live runtime, historical record,
  deployment allowlist, session or queue was changed by source implementation.
- The next bounded slice presents physical machine, hostname, environment and
  execution identifier as separate guidance facts through the same map.
  Prospective creation metadata/naming and allocation replay remain distinct
  implementation obligations; qualification and installed acceptance still
  gate the containing Phase 3d objective.

### 2026-10-19 - Reviewed physical boundary and runtime-admission implementation
- The Phase 3d contract merged in #7298 after current-head approval and required
  CI, using the ordinarily installed managed completion path. The prerequisite
  #7308 repair preserved the checked Maintainer exception for target-branch
  deletion/force-update protections without waiving required CI or other merge
  constraints. Its qualified release preceded the successful installed use.
- The first implementation slice reuses the shared registry selection to expose
  a physical/environment tuple separately from the execution ID. Runtime update
  and launch admission consume that same projection; exact execution selectors
  remain compatible, unknown labels are not suffix-normalized into another host,
  and ambiguous or conflicting selections refuse.
- The original regression failed in both update and launch planning. Bounded
  disconnected-container validation then covered the affected identity,
  reconciliation and updater suites. Conditional native/probe skips do not
  constitute installed native acceptance.
- Structured guidance and prospective creation metadata/naming remain the next
  slice. Historical IDs, record ownership, references and existing allocations
  have not been changed. Source review, qualified release and authorized
  installed acceptance still gate this implementation; no plan checkbox above
  claims the whole Phase 3d boundary or containing rollout is complete.

### 2026-10-19 - Physical identity clarified after installed update
- An installed update exposed a real dimension mismatch: the receiving context
  supplies a compact guest execution ID to a runtime gate whose manifest lists
  physical machines. The gate's conservative denial is not a reason to broaden
  adopter policy or force an installer.
- The operator clarified one physical machine with independently selected
  Windows/WSL environments, allowing a compact address only at one-ID
  interfaces. Captured that clarification above and linked one bounded Phase 3d
  contract; existing execution-space ownership and identity-repair owners remain.
- Current naming code already suppresses a repeated qualifier in its modern
  path, and a read-only preview resolves a historical repeated-qualifier
  worktree. That is not proof that all Manager failures are fixed. The remaining
  implementation must correct the structured boundary, not rely on string
  suppression or mutate historical IDs.
- No adopter deployment allowlist, real record or native Windows runtime was
  changed to accommodate the mismatch. Implementation and further rollout wait
  for the normal review and acceptance gates below.

### 2026-10-16 - Operator selected canonical convergence
- The operator selected convergence on the already merged #7048 command.
  The competing migration surface and blanket linked-record refusals are
  removed. Existing linked checkouts retain their observed-host and Git-common-
  directory proof; removed native records gain independent receiving
  registration/namespace proof behind that same command.
- Relationship bytes and configured caller identity do not change. Regression
  coverage proves that preserved old aliases still resolve the same local
  records and retain handoff/owner/pair authority before and after normalization.
  Existing broken relationships are not reported as repaired by label changes.
- #7097 owns targeted misplaced-record integrity, including duplicate and
  orphaned records. This source slice retains its project fence and never
  silently relocates, deletes or finalizes such records.
- Exact-record mutation and resident roster freshness are distinct facts.
  Coordinator-owned live verification remains required; no installed runtime,
  service or real record is changed by the source participant.
- The read-time machine CAS token is a private non-serialized dataclass field,
  so ordinary `dataclasses.replace` and copies derived from read-only cache
  models preserve current-snapshot intent. A genuinely stale writer still
  refuses after an explicit label repair.
- Native/Git proof is repeated under publication authority before the short
  record-lock phase. The final exact-byte CAS catches intervening record writes;
  no Git I/O occurs under a record lock. Only material receiving bindings are
  compared, not incidental profile/preferences configuration.

### 2026-10-16 - Explicit owner-migration slice proposed
- #7057 reserves the bounded capability; `chugging-ratchet` owns its source
  plan and implementation, not the wider alias campaign or local application.
- This closes the durable identity and explicit-relation vision requirements
  through an explicit repair seam, not automatic alias-based ownership.
  The daemon-mediated-write-authority and single-write-path-across-plugins
  invariants remain binding: implementation must reuse the owning public
  mutation path and locks rather than add an independent YAML writer.
- Plan captures the requested report-first migration and preserves surrounding
  authorization constraints. All algorithm/CLI integration details above are
  visibly agent-recommended and require the implementation's own review/tests.

### 2026-10-16 - Owner-migration plan reviewed; bounded implementation
- #7059 merged as `71104231fa60aa316728978f77377e9d2a4ca948` after
  exact-head Copilot approval and passing required CI; execution synced onto
  that reviewed intent through the supported Git flow.
- The agent-recommended first integration is one exact record in its receiving
  project, with a canonical cross-project/knowledge inventory for dependency
  checks. Linked/provider-owned records are explicitly refused rather than
  partially migrated. This does not claim multi-record graph transfer.
- Real-record recovery and rollout remain coordinator-owned and unexecuted by
  the source participant. The existing wider effort remains Active.

### 2026-10-16 - Independent relationship authorities fenced
- Bare caller, controller, owner, pair and typed resource references are durable
  relationships, not passive strings. Their peer ledgers now fence matching
  identities without requiring a machine qualifier; passive notes stay inert.
- Claim reservations use `handoff_bundle`, and claim-handoff endpoints also live
  outside worktree records. The repair inspects that registry, refuses retained
  endpoints and producer-owned claims, and holds its native lock through commit.
- Canonical repository registration now publishes through the existing
  installation authority, excluding newly visible knowledge ledgers during the
  final inspection/write boundary. A real concurrent-writer regression proves
  registration waits until the record commit.
- The fresh-home native CLI fixture uses the same platform detector as its
  child, including WSL. Expanded affected validation passes 384 tests.
- Itemized follow-up references and retained release snapshots participate in
  publication fencing too, including a new-peer save attempted after the final
  inventory check. Passive titles, notes and summaries are not ownership edges.
- The transaction retains a constant-size lock set: publication authority,
  claim-handoff registry and selected record. Peer claim/controller metadata
  changes are authority-fenced rather than locking every historical ledger.
  Busy unrelated peers remain available; duplicate checkout pointers refuse.

### 2026-10-16 - Guest source repair merged; release containment remains
- #7009 merged as `6fe63b78740cc0adbf49fad4cd8970dca656ce83`, from exact head
  `3752cad431173bbb789581d5c0252a4450699d99`. Current-head Copilot approval
  `5548783502`, zero unresolved findings and successful required CI
  `38909598711` preceded the ordinary managed merge.
- That candidate's native installation-context smoke passed 186 cases with
  one platform skip, including all three guest writers/readers and the six
  enrollment-admission parameters. Windows core/native coverage passed 28
  with one POSIX-only skip; targeted doctor passed 14. The POSIX admission
  cases were executed in hosted CI, not counted as Windows proof.
- The independent MCP follow-up #7000 and read-only preview repair #6998 also
  merged. Existing Dispatch and MCP argument-binding repairs were preserved.
  None of those independent source merges proves deployment.
- Main remains under the operator-approved promotion pause #6949 while #6237
  retains release-pipeline qualification. No unpause, manual promotion/retry,
  updater, service restart, guest enrollment or OS/default change was made
  for this containing rollout.
- The operator approved canonical post-merge reconciliation of the three
  clean, fully landed source worktrees. Recovery refs and checkout directories
  were retained; no worktree cleanup or unrelated data deletion was performed.
- **Next slice:** await the retained owner's qualified containing release,
  then use the authorized unified update path and obtain explicit enrollment
  consent where required. Prove actual direct/SSH/supervised/interactive
  entry, default-drift behavior and real launch/update before closing
  Phase 3b. The effort remains Active; installed acceptance is still open.

### 2026-10-16 - Guest review correction preserves comparison-only selectors
- The first implementation review found one canonical defect, repeated across
  its generated copies: the Python status comparison selector could supply
  otherwise missing guest identity. Status now resolves actual identity first,
  then compares the requested name, matching Bash and PowerShell.
- Added pre-enrollment SSH refusal coverage for every native runner and the
  targeted doctor, plus independent selector mismatch controls. All vendored
  copies were regenerated from the canonical source.
- The same review overview identified an enrollment admission race. Every
  runner now re-resolves identity under the real enrollment lock, refusing a
  conflicting registration published after its initial read and preserving
  canonical case for idempotent admission. Native publication-at-lock-entry
  fixtures cover the interleaving without mocking lock ownership or writers.
- Hosted candidate `fc33b115bc4e204b078177e592d2b88d14f871af` passed the existing
  installation-context smoke: 180 passed, one skip, with all three guest writer
  parameters executed. The correction requires fresh hosted proof and review;
  that earlier pass is not reused as current-head acceptance.
- A later Containers CI assertion matches the retained #6791 timing failure,
  owned by #6551. No competing lock-protocol fix or timeout change is included.
  Actual passing required CI remains mandatory.

### 2026-10-16 - Guest identity candidate prepared; POSIX proof pending
- The shared Python, Bash and PowerShell primitive now supports explicit
  guest enrollment and read-only status, without consulting incumbent runtime
  ownership. Native/container detection and distribution conflicts retain
  independent-space refusal.
- Worktrees adds a targeted `doctor --guest-distribution` surface. Report mode
  does not create enrollment; `--fix` enrolls only a matching named direct
  guest entry and never repairs unrelated records or changes the OS default.
- Native Windows core/PowerShell boundaries pass 24 cases with one POSIX-only
  skip; targeted doctor passes 14, including real primitive enrollment,
  repeated SSH-style no-write checks and the actual native CLI front door.
  Existing context and vendoring coverage passes 45 with five platform skips;
  governance/diagnostic smoke passes 127 with seven platform skips. Counts
  describe rosters, not a cumulative test total.
- A separate native PowerShell long-path failure was reproduced and tracked
  in #7010. No unrelated final-path implementation was added to this slice;
  the unchanged governance roster passes at ordinary temporary-path depth.
- Local remote POSIX test invocation was denied before execution. That is a
  validation-surface blocker, not a product verdict. All three POSIX writers
  and readers remain in the existing hosted smoke selection; the candidate
  stays draft until that native proof passes. No installed enrollment,
  release, rollout or acceptance is claimed.

### 2026-10-16 - Guest-identity proposal merged; implementation starts
- #6993 merged as `7887dfb2c6f40e556a298f015e1d629f63a89e63`.
  The source worktree was pulled forward through the supported sync path.
- Phase 3b now implements the reviewed identity/registration and default
  diagnostic boundary. The canonical effort remains Active; proposal approval
  is not implementation, release qualification or installed acceptance.

### 2026-10-16 - Installed compatibility succeeds; guest identity follow-up proposed
- The operator confirmed a successful real launch after the authorized
  Worktrees 1.24.68-dev1 and Manager 0.6.33-dev1 rollout. The historical
  record's machine label was preserved while the launch selected its guest
  execution environment.
- The supported doctor report/apply/re-report flow covered eight adopted
  project/environment registries. It repaired stale lifecycle and reciprocal
  metadata without session garbage collection or checkout removal.
- The updater exposed #6945: the same guest has a distribution identity
  through a direct WSL entry but lacks it through SSH. The installation
  primitive reads the missing variable as ordinary POSIX, then rejects the
  incumbent completed lifecycle owner. The canonical source fingerprint,
  profile and runtime namespace agree; no owner was reset or copied.
- This amendment captures the default-WSL and compact host/guest direction
  for reviewed implementation. The explicit registration mechanism and
  default-drift safeguards in the linked proposal are agent recommendations,
  not a claim that code or OS settings already changed.
- Existing materialized-main qualification gates and original source owners
  remain intact. #6947 and #6948 retain separate bounded repairs.

### 2026-10-16 - Authorized bootstrap merged; containing release remains open
- The retained recovery owner landed workflow-only #6867 as
  `ad7376904b0e622f7f44abf516cbef560b322f36` at `2026-10-16T05:51:54Z`.
  Its exact published head, `332c6fd5e4d89b0cb7b7cce5b0409ab3ebd034aa`,
  received independent approval and passing source-admission and hosted
  provisioning proof. The genuine cleanup-order finding was corrected before
  merge; the earlier head's green proof was not reused as new-head evidence.
- This clears the exceptional bootstrap prerequisite, not Phase 3's release
  gate. Promotion run `38855568035` started from trusted `main` commit
  `8abb234719a922e5a8e280f2bb0b6864adfae474` before the bootstrap merged;
  its existence is not evidence of a promotion using the repaired workflow.
  The retained #6848 owner still owns normal containing-release qualification.
- The effort remains Active. Verify the regenerated release actually contains
  #5689 and #6856, then perform the authorized unified Windows/WSL updates and
  all-existing-worktree and Manager acceptance. No installed result, runtime
  update, manual promotion, policy waiver, archive or resource closure is
  claimed. Attribution-only #6815 remains deferred until deployment.

### 2026-10-15 - Source merged and native proof complete; release remains blocked
- The original #5689 merged as `24f6f61df26f57456c91cfa9ce98e84763266bc7`
  after exact-head review of `bf0457fd6be5c8fac998a6e53c485bb5827e6a66`
  reported no Medium/High findings and the required CI gate passed. The sole
  Low documentation-impact finding was corrected in the PR description.
  Publication used the ordinary supported flow, without a recovery override.
- Final corrections retain native preferred-name compatibility under legacy
  rosters, contextual hostname selection for unscoped host/guest pairs, and
  machine-key precedence when an SSH profile reuses that key. Explicit
  execution-space and profile-only restrictions remain enforced.
- Native Windows executed the final alias/configuration/detection grid
  (210 passed), Machines alias/discovery grid (56 passed), and complete shared
  transport suite (292 passed). The preceding native delta passed 42 Related
  and 92 Manager cases. Earlier native launch, ownership, handoff and staged
  payload portfolios also passed; counts overlap and are not installed proof.
- Final bounded Linux validation passed 1,165 Worktrees guards with 64
  conditional skips, 178 Machines identity/discovery tests and 292 shared
  transport tests. CI's unchanged Index hook exceeded its 20-second bound once;
  one accepted same-head job retry and its dependent required gate passed.
  No timeout, dependency policy or assertion was weakened.
- The independent readiness prerequisite #6856 merged as
  `0731ed01d2d24168ecba15f1769187208e94f218`. Its native Windows portfolio
  passed 24 cases with one POSIX-only socket skip; the real missing-socket host
  portfolio passed 25 cases without creating or stopping a server.
- Promotion run `38752078211` still used trusted release commit `8abb234719`
  and failed artifact-validator provisioning with
  `canonical local graph lacks library attribution`. Existing #6848 retains
  the source/bootstrap owner and explicit-authorization gate. No competing
  repair, unchanged promotion retry, direct release-branch edit or policy
  waiver was performed.
- The effort remains Active. Next is the containing release, followed by the
  authorized unified updates, all-existing-worktree and Manager acceptance,
  and only then archive/release and resource closure. Earlier pending-native
  annotations describe their historical checkpoints, not the current result.

### 2026-10-15 - Align unscoped guest task routing and shared self checks
- Manager action locality now uses the same contextual tuple mapping as source
  construction for all readable rosters, including unscoped host/guest pairs.
  Real task-command construction and resume decisions stay local for supported
  historical host, qualified guest, hostname and alias spellings in WSL.
  Explicit native-space keys remain foreign to the guest.
- Shared guest self checks validate the configured entry before accepting an
  equal name. Native entries, including native keys with guest-looking suffixes,
  cannot use the fast path; registered WSL cells without suffixes remain valid.
  Native callers retain the no-topology-read fast path, and qualified guest
  names retain the absent/legacy-unscoped fallback without granting unknown
  names authority in an explicit execution-space registry.
- Audited direct identity-helper consumers; native/no-guest call contracts
  remain unchanged. Core fixtures now declare their intended actual platform
  or absent registry rather than depending on an ambient detector.
- Bounded validation passed 279 shared transport tests, all 1,143 Worktrees
  guards with 64 conditional skips, and 134 focused Manager/task-routing tests.
  Ruff, install contracts, module ceilings and whitespace checks passed.
- Native validation and the separate no-running-mux readiness prerequisite
  remain coordinator-owned. No mux/session runtime, other implementation source,
  native workspace, publication or deployment was changed here.

### 2026-10-15 - Self-offer, snapshot and interrupted-completion proof
- Alias-spelled source/consumer references resolving to one ledger now reject
  before reservations. The mutation compatibility shortcut compares the
  supplied immutable identity/platform/path with the proven disk record.
- Before source release, ordinary handoffs durably capture child-specific
  source-owner alias equivalence. Accepted completion uses that proof only for
  the named child after receiving-platform/namespace validation. Local/remote
  round-trips preserve it; older exact-reference bundles remain valid, while
  old alias-only bundles without source/proof fail clearly.
- Related current identity, matching and doctor now select the same first
  topology that resolves the current machine, skipping empty/unrelated earlier
  registries without losing exact key/profile precedence.
- A bounded probe confirmed a missing acceptance cell: observed raw hostname
  with no hostname metadata was not local despite a valid preferred identity.
  The shared current-process fallback now covers that name on Windows/WSL,
  including stored-record namespace proof and launch selection. Registered
  foreign keys/profiles and conflicting contexts still override or reject it.
  The 58-case Manager grid now checks the observed name even without metadata
  and retains raw row names while assigning the selected logical source ID.
- Final bounded checks passed: 94 focused Worktrees tests, 86 handoff/recovery
  tests, 12 interrupted-completion cases including owner session fragments,
  1,143 complete guards with 64 conditional skips, 260 shared transport tests
  and 272 Manager tests.
  Counts overlap. Ruff, install contracts, module ceilings and whitespace pass.
- The coordinator's reported native acceptance applies to `c323`, not this
  new delta. Shared identity, Worktrees record/handoff/related/launch adapters
  and Manager locality need fresh native validation; no native workspace,
  publication, deployment or unrelated namespace-repair state was touched.

### 2026-10-15 - Close native handoff, retry and related-availability gaps
- All handoff records now require actual-platform and receiving-namespace
  proof on every platform, including identical machine labels under an empty
  or unscoped registry. Native positive fixtures declare their real receiving
  roots/platforms; foreign-platform and namespace negatives remain intact.
- Accepted ordinary bundles retry from validated consumer/child records
  without loading a retired source project or acquiring a new cross-machine
  fence. Explicit scoped-roster regressions remove the real source ledger and
  retain consumer artifacts and the child's owner transition.
- Producer admission and acceptance both use complete-record locality, so
  historical source labels and qualified guest consumers reach real
  reserve/bind/offer/accept/retry flows without relabeling metadata. Foreign
  namespace/platform records reject offers and acceptance without writes.
- Related resolve/doctor retain exact key/profile precedence and use the actual
  platform only for physical hostname/display metadata. Local, machine and
  container availability cover Windows/WSL case variants without conflating
  siblings; legacy unscoped current-machine reporting stays unchanged.
- Final bounded validation passed 60 exact regression cases and the complete
  guard portfolio (1,101 passed, 64 conditional skips). Ruff, install contracts,
  module ceilings and whitespace checks passed. Native Windows validation,
  current-head review, publication and deployment remain coordinator-owned.

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

### 2026-10-15 - Empty-mux readiness prerequisite
- A known independent blocker, #6739, classifies a normal absent tmux server as
  failed enumeration. The monitor therefore cannot publish its first roster,
  and the local Manager source can fail even when machine matching is correct.
- The bounded repair distinguishes those recognized empty results from genuine
  probe failures in both the monitor and session-liveness paths. No session or
  service was stopped to reproduce the issue, and no dummy server is required.
- This readiness prerequisite does not replace the original identity repair or
  waive its review, release, deployment and installed-acceptance gates.
- Focused hardened-container validation passed 393 tests with five conditional
  skips. The host's actual tmux missing-socket contract passed within a 25-test
  probe portfolio without creating or stopping a server. Probe/parsing
  extraction keeps both existing module ceilings shrink-only.
