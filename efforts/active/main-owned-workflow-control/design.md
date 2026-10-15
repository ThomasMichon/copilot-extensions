# Proposed control ownership and migration

Parent contract: [README.md](README.md).

## Primary outcome and sequencing

The operator's primary goal is fewer redundant status checks and fewer overall
pipeline runs, so Copilot reviews can run more often without throttling.
Main-owned control is a means to that end, not a higher-priority rewrite.
Take safe reductions covered by existing proven gates first. Introduce ownership
changes only when the next reduction or critical repair needs them.

Measure event-normalized runs, jobs, reported checks, reviewer start/queue latency
and observed throttle responses. Skipping jobs is not equivalent to eliminating
a workflow invocation. Actions runner concurrency and Copilot service quotas may
be independent; do not promise a causal improvement without observed evidence.

## Ownership

Keep main as GitHub's default and consumable release branch. Critical control
declarations and control metadata may change through authenticated-owner,
reviewed main PRs. The complete diff must fit the trusted allowlist. A mixed
workflow/source PR is rejected even from an admin; metadata cannot authorize
other changes in its own PR.

Reusable helper source, tests, documentation and implementation workflows/actions
remain dev-owned. Critical wrappers use local `workflow_call` implementations
from the same composed commit, never mutable `@dev` or arbitrary YAML loading.
Privileged helper imports and action references are part of the validated
execution contract. An unprivileged test job must not inherit publication
credentials or persistent trusted-runner access.

Use distinct top-level control and implementation workflow names. GitHub does
not discover nested workflow directories. Initial exact ownership paths must be
specified and owner-reviewed before activation; broad directory exemptions are
not the default.

## Phase execution gates

Phase numbers refer to the parent effort. The campaign driver executes each
slice and owns its PRs/evidence. The repository owner additionally authorizes
Phase 4 critical main PRs and any separately requested administrative mutation.
No delegated executor is implied; assign one explicitly before delegation.

| Phase | Prerequisite | Required evidence | Abort condition | Reversible recovery |
|-------|--------------|-------------------|-----------------|---------------------|
| 0 - Baseline | Operator mandate and reconciled coordination | Reviewed plan, classified workflow/dependency inventory and live policy snapshot | Competing owner, unclear trust boundary or missing baseline | Keep existing pipeline; correct plan before execution |
| 1 - Safe reductions | Phase 0; existing gates cover the specific removed work | Event-normalized counts, preserved required coverage/contexts and live review delivery | Lost coverage, missing required status or stalled release/review delivery | Revert the affected reduction through its authorized PR lane; preserve existing release gate |
| 2 - Preservation | Phase 0; no active manifest | Strict-path unit/real-Git tests and released inert support | Ambiguous paths, changed legacy behavior without a manifest or incomplete provenance | Revert dev implementation; never activate the manifest |
| 3 - Admission | Phase 2 for composition-dependent work; independent admission work may follow Phase 0 | Negative trust tests, final-tree evidence, helper compatibility and enforced freshness | Spoofable claimed boundary, unsafe execution or unfenced stale merge | Keep new writer disabled and old gates active; revert inert additions |
| 4 - Activation | Phases 2 and 3 proven and support released | Owner-reviewed allowlist, exclusive writer, live canary, repair survival and rollback | Missing proof, dual writer, stale control input or policy restoration failure | Fence publication, drain admitted runs, revert activation and restore captured policies before resume |
| 5 - Consolidation | Phase 1 baseline; Phases 3/4 only for reductions using their new guarantees | Coverage-equivalent exact-input reuse, migrated callers/checks and named repair ownership | Distinct coverage lost, orphaned required check or unowned failure | Restore the affected old lane/context before disabling its replacement |
| 6 - Completion | All preceding scoped gates resolved | Two cycles, failure/recovery, rejection/repair proof and final policy/performance audit | Unresolved validation or unsupported throughput claim | Remain Active; revert offending slices or transfer unresolved work to a named tracked objective |

Each implementation PR records its exact before/after identities, commands,
results and applicable rollback commit. Policy/credential changes require a
separate bounded confirmation; these recovery descriptions do not authorize
automatic bypass or direct-main source changes.

## Composition

Extend, do not replace, the existing changefile/materialization composer:

1. Pin dev and main inputs.
2. Materialize dev content and generated versions through the existing engine.
3. Read the ownership declaration from pinned trusted main, never dev.
4. Preserve only declared regular control files from that main tree.
5. Validate final composition, compatible local implementations and provenance.
6. Admit the exact generated candidate through independently trusted gates.

Absent declaration keeps current behavior until support has safely shipped.
An active malformed declaration fails, never silently falls back. Validate
paths canonically on every OS; reject traversal, case-equivalent duplicates,
unsafe modes/types, missing declarations and ambiguous ownership.
Intentional retirement changes the trusted declaration through review before
the corresponding promotion; no accidental deletion or arbitrary drift retention.

## Evidence, freshness and trust

