# Third-Party Release Lock Inputs

- **Slug:** `release-lock-inputs`
- **Repo:** copilot-extensions
- **Branch(es):** one bounded input-generation PR
- **Created:** 2026-10-16
- **Status:** Active
- **Vision:** closes the input-generation portion of Runtime Assembly's release lock and no-parallel-provenance intent
- **Umbrella issue:** #6937
- **Composes with:** #4876, #6234 and #6707; approval, selection and consumer admission remain with their existing owners

## Guiding Intent

Produce one deterministic, reviewable set of third-party runtime declarations
from the canonical first-party sources, without resolving or installing
anything. Keep optional heavy environments separate and preserve the conditions
under which each dependency is required.

## Participants

| Participant | Role | Reached via |
|-------------|------|-------------|
| PR submitter | Candidate input generator, guards and documentation | #6937 and its linked PR |

## Coordination

The [installation convergence map](../source-managed-runtime-installs/installation-convergence.md)
uses these candidate declarations through their existing resolution/admission
owners before app staging. They are not approved locks or a new constructor.
Fresh install and update share that input path; the existing source exceptions,
consumer acceptance and unresolved Plan/Validation rows remain unchanged.

- **Topology:** one isolated implementation PR.
- **Host:** the #6937 claimant owns this slice through review and merge.
- **Delegates:** none.
- **Boundary:** no profile selector, approval source, constructor, credential
  provider, workflow change or production rollout. Do not pre-empt the existing
  artifact/admission work.

## Context

The existing canonical inventory already excludes real vendored copies.
Standalone consumers also have a registry. Reuse those sources instead of
copying a roster or treating a build-toolchain lock as a runtime lock.
Existing exact-profile and artifact owners retain their later boundaries;
this slice creates only candidate constraints, not a new authority or provenance
record. Root/extra/condition selection is shared with the core artifact builder
through `python_project_inventory.runtime_roots`, not a second local roster.

The current universal input projection still refuses the qualified local
`dbus-next` wheel source. #7606 retains its existing native validation and
removal gates. Do not drop that mapping, substitute a public source, extend
its exception to a different project or treat the calendar date as permission.

The bounded target/group API provides an independent input seam without
claiming a universal lock. It uses complete explicit marker facts, preserves
canonical traversal, checks applicable Python compatibility and refuses
unrepresentable resolver/source settings. Only an explicitly inactive edge may
be omitted; target attribution is not native qualification or approval.
Default CLI generation/check and the active-Linux refusal remain unchanged.

The incremental proposal delivers the inert projector and scoped API, not a
checked-in universal artifact that the current source graph cannot qualify.
The proposed real-tree positive check is replaced by an explicit origin-refusal
and no-write control; positive universal generation/check/drift remains tested
on isolated complete graphs. No existing upstream runtime/publication guard is
removed. Universal source-qualified data remains open under #6937 with #7606
as its native-origin prerequisite.

## Request

Continue improving install/update correctness and throughput. While lifecycle
source changes await review and release gates, advance the independent,
unowned input-generation foundation.

## Plan

- [x] Inspect the canonical project inventory, standalone registry, runtime
  extras and existing artifact/profile ownership.
- [x] Implement deterministic generation/check with three separate groups,
  first-party traversal and inherited marker conditions.
- [x] Adopt the core builder's shared canonical root selector, preserving the
  input owner's refusal when any of its three environments has no roots.
- [x] Refuse ambiguous, dynamic, unknown-extra and unsupported source inputs;
  keep the tool read-only except its explicit generated repository file.
- [x] Provide a pure group/target projection seam, preserving the default
  universal refusal and auditing unrepresented external source mappings.
- [x] Collect all new contract cases through the already-wired installer guard
  suite; document candidate-only semantics.
- [ ] Clear the exact-head PR review and CI, merge, and record the source result.
- [ ] Transfer resolution/approval/selection/publication/consumer work to its
  named existing objectives without claiming those capabilities here.

## Validation Plan

- [x] Unit/contract: the current 91 input-focused cases pass through the existing
  install-contract test module, including input graph, extras, cycles, target
  markers, refusals, drift and shared-selector ownership. Historical full-suite
  results are retained in the journal, not claimed for a later source head.
- [x] Simple end-to-end: a real CLI subprocess generates a fresh virtual
  project, checks it, detects changed source constraints without rewriting
  output, regenerates and checks again.
