# Adopter Material Refresh

- **Slug:** `adopter-material-refresh`
- **Repo:** copilot-extensions
- **Branch(es):** independent, serial per-phase PRs to `dev`
- **Created:** 2026-10-08
- **Status:** Active
- **Vision:** `visions/README.md` and the subject-specific visions, used as
  intent boundaries rather than evidence that a capability is shipped
- **Umbrella issue:** `ThomasMichon/copilot-extensions#5799`
- **Sub-issues:** carved after the reviewed inventory identifies discrete work

## Guiding Intent

Bring adopter-facing documentation and preview assets back into agreement with
the repository's supported, shipped capabilities. An adopter should be able to
understand what exists, follow a current workflow, and recognize the current
operator surface without translating obsolete claims or images.

This is a **separate sibling** of `vision-backport-sweep`, not a new phase of
its completion gate. Visions describe standing intent; these materials describe
what an adopter can actually use. A future feature or an effort's Done marker
is not sufficient evidence for a present-tense capability claim.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Planning lead | Owns the bounded proposal and review gate; does not assume material-execution ownership | managed proposal worktree |
| Execution lead (unassigned) | Claims the independent campaign before executing its reviewed phases | umbrella issue and a new managed worktree |
| Documentation participant (complete) | Primary structure/prose and checks verified and merged; responsibility released; preview/reference work remains unassigned | merged `#6289` and `#6312`, umbrella `#5799` |

## Coordination

- **Topology:** serial per-phase PRs; one coherent material family per slice.
- **Host (owns PRs):** planning lead for the proposal; documentation participant
  for the merged direction/implementation and their bounded closeout. Further
  material PRs require a new explicitly claimed execution lead.
- **Delegates:** none assigned. A future lead may use disjoint, bounded evidence
  or asset scopes while retaining integration and publication ownership.
- **Handoff:** planning publication transfers execution to the independent
  tracked umbrella, not back into the vision sweep. A claimed execution slice
  retains responsibility through actual merge and its validation.

## Context

- Seed: the operator's original material concern in
  [archived `vision-backport-sweep`](../../2026/10/09%20vision-backport-sweep/README.md), umbrella
  `ThomasMichon/copilot-extensions#5456`.
- Decision: the operator selected a separate sibling and confirmed this slug.
  The proposal did not capture screenshots, rewrite a material family, or
  change runtime behavior. The documentation participant subsequently refreshed
  the primary reading family; this does not declare all materials current.
- Target-owned effort adoption was verified with the efforts plugin's exact
  read-only capability probe. Tracker and active-effort searches found no
  existing campaign for this combined scope.
- **Agent-recommended planning approach:** inventory before rewriting; ground
  each claim in the owning implementation and the referenced build; reuse
  existing docs/asset generation surfaces rather than introducing parallel ones.

## Request

Original material concern, verbatim:

> "The docs, preview images, and other user-facing materials are also getting out
> of date with all the new improvements, enforcements, and capabilities."

Relationship decision, verbatim:

> "Separate sibling effort"

Confirmed slug, verbatim:

> "adopter-material-refresh"

The phase structure and validation obligations below are **agent-recommended**,
not additional operator-requested product features.

## Plan

### Bounded documentation participant

- [x] Land documentation intent and page-role architecture before dependent
      adopter-page rewrites.
- [x] Inventory and refresh the primary reading path: README, adopter index,
      capability/adoption, configuration, architecture, and runbook waypoints.
      Preserve component references and current command/configuration owners.
- [x] Ground installation and support claims in manifests and owning
      implementation; label generic deployment examples as hypothetical.
- [x] Validate changed navigation and existing documentation guards; extend an
      existing guard only for an objective invariant, with fixture tests.
- [x] Land the implementation slice, journal merged references, and release
      this participant's responsibility without completing the whole campaign.

Preview capture and asset regeneration remain **outstanding and unassigned**.
The campaign execution lead and exhaustive plugin-reference inventory are not
implicitly assigned by this bounded documentation claim.

### Phase 0 — Publish the independent reviewed plan
- [x] Confirm sibling relationship and slug; verify target adoption and dedup.
- [x] Land this proposal through the repository's normal review gate before
      material execution.
- [x] Record the merged plan and leave execution independently claimable under
      the umbrella issue.

### Phase 1 — Inventory and baseline (agent-recommended)
- [ ] Inventory root/plugin README claims, adopter procedures, linked reference
      pages, Picker preview images, captions, and asset-generation sources.
      Keep the inventory in a linked sibling document when it grows.
- [ ] For each material family, identify the owning implementation, referenced
      shipped build, supported venue/mode, and current evidence.
- [ ] Dedup discrete gaps against existing documentation/feature issues and
      carve bounded material tasks without copying their implementation scope.