Bind evidence to repository, exact dev/control commits, execution recipe,
expected suites/platforms, run and attempt. Read authoritative run/job evidence;
do not accept an arbitrary dispatch payload or candidate-provided success blob.
PR synthetic-merge tests are not evidence for a different dev snapshot.
Generated output needs its own contract tests, not only pre-materialization tests.
The bounded [materialized-candidate validation slice](materialized-candidate-validation.md)
specifies the proposed pre-merge consumer gate while retaining the existing
post-merge verification and repair ownership.

Final merge freshness must be enforced. A candidate composed against an older
main cannot overwrite a later critical repair. Select and prove a provider
mechanism before enabling publication; a last-minute read plus an unfenced merge
still has a race. Rebuild stale candidates and revalidate changed inputs.
Rollback restores release-owned content while preserving current control and
uses the same admission/provenance contract.

Authenticated owner-only checks protect both targets' critical wrappers,
implementation/actions and ownership metadata. Required check names emitted by
the same Actions app are not a cryptographic guarantee against a malicious
Write actor. Either enforce an independently trusted publisher/required-workflow
mechanism available on this account or explicitly document the accepted narrower
threat model. Do not enable a stronger claimed guarantee without proof.

## Cutover and recovery

Land preservation support through dev before any active manifest. Add enforcement
and shadow final-tree validation while old gates remain. Test rejection paths
and compatible wrapper/helper upgrades. Only then activate a reviewed manifest
and narrow main wrappers through a main PR.

Prefer existing bootstrap authority without policy relaxation. If a bounded
administrative adjustment is necessary, obtain execution-time confirmation with
exact PR, rule IDs, before-state, expiry and restoration procedure. Fence release
credentials/admissions and drain existing privileged runs first; a pause flag
alone is insufficient. Restore and prove policy before resuming publication.
No bypass actor, broader token or direct-main helper implementation is needed.

The supporting [validator-bootstrap independence proposal](validator-bootstrap-independence.md)
tracks #6865's concrete self-repair deadlock. It preserves the existing
candidate-gate owner and proposes deny-local bootstrap admission, not another
installer policy or release writer. Its implementation and activation require
their own reviewed evidence; an emergency main seed is not its completion.

Keep one release writer. Switch only after old runs settle and one canary proves
the merged tree and tag, concurrent main repair and rollback. Required statuses
and caller references migrate before old definitions disappear.

## Separate validation consolidation

Consolidate one contributor gate, one exact-SHA
trunk/release gate and one reusable deep/platform scheduler. Execute identical
tests once per identical trusted input tuple; retain Windows, heavy, stress and
fresh-install coverage. Promotion PRs should run compact candidate verification,
not ordinary lint again. Name a component owner and release steward for every
failure, deduplicate signatures and escalate when the bounded repair worker is
disabled. Reconcile existing #6142 rather than introducing a second repair loop.
Ownership and candidate enforcement are prerequisites only for reductions that
depend on their new guarantees, not for every redundant event or optional check.

### Exact-input reuse acceptance boundary

This is the next consolidation contract, not an enabled evidence consumer or
permission to remove main-push CI. Reuse the existing validation and artifact
owners; do not introduce another signer, scheduler or credential.

| Identity | Evidence required before treating two executions as equivalent |
|----------|---------------------------------------------------------------|
| Source | Repository and actual checked-out commit/tree, not the outer dispatch's `head_sha`, latest branch tip or PR synthetic merge |
| Recipe | Workflow/tool source commits, selected suite/test set, arguments, relevant environment and dependency/toolchain identity |
| Platform | Operating system, architecture and interpreter/shell profile; Linux success does not discharge native Windows coverage |
| Execution | Authoritative run ID, attempt and job IDs, complete terminal results and expected coverage outputs |
| Composition | Pinned dev/main inputs and actual generated content tree, including versioning, materialization and checkout attributes |

A suite name, green aggregate, coverage JSON or signed package index alone is
not proof of that tuple. `tools/promotion_python_artifacts.py`'s current transfer
index binds `dev_head`, `main_before`, `content_tree` and bundle records; it
does not record the suite/recipe/run evidence above. Its existing artifact
attestation must not be relabeled as a test-suite attestation.

The compact candidate check must establish the expected tuple independently,
then verify complete authoritative evidence for every reused requirement.
Missing, failed, skipped, cancelled, wrong-attempt, wrong-recipe or wrong-input
evidence cannot authorize pruning that requirement's existing execution.
Different recipes need an explicit coverage-equivalence proof, not just matching
source SHAs. Generated output still needs its distinct composition/installation
checks; a dev-source pass cannot stand in for behavior changed by materialization.

Keep the existing seven deep/platform lanes and six schedules until each affected
row has its own replacement proof. The full validator's ordinary suite execution
and separate instrumented coverage collection are another possible consolidation,
but changing their failure, containment or coverage-output contract requires a
separate demonstrated equivalent path. Do not remove either merely because both
call pytest.

The existing #6142 emitter/template is offline construction, not a live repair
owner. Before retiring a failure-producing lane, record its component repair
claim, release steward and escalation destination when automation is disabled,
declines or cannot authenticate. Keep the existing watchdog's signature dedup
and ordinary reviewed dev repair path. Neither this contract nor a green
disabled-fixer skip authorizes activating a worker or accepting a test flake.
