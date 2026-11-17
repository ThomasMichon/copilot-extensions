# Persistent strict files in pending projects

Back to the [canonical effort and Goal](README.md#goal).

**Status:** Proposal; implementation and qualification have not started.
The operator selected the manifest-backed `strict_files` approach for review.
This document adds no test budget, SDK authority, admission or rollout grant.

## Intended outcome

A file that passes the whole-file ratchet must not lose strict checking when
a later PR changes another file or a dependency. Deliver Phase 1a's persistence
criterion without admitting the rest of its pending project, duplicating
ownership in a second ledger or changing the complete-program contracts.

This is a bounded persistence milestone, not completion of Phase 1a. Arbitrary
unmodeled emitted programs, complete new-code coverage and exact-candidate
regression evidence remain independently required by the parent effort.

## Manifest and checking contract

- Add optional `strict_files` to the existing project declaration in
  `tools/typecheck-projects.json`. Entries are exact repository-relative
  physical Python paths, not directories, globs or temporary source-view
  outputs. Every entry must exist and belong to that same declared owner.
  Reject duplicates, invalid paths, missing files and ownership mismatches.
- Keep each project's identity, admission level, dependency lock, import
  roots and interpreter/platform matrix authoritative. Recording files does
  not rename or split the project or change `pending` to `strict`.
- Ordinary enforced checking must include recorded files even without a
  current diff. Use the existing complete-program expansion for Installation
  Context, Payload Invocation and Harness programs; never check a fragment
  alone or omit its required full-body target. A required expansion that
  cannot be qualified remains a visible failure.
- Full strict projects retain complete checking. Basic projects retain their
  complete basic check in addition to strict checking of recorded subsets;
  adding a record cannot remove existing enforcement. A successful recorded subset
  may pass its scoped enforced check while its owner remains pending; an
  all-project diagnostic probe still cannot certify that owner's admission.
  Checks never install dependencies implicitly or substitute an unprepared
  environment.
- Legacy declarations without `strict_files` retain their existing behavior.
  Unsupported consumers must refuse the new policy rather than silently
  ignore it. Qualify the updated source, frozen and candidate front doors
  together; do not assume independently selected tooling upgrades in lockstep.

## Recording and monotonic coverage

Provide an explicit local recording operation after successful complete-file
and complete-program checks on every declared target. Preserve existing
checking-only local commands. Failed, blocked, empty or partially executed
checks cannot write a passing record; CI never silently edits or commits one.
Any new command spelling is part of the implementation's interface review,
not an already available command in this proposal.

Required source CI must reject added or modified pending-owner Python that
lacks persistent strict coverage, without grandfathering old diagnostics in
the touched file. Reuse the existing manifest/policy owner for this obligation,
not a second policy ledger or a history-wide scan.

Coverage is monotonic across reviewed changes. Removing a record is permitted
only when the file is actually deleted, correctly renamed into another
enforced scope, or covered by genuine full strict admission. A dependency,
owner or configuration change must not silently shed the recorded file or
weaken its checks.

Keep the physical-file inventory single-owner and report effective persistent
file coverage separately from whole-project admission. A partially recorded
project remains pending in the project numerator/denominator. Inventory is a
policy projection, not executed qualification; the merged coverage tracker
credits only actually merged enforcement.

## Source and candidate integration

Use the existing project manifest and normal checker paths in source CI and
the frozen/exact-candidate checks. Preserve canonical-source and generated-copy
correspondence and the existing full-program views. Qualify that captured
inputs contain the policy and its complete checker closure.

The existing Main controller is not changed by this proposal. If realization
would require another Main-controller operation, stop for its separately
scoped authorization rather than treating this plan as permission.

Required PR CI remains change-scoped and unit-only under the existing test
portfolio. Add focused guard tests and retain real MyPy/process boundaries
where they are the contract; do not introduce an unconditional exhaustive
process matrix. Measure selection/checking cost, reuse qualified dependency
environments and caches, and preserve the existing deadlines. A budget failure
is a blocker to resolve, not permission to omit records or raise a deadline.

## Validation obligations

- [ ] Prove legacy-manifest compatibility and validation of paths, duplicates,
  existence and owner membership, including invalid or empty coverage claims.
- [ ] Prove recording has no successful side effect after failed, blocked or
  incomplete checks, and that all declared targets must pass before recording.
- [ ] Prove an unchanged recorded file is selected by the normal enforced
  command in a later unrelated PR and after dependency/configuration changes.
- [ ] Use a real dependency-only type regression: the changed dependency
  remains internally type-correct, but an unchanged recorded caller fails.
  Verify rejection through normal source and frozen/candidate entry points.
- [ ] Preserve complete known-program expansion, all target versions/platforms,
  ordinary module identities and canonical/copy correspondence. Missing
  composition or full-body coverage must fail rather than yield an empty pass.
- [ ] Reject removal or migration that drops live recorded coverage; permit
  real deletion, correctly enforced rename and genuine full strict admission.
- [ ] Reconcile effective file coverage and unchanged project admission counts,
  without double ownership or crediting proposed declarations as merged.
  Prove that mixed basic/strict coverage retains the complete basic check.
- [ ] Exercise the required source and exact-candidate contracts at the actual
  accepted source, with real negative controls and no Main-controller bypass.

## Execution and return

Land this proposal through normal review before implementation. Then identify
the smallest source/CI integration slice and its new or genuinely affected
controls. Reuse only environments whose native owner permits their actual
inputs. Missing or stale setup remains a blocker; held SDKs, original failed
clocks, protected IPC and resource/custody limits remain unchanged.

Obtain any genuinely missing bounded qualification authority before running
that packet. No prior one-shot budget is renewed by this plan or its review.
After each accepted slice, return to the parent Goal and remaining Phase 1a
criteria; neither this milestone nor a passing recorded subset completes the
all-Python campaign.
