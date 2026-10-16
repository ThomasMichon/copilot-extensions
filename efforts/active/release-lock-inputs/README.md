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
The existing exact-profile proposal remains proposed; this slice creates only
candidate constraints, not a new authority or provenance record.

## Request

Continue improving install/update correctness and throughput. While lifecycle
source changes await review and release gates, advance the independent,
unowned input-generation foundation.

## Plan

- [x] Inspect the canonical project inventory, standalone registry, runtime
  extras and existing artifact/profile ownership.
- [x] Implement deterministic generation/check with three separate groups,
  first-party traversal and inherited marker conditions.
- [x] Refuse ambiguous, dynamic, unknown-extra and unsupported source inputs;
  keep the tool read-only except its explicit generated repository file.
- [x] Collect all new contract cases through the already-wired installer guard
  suite; document candidate-only semantics.
- [ ] Clear the exact-head PR review and CI, merge, and record the source result.
- [ ] Transfer resolution/approval/selection/publication/consumer work to its
  named existing objectives without claiming those capabilities here.

## Validation Plan

- [x] Unit/contract: all 137 cases in the existing install-contract suite pass,
  including input graph, extras, cycles, target markers, refusals and drift.
- [x] Simple end-to-end: a real CLI subprocess generates a fresh virtual
  project, checks it, detects changed source constraints without rewriting
  output, regenerates and checks again.
- [x] Fresh input tree: isolated fixtures contain no prior generated output or
  installed runtime. The actual repository generate/check succeeds.
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