### Phase 2 — Correct claims and workflows (agent-recommended)
- [ ] Refresh one coherent documentation family per reviewed slice; preserve
      current installation, account, contribution, ownership, and recovery
      boundaries.
- [ ] Verify examples through the real supported consumer contract where
      practical, using public-safe fixtures and scoped identities.
- [ ] Distinguish shipped support, optional capabilities, compatibility limits,
      and standing future intent; do not make a docs-only patch claim to fix a
      runtime gap.

### Phase 3 — Refresh preview assets (agent-recommended)
- [ ] Reuse the owning public-safe asset/capture tooling, or establish the
      smallest justified reproducible fixture when none exists.
- [ ] Capture the actual supported surface against an identified build and mode;
      exclude private repositories, machine identifiers, personal activity,
      credentials, and unredacted logs.
- [ ] Update descriptions, links, and accessibility text with the asset; retire
      misleading previews without removing the capability they represented.

### Phase 4 — Cross-surface validation and closeout (agent-recommended)
- [ ] Resolve the inventory or transfer each residual item to a named tracked
      objective; retain truthful limitations where evidence is unavailable.
- [ ] Verify navigation, examples, captions, and build/mode attribution across
      the updated surfaces.
- [ ] Journal merged work, promote durable capture/generation procedures into
      owning docs, and declare Done only after Plan and Validation Plan resolve.

## Validation Plan

- [x] The proposal passes effort-structure and repository review gates before
      execution begins.
- [ ] Every changed present-tense claim has implementation/build evidence;
      aspirational vision text is not substituted for that evidence.
- [ ] Every changed example/link/asset is validated with the owning tooling or an
      explicit, tracked reason when a required lane is unavailable.
- [ ] Preview provenance identifies the actual build and mode, and all captured
      inputs/outputs are public-safe and credential-free.
- [ ] Final material families are internally consistent across README, linked
      docs, images, captions, and accessibility descriptions.
- [ ] All Plan and Validation Plan items are resolved or explicitly transferred
      before execution declares the sibling effort Done.

## Proposal

**Reviewed plan; primary documentation slice complete, remaining execution unassigned.** The operator confirmed relationship
and name, and the agent-recommended execution/validation structure cleared the
normal review gate in `#5807`. No runtime implementation, service repair,
repository creation, or private-environment capture was part of that proposal.

## Journal

### 2026-10-12 — Verified primary-documentation slice released
- Direction `#6289` merged as `26680fbad595691d93276fc4e9e94e492c61e703`;
  implementation `#6312` merged as `fdc8f2f7e6f849a08765ec1b7bfb041b68abb5e8`
  after current-head Copilot approval with zero findings and required CI run
  `38266255352` plus workflow-lockdown success. No review/CI bypass was used.
- Re-read the canonical effort after actual merge. All bounded documentation
  participant items are resolved. The primary adopter family, preserved
  references, source-grounded claims, and dependency-free existing-guard
  enforcement are upstream on `dev`; the documentation responsibility is
  released. This closeout records the completed slice, not campaign completion.
- Local proof includes twenty-nine focused fixtures, F/E9, live primary
  navigation/anchors, owner identifiers, effort/vision structure, and size
  guards. All repository-selected CI suites passed. Runtime install, clean-room
  provisioning, external-venue execution, and preview capture were not part of
  this documentation-only scope; no installed plugin/runtime was updated.
- Preview provenance/capture/assets and the exhaustive remaining plugin-owner
  reference inventory remain **outstanding and unassigned under `#5799`**.
  The known mesh-setup payload drift needs a separately claimed,
  changefile-bearing plugin slice. No campaign execution lead is implicitly
  appointed, and the overall Plan/Validation Plan and Active status remain open.
- Canonical index validation also repaired two moved archive link targets
  and aligned their index grouping with the existing canonical Done status,
  without editing those archived efforts or claiming their work.

### 2026-10-12 — Documentation structure and prose claimed
- Reused umbrella `#5799` and reviewed proposal `#5807`; no competing open
  documentation slice was found. The documentation participant owns serial
  direction and implementation PRs, not runtime changes or preview assets.
- Generic request capture: make the toolkit's adopter path current,
  navigable, and honest about optional dependencies and supported behavior;
  keep hypothetical deployments separate from capability claims.
- Baseline: the README fronts unavailable Configurator bootstrap commands
  before current install instructions, repeats runtime diagrams and references,
  and makes a suite-wide platform assurance. The runbook's curated set carries
  obsolete total counts. Existing catalog/skill/count and runbook-reference
  guards remain the validation owners; no dedicated adopter docs index exists.
- Added a documentation vision and current page-role map as the direction
  gate. Dependent adopter rewrites wait for that PR's actual merge.
- Screenshot phases remain outstanding/unassigned; this participant will not
  declare the campaign Done or replace its existing completion gate.

