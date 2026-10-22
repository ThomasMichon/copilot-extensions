# Materialized candidate consumer validation

**Historical proposal:** #7255 superseded this generated/native integration
matrix as a promotion prerequisite. The reviewed
[unit-only release contract](../dev-branch-release-pipeline/promotion-burndown.md)
governs current admission; retained integration source, evidence and owners
belong to the periodic portfolio, not a restored release gate.

Supporting slice of [Main-Owned Workflow Control](README.md), implementing the
reviewed final-composition and exact-input validation items in Phases 3 and 5.
This document proposes the bounded execution contract, not an enabled gate or
permission to change production credentials or branch policy.

## Request and scope

Additional operator direction, verbatim:

> Make the necesary fixes, and figure out how to make this more robust

The current release can pass development-layout validation and publish a
materialized tree whose ordinary consumers fail. The new gate must catch that
representation change before `main` advances, without weakening post-merge
verification or competing with retained consumer repairs.

Existing writers retain Budget Guidance #6801, Index #6809, the standalone
preparation/Manager and historical/current authority-corpus reservation, and
the separately coordinated Dispatch/SSH/Pull Requests fixture families.
Historical authority evidence is not rewritten and old authority hashes do not
gain runtime acceptance. This slice owns only the candidate-validation
orchestration, contracts and directly related documentation.

## Proposed execution

1. Keep the existing full development validation, materializer, versioning,
   artifact build/attestation and candidate-readiness transaction.
2. After the trusted promoter creates the exact candidate commit/PR, but before
   `merge-promotion`, run a separate read-only consumer-validation job against
   that immutable candidate. No candidate code executes in the release writer,
   signer, merger or publisher.
3. Use fresh public-hosted Linux/Windows runners, `contents: read`, credential-free
   checkouts, no release environment, no release PAT, no OIDC and no persistent
   self-hosted runner. Candidate tests and their normal test tooling are
   intentionally unprivileged code, not trusted signing implementations.
4. Pin the candidate commit, development input and main parent from trusted
   promotion outputs; reject malformed identities, a checkout at another commit
   or inconsistent composition state before tests. Check tracked input
   immutability again after execution.
5. Exercise the actual materialized consumers rather than path-existence
   proxies: plugin test collection and normal smoke/guard recipes, standalone
   dependency preparation/tests on their existing platforms, and Manager
   collection/tests. Retain the distinct existing full development, heavy,
   native-platform and installation lanes.
6. The merger requires the candidate job's authoritative successful result
   and the same candidate head. Failed, skipped, cancelled or missing required
   work cannot authorize merge. Keep the existing final freshness check,
   expected-head merge, artifact-readiness checks and post-merge verification.

Initial correctness favors the existing main-consumer recipes over an
unproved reduced selector. This temporarily adds generated-tree execution;
measure it explicitly. A compact materialization-only selector or reuse of
equivalent results is a later, separately proved consolidation under the
parent design's exact-input boundary, not an excuse to omit current failures.
Do not relabel the artifact attestation as test evidence.

## Implementation and validation plan

- [ ] Confirm no conflicting candidate-gate implementation with the retained
  #6237 owner; preserve the active consumer PRs and publisher ownership.
- [ ] Implement a bounded candidate-input validator and fixed consumer recipe
  interface, using the existing test runners and their containment/admission.
- [ ] Integrate the isolated gate before `merge-promotion`; require success
  explicitly and include the new failure producer in existing failure reporting.
- [ ] Add negative contracts for wrong commit, wrong dev/main composition,
  tracked-source mutation, failed/absent consumer execution and unsafe
  credentials/runner permissions.
- [ ] Reproduce at least dependency-source conflict and embedded-test collection
  failures with real generated fixtures; show refusal before the merge seam.
- [ ] Prove repaired source and real materialized consumer cases, including the
  existing owners' actual regression selectors rather than unrelated green tests.
- [ ] Qualify the actual hash-pinned validator bootstrap on Linux and Windows
  from a fresh venv/cache, using the same deny-local caller recipe as build,
  attestation, promotion and publication; retain #6865's separate emergency
  recovery and normal source/control activation boundaries.
- [ ] Preserve each existing full runner's aggregate execution budget and allow
  preparation, all recipe invocations and enclosing hosted-job setup; reject
  unreadable budget metadata rather than shortening a healthy suite's deadline.
- [ ] Clear current-head review and required CI; land ordinary implementation
  through `dev`, never direct-to-main source changes.
- [ ] Observe a normal containing promotion and successful exact-generated-tree
  consumer validation, then the unchanged post-merge immutable verification.
  No manual workflow retry/dispatch, runtime update or gate waiver is implied.

## Publication boundary and recovery

The current main preservation manifest must be resolved before activation.
If the critical caller is preserved, ship inert reusable implementation via
`dev` first and obtain the existing owner's separately reviewed main-wrapper
activation; never silently override release-owned control. With no preservation
declaration, use the ordinary contribution/promotion lane.

The existing operator-accepted trusted-collaborator boundary remains unchanged;
this is not a cryptographic defense against a malicious writer replacing tests.
No new signer, credential, scheduler or competing release writer is introduced.
If the new consumer gate fails, leave `main` unchanged and retain its exact
candidate/log evidence for the named component owner and #6237 release steward.
If gate integration is defective, correct or revert its ordinary source PR;
do not disable required consumer checks to ship an unrelated release.

Completion means both the consumer repairs and the actual gate have their
required evidence. A merged proposal, an inert helper, passing development
tests or another published version is not completion.
