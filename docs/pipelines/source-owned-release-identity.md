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

Version aliases may point at a prepublished candidate that later squash-landed
or never landed. A later release retaining that plugin version does not require
the old candidate to be a Main ancestor. For version-2 sets, the publisher
authenticates the prior candidate's single-parent/source provenance, Dev
ancestry and actual canonical composition against its signed frozen Dev index.
It then checks the **current qualified manifest** against the prior candidate's
actual plugin and vendored-source closure. Neither a Dev marker alone nor a
cached index authorizes a different composition. This path consumes no old
bundle bytes; current signatures, immutable-asset checks and raw readback still
apply. Legacy version-1 aliases retain their original ancestry rules.

## Qualification before Main publication

The full-source gate also requires a collected baseline for every enrolled
plugin. `tools/coverage_guided_selection/baseline.py` defaults to five-file
sequential instrumentation batches, each retaining the 300-second subprocess
limit. Every discovered test file still executes; coverage attribution, tiers
and durations merge across batches. A failed or timed-out batch aborts collection
without returning partial evidence or retrying a smaller subset. A successful
upload step with no file does not satisfy the required baseline gate.

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
the pinned hosted signer, source-bound archives and ready set, publishes
required final version assets without clobbering, and reads their raw bytes
back with consumer authority before emitting publication success. A failed
readback does not emit a success-shaped receipt.

The trusted controller must require both bound qualification and publication
receipts before its atomic exact-head, strict-base, non-administrator Main
merge. It must recheck qualification freshness immediately before merging.
Postmerge work may record the actual landing; it is not the first publication,
consumer test or availability check. A Main promotion PR merging means the
release is already qualified for marketplace consumption.

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
