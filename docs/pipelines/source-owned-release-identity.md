# Source-owned release qualification

The verified development commit owns the release. Candidate, Main parent and
landed Main commits remain exact operational/freshness receipts, not additional
semantic artifact keys. The helper APIs below do not themselves activate a
trusted Main workflow: that remains an independently reviewed control change.
The continuing activation and live acceptance gates are in
`efforts/active/dev-branch-release-pipeline/README.md`, Phase 9, and #6237.

## Immutable composition and reuse

New `python-artifacts.json` indexes use schema version 2. They bind the full
development SHA, canonical composed content tree and complete artifact set;
bundle manifests retain their admitted build-toolchain lock and raw wheel
digests. They do not bind the incidental Main parent. Historical version-1
indexes keep that original parent check and candidate-scoped lookup.

`publish_python_artifacts.py prepare` publishes the version-2 set under
`python-artifacts-<full-dev-sha>`, targeting that Dev commit. The prerelease is
never latest and its existence is not installation authority. The helper
validates actual candidate source/composition, immutable metadata and every
downloaded byte with consumer read authority.

During artifact preparation, `promote_release.py
--reuse-artifacts-repository <owner/repository>` first looks for that source's
admitted set. A present set must have the original hosted Main-workflow
attestation, a signer in the current trusted Main history, the expected Dev and
composition, complete readable assets, admitted source policy and one unchanged
toolchain lock. It reuses the original index and bytes before resolving or
building anything. Only a genuine missing release permits a new build.
Authentication, signature, missing-asset, composition, governance and byte
conflicts refuse rather than silently choosing another lock or replacing
published objects.

For version-2 sets, publication and readback use only
`python-artifacts-<full-dev-sha>`. They validate the current candidate's actual
source closure and complete signed artifact set, then verify the source tag,
immutable asset metadata and every downloaded byte. They neither publish nor
consult per-plugin version aliases. An older failed candidate may have claimed
the same plugin version with different content; that alias does not identify
this development source and cannot gate its independently bound artifact set.
Existing aliases are not moved, replaced or deleted. Legacy version-1
publication retains its per-plugin tags, ancestry and source-binding checks.

Main selects the released development snapshot, recorded by its landed source
marker and `promote-dev-<full-dev-sha>` tag. Artifact availability alone never
selects a release for install or update: an unmerged candidate's artifact set
may exist without becoming the selected source. This publication contract does
not enable the separately owned installer artifact-consumption lane.

### Main-derived plugin versions

The composer seeds versions from landed Main and applies the ordinary pending
changefiles. Unlanded plugin-version aliases do not alter that computation or
require a scan of historical reservations. Different unlanded Dev sources can
therefore retain the same next plugin version while their Dev-keyed artifact
sets distinguish their exact content. After Main advances, a later changed
plugin receives the normal next version so update detection still works.

Publication verifies the already prepared source set without creating or
repairing assets. Missing or invalid current-source evidence still refuses
publication; unrelated aliases cannot substitute for it. Existing tags are
neither moved nor deleted. Exact source-index reuse, qualification, signatures,
source closure and raw-byte readback remain mandatory.

## Qualification before Main publication

The full-source gate also requires a collected baseline for every enrolled
plugin. `tools/coverage_guided_selection/baseline.py` defaults to five-file
sequential instrumentation batches, each retaining the 300-second subprocess
limit. Every discovered test file still executes; coverage attribution, tiers
and durations merge across batches. A failed or timed-out batch aborts collection
without returning partial evidence or retrying a smaller subset. A successful
upload step with no file does not satisfy the required baseline gate.

An enrolled collector or upload failure fails its source job immediately;
missing upload files are errors. The later complete-baseline-set check remains
an independent publication requirement. Chunk failures identify their original
index and exact test paths, including after a concurrent sibling drains.

Worktrees and Dispatch opt into `--workers 2` for this separate baseline step.
The two drivers retain independent coverage databases, reports, pytest temp
roots and host-state sandboxes. They share **one** unchanged 900-second,
128-process, 4096-MiB memory and 2048-MiB temporary-storage containment budget;
the full-suite-plus-baseline job still has its 30-minute deadline. Other
plugins and local callers remain serial by default. Results merge in discovery
order with per-line context unions, not last-writer wins. A failed chunk stops
new scheduling, drains bounded active work and refuses the entire baseline;
no tests, enrolled baselines or upload requirements are removed.

`qualify_promotion_candidate.py` dispatches canonical `ci.yml` in candidate
mode at the exact generated branch/SHA and owning promotion run-attempt. It
validates the actual candidate/dev/base relationship and derives required job
names and matrix rows from the candidate's canonical promotion aggregate.
The entire promotion portfolio, including full suites, global guards and
native platform consumers, must execute successfully. Unknown recipes,
omitted/duplicate rows and skipped, failed or cancelled required coverage
cannot produce a receipt.