### 2026-10-12 — Direction gate merged; adopter path implemented
- Direction `#6289` merged as `26680fbad595691d93276fc4e9e94e492c61e703`
  after required CI and a passing current-head Comment with no Medium/High
  findings. The substantive prior head also received approval. The additive
  vision-index conflict was reconciled without dropping the other vision.
- Refreshed the root front door and primary adopter family: installation,
  capability selection, adoption examples, docs index, configuration,
  architecture, and runbook. Detailed references remain in their owning
  components and are collected in a reference index.
- Removed future Configurator bootstrap from the executable quick start,
  blanket platform assurances, fixed relay-port narrative, obsolete curated
  totals, and the assumption that every deployment requires the full mesh.
  Runtime requirements come from manifests/owner guides; hypothetical
  deployments are explicitly labeled.
- Extended the existing docs-consistency guard for objective primary-page
  paths/anchors and reading-path edges, with focused fixture coverage in the
  existing CI guard step. No runtime, plugin version, or installed deployment
  changes are part of this slice.
- Preview assets and exhaustive remaining plugin-reference/asset inventory
  stay outstanding under `#5799`; completion of these adopter pages cannot
  complete the whole campaign.
- Residual owner-reference audit: the mesh setup skill still carries legacy
  fixed-port/import-order prose, while current architecture describes
  discovered endpoints and filesystem providers. Reconcile that plugin-owned
  payload in a separately claimed changefile-bearing slice, not by inventing
  alternate mechanics in these root waypoints. No plugin payload is modified
  by the documentation participant.
- Local validation: twenty-nine focused guard fixtures, F/E9 lint, real primary-path
  navigation (including exact casing), runbook/plugin identifiers, canonical
  effort/vision structure, and staged size checks pass. Root references are
  retained through the primary family; existing preview files are untouched.
  Clean-room and external-venue installation tests are not applicable to this
  documentation/guard-only change; repository-selected CI remains a separate
  publication gate. Identifier lint has no configured private denylist, so
  public-neutrality also received manual review.
- Plugin activation guidance follows the current installation owner: ordinary
  plugin loading is not experimental; trusted repo payload activation differs
  from user-level session-extension activation. The guard covers shortcut,
  collapsed, and full reference links without treating unused definitions or
  labels inside other link forms as navigation.
- Selected session extensions retain the separately documented user-global
  experimental activation gate from the install contract; ordinary payload
  loading does not inherit that prerequisite.
- Guard fixtures also prove fence indentation/closure, image-vs-hyperlink
  reading edges, exact casing, and resolved symlink containment (including a
  directory README anchor) without reading external targets.
- The current pull-request owner implements status/create/merge/wait; primary
  capability pages expose those verbs and the account-resolution dependency.
  The composition reference now describes the relay's discovered dynamic port,
  explicit pinning, and compatibility fallback rather than a fixed default;
  the elevated sub-daemon row also follows its actual discovery owner.
- Comment state is interpreted before fence markers outside real code, and
  angle-bracket destinations retain spaces in inline/reference links. Focused
  fixtures cover those rendered-navigation boundaries without changing runtime
  behavior or introducing another documentation pipeline.
- Reference definitions preserve the first normalized label, matching rendered
  Markdown even when later definitions change casing or whitespace.
- The self-contained navigation guard also enforces the sequential waypoints
  advertised by the adopter index. Multiline code-span and graph-edge fixtures
  prove those primary-page contracts without new runtime/tool dependencies.
- A mandatory external-parser proposal was withdrawn: the promotion workflow
  loads from `main` and cannot bootstrap a new dependency solely from a `dev`
  change before that change promotes. Retaining self-contained validation
  avoids that release deadlock without skipping checks or taking an
  unauthorized main-workflow bootstrap path.
- HTML targets use the standard-library parser, distinguishing real
  href/src/id attributes from data attributes and honoring attribute syntax
  without introducing a dependency.

### 2026-10-08 — Independent planning seed
- Captured the original material concern and both literal follow-up selections.
  Re-read them against the operator's words; no scope choice was softened into
  an open question.
- Created umbrella `#5799` after dedup and verified target effort adoption.
  Prepared this Draft proposal; execution remains unassigned and separately
  claimable, and the vision sweep's remaining audit gate is unchanged.
- Optional worktree-effort binding was refused by path-format validation, so
  this proposal continues with the standalone effort lifecycle. No tracking
  record was hand-edited to manufacture a binding.

### 2026-10-09 — Planning publication complete
- Proposal `#5807` merged after current-head Copilot approval with zero findings
  and passing required CI/PR gates. Publication was recorded on `#5799`, and
  the planning worktree was finalized after provider-confirmed merge.
- Phase 0 and the proposal-review validation are complete; execution remains
  unassigned and independently claimable under `#5799`. The effort stays Draft,
  not Done, and no documentation family or preview asset has been refreshed.