- [ ] Fresh input tree: isolated fixtures contain no prior generated output or
  installed runtime and pass. Current real-repository generation/check remains
  blocked by the qualified source above; resolve through #7606's actual gates
  rather than emitting or checking in unqualified universal inputs.
- [ ] Hosted exact-head CI exercises the existing wired guard.
- [x] Live installation is not applicable: this slice resolves/downloads
  nothing, creates no runtime and changes no machine configuration.

## Proposal

Below the approved Runtime Assembly vision's altitude. The candidate input
tool does not select a lock, approve a feed, publish an artifact or introduce a
second installation/provenance path.

## Journal

### 2026-10-16 - Input generation
- Claimed #6937 for this bounded slice only. Existing artifact/admission
  owners are unchanged.
- Reused canonical discovery and path containment plus the standalone
  registry. First-party traversal retains declared extras and transitive
  platform/Python conditions without evaluating them on the host.
- Generated the real-tree virtual project; all 127 wired guard cases and
  targeted F/E9 checks pass. Review, hosted CI and merge remain open.
- CI found a missing native UV cutoff on the generated virtual project.
  Generation now inherits the shared seasoning constant; the real policy
  validator checks the generated data. Five additional cases first
  reproduced malformed-name/dynamic/flag acceptance and silently substituted
  vendored runtime metadata. Those now refuse explicitly; all 132 wired
  cases pass, and the actual repository regenerates/checks successfully.
- Review found that direct metadata equality did not validate the selected
  copy's nested source graph, and that the tooling's 3.11 floor had leaked
  into generated runtime targets. Four new cases failed first. The same
  traversal now retains selected manifest identity through nested mappings
  and cycles; it does not substitute a canonical child graph. Runtime
  Python ranges derive from the first-party closure separately from the
  tooling prerequisite. All 137 wired cases pass; real-tree output retains
  Python 3.10. Current-head review/CI and merge remain open.
- Three further review findings reproduced missing project metadata, dynamic
  Python metadata and unconditional intersection of a conditional child's
  Python range. Five new cases cover the failures and safe redundant-range
  behavior. Generation now refuses missing/dynamic metadata and conditional
  ranges not already represented by the unconditional group constraints,
  instead of silently narrowing the target set. All 142 wired cases and
  targeted lint pass; real-tree generate/check succeeds without input drift.

### 2026-10-28 - Shared canonical root selector
- Refreshed the four existing implementation commits onto current development
  source, preserving both independent effort-index additions at the sole
  rebase conflict. No source or native qualification gate was discarded.
- Two controls fail before correction: the input owner never calls the shared
  selector, and it ignores a shared result with missing heavy-environment roots.
  `_roots` now delegates to `python_project_inventory.runtime_roots` while
  retaining the three-environment refusal. The duplicate optional-core roster,
  portable-condition declaration and root classification are removed.
- All 36 input-focused controls pass; targeted fatal lint and diff checks pass.
  This is source preparation for #6941, not a merge, real universal
  qualification or production lock. #7606's native gate remains open, and no
  dependency resolution, source replacement, installation or activation runs.

### 2026-11-02 - Explicit target and group inputs
- Added `groups` and complete `target_environment` parameters to the existing
  collector/renderer, not a second traversal or resolver. The default CLI and
  checked-in candidate remain unchanged when the universal source cannot be
  represented.
- Initial API controls fail 44 cases with 37 baseline/default passes. Review
  adds six prerelease and transitive-only source-mapping controls that fail
  before correction. All 91 focused cases now pass, with syntax, fatal lint
  and existing module ceilings unchanged.
- Actual Windows/Python 3.11.16 core projection produces 27 direct external
  requirements deterministically in memory. Default all-three, untargeted core
  and a synthetic active-Linux case retain the qualified-origin refusal.
  Existing candidate bytes are unchanged. Direct requirements are not a
  resolved transitive wheel closure.
- No package resolution, compilation, network acquisition, profile approval,
  publication or runtime change is performed by this seam. Exact-head
  publication/review/merge and the original native-origin gate remain open.
- Current-source replay confirms the scoped controls pass while the earlier
  proposed universal-file assertion fails on that retained origin. The
  incremental proposal therefore withholds the unqualified generated file and
  tests the real no-write refusal explicitly. It does not claim that universal
  data became valid or silently transfer that qualification into the API.