The receipt binds the repository, Dev, candidate, content tree, Main parent,
trusted control, exact CI run-attempt and executed job IDs. Its SHA-256 covers
the exact canonical UTF-8/LF file bytes. Repeated calls reuse the same exact
successful run; ambiguous runs refuse.
Receipt replay also checks the provider's actual run title against the owning
promotion run-attempt; editing that field in a local receipt cannot relabel
old evidence for a new attempt.
CI workflow source parsing and its source fingerprint use declared UTF-8/LF,
normalizing CRLF checkout/staging representation first; an unsupported lone CR
refuses. This changes no downloaded index, archive, wheel or receipt bytes.

`publish_python_artifacts.py publish-candidate` requires that receipt and
rechecks its current provider run/attempt before publication. It verifies
the pinned hosted signer, source-bound archives and source-owned ready set, and
reads the index and every bundle back with consumer authority before emitting
publication success. It does not create a second version-alias publication. A failed
readback does not emit a success-shaped receipt.
For schema 2, the candidate-publication CLI performs one artifact-set validation
against the current candidate and one complete source-set readback after qualification and
signature checks. The readback returns its validated index for the publication
receipt; the CLI does not call the legacy publication helpers or repeat those
transfers. Direct helper callers retain independent validation, and the
historical `publish` landing checks and schema-1 flow remain unchanged.

The trusted controller must require both bound qualification and publication
receipts before its atomic exact-head, strict-base, non-administrator Main
merge. It must recheck qualification freshness immediately before merging.
Postmerge work may record the actual landing; it is not the first publication,
consumer test or availability check. A Main promotion PR merging means the
release is already qualified for marketplace consumption.

## Promotion PR ownership and recovery

The qualified-before-PR controller opens its generated PR only **after** complete
candidate CI, immutable source publication and raw-byte readback succeed.
Candidate preparation alone emits a branch/SHA, not a PR number or a completed
release. The already credentialed publisher opens the ready PR; the separate
checkout-free merger retains only its existing non-administrator Actions
authority. No second publisher or merge writer is introduced.

`promotion_lifecycle.py` reads the complete open-Main-PR inventory before a new
generation starts. It uses creation-ordered stable cursors, not page offsets
that can skip a surviving promotion when an earlier PR closes. Changed totals,
duplicate identities or invalid cursors refuse admission rather than claiming
the inventory is idle. A failed, cancelled or interrupted producing run does not
release its open promotion PR. The source-owned trigger declines another
dispatch, and the trusted Main admission gate independently refuses new source
validation. Incomplete or ambiguous inventory, an unexpected same-repository
publisher, or malformed promotion identity fails closed. Foreign PRs cannot
claim the same-repository release namespace.

Deferred CI events are not discarded: terminal promoter completion and
owner-authored Main PR closure wake the same trusted dispatcher. It re-reads
Main pause/source state and current successful Dev CI before scheduling pending
advances. A cancelled or failed promoter may have already merged using the
Actions token; recovery requires the PR's actual verified merge, not a
successful run conclusion. Failed or cancelled runs without that merge cannot
retry qualification through this path. A merged resume/control transaction can
drain already-green changes without another push. Successful no-ops with no merged PR cannot form a
dispatch loop; an unmerged retired candidate does not automatically retry its
unchanged source. Stale completion events do not take responsibility back from
a newer generation, including one whose real failure still needs recovery.
For a publication-intending invocation, the trusted Gate issues one immutable
admission artifact for the exact Dev SHA and freshly admitted Main base after
ancestry and ownership checks. Its name
binds those inputs; provider workflow identity and the successful trusted Gate
upload step bind the original attempt and creation window. The artifact is an
ownership record, not a substitute for qualification or publication proof.
Existing repository artifact inventory discovers only the selected input pair,
without scanning every historical workflow run.
An explicit manual `push=false` preview keeps its existing validation behavior
but emits no publication-ownership record. It cannot strand a later real
promotion behind a dry-run generation that has no publication job to resume.

CI, closure, completion and queued automatic admission all consult that same
record. A failed publication before PR creation still retains its admitted
inputs; a delayed older CI event cannot start their full qualification again.
Changed Dev or Main-base inputs remain eligible. A queued run, failed admission
or paused no-op has no successful record and does not own progress. Legacy
source-job uploads cannot manufacture the trusted Gate's admission authority.
Resume/control wakes remain eligible behind a finishing paused no-op.

Deferred CI discovery accepts both push and manual CI for the exact current Dev
SHA and trusted repository/workflow. The latest eligible run wins by creation
time, then run ID; its current attempt must succeed. A newer failed or pending
run blocks an older green one. The triggering run is included when the provider
index has not caught up, so index lag does not discard direct evidence.
Successful manual CI can drain after resume or merge without requiring another
push or selecting an arbitrary older green run.

Retirement is read from the complete closed-promotion history and the immutable
candidate's recorded source, independent of the waking event. A delayed green
CI event cannot retry the same retired source. The trusted admission gate
rechecks that policy for automatic dispatches after resolving the live Dev tip,
so events already queued before retirement cannot bypass it. Explicit manual
qualification remains an operator action, not an automatic retry.
Known non-owner authors are excluded before requiring fork provenance, so
deletion of an unrelated foreign fork cannot block automatic promotion;
owner-authored candidates still require complete repository identity.
Admission artifacts are not overwritten on a full Gate replay. Recover the
existing publication/merge job with its original qualification owner, or use a
genuinely changed-input generation; do not replace the record to retry unchanged
qualification automatically.
Normal admission also declines duplicate validation of a source already
recorded on Main; an explicit qualification-only request remains distinct.

The publisher rechecks ownership after qualification. It can reuse exactly one
existing PR only for the same generated branch and SHA, allowing recovery after
an ambiguous PR-creation response without opening another PR. Closed, replaced,
cross-repository or differently authored PRs are not silently revived.

Recover a publication or merge failure by resuming the **existing failed job**,
not rerunning full qualification or dispatching another generation. The
qualifier's `qualification_promotion_run` output retains its original owning
run-attempt; publication and merge retries use that recorded key even when the
current job attempt advances. Foreign or future qualification ownership, moved
Main/head, changed CI attempts and failed required checks still refuse.
Explicit retirement is required when the old candidate cannot be recovered.
An uncertain merge-command response is reconciled through bounded readback,
never another blind merge write. Confirmed policy refusal preserves the
original error and the active PR's recovery obligation.

The existing whole-workflow concurrency slot remains held until exact Main
merge is read back. Only the required Main checks gate that write; already
completed full candidate qualification is not dispatched again after opening
the PR. An Actions-token landing suppresses automatic Main-push CI; only the
bounded attribution refresh described below is dispatched afterward. Ordinary
Main control/rollback validation is not removed by this release contract.

The helper and controller contracts do not activate themselves. Main's reviewed
workflow/pin change must be proven against its exact prepared control data before
the next qualification; the legacy Dev workflow is not the authority for that
independently owned Main controller.

The checkout-free merge waiter needs explicit job-level `actions: read` for
that final qualification-run lookup; its own permission map replaces the
workflow-level grant. After landing, a separate bounded dispatch refreshes
existing Dev attribution checks against the landed Main policy, because a
merge using the Actions token does not produce ordinary push-triggered runs.
That idempotent metadata refresh is not release qualification or artifact
publication. Dispatch failures remain visible without retroactively failing
an already qualified release.

The required Main source gate also authenticates the actual owning promotion
run. Its immutable executing Main SHA must belong to Main history, and its CI
and promotion workflow blobs must equal the current PR base's control blobs.
An old in-flight workflow cannot resume later and publish through its original
incomplete graph merely by copying newer control files into a candidate.
Pause/resume-only Git containers with unchanged control bytes remain valid;
this is execution authority, not a second release identity.

## Paused replacement and landing records

`promote_release.py --qualification-only` permits composing a repaired
replacement while preserving `paused=true`. It neither resumes the pipeline
nor bypasses source, rollback, stale-candidate, artifact or CI checks. It
cannot directly push Main or create an early release tag. A trusted,
explicit manual control flow may land the replacement only through the same
fully qualified candidate PR path. Normal automatic promotion remains paused.

The cheap admission gate reads fresh trusted Main state before normal source
validation. A valid pause explicitly defers normal work, while missing,
malformed or wrongly typed state refuses. This avoids occupying the serialized
lane with expensive source portfolios that cannot publish. It does not change
GitHub's rolling pending-slot semantics or cancel an existing running job;
an explicit replacement still needs all qualification and publication proof.

Promote runner admission also requires a successful source gate and its
`is_dev=true` output. A failed or declined gate therefore allocates no Promote
runner or deployment environment. Admitted sources still reach the existing
decision step after failed dependencies, and every required validation result
must be successful before candidate work can proceed. This job-admission policy
requires matching reviewed Main control activation; a Dev edit alone is not live.

New landed releases are recorded as `promote-dev-<full-dev-sha>`.
`tag_promotion.py` verifies the landed Main state. A second equivalent Git
container reuses the original immutable tag without moving it; different
payload bytes under the same source identity refuse. Legacy timestamp/Main-SHA
tags remain verifiable under their original rules.
Unlike prepublished per-plugin version aliases, an existing landing tag must
actually belong to fetched Main history. An equivalent never-landed candidate
is an immutable collision, not a successful landing receipt.

The direct/report producer also reuses an existing Dev tag only for the same
recorded source and payload; `--force` does not authorize rebinding immutable
bytes. If a latest landing receipt is unavailable, rollback preparation refuses
instead of silently selecting an older source's promotion. This does not grant
rollback execution authority or waive the operator's verified-target gate.

## See also

- [Pipeline mechanics](../pipelines.md)
- [Install contract](../install-contract.md)
