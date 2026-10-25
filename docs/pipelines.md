# CI/CD Pipelines

This is the mechanical reference for how a change actually moves through
this repo: the automated gates a pull request passes before it merges to
`dev`, how `dev` promotes to `main`, how to preview a pending release
without waiting for a real promotion, and the complete list of scheduled
and reactive automation this repo runs. `CONTRIBUTING.md` owns contributor
*process* (how to open a PR, the review-verdict waiting loop, documentation
requirements); this doc owns the *infrastructure* those steps run on top
of — link here rather than restating any of it.

## Standalone PR job admission

The existing manual `installation-context-full.yml` workflow additionally accepts
`portfolio: foundation` for bounded Windows/Ubuntu foundation construction and
custody qualification. Its scheduled/default `context` portfolio is unchanged;
the two portfolios do not run together. This explicit integration lane has a
25-minute per-host job deadline and is not a required unit-only PR or promotion
job. Separate bounded foundation/adapter and actual cold first-use portfolios
qualify Windows/Linux Python-free genesis and the adopted Vault command/bootstrap
paths. Each keeps the runner's 900-second aggregate and storage caps; one-file
sub-suites release private acquisition/runtime fixtures between modules.
This does not qualify macOS or authorize a host rollout.

Source CI contains three jobs: operational `checks`, combined impact/execution
in `unit-tests`, and the fixed-name `PR gate (required check)`. The unit job
reads the actual PR base/head diff before installing test tooling or executing
tests. Markdown-only changes have no executable coverage overlap and select
no unit tests, including nested plugin documentation, skills and root policy
documents. Unknown, code or
configuration impact runs only the positively admitted unit packet.
PR impact uses the merge base of the exact event base/head commits, matching
the provider's PR change set even when the target branch advances. Comparing
the two tips would incorrectly include unrelated upstream code as a reverted
change. Push impact retains its before/after comparison. Missing common
history is a failure, never an empty-change result.
It never falls back to standalone services, fleet, native or general plugin
suites. Guards and the unit job remain independent and parallel. Combining
impact and execution removes the separate planning runner and duplicate
checkout without placing unit execution behind guards. The aggregate requires
both jobs to succeed, a valid selection, and the actual qualification step's
`success` or explicit `skipped` outcome matching that selection. Selected
qualification additionally identifies fresh execution or verified prior
execution, the producing run/attempt and the canonical packet digest.
Job success alone is not unit-execution evidence.

For repeated PR inputs, `tools/ci_pr_unit_reuse.py` may reuse a successful
packet only for the exact repository/PR, source/merge/base, workflow, manifest,
hosted image, interpreter, installed dependency bytes and sealed child
environment. The PR unit child receives no credentials or event-dependent
environment; no intervening dependency installation occurs between binding
and execution. The unit job alone receives read-only Actions access.
A bounded lookup verifies the original successful run/attempt/job, actual
fresh qualification and upload, immutable provider archive and unchanged
three-file coverage packet. Missing, incompatible, expired, failed or
unavailable proof executes fresh units; it never manufactures skipped success.
The complete bounded current-head inventory contributes a qualification epoch:
retry attempts and non-success outcomes change the key even on lower-ID runs.
Unknown or truncated history retains fresh execution. This does not infer
attempt chronology from run IDs or mutable update timestamps.
Only fresh execution uploads a reusable artifact, preventing reuse chains.
Artifact names include their producing run/attempt. A failed-job-only retry
may retain an already-successful unit job from an earlier attempt of that
same immutable run; future attempts and another run's unverified outputs
remain rejected.
Whenever a rerun actually invokes the qualifier, it executes fresh units
without looking up old success. Mandatory checkout validation precedes every
optional reuse/toolchain fallback.
Current guards/metadata, base-retarget handling and the required aggregate
remain independent. Manual CI and every Main release still execute units;
this PR-only reuse neither changes nor supplies promotion qualification.

The nonblocking marketplace-isolation inventory runs on pushes and manual CI,
not again on each PR update. Admitted Dev-push executions retain that diagnostic;
strict source guards and required validation remain in PR CI. A cancelled or
superseded execution is not diagnostic or test evidence.

Before the Agent Bridge contract guard, CI explicitly acquires each HTTP-47,
HTTP-48, HTTP-49 and HTTP-50 packet's exact captured source commit from the
CI-selected, credential-free HTTPS repository URL when it is not already
available. The separate bounded
`tools/prepare_bridge_contract_evidence.py` step accepts only a full commit ID,
does not change checkout, refs or `FETCH_HEAD`, and fails visibly if acquisition
or verification fails. A normal full branch checkout can omit a squash-orphaned
capture checkpoint. The checker itself remains offline: it still validates
complete historical inventory and content-addressed blobs, never regenerates
capture evidence, and does not waive a changed or unprovable closure.

The checks job independently selects its tracking-write, headless-launch and
asyncio-timeout AST scans from the pinned diff. Known narrative PRs and changes
limited to direct Python files in `tools/promotion_unit_tests/` plus the exact
`tools/promotion-unit-manifest.json` omit the group: these inputs are outside
all three scanners' read roots. Unit selection is unchanged and still executes
the admitted packet for packet-file changes. Other, nested/lookalike, mixed,
production, scanner/policy and workflow changes retain complete scans; empty
evidence, push, manual and unknown events always run them. Invalid or
unavailable diff evidence fails the job, not a success-shaped skip. All other
code-focused guards share the same conservative impact classification:
version/install/runtime contracts, Bridge source closure, generated-file and
vendor checks, source hygiene and Ruff do not run for Markdown-only diffs.
Push guard selection also uses its exact before/after diff; manual, unknown
events and an absent initial-push base retain code checks. Invalid or
unavailable diff evidence fails rather than claiming no overlap. Source-to-doc
renames retain the deleted source path and therefore cannot skip validation.

Markdown changes still run release changefile lint, skill/effort/vision structure, documentation and
runbook lint, file-size limits, workflow policy and gh-aw Markdown pin/version
checks. The checks job and fixed-name required aggregate remain present.
Markdown skills and instructions are shipping payload even without executable
coverage overlap; their required changefile/version bump must not be skipped.
The aggregate accepts only an explicit unselected/skipped unit result without
fabricated coverage or reuse evidence; it does not mistake missing coverage for
proof that executable changes are untested. The ungated trusted-CI guard binds
both selectors and the conditional scan commands.

Push CI performs source-policy admission without repeating promotion's unit
execution. Manual CI runs the same admitted packet, not the old full portfolio.
The retired self-hosted PR workflow is a manual routing pointer; payload-CWD
native execution is explicitly unenrolled pending a container route.

See [the unit packet reference](../TESTING.md#unit-only-promotion-packet).
The [reviewed burndown](../efforts/active/dev-branch-release-pipeline/promotion-burndown.md)
owns the migration and completion gate. Source CI changes do not activate,
resume or bypass Main's separately governed controller. Inspect its actual
state and qualification rather than inferring either pause or activation from
the source-CI layout. Historical full-candidate descriptions below are not
instructions to reactivate those integration gates.

### Composed-candidate promotion receipt

The source promotion declaration owns its independent source-unit transaction.
Its governed Main control must pin reviewed helpers and qualification evidence;
combining source-CI jobs changes neither that transaction nor its authority.

The Source controller proposal freezes the stamped, vendored candidate before
release execution. `compose-candidate` runs the existing composer once and
retains its exact Git commit, content tree, admitted Dev/Main inputs and
preparation time in a bounded `candidate.bundle` / `candidate.json` transport.
Every consumer also requires the candidate SHA emitted by the composition job;
internally consistent replacement transport data cannot select another object.
Units and the unsigned artifact build independently import those objects.
Artifact validation and publication consume the same frozen object; they do
not recompute version allocation or vendoring after tests. A changed Main base
requires a new admission, not a silent restamp.

`source-units` retains its provider job identity but executes the fixed Dev
inventory on candidate bytes using the explicit
`copilot-extensions.promotion-candidate-units/v1` manifest. Declared materialized
uv copies extend the admitted import closure, never the test-node inventory or
five-minute budget. Attestation still requires successful units and the
unsigned build. `qualify-candidate` uses `candidate-units-v3`, validates the
candidate hashes and development correspondence against both authoritative
source roots, and checks the original successful job/upload/immutable archive.
It never dispatches candidate CI.

Raw candidate line coverage remains in the three-file packet. Its separate
development projection is bound to Dev bytes, candidate bytes, tree and copy
origins. Exact copies map directly; recognized same-line version assignments
leave the changed statement unattributed. Unsupported rewrites and shifted
regions remain explicitly unmapped. Copy contributions are retained before
unioning Dev coordinates. No projection asserts that raw Dev bytes, branches
or functions executed.
The manifest binds the entire admitted closure, but coordinate maps cover
observed production files only, not every unexecuted vendor copy or test file.

Coverage is generated evidence, not checked-in source. The collector and hosted
archive verifier share a 128 MiB per-file bound; exact packet membership, source
hashes, context completeness and the five-minute execution budget are unchanged.
The complete expanded version-1 coverage projection is retained without a
new report encoding merely to fit the former 16 MiB bound.

After qualification, the existing publisher attaches the exact three packet
files and canonical `qualification.json` in one deterministic ZIP to the same
Dev-owned `python-artifacts-<Dev SHA>` GitHub Release. Its immutable filename
binds Dev, candidate and archive SHA-256. Upload never clobbers an asset; an
existing or newly uploaded asset must pass consumer-authority raw-byte readback
before the publication receipt can enable Main merge. The receipt also binds
the retained evidence identity. No generated report, packet or archive is
added to the Source or generated Main tree.

The current candidate-unit publisher additionally requires a self-contained ZIP
of **every tracked blob in the final candidate tree**, including metadata,
admitted Main controls and materialized first-party sources. Raw Git object
batches avoid `git archive`'s `export-ignore` and `export-subst` transformations.
The verifier reconstructs the exact Git tree from member bytes and modes,
without extracting files or following source links. Link resolution uses the
complete in-memory graph, including case-equivalent references; chained
escapes, cycles, more than 40 traversals or literal targets over 4096 bytes
fail closed. Unsupported submodules and unsafe/ambiguous paths are refused.
The existing artifact
bounds remain 512 MiB for the archive and expanded tree, 128 MiB per file and
100,000 files.

An external canonical source manifest binds that full tree, Dev, candidate,
Main base, current controller, qualification digest and the **whole unchanged
source-index digest**, including any optional profiles. It records the archive
digest without being added to the tree it describes. Both subjects receive
fresh trusted-Main attestations and immutable hash-named assets on the same
Dev-owned release. The existing publisher verifies the exact subject signatures
and independent raw-byte readback before including them in its publication
receipt; missing subjects or failed readback cannot enable the final Main PR.
This does not reinterpret the signed index's existing `content_tree` or
authorize another candidate, controller, runtime profile or package build.
The Dev tag's automatic archive and an incremental candidate Git bundle are
not substitutes for this complete-source proof.

Readiness and frozen-code reuse recognize only the bounded, hash-named
supplementary evidence namespace for that same Dev release (at most 64
retained packet/source/manifest assets in total). They retain earlier immutable
generations without overwriting them. This data namespace grants no code-execution or
qualification authority. The separately attested code/index inventory remains
exact; foreign, malformed, duplicate or arbitrary extra assets are refused.
Actions artifacts remain the original producing-job evidence, while the
release copy retains the verified packet after Actions retention expires.

Source support does not itself activate Main. Its independently reviewed
controller and immutable helper pin select the active qualification contract.
Historical raw-source and legacy-CI packets retain their original verification;
neither can be relabeled as candidate execution.

Chat's enrolled package initializer resolves its version from distribution
metadata, with a source-project metadata fallback when uninstalled. Its
registered version contract forbids a checked-in Python version literal:
ordinary Main seeding and changefile allocation must not change imported unit
source bytes. All enrolled Python files and the raw byte-equivalence verifier
remain required; no version normalization or source omission bypasses that
verification.

Job-only retries retain the original producing attempt and archive rather than
rerunning units or demanding that the still-executing controller already be
complete. Publication revalidates that exact packet and source composition.
The checkout-free, nonadministrator merger rechecks the trusted Main generation,
original successful job/upload/archive and required checks before its exact-head
write. Existing publication/readback, active-PR ownership and source tagging
remain separate production operations. Main's proposed CI is metadata-only;
neither that status nor a skipped integration lane is unit qualification.

## Python artifact-builder toolchain policy

`tools/build_python_artifacts.py` can run standalone and is also invoked by
promotion's credential-separated `build-python-artifacts` job. Its default
`--toolchain-source governed` requires an
affirmatively trusted governed feed. There is no automatic public fallback.
Public promotion runners may explicitly choose
`--toolchain-source seasoned-public --toolchain-min-age-days 7` (seven days
is the default). This exception applies only to the build toolchain:
`setuptools`, `wheel`, and `packaging`, never managed-device runtime
dependency resolution or published third-party wheels.

Hosted validator provisioning also hash-pins `packaging`; its binary-only,
hash-checked install accepts `ARTIFACT_VALIDATION_INDEX_URL` as an explicit
feed override, defaulting to the public tooling index on public runners.

The canonical seasoning CLI's explicit `--registry-only` mode admits such
registry toolchains without discovering product project/library roots. It
denies local source projects and retains the existing cutoff, selected index,
hash and retained-input checks; it is not a public-feed fallback or a second
installer policy. The build, attestation, promotion and publication validator
provisioning steps opt in explicitly. A source-support merge alone does not
activate a main-owned caller; reviewed control activation and exact
materialized bootstrap qualification remain separate requirements.

Public mode selects exact non-yanked stable pins from PyPI metadata, measures
age from the newest file, and installs binary distributions only from one
explicit public index. An incompatible pin set fails resolution; it never
relaxes a pin or the seasoning floor. Every source's build requirements are
also checked against the locked interpreter before building. Seasoning is an
age floor, not proof that any consumer's governed feed has mirrored a version.
The metadata snapshot's eligible SHA-256 file hashes are carried into a
`--require-hashes` install. A wheel uploaded to the same version after that
snapshot cannot silently win live-index selection: its bytes fail verification
before the toolchain is published. Hash mode also refuses any unpinned
transitive build-tool dependency.

Manifest schema 3 records toolchain source policy and resolved interpreter in
`build_toolchain.provenance`; public provenance additionally records the
index, seasoning floor, exact pins, and eligible file hashes. These fields participate in
`lock_id`, hence `artifact_id`. Caller-supplied toolchain locks with missing
or inconsistent provenance are rejected before any wheel is built.
Shared `--toolchain-venv` reuse checks the
same policy/pins/interpreter identity and rechecks installed public pins;
a different identity uses an alternate slot without disturbing an active
build. The existing `.governed-feed-provenance.json` filename is retained
for compatibility, but public markers explicitly say `seasoned-public` and
contain no `validated_index_identity` claim.

Path-scoped CI runs a fresh public toolchain plus real first-party wheel
builds on a public hosted runner. Managed machines should retain governed
mode when exercising the builder locally.

### Registered standalone first-party artifacts

The same locked builder accepts `--standalone pr-service` or
`--standalone fleet-manager` (the positional value is a registered component,
not an arbitrary path). Promotion discovers `services/pr` and `fleet-manager`
explicitly alongside its existing plugins. Standalone manifests use schema 4,
`component` instead of `plugin`, a `standalone` primary wheel and an exact
`build_resources` source closure; plugin schema-3 manifests are unchanged.
Schema-2 promotion indexes carry either explicit identity, with standalone
filenames and version tags prefixed `standalone-`. Existing plugin release
names, signature checks and optional artifact/source selection remain unchanged.

Standalone builds share the existing toolchain, generator, source-content,
wheel-hash and candidate composition checks. Fleet's resource-injecting setup
and helper must match trusted producer code. The verifier maps its injected
modules to canonical resource bytes without executing candidate code; changed,
missing, linked or substituted inputs fail closed. This does not admit arbitrary
`setup.py` files or weaken plugin static-build admission.

The manual **Installation context native acceptance** workflow's
`pr_artifacts` input builds real plugin and standalone wheels on Linux and
Windows, verifies their bundles against the source tree, then installs only
the standalone wheels into a fresh environment offline with no dependencies.
Its outside-checkout import/resource checks prove first-party packaging, not
service execution, governed-feed runtime closure, native Worktrees selection
or image/fleet acceptance. Public toolchain provisioning remains the narrow
hosted-runner exception above; no third-party runtime bytes enter release bundles.

### Materialized candidate consumer gate

Ordinary guard CI also exercises installer human-output declarations with
explicit canonical and already-materialized source fixtures. Both use the real
producer, materializer and runtime admission checker, including complete local
engine/support assets, byte-stable reapplication and tamper refusal. A
materialized installer correctly needs no second reference rewrite; a canonical
installer must still demonstrate that rewrite and declaration rebinding.

Before the checkout-free merger can advance `main`, the exact pushed candidate
has a separate consumer gate. `materialized-candidate-plan` validates its
immutable candidate/dev/main identities and builds the consumer matrix from
the actual generated tree, rejecting missing or empty suites against a third
credential-free checkout of the pinned, validated development input.
`materialized-candidate-consumers` executes the
existing plugin recipes (collection plus guards for Worktrees), Manager and
standalone Dispatch/Index/Fleet on a statically routed Linux host.
`materialized-candidate-windows` retains Index and Fleet's statically routed
native Windows lanes; both platforms also require validator bootstrap. No
matrix input can select a runner label. The supported
recipes must cover the validated development input's canonical standalone
roster. Fleet's canonical literal partition selectors from the immutable
candidate's CI definition become independent matrix rows on both platforms.
Each portfolio invocation retains its own 120-second deadline and existing
exhaustive-test exclusions; selectors do not filter the prerequisite coverage
guard. Missing, duplicate, nonliteral or platform-divergent partition metadata
refuses qualification instead of collapsing the portfolio under one deadline.
The canonical smoke portfolio similarly derives every plugin partition from
`tools/ci_plugin_partitions.py`'s literal scheduling map without importing
candidate code. Qualification requires each executed partition, and its
workflow digest binds normalized bytes from both the workflow and that helper.
The PR-owner suite uses two partitions so its existing 25-file process groups
run independently within the unchanged eight-minute smoke-job deadline.
Partitioning preserves every test file and the runner's containment limits.

Validator bootstrap starts with a fresh venv and private empty UV cache. It
runs the candidate's actual `seasoned_uv.py --registry-only` with the same
configured index, binary-only/hash restrictions and pinned requirement file as
all four trusted workflow callers, then imports the installed validator in
isolated Python. A broken product graph grants no local-source authority;
unsupported helper modes, failed installation or missing bootstrap inputs fail
the gate before merge.

Plugin recipe deadlines derive from the runner's own literal aggregate timeout
policy, plus three minutes for preparation and verification. Each enclosing job
allows all recipe invocations plus five minutes for checkout/tool setup.
Unknown or invalid timeout metadata refuses qualification rather than silently
substituting a deadline shorter than the established suite budget.

Each consumer gets a fresh public-hosted runner, `contents: read`, no release
environment or API/release authority, and credential-free checkouts of both
the pinned trusted validator and the candidate. Candidate test code executes
only in this unprivileged lane, never the signer, writer or merger. The
validator checks the candidate checkout, single main parent, composition state
and unchanged tracked inputs before and after execution. A failed, missing,
skipped or cancelled consumer cannot authorize merge. The existing expected-head
freshness, artifact readiness and post-merge verification remain in force.

This intentionally validates a different, materialized input from the earlier
development-layout suites. Initially it reuses actual consumer recipes rather
than an unproved reduced selector. The temporary post-merge overlap is not
silently retired: later consolidation needs the existing exact-input,
recipe/platform and authoritative-execution equivalence proof. Package
attestation is not test-suite evidence.

### Promotion artifact transfer, signing and publication

After the full validation gate, `build-python-artifacts` runs from the default
branch's pinned workflow/tools without `main-promotion` or the release PAT.
It calls `promote_release.py --build-python-artifacts-dir <dir>` without
`--push`, using the explicitly opted-in seasoned public toolchain. Promotion
first seeds shipped versions, consumes new changefiles and materializes
vendored sources. Wheel builds run in a second disposable checkout of the
resulting candidate commit, so build residue cannot change the promoted tree.
Export executes the pinned trusted driver and builder against that disposable
candidate's source data, never its copies of promotion or toolchain code.
The signer requires every bundle to share one lock and enforces the hosted
`seasoned-public` source policy with the configured seven-day floor.
Build admission and source mapping both require the exact static
`setuptools.build_meta` contract. Custom backends, `backend-path`, `setup.py`,
`setup.cfg`, dynamic metadata and custom setuptools commands/extensions fail
before any candidate build or trusted attestation.
Candidate-root modules that shadow the standard library, locked tooling or
Python startup customization hooks are rejected as well.
This candidate-executing job has only `contents: read`, no OIDC or attestation
permissions, and no persisted checkout credentials. Every Python plugin
shares one toolchain venv; every bundle contains only its
own wheel, required first-party library wheels and schema-3 manifest.
The trusted validator requires the complete installed payload set to equal
the candidate's Python sources plus its explicit package-data declarations,
with matching bytes. Undeclared data, `.pth` files and script payloads fail
closed; unsupported data-files or exclusion layouts do not receive signatures.
Both preparation and release-authorized promotion compose candidate data
using tools and the projection manager from the pinned workflow-source
checkout, never Python tooling imported from the candidate scratch tree.
The release writer checks out that same pinned source; `origin/main` remains
the promotion parent and version/state seed, not the executable tooling root.
The writer's job-wide token is read-only. Checkout persists no credentials,
and the release PAT is scoped only to the trusted push, coverage publication,
PR landing and tagging shell steps. Git uses the `gh` credential helper, which
reads each step's scoped token rather than storing the PAT on disk; artifact
downloads, action setup, validation and merge-wait reads receive no PAT.
The read-only job token includes pull-request, check and commit-status reads
so merge polling can observe both the exact PR and its required gates.

The transfer index binds the exact validated dev SHA, pinned main parent,
composed content-tree SHA, complete project/version set, and bundle digests.
The content tree excludes only existing release-state and coverage-pointer
bookkeeping. A fresh `python-artifacts` job checks out only the pinned trusted
default-branch tools and downloads the unsigned bundles; it never executes
candidate tools or build backends. Its non-executing validator checks the
expected dev/main identities, manifest schema and plugin/version/artifact ID
binding, exact declared first-party member set, and every wheel's SHA-256.
It also enforces the complete schema-3 provenance: payload hash, wheel and
aggregate tags, first-party source paths, locked generator, toolchain pins and
canonical source/interpreter provenance. It recomputes both the toolchain lock
ID and artifact ID using the trusted builder's identity rules, without
resolving a toolchain or executing a build.
Using only those trusted tools, it recomposes the bound dev/main snapshot
(including version bumps and materialization), discovers the plugin/vendored
source closure, checks distribution names and versions, and recomputes each
payload hash from that candidate data. Before hashing, it checks out the
composed index through Git so declared checkout attributes, including CRLF
scripts, match the fresh candidate checkout used by the exporter. It does not
normalize payload hashes or waive byte equality. The promoter uses the same
canonical composed-index validator before creating a release candidate.
That boundary first verifies the composed index's tree identity, before reading
artifacts or checking out source. It reports index drift separately from an
artifact/source mismatch rather than comparing unrelated snapshots and emitting
an opaque payload-hash refusal. Working-file timestamps, untracked caches and
raw LF/CRLF generator output are not source identity: only the admitted index's
canonical checkout is hashed. Attribute lookup is explicitly pinned to that
same tree, not `HEAD` or unstaged/untracked `.gitattributes` in the scratch
directory. Canonical validation requires Git 2.45 or newer and never falls back
to working-tree attribute policy. A real source-content or checkout-policy mismatch
still refuses publication and names the plugin/version and expected/actual
payload digests. Archive and wheel digests remain exact integrity checks; they
are not relaxed or used as a substitute for this source contract.
Candidate-supplied hashes and
first-party labels alone never establish source provenance. The publisher
repeats this source binding against the actual merged snapshot.
It rejects duplicate, extra, missing, non-regular or unsafe members, with a
1 MiB manifest limit and 512 MiB total uncompressed bundle limit.
Each declared wheel must itself be a safe ZIP with one matching distribution's
`WHEEL`, `METADATA` and `RECORD`. Trusted checks compare the real generator,
distribution/version and tags with the validated manifest, reject unsafe wheel
members, and verify complete RECORD hashes/sizes. An opaque byte string with a
`.whl` filename and a matching outer digest is not a wheel.
`Requires-Dist`, `Requires-Python` and `Provides-Extra` must match the static
project declarations, including optional-dependency markers and normalized
extras. Direct runtime URLs and dynamic runtime declarations are rejected, not
certified as governed-feed admission. The standard PEP parser is hash-pinned
in `tools/artifact_validation_requirements.txt`; hosted jobs provision it in
an isolated validator venv from the explicit approved public tooling source.
Its pinned wheel predates the seasoning floor, installs with no dependencies
or source builds, and is never bundled in first-party assets. Provisioning
receives no release-write token. Local callers supply `packaging` through
their governed tooling environment; no command silently provisions a public
dependency on a managed device.
The signer also binds actual payload files to the candidate: every installed
file must byte-match its static setuptools source path, the Python member set
must be complete, and declared package data must be present. Generated console
and extension entry points must exactly match the project declarations;
top-level and license metadata are source-checked as well. This verification
supports the repository's `src` package layouts and explicitly declared flat
`py-modules`. Generated code, custom discovery/mapping and compiled outputs
without a verified source-to-wheel mapping fail closed; they are not silently
signed as source-equivalent artifacts. These checks execute no backend in the
OIDC-bearing job.
`actions/attest-build-provenance` then signs both this index and each
bundle using GitHub OIDC, with `contents: read`, `id-token: write` and
`attestations: write`. Neither signing authority nor release-write authority
substitutes for the other. Manual promotion must run from the default branch;
an otherwise-valid dev-ref dispatch cannot supply the trusted signing workflow.
The attestation's source digest identifies the trusted default-branch workflow
run, not the generated payload commit. The signed transfer index separately
binds the validated dev SHA and composed payload tree.

The privileged job downloads only the same run's named transfer artifact.
Before consuming metadata, `gh attestation verify` requires this repository,
the exact promotion workflow path and workflow digest, the source digest/ref,
and a GitHub-hosted runner. Verification uses the read-only workflow token,
not the release PAT. `publish_python_artifacts.py`
verifies the index first, then validates its exact bundle set before checking
those bundles' attestations; unrelated or missing files fail closed.
Index authentication, bundle-set validation and any original Main admission
verification remain serial. Only the independent bundle attestations use up
to four concurrent verifications, with the same pinned identities, read-only
token and per-subject deadline. Every verification must pass and all workers
are joined before the caller can publish or emit a success receipt.
`promote_release.py --python-artifacts-dir <dir>`
recomputes composition and refuses missing/corrupt/mis-targeted artifacts
before pushing a candidate, including repeating the archive-content checks.
Until the shared composed-index reader reaches trusted `main`, the workflow's
temporary promoter adapter supplies the canonical checkout to the legacy
validator. It runs with an isolated Python import path rooted in the pinned
trusted checkout, preserves the promoter's arguments and error boundary, and
does not wrap a CLI that already has the shared reader. The DEV workflow copy
is preparation only: bootstrapping the trusted MAIN workflow still requires
separate explicit authorization. Remove both temporary workflow adapters once
the repaired CLI is released; neither permits executing candidate tools or
relaxing artifact evidence.
A no-content-change or paused run builds and
publishes nothing.

The source-owned helper interface uses an immutable
`python-artifacts-<dev-commit>` prerelease for schema-version-2 indexes. Its
signed composition and exact toolchain lock are reused across equivalent Git
containers, not rebuilt under a new Main identity. Historical version-1 indexes
retain the original `python-artifacts-<candidate-commit>` lookup and parent
binding. Version-2 publication and raw readback use that complete Dev-keyed set,
not per-plugin version aliases left by older candidates. Main's landed source
selects the release; the existence of assets for an unmerged source does not.
Legacy version-1 publication retains its original version-tag checks.
See [source-owned release qualification](pipelines/source-owned-release-identity.md)
for the helper interfaces and their independently reviewed control activation.

Before creating or merging the candidate PR, readiness publishes
the attested transfer index and every bundle. It validates the candidate's actual
parent/content tree, complete project set and archive contents first. The
prerelease is never marked latest and is not a source-release or dependency-policy
approval. A failed candidate may leave immutable inputs there, but never claims
an installed source release. Clients still have to independently verify their
selected source and the pinned artifact attestation; the prerelease's existence
is not installation authority.

The readiness gate pages through all remote assets, requires uploaded state
and matching SHA-256 metadata, and downloads the index and every bundle using
the **read-only** workflow token to verify their actual bytes. An upload result
or a release-writer-only view is not availability proof. Authentication,
publication, incomplete-set and byte-integrity failures fail the promotion job
before any PR merge is attempted. A retry may fill missing assets but cannot replace
existing bytes.

Missing assets in the same readiness release share one `gh release upload`
invocation, without `--clobber`. After complete metadata validation, readiness
and final-publication raw readback use at most four concurrent downloads, each
with the same consumer token and 120-second deadline. Every download must
finish and pass its exact digest check before a success receipt is emitted;
worker failures propagate and all owned workers are joined. Release/tag/source
validation remains serial; signer identity checks remain mandatory as described
above.
These source helpers take effect only when the independently governed control
selects a reviewed helper revision; a Dev merge alone does not change its pin.

The generated PR links its ready set, and merge authorization
and the merge waiter retain the SHA appended directly by the trusted promoter
to the step's GitHub output file. A remote branch lookup never selects that
identity: both the lookup and fetched ref must equal the promoter's SHA before
any readiness publication. A separate checkout-free merge job polls
required checks and issues a direct squash merge with an atomic expected-head
condition using its non-admin Actions token, which has contents and pull-request
write permissions. That job never receives the owner release PAT and executes
no checkout, setup actions or repository code. It never arms auto-merge:
GitHub's auto-merge request can survive a moved
head, so checking a head only when enabling it is not durable candidate binding.
An existing matching PR can be resumed; a different head, cross-repository PR,
closed-unmerged PR or ambiguous match fails rather than creating a replacement.
Both pre-merge readiness failures and final-publication failures enter the
shared failure reporter, including when final publication is skipped because
the promotion job failed before merge.
Promotion reads its platform-assigned immutable workflow `created_at` (not a
rerun's start time) with read-only authority. `--promotion-time` fixes both state
timestamps and Git author/committer dates; the workflow commit identity, UTF-8
encoding and unsigned commit construction are fixed too. Re-running the failed
job therefore recomputes the identical complete candidate SHA and tag stem.
An existing branch is accepted only when that exact recomputed SHA matches;
normalized source/state comparison, altered timestamps or pre-seeded refs never
establish provenance. Changed inputs fail rather than force-pushing a replacement.
Post-merge workflow discovery uses that same immutable creation time as its
inclusive lower bound, excluding unrelated retained history while reusing
earlier dispatches within the same run-attempt identity and still refusing
duplicate matching runs. An Actions rerun changes `github.run_attempt` and
dispatches new consumers for that attempt; its original `created_at` remains
the discovery bound, not the later rerun start time.

For a final post-merge binding, the publisher fetches the candidate commit
explicitly (squash does not preserve its ancestry), repeats
read-only availability verification without repairing the ready set, and
requires exact complete-tree equality with that ready candidate (including
release state and coverage pointers), rechecks the actual merge's parent/content
tree and project set and revalidates
archive contents. This final binding is not the first availability check.
Content-tree filtering removes only release state and coverage pointers from a
throwaway index loaded from the supplied candidate tree. Its index-only removal
does not require those entries to match the publisher's older trusted checkout;
the real index, working files and candidate payload remain unchanged.
Legacy version-1 publication uses the deterministic
`<plugin>-v<version>` GitHub Release tag. Each ZIP filename includes the
manifest's content-addressed `artifact_id`, permitting independently attested
rebuilds without replacing a prior asset. Outer ZIP members have fixed
timestamps/permissions and canonical order;
already-compressed wheels are stored without recompression, so identical
inputs cannot drift with filesystem metadata, wall-clock time or zlib versions.
No upload uses `--clobber`; an already-present same-name asset is accepted only
when GitHub's reported SHA-256
digest matches. Draft or malformed releases are rejected, not treated as
consumer-visible publication. The publisher resolves lightweight/annotated
tags and validates their source binding before accepting or uploading assets.
Only an explicit HTTP 404 in a supported GitHub CLI error format means a
missing tag or release; permission errors and ambiguous diagnostics stay fatal.
A new tag must resolve to the exact merged commit after release creation;
asset upload is separate and happens only after that recheck. An existing
version tag may point to an older shipped ancestor only when its exact
plugin/vendored closure, distributions/versions and payload hash still match.
Unrelated promotions must not move unchanged plugins' stable version tags,
and a toolchain-only rebuild may add a new content-addressed asset to that
same version. An unlanded alias requires the authenticated frozen-source
provenance described below; an unauthenticated or different-payload tag is rejected.
Version-2 publication instead verifies the complete Dev-keyed set and never
consults or creates those aliases. Composition keeps the ordinary landed-Main
version baseline and pending changefiles, without a reservation scan. See
[Main-derived plugin versions](pipelines/source-owned-release-identity.md#main-derived-plugin-versions).
The release PAT and verification token are scoped to their readiness/final
publisher shell steps, not exposed to checkout, Python setup or transfer-artifact
actions. Publication/readback remains its own job so a failure can be retried with
**Re-run failed jobs**, reusing the successful signer and exact merge
outputs rather than repeating promotion. Such a failure does not erase the
complete pre-merge candidate set, but the overall pipeline remains failed until
final publication is green. It also enters the failure reporter.
Transfer artifacts expire after two days; durable release-asset
retention remains a separately tracked policy.

These first-party sets are **not yet an installer fast path**. Third-party
closure/admission, verified shared-installer consumption and retention remain
open in `efforts/active/governed-python-artifact-promotion/README.md`. No
published bundle contains third-party runtime dependency bytes or certifies a
consumer's governed feed. Artifact consumption during install/update remains
optional: an authorized independently verified source/local-venv path must not
wait for artifact publication or rollout proof. This does not permit bypassing
source integrity, dependency governance or an invalid artifact's security refusal.

For manual runs, dispatch this workflow on the default branch. The gate
resolves its live dev tip and applies the same ancestor proof as automated
dispatch; it does not mistake the generated main workflow commit for a dev
candidate. Other workflow refs cannot enter the signing job.

## Branch model at a glance

- **`dev`** is this repo's contribution branch and the only one an ordinary PR
  targets. All review, status-check, and merge-approval policy below
  applies to `dev`.
- **`main`** is a **generated, release-only** branch. It is never a PR merge
  target for ordinary contribution — a CI promotion pipeline wholesale-
  regenerates it from `dev`'s current tip on a green build (see
  [Promotion: dev → main](#promotion-dev--main) below). `main` is PR-gated,
  not push-gated: direct pushes are blocked for everyone, and only four
  narrow, verified PR shapes are accepted — the promotion pipeline's own
  PR, a workflow-file-ONLY bootstrap PR, or rollback bookkeeping/revert PRs (see
  [Never admin-merge a PR into `main`](#never-admin-merge-a-pr-into-main)
  below for exactly what each shape requires).

If you (or a stale worktree/bookmark) still have a PR open against `main`,
retarget it — `gh pr edit <#> --base dev` — then rebase onto current `dev`
and resolve conflicts for real; there's no need to open a fresh PR.

Dependabot **version updates** explicitly target `dev` in
`.github/dependabot.yml`, retaining ordinary review, validation and changefile
requirements. GitHub's `target-branch` option does not redirect **security
updates**: those still use its default branch, `main`. A security fix must be
carried through a dev-based contribution and validated promotion; it is not a
direct-main source exception. See the
[Dependabot options reference](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference#target-branch).

## PR gating: the four enforcement layers

Every change lands through a pull request against `dev`. This is enforced
on four independent layers that agree:

### 1. Tooling

`.agent-worktrees/config.yaml` sets `pr.required: true`, so `agent-worktrees
push-changes` refuses direct-to-`dev` and the PR-workflow git-hooks block
committing to `dev` / pushing a worktree branch directly.

### 2. Branch policy (GitHub rulesets)

A GitHub repository ruleset ("dev branch policy: PR-required") carries a
`pull_request` rule (+ `non_fast_forward`) that blocks direct pushes to
`dev` server-side, for everyone (no bypass). `main` has its own branch
protection: no one pushes to it directly, including the promotion
pipeline itself — a personal GitHub account's branch rulesets have no way
to grant a bypass to the GitHub Actions app the way an organization's can.
Instead, the pipeline pushes its generated commit to a
`release/promote-<run id>` branch and lands it on `main` through a real
pull request, merged under the pipeline's own identity
(`APERTURE_RELEASE_TOKEN`) — the same PR-based path every other change to
this repo uses, just automated end to end. Repo-admin escalation is
retained for genuine emergencies (see
[Never admin-merge a PR into `main`](#never-admin-merge-a-pr-into-main)
below).

### 3. Review automation

The `request-review-if-maintainer` job in
`.github/workflows/workflow-lockdown-guard.yml` requests a Copilot review
automatically, but **only** for a PR authored by a **Maintainer** (the
CODEOWNERS root roster, owner included) — not merely any invited
collaborator. A Contributor's PR gets no automatic request (a Maintainer
can still request one manually via the "Reviewers" sidebar), and an
uninvited outsider's PR gets no automatic review at all. This scoping
exists because GitHub's `requestReviewers` call for
`copilot-pull-request-reviewer[bot]` silently no-ops — no exception, no
timeline event — when made by the default `GITHUB_TOKEN`
(`github-actions[bot]`) for a PR whose author isn't this repo's owner (a
personal, non-org account has no equivalent of the org-only "members
without a Copilot license" carve-out). `MAINTAINER_REVIEW_PAT`, a
fine-grained PAT for the owner scoped to only this repo (`Pull requests:
write` + `Metadata: read`), makes the request instead, as a licensed
account rather than the bot — used only for Maintainer-authored PRs, since
Maintainers already hold ruleset self-merge bypass (this extends no new
trust), and a Contributor's PR still needs a Maintainer's own approving
review regardless of Copilot's verdict. (The ruleset-native
`copilot_code_review` auto-review rule has no such condition — it would
fire for literally anyone — so this repo has removed that rule.)
Both the required guard and the review fallback run on
`pull_request_target`, so it can't fire until its own file has reached
`main` via a promotion — request a review manually for any PR opened
before that first promotion completes.

The automatic request reads one live eligibility snapshot and requests review
immediately when no existing activity qualifies; it does not hold a runner for
an initial event-based grace period or poll the API. Invalid/future event
timestamps still fail before requesting review. Closed/draft/retargeted/
superseded PRs, pending Copilot review and completed current-diff review suppress
the request. An observed native/manual request still inside its own five-minute
grace period also suppresses a nudge, without waiting or scheduling another run.
Completed reviews must be newer
than the latest base-retarget/reopen event. Identity checks accept the observed
`Copilot` and `copilot-pull-request-reviewer[bot]` aliases only for API users
typed `Bot`; the review write retains the canonical app login. A run superseded by a newer push
leaves that push's request to its own queued run. Per-PR serialization keeps
an active run plus the latest pending event; independent PRs remain concurrent.

If no review activity qualifies, the workflow makes **one nudge**. All reads use
`GITHUB_TOKEN`; only that write uses the licensed owner credential. The separate
read client is constructed from github-script's injected Octokit constructor
with automatic retries disabled; bundled action dependencies are not available
through dynamic `require`. A successful
request is reported as accepted, not as proof that Copilot started. There is
**no post-nudge confirmation polling**; review completion remains observable
through the normal PR gate. Write failures report reset/backoff headers when
present without blind retries. Manual requests remain available.

This fallback neither enables native automatic review nor infers the author's
Copilot subscription. The documented seat-management APIs require organization
owner access and do not supply a general personal-seat lookup for arbitrary PR
authors. Native review remains disabled under this repository's trust policy,
so the fallback may still supply the normal request until that policy changes
separately. Reduced amplification is not a guarantee against other clients
exhausting the owner's shared API budget.

Whether a review (Copilot's or anyone else's) must formally *approve* the
PR before merge is governed by a second ruleset ("dev branch policy:
review required (maintainer bypass)") plus `.github/CODEOWNERS`
(root-scoped to the full **Maintainer** group — CODEOWNERS review
satisfaction is OR across listed owners, so any *one* Maintainer's
approval counts, not all of them):

- A PR authored by anyone **other than** a Maintainer requires **some**
  Maintainer's own approving review before it can merge — Copilot's review
  alone is never sufficient for a Contributor's PR, however clean it comes
  back, so a change never lands without a Maintainer being aware of it.
  This holds even though the repo's **"Allow Copilot to approve pull
  requests"** setting (Settings → Copilot → Code review → Auto-approval)
  is enabled, letting Copilot submit a genuine `Approve` review that
  counts toward `required_approving_review_count`: that count and
  `require_code_owner_review` are independent, both-must-pass gates, and
  Copilot is deliberately **not** listed in CODEOWNERS, so its approval
  alone can never satisfy the codeowner-specific half of the requirement
  for a Contributor's PR.
- Each Maintainer is a named `User`-actor `bypass_actors` entry on the
  ruleset (`bypass_mode: pull_request` — still requires a real PR and all
  required status checks; only the *review* requirement is exempted),
  which in practice lets them self-merge their own PRs without a second
  approving review. Deliberately, this bypass is a per-`User` ruleset
  entry, **not** a bump to GitHub's `Maintain`/`Admin` repository role — a
  Maintainer here keeps their ordinary `Write` permission (no
  repo-settings, Actions-secret, or collaborator-management access) and
  gains only the self-merge capability.
  > **Read the bypass mechanism precisely: it is bound to the *merging*
  > actor, never to the PR's author.** GitHub ruleset bypass has no "only
  > my own PRs" concept: `bypass_mode: pull_request` means "when this
  > named actor performs the merge, this rule doesn't apply to them," full
  > stop — regardless of whose PR it is. In practice this means any
  > Maintainer *could* merge a Contributor's still-unapproved PR
  > themselves, bypassing the review-count/codeowner requirement meant for
  > that Contributor. There is no GitHub-side technical control for
  > "bypass only when merging your own PR"; the mitigation is that
  > Maintainers are trusted not to merge past a Contributor's required
  > review, and every bypass is visible in the ruleset insights / audit
  > log after the fact.
- Required CI status checks (`PR gate`, a fixed-name aggregate — see its
  own definition in `.github/workflows/ci.yml` for why a fixed anchor job
  exists rather than naming dynamic matrix jobs directly) apply to
  everyone with no bypass, including every Maintainer.

#### Review escape hatch is separate from CI enforcement

The `dev` classic protection's `enforce_admins` setting is enabled. An owner
merge using `--admin` therefore cannot waive the existing required CI context.
The separate review-required ruleset still grants Maintainers their existing
pull-request-scoped review bypass: a stuck or imperfect Copilot review does not
remove the worst-case self-merge escape hatch.

Agents must not reach for that review escape hatch prematurely. Follow
`CONTRIBUTING.md`'s review loop, address blocking findings and independently
confirm every configured required context has completed successfully for the
current head. A successful rollup of *reported* checks is not enough when a
required check has not registered. Missing or unreadable required evidence blocks.

Administrative recovery remains available for genuine infrastructure deadlock,
but changing enforcement requires an explicit human decision for the named
incident and PR, bounded scope, recorded before-state and restoration proof.
There is no standing agent permission to disable CI enforcement, add a bypass
actor, or wave away an ordinary test failure. The review escape hatch does not
relax the dev contribution route or the critical-workflow/metadata-only main
repair boundary.

#### Onboarding a Maintainer

The repository owner applies both halves of the existing Maintainer tier:
keep the invited collaborator's **Write** permission, add their GitHub user
ID to the review-required ruleset as a `User` bypass actor with
`bypass_mode: pull_request`, and add their login to `.github/CODEOWNERS`'
root `*` pattern through an owner-authored PR against `dev`. The automatic
Copilot review workflow reads that root roster directly; there is no second
workflow roster to update.

Verify the live ruleset entry, collaborator permission, and merged root
roster before declaring onboarding complete. Do not add bypasses to the
PR-required, required-check, workflow/CODEOWNERS-lockdown, or release-branch
rulesets. Maintainers still follow [the contribution and review
flow](../CONTRIBUTING.md#contribution-flow-pr-required), including required
CI and resolving blocking review findings before merging.

### 4. Workflow/CODEOWNERS lockdown

`.github/workflows/`, `.github/actions/`, and `.github/CODEOWNERS` itself
are locked to **this repo's owner alone** (`ThomasMichon` here, derived
from the immutable `github.repository_owner` context value rather than a
repository secret/variable — a repository *variable* is writable via the
API/CLI by ordinary Write access, the same Maintainer tier this lockdown
restricts, which would let a Maintainer defeat it by simply re-pointing
the value, so a user-owned fork's own owner is protected automatically
with zero configuration instead — this policy is scoped to user-owned
repos only; an organization-owned fork needs its own authorization
mechanism, since no individual PR author can ever equal an org login), not
the wider Maintainer group (workflow changes can exfiltrate secrets/PATs,
a materially different risk than an ordinary code change). A Maintainer's
review-bypass above does *not* cover this: a required status check
(`workflow-lockdown-guard`, from
`.github/workflows/workflow-lockdown-guard.yml`, run via
`pull_request_target` so a PR can't neuter its own trusted definition) in
its own ruleset ("dev branch policy: workflow/CODEOWNERS lockdown") fails
whenever a protected path is touched unless the **PR's registered author**
(`pull_request.user.login`) matches `github.repository_owner`.

This deliberately checks the PR's submitter, not individual commit
metadata: per-commit `author`/`committer` login is just GitHub's
resolution of the commit's plain-text git identity against an account,
not cryptographic proof — any contributor could set a commit's author
string locally and pass it with zero real involvement, so validating
commit metadata would buy nothing but complexity. A PR's `user.login`, by
contrast, is an authenticated fact GitHub sets once at PR-creation time
(you cannot open a PR as another account) — there's no equivalent way to
forge it, and unlike an event's `sender`, it doesn't change on
close/reopen.

**Known, accepted residual risk:** this checks who *opened* the PR, not
who pushed every commit in it — an already-invited Write collaborator
could still push a follow-up commit directly onto the owner's own
already-open PR branch, and this check would still pass. Closing that
fully would need commit-signature verification, which this repo has
decided against setting up (too much operational hassle for the residual
risk). This is a narrower, more unusual threat than an arbitrary outsider
or a Contributor's own PR, both of which this check fully closes.

**Known, accepted structural limitation:** GitHub's `required_status_checks`
rule matches purely by context name (`workflow-lockdown-guard`), not by
which workflow file produced it. A Write Maintainer could modify an
existing, untrusted-`pull_request` workflow (e.g. `ci.yml`) within their
own PR to add a trivially succeeding job of the same name — GitHub does
not distinguish that forged check from this one by app identity. Closing
this fully needs a check reported by a distinct, separately trusted GitHub
App pinned in the ruleset by `integration_id` — real additional
infrastructure this repo hasn't built. Until it does, treat this lockdown
as a strong deterrent against an ordinary Contributor's PR or an
unsophisticated mistake, not a cryptographically hard guarantee against a
Write Maintainer deliberately trying to defeat it.

This ruleset has **no bypass actors at all** — not even the owner —
because the check's own pass condition already grants exactly the
intended exemption. The practical effect: the owner can merge their own
workflow-touching PRs freely; adopting anyone else's such PR requires
re-authoring/re-pushing it under the owner's own account first — a
deliberate friction, not an oversight. (This is a required-status-check
workaround, not GitHub's native `file_path_restriction` ruleset rule: that
rule type returns `Validation Failed` on this personal, non-Enterprise
account — it's an Enterprise-only feature.)

> **Rollout note:** `workflow-lockdown-guard.yml` runs on
> `pull_request_target`, which always executes the workflow definition
> from the repository's ACTUAL default branch — **`main`**, not `dev` —
> so the check structurally cannot report at all until `main` has its own
> copy, which only happens after the next promotion. The enforcing
> ruleset is created but left `disabled` until after that promotion
> completes and a subsequent PR confirms the check actually reports
> successfully — only then is it flipped to `active`.

### Required dev source metadata

`source-attribution` is a distinct check in the same trusted
`workflow-lockdown-guard.yml` workflow. Its script is checked out at
`github.workflow_sha`, never the PR head, and reads the current description
through the GitHub API. Body/base edits and metadata changes reevaluate the
same head; repository automation and manually opened PRs are covered too.
Both native enforcement and refresh fail closed when multiple open dev PRs
share a head commit: GitHub's commit-scoped checks cannot safely express
independent per-PR results. Each PR needs a distinct head. Refresh snapshots
include current same-head PR membership, so a peer joining or leaving during
publication invalidates the result just like a concurrent metadata edit.
Freshness rereads normally query only PRs associated with that head commit,
not the entire repository's open-PR list. If this endpoint omits the current
PR (as it can for a commit already on the default branch), a full current
listing is required rather than assuming a unique head.
The same fully paginated census is authoritative when commit association fails
with HTTP 500, 502, 503 or 504; the script reports that fallback explicitly.
Authorization, rate-limit and unclassified errors still fail closed, as do
failed/incomplete censuses, missing membership, duplicate heads and moving
metadata. Fallback pages consume the existing refresh request budget and keep
the same final stable-snapshot and failure-fencing requirements.
The association response supplies current PR metadata too; normal refreshes
do not add a separate per-PR GET to each freshness read. New checks remain
pending during pacing and are completed only after a fresh matching snapshot.
The 150-distinct-head contract counts every read, write and pagination request
(902 total), below the standard 1,000-request Actions quota for that pass.
Backfill also enforces a 950-request per-run budget, including every pagination
page, metadata read, check write and retry. It reserves failure-fencing requests
before inspecting a head, including deduplication of an existing result, so
exhaustion cannot strand a pending check or leave an inspected but unverifiable
completed success as the latest result. Transport retries are disabled in all
trusted metadata script steps; policy retries consume the same budget. A larger or
unstable batch stops with an explicit incomplete-refresh error instead of
hammering the exhausted quota or reporting that every PR was checked.
Retry after the rate-limit reset, or refresh remaining PRs through their normal
metadata events; native per-PR enforcement remains independent of this batch.
The trusted checkout's integrity-locked CommonMark and HTML parsers identify
actual HTML comments, excluding fenced/indented code and quoted examples even
inside continuation list items. HTML containers cannot conceal additional
source comments; attribute strings and raw-text elements are not comments.
All metadata jobs install these pinned dependencies
with `npm ci --ignore-scripts`; no PR-head package or lifecycle script runs.
CONTRIBUTING.md and failure output publish the accepted codename, workflow and
native gh-aw origin forms and concrete manual repair steps. Encryption remains
optional, and syntax validation is not proof of actual source ownership.

The source declarations coalesce Dev synchronize departure refresh into the
existing `workflow-lockdown-guard.yml` invocation. Its isolated
`refresh-departed-source-attribution` job reuses `.departed` to repair current
Dev peers on the old `before` head; native enforcement independently validates
the moving PR's new head. The departure-refresh job retains its own checks-write
grant; native required-check jobs remain read-only. Existing review and Main
push/manual refresh jobs retain their permissions and behavior.

`source-attribution-departures.yml` retains closed and edited events, including
retargeting away from Dev. Its trusted, checks-writing job has a distinct
non-required name and publishes the actual source context only for current
Dev peers. It is not branch-filtered, so retarget-away remains observable; the
route never creates a skipped
`source-attribution` or `workflow-lockdown-guard` result that could bless a peer.
This clears collision failures without requiring an unrelated edit to the
remaining PR. It uses the same validator, freshness checks and request budget.

These source declarations become active only through qualified delivery to the
trusted default branch. A Dev merge is not evidence of changed live routing.
Sharing the synchronize invocation removes a workflow admission, not an
allocated refresh job; non-synchronize events can gain a skipped non-required
job record. Measure workflow runs, jobs and check records separately.

Pushes to the default release branch and trusted manual dispatches refresh the
distinct context on current open dev PR heads without editing their descriptions
or labels. Unchanged metadata/validator decisions are deduplicated and writes
are paced. This refresh closes the rollout gap for already-open PRs whose old
green lockdown runs never examined attribution.
An unstable or unreadable PR fails its refresh without starving later heads;
the job reports sanitized failures after processing the batch.
Refresh publication starts pending, rereads metadata before and after completing
its result, and supersedes any changed/unverifiable snapshot with a failure
before a bounded retry. A delayed refresh must not leave a stale successful
result after a concurrent description or metadata edit.
Completed check conclusions are not rewritten: superseding failure uses a new
check, and only an unfinished original is closed out directly.

**Activation is separate from implementation merge.** After promotion to
`main`, verify a real source-check failure and a body-only repair from the
actual trusted workflow. Only then extend the existing no-bypass required-check
ruleset with `source-attribution`, preserving its other checks and conditions.
Do not report enforcement live based only on a dev merge or an unrelated
same-named check. The existing context-name/publisher forgery residual risk
described above applies here too.

Source-policy decisions do not waive protected-path authorization, review, or
other required CI. Agents repair missing metadata and do not apply or remove
operator-only policy labels.

### Everyone else

Anyone who hasn't been invited as a collaborator at all **gets no
automatic code CI, no automatic Copilot review, and no agentic-workflow
support**, only the strictest built-in fork-PR-approval gate (Settings →
Actions → General → "Fork pull request workflows" → **"Require approval
for all outside collaborators"**), which already blocks every Actions run
(including CI) from starting until a Maintainer manually approves it, plus
the trusted review job's own collaborator check (above) for review. A
Maintainer can still manually approve a run or request a review for an
outside PR at their discretion — this only removes the automatic path for
someone the repo owner never invited. Trusted metadata checks may still run
without executing any fork/PR-head content.

**PRs targeting `main` do not allocate CI**.
`.github/workflows/base-branch-reminder.yml` supplies the safe
reminder and a trusted, metadata/Git-data-only gate without running fork code:
see
[If you opened a PR against `main` by mistake](#if-you-opened-a-pr-against-main-by-mistake).

## Automated workflows reference

Every workflow under `.github/workflows/`, what triggers it, and what it
does. Grouped by role; see each file's own header comment for full
rationale and any known limitations.

**PR-related gating** (not all run on every PR — `ci.yml` runs on every
PR except those targeting `main`; trusted review, lockdown, and source-attribution PR checks target `dev`,
with source-attribution refreshes on `main` push/manual dispatch; the reminder
targets only `main`; `trusted-ci.yml` is now manual routing metadata only):

CI explicitly includes `edited` alongside the default PR activities so a
main-to-dev retarget immediately creates the required contribution checks.
GitHub cannot filter this activity to base edits alone at workflow admission,
so other PR edits also run CI; excluding main still prevents its redundant
release-candidate invocation.

Within each PR ref, a new head's `synchronize` event supersedes the older head's
executing CI. Metadata-only edits still trigger CI, but do not cancel and
restart an in-progress run. Cancellation stops the compact unit path and its
aggregate; an uncancelled dependency failure still reaches the aggregate and
reports failure. Dev pushes retain rolling queue semantics, so frequent
contributions do not restart an executing source-admission run.

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | `pull_request` (all bases except `main`), `push` (`main`/`dev`), `workflow_dispatch` | Operational source guards, impact-conditional admitted units with single-pass coverage, and the fixed-name required PR aggregate. Pushes do not duplicate promotion's unit execution; manual CI remains unit-only. |
| `copilot-code-review.yml` | `workflow_dispatch`; official Copilot Code Review setup after default-branch publication | The `copilot-setup-steps` job preserves hosted review capacity with literal `ubuntu-latest`, with no checkout or dependency setup; Copilot supplies its own checkout afterward. It does not change the coding-agent fallback. The independently guarded `copilot-extensions-review` ARC role remains available only for an explicit reviewed route change, not additive overflow. A default-branch selector change requires an activation decision and an observed managed-review canary. |
| `workflow-lockdown-guard.yml` | `pull_request_target` → `dev` (including body/label edits), `push` → `main`, `workflow_dispatch` | Independent trusted jobs host the existing required `workflow-lockdown-guard` check, separate `source-attribution`, Maintainer-only review fallback, and synchronize-only departed-head refresh. Default-branch push/manual runs retain existing source refresh for open `dev` PRs; job-scoped permissions/concurrency preserve their boundaries. Source changes need qualified trusted-default delivery. No PR code executes. |
| `source-attribution-departures.yml` | `pull_request_target` (`closed`, `edited`) | Trusted metadata-only refresh for closed dev PRs and retargets away from dev, without a new-base filter. Synchronize refresh shares the existing trusted Dev entrypoint. Publishes no required-context skipped jobs and never executes PR content. |
| `base-branch-reminder.yml` | `pull_request_target` → `main` | Hosts the trusted `main source gate`, the existing one-time retarget reminder, and identifier reporting for owner-authored PRs that pass admission. Gate permissions are contents-read only; any checkout is pinned trusted main, and PR commits are fetched only as Git data. No PR code executes. This replaces main-PR CI admission and post-CI scanning. |
| `trusted-ci.yml` | `workflow_dispatch` | Routing metadata only: the old self-hosted PR integration route is retired; hardened periodic Linux integration belongs to `windows-coverage-rotation.yml`. |

The dedicated review setup is independently guarded: only its exact filename,
official job identity, minimal permissions and setup step may select literal
`ubuntu-latest` or the named `copilot-extensions-review` ARC scale set. This
exception does not authorize that label in another workflow or relax the
validator lane. GitHub documents one runner selection for a repository's managed
review lane, not automatic hosted/ARC overflow. Label arrays require every
label; they are not alternatives or failover. Selecting ARC replaces this
lane's hosted routing, so it must not be described as additive capacity.
Keep hosted review primary unless a separate supported routing decision is
explicitly approved. See [Copilot runner configuration](https://docs.github.com/en/copilot/how-tos/copilot-on-github/set-up-copilot/configure-runners)
and [cumulative runner labels](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/use-in-a-workflow).
The committed setup remains hosted; accepting the alternative label
in policy is not a capacity-readiness signal or a selector activation. Main
preservation requires its own owner-coordinated named-allowlist update before
the later canonical manifest transaction. The generic preservation helper
already supports a fourth workflow path and needs no change solely for that
addition. Guard source and tests still require qualified default-branch delivery
before a custom ARC selector is enabled.

**Reactive, post-CI (triggered by `ci.yml` completing):**

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `promote-trigger.yml` | `workflow_run: ["CI"]` | One trusted invocation hosts independent dev-ancestry dispatch, fork-safe identifier scanning and committed-dev module baseline maintenance. Baseline PRs target dev only; pinned trusted tooling reads the completed dev input as data, with isolated Python, credential-free checkouts and baseline-only publication. Success/failure completions can widen existing ceilings without admitting new offenders; stale inputs skip. Automation never lowers or removes ceilings; tightening remains deliberate manual work. A stale higher ceiling is tolerated under ordinary review rather than exact-baseline equality or global strict-base CI. The release PAT is confined to PR publication. No extra verification dispatch is needed because that PAT triggers ordinary PR CI/review. |

**The promotion pipeline (dev → main):**

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `validate-and-promote.yml` | `repository_dispatch: [dev-advanced]`, `workflow_dispatch` | The pipeline itself: `gate` (confirm the dispatch really reflects `dev`'s current tip) → full validation suite → `promote` (publish/read back candidate inputs and open a generated `release/promote-<run id>` PR against `main` using `APERTURE_RELEASE_TOKEN`) → a separate checkout-free `merge-promotion` job (wait for required checks and squash-merge that exact head using the non-admin Actions token) → final publication and explicit post-merge workflow verification. See [Promotion: dev → main](#promotion-dev--main). |
| `purge-consumed-changefiles.yml` | `schedule` (daily), `repository_dispatch: [purge-changefiles-requested]` | Standalone daily (or on-demand) companion to the promotion pipeline: opens the "clear consumed changefiles on dev" housekeeping PR for whatever changefiles a promotion has already consumed by then -- pure housekeeping, not per-promotion and never load-bearing for promotion correctness. |

**Scheduled sweeps and watchdogs** (`stale-branch-sweep.yml` acts on
already-merged PRs' branches):

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `module-health-watchdog.yml` | `schedule` (daily), `workflow_dispatch` | Proactively finds the single worst module-size-ceiling offender (organic drift no PR-time gate catches) and files/updates one tracking issue for it. |
| `stale-branch-sweep.yml` | `schedule` (weekly), `workflow_dispatch` | Deletes merged-PR branches still lingering on `origin` — the automated form of the one-time 3000+-branch cleanup. |
| `installation-context-full.yml` | `schedule` (daily), `workflow_dispatch` | Full-sweep variant of the installation-context contract smoke check `ci.yml` runs per-PR. |
| `installation-context-acceptance.yml` | `workflow_dispatch` | Explicit all-copy native diagnostic acceptance on fixed GitHub-hosted POSIX and Windows PowerShell 5.1 routes, partitioned by mode and pointer evidence. [Portfolio and exact-ref gate](../TESTING.md#derived-native-installation-diagnostics). |
| `context-handoff-exhaustive.yml` | `schedule` (weekly), `workflow_dispatch` | The real-git/child-process/lock-race exhaustive suite for `context-handoff`'s exhaustive test tree — deliberately its own workflow so its `schedule` trigger doesn't apply to the rest of `ci.yml`'s matrix. |
| `crash-diagnostics-stress.yml` | `schedule` (daily), `workflow_dispatch` | Stress-tests `context-handoff` crash-diagnostics paths. |
| `agent-dispatch-pwsh-race-exhaustive.yml` | `schedule` (weekly), `workflow_dispatch` | Runs `agent-dispatch`'s multi-process `pwsh` mutex-race tests (self-skipped by default in `ci.yml`'s smoke lane) on both `ubuntu-latest` and `windows-latest` — deliberately its own workflow for the same reason as `context-handoff-exhaustive.yml`. |
| `payload-cwd-contract.yml` | `schedule` (weekly), `workflow_dispatch` | Routing inventory only until hardened root-tools/native containers are enrolled. Native commands are preserved but explicitly disabled; no PR tests or passing native-evidence claim. |
| `windows-coverage-rotation.yml` | Existing daily `schedule`, `workflow_dispatch` | Immutable-source hardened Linux full-plugin containers, with logs and portfolio-owned failure tracking, including selection/preparation failure before outputs exist. Only scheduled or manual Dev runs update the shared tracker; other manual refs remain inspection-only. Windows execution is explicitly unavailable pending container capacity; Linux success does not qualify other platforms or unenrolled tiers. |
| `ci-failure-fix-attempt.lock.yml` | `issues: [labeled]`, `workflow_dispatch` | Generated agentic-workflow (`gh-aw` compiled) that reacts to a labeled CI-failure tracking issue; also manually dispatchable with inputs. |

The release reporter checks the optional fixer's workflow state once before
dispatching newly filed signatures. An operator-disabled workflow is a visible,
successful skip: no dispatch requests are sent. Repeated issue IDs in one batch
are dispatched at most once. If disablement races with a
dispatch, the reporter rechecks state once and skips the remaining batch without
retrying. Authentication, metadata, and dispatch errors for an enabled workflow
remain failures; detection, signature deduplication, and comment cooldowns are
unchanged.

## Promotion: dev → main

Merging to `dev` is not the same as shipping. Every consumer still only
ever polls `main`. The promotion pipeline
(`.github/workflows/validate-and-promote.yml`, fired by
`promote-trigger.yml`) is **triggered by a green `dev` build, not a
schedule** — there is a real wait between a merge landing on `dev` and a
promotion actually shipping it to `main`, not an instant release.

The source promotion trigger omits its ancestry-filter runner for successful
`main`-push CI completions. Main-push CI itself remains unchanged. Manual CI
on any ref, successful Dev CI, promotion-completion recovery (including failed
or cancelled runs) and owner-closed Main PR wakes retain their existing paths.
An admitted branch name is never ancestry evidence: the filter still checks
actual Git ancestry and the dispatcher retains source, freshness and ownership
checks. Identifier reporting and Dev baseline maintenance are unchanged.
This omits a no-op job allocation, not the surrounding workflow invocation.
Preserved Main control requires its own qualified adoption before this source
condition changes live admission.

### Inert main-control preservation support

The composer and trusted artifact recomposition share
`tools/promotion_control.py`. With no control manifest on either input, existing
composition is unchanged. No manifest is enabled by this implementation.

A future owner-reviewed `.github/promotion-control.json` on pinned `main` uses
schema `copilot-extensions.promotion-control`, integer `version: 1`, and a
nonempty `files` array of exact lowercase `.github/workflows/<name>.yml` or
`.yaml` paths (at most 64). The manifest itself is preserved automatically.
Only non-executable regular Git blobs are admitted; duplicate keys/paths,
case aliases, traversal, unsafe candidate destinations and missing declared
files fail closed. Source, helper, test and documentation paths are not allowed.
`dev` cannot introduce or redefine the declaration. Preserved bytes come from
the pinned main commit, after ordinary versioning and materialization, and
their blob IDs appear in the promotion summary and pipeline provenance.

Removing a workflow from the trusted declaration while leaving it on Main returns
it to normal Dev composition. Removing the actual Main file as well retires it
only when Main's prior `last_promotion.control_preserved` receipt authenticates
the exact prior declaration and regular blobs against an ancestor Main commit.
Dev state never grants deletion authority. The composer retains at most 64
`retired` path-to-origin tombstones in that receipt, revalidates their ancestor
declarations and removes matching obsolete Dev copies before staging. This
survives later promotions; explicit Main reintroduction clears the tombstone.
Unsafe deletion destinations and malformed or unavailable provenance refuse the
composition before controls are written.
Once Main records control provenance, removing its ownership manifest also
refuses composition rather than silently restoring legacy behavior.

`tools/promotion_control_transition.py` classifies two pinned ancestor-related
Main snapshots against their actual exact declarations. Only declared control
changes and the operator's `paused`/`pause_reason` settings are control-only.
Payload, coverage-baseline, unknown-governance, promoter-lineage and rollback
changes require a new plan; removal of the ownership manifest also refuses.
Non-operator state comparison preserves JSON types, including nested values;
boolean/number substitutions and non-finite values cannot hide a policy change.
Every changed control snapshot requires a fresh trusted execution. This
classification does not qualify artifacts, prove equivalent construction inputs
or authorize the old running controller to execute newer policy. The current
frozen-candidate publication and merge paths still refuse a changed base.

This mechanism
does not establish admission authorization, atomic merge freshness, wrapper/helper
compatibility or control-preserving rollback. Those contracts and their live
rejection/recovery proof are prerequisites to activating a manifest; existing
validation and candidate gates remain required.

### A red `dev` build ships nothing

The pipeline is a hard chain: the `gate` job only proceeds once the
triggering "CI" run genuinely reflects `dev`'s current tip and was green,
and the full validation suite + `promote` job only run once `gate`
confirms that. If a merge leaves `dev`'s CI red, the chain simply never
fires for that commit — **no candidate PR, no promotion, no release** —
and this blocks every other contributor's already-merged work sitting on
`dev` behind it too, since the next successful trigger promotes everything
accumulated on `dev` so far. Treat a red `dev` build as the first
priority: land a forward fix immediately, or revert the breaking merge,
rather than leaving it red at leisure.

### Delivery targets and what to watch

Target **thirty minutes** for validate-and-promote, not an assumed ten-to-twenty
minute guarantee. Ordinary PR event-to-required-gate completion targets under
ten minutes including queueing; a one-hour promotion allowance requires separate
acceptance of the local-Dev workflow. Actual timing and release acceptance
remain campaign gates.

The [source-owned controller contract](pipelines/source-owned-release-identity.md)
owns qualification-before-PR ordering, single active promotion admission and
job-only recovery without duplicate full qualification. Both controller profiles
retain exact-head squash merge in a separate, checkout-free job using the
non-admin `GITHUB_TOKEN`/Actions identity. The owner `APERTURE_RELEASE_TOKEN` creates
the candidate PR and publishes artifacts, but never authorizes this routine
merge. To confirm a change actually shipped rather than
assuming the `dev` merge itself was the release:

The promotion job retains the workflow's exclusive concurrency slot while
waiting for required checks and its exact generated PR to merge. It does not
arm a head-independent auto-merge request. The waiter verifies the PR identity
before the atomic expected-head merge, requires strict server-enforced
base freshness on the existing main source gate, and reports a bounded timeout,
closed-unmerged state or provider error as a failure.

#### Legacy timestamp tags and post-merge verification

The following timestamp-tag/postmerge-dispatch details apply to the legacy Dev
controller and historical receipts, not the source-owned Main generation's
premerge publication boundary. The release tag uses that
PR's actual merge commit, even if `main` advances again before tagging.
`tools/tag_promotion.py` provides the intended naming for that tag:
`<stem>-<merge sha8>`, where the stem (`promote-<UTC timestamp>`) is the
`last_promotion.tag` recorded in the merged commit's own
`.github/release-pipeline-state.json`, so a tag and the state it releases
agree. It refuses to tag when that state names a different promoted `dev`
commit or the tag already points elsewhere, and a re-run on the same commit is
a no-op. Only a successfully verified tag is emitted as a workflow receipt.

An Actions-token merge does not trigger push workflows. The promotion flow
therefore explicitly dispatches CI at that promotion tag, and the privileged
Workflow lockdown guard from trusted main, with the exact expected merged SHA
and owning run-attempt identity. The guard independently verifies that target
SHA is on its immutable main control snapshot before checking out the release
implementation; matching caller-supplied tag/SHA fields are not authority.
Its control snapshot may be newer than the release, and verification records
both identities. CI's promotion dispatch retains all main-push discovery, plugin,
native Windows and standalone-service coverage, without selecting the separate
manual exhaustive tiers or repeating the full validation matrix. Its promotion
aggregate rejects any skipped required job. The verifier waits for both exact
runs, checks all job results and specifically requires the source-attribution
refresh; accepting a dispatch request is not completion.

Native Windows command-binding and lifecycle contracts run in a separate,
required, change-scoped CI job. Real installer admission, teardown, and hidden
child stream tests retain their complete selection and individual deadlines;
they do not share the read-only provenance lane's two-minute step budget.
The lifecycle job preserves the catalog, read-only lifecycle and native
first-touch partitions, each bounded at two minutes, with eight minutes overall
based on a 316-second native run of the former combined selection. Each pytest
step first provisions through the canonical seasoning adapter; duration output
identifies future growth without hiding timeouts or weakening the required
aggregate.
CI-selection contracts and payload-source attribution also have their own
two-minute steps, so native PowerShell checks of workflow failure propagation
and source graphs cannot consume the read-only diagnostic API tests' budget.

The human administrator escape remains available for independently
shape-validated workflow/control repair, never arbitrary source. Such a repair
may advance main beyond its latest published release. Snapshot checks require
that release to remain on main, not that it remain main's current control tip.

```bash
gh pr list --repo ThomasMichon/copilot-extensions --search "is:merged head:release/promote-" --limit 5
```

**Don't substitute a SHA-ancestry check for the command above.** `git
merge-base --is-ancestor <your-merge-sha> origin/main` (or an equivalent
`gh api .../compare/<old>...<new>` lookup) can report **false** even after
your change has genuinely shipped: the `promote` job produces a *new*
commit on `main` by replaying the `gate` job's pinned, validated `dev`
snapshot (`DEV_SHA`, not necessarily `dev`'s current tip at promotion
time), so your original merge commit's SHA never literally appears as an
ancestor, even though its content landed. Confirm a promotion either via
the `release/promote-*` PR query above, or by diffing the actual file
content on `origin/main` against what you expect (`git show
origin/main:<path> | grep <distinctive string>`) -- never by SHA ancestry.

A small, separate **"clear consumed changefiles on dev"** housekeeping PR
(opened by `purge-consumed-changefiles.yml`) follows on its own **daily**
schedule (or on-demand via a `repository_dispatch`), not per-promotion --
it is a standalone companion, deliberately decoupled from the promotion
pipeline itself (the purge is pure housekeeping, never load-bearing for
promotion correctness: `tools/promote_release.py`'s own bump computation
already excludes a consumed changefile regardless of whether it still
physically exists on `dev`). Don't expect one to follow within minutes of
every promotion -- routine cleanup, not something that needs review, but
it can take up to a day to appear. Before committing or pushing a new cleanup,
the trusted workflow completely pages open `dev` PRs. An owner-authored,
same-repository `chore/purge-changefiles-<run>` PR with its matching workflow
origin blocks another publication, including when it is draft or held. The
job fails visibly with links for the existing cleanup owner; it does not
merge, close or replace those PRs. Unavailable or invalid PR metadata also
fails closed. Run-level serialization alone cannot prevent duplicate cleanup
PRs after an earlier run times out waiting for checks. This source behavior
becomes live only when the workflow's existing Main control owner adopts it.

### Previewing without waiting for a real promotion

- **`python tools/preview_release.py <plugin>`** builds a scratch copy of
  that plugin's payload — with its vendored `libs/<lib>` materialized from
  canonical, and the version it would get if its pending changefiles were
  consumed right now — entirely read-only against the real checkout. Good
  for "what would ship" without touching anything.
- **`python tools/materialize_main.py --candidate --dest <new-directory>`**
  builds the complete marketplace from committed HEAD using the promotion
  materializers, with a durable `MATERIALIZED.json` whole-tree hash receipt.
  Unlike `PREVIEW.json`'s informational `source_commit`, this receipt binds that
  single source claim to the artifact's paths, bytes and executable/link identity.
  Commit first; the builder intentionally does not claim uncommitted bytes as
  HEAD. The standalone copy needs no Git checkout. Clean-room candidate consumers
  verify the expected source commit and every path with their separately mounted
  trusted library before accepting the product; the candidate's own tools do not
  verify themselves. This is reproducible source provenance, not a signature from
  an untrusted builder, and is not shipped-release or clean-room pass evidence.
- **For actually running uncommitted/unmerged code against the real
  deployed CLI**, use the **mutable-dev-slot** pattern:
  `docs/patterns/mutable-dev-slot.md`. It gives each plugin a claimed,
  first-class `versions/dev/` runtime slot rebuilt in place, GC-protected
  by a `dev-claim.json` sidecar.

If something promoted to `main` turns out to be bad, see
`tools/rollback_release.py` (pause the pipeline, revert the generated
commit, then resume once `dev` has an actual fix) rather than hand-editing
`main`.

## Never admin-merge a PR into `main`

`main` only ever moves via a `release/promote-*` PR, a genuine
workflow-file-ONLY bootstrap PR, or rollback bookkeeping/revert PRs
(see the `main-gate` job in `base-branch-reminder.yml`), and `main-gate`
recognizes and passes **all four** shapes on its own — unassisted, no
override needed. That means `gh pr merge --admin` (or the
equivalent `--admin` flag on any PR-merge tool) has **no legitimate use
against an ordinary contribution or promotion PR** once this gate is in
place: if `main-gate` is failing a PR, that is the gate correctly telling
you the PR doesn't belong on `main`
— retarget it to `dev`, don't override the check. (This prohibition
covers routine work only — `main`'s ruleset also grants a narrower,
separate `RepositoryRole: admin` bypass reserved for a genuine
human-operator emergency, e.g. `tools/rollback_release.py`'s own
documented escape hatch for landing a revert when the normal automated
path can't. That bypass is never automated, and is not a license to
override `main-gate` on a routine PR it's correctly failing.) This is not a
hypothetical risk: a PR landed directly on `main` via admin-bypass once,
stranding content that the next wholesale dev→main promotion would have
silently reverted, because `main`'s tree is regenerated entirely from
`dev`'s current tip on every cycle — anything that only ever touched
`main` is invisible to that diff and vanishes the next time anything else
promotes.

`main-gate`'s own diff of "what does this PR actually change" is
**merge-base-relative** (`git diff --name-only "$(git merge-base base
head)" head`), matching GitHub's own "Files changed" tab and the
`pulls.listFiles` API — not a literal `base_sha..head_sha` diff, which
would disagree with those on a diverged branch (e.g. wrongly rejecting a
workflow-only PR solely because `main` independently gained an unrelated
file after the branch forked).

The admission program lives in
`base-branch-reminder.yml`, using the existing `main source gate` context.
Its job is independent of the reminder and has only contents-read permission.
Promotion and non-owner cases need no checkout; owner workflow/state/rollback
cases check out pinned trusted main and fetch the PR commit as Git objects,
never as an executable working tree. The trusted producer was released and
live-proven on default main before removing the original CI producer, because
`pull_request_target` loads its definition from that branch. Main PRs no longer
allocate CI. Full main-push validation, contributor CI, fork approval and
deep/platform lanes remain unchanged.
The existing post-CI caller and an admitted-owner-only job in this host share
`.github/actions/identifier-leak-guard/`: the same trusted scanner reads changed,
non-removed PR blobs without checking out PR code. Filenames travel as JSON-string
records end-to-end (`--paths-jsonl`), preserving newlines and other Git filename
characters rather than splitting them into nonexistent paths. The same reporter publishes
the `identifier leak guard` context with full match details. It fences stale heads
before scanning and again before reporting. Only a working but unconfigured guard
is neutral; infrastructure failures fail, including checkout/action-loading
failures reported by thin caller-side steps independent of local files.
Non-owner and rejected main PRs do not enter this secret-backed scan; the existing
fork approval boundary is not widened. The post-CI caller still scans CI-tested
PRs targeting other branches. The main-PR host separately scans generated
content; full main-push CI is not a substitute for that scan.

### If you opened a PR against `main` by mistake

Main-targeting PRs do not allocate `ci.yml`, regardless of author.
`.github/workflows/base-branch-reminder.yml` supplies feedback without executing
unapproved fork code: a
narrow, `pull_request_target`-triggered job (not subject to the
fork-approval gate) posts a one-time comment asking the author to retarget
to `dev`, for any PR against `main` not authored by the repo owner. It
never checks out or executes the PR's own code and never runs tests or requests
review. The independent contents-read gate in the same trusted invocation can
report the existing admission decision without approving a fork's code execution.
On `synchronize`, admission and the admitted-owner scan run; the reminder retains
its original event scope and one-time-comment behavior. The Dev source also
omits the reminder job before runner allocation for owner-authored PRs, rather
than allocating it only to return without posting. Its script retains the
owner/signature checks as defense in depth. Adoption by the independently
preserved Main workflow is a separate control change; a Dev merge alone does
not reduce live Main reminder allocations.

It recognizes `main-gate`'s own four legitimate automated-PR shapes (the
release pipeline's own PR, a workflow-only bootstrap PR, and `rollback_release.py`'s
own pause/resume state-commit PR and revert rollback PR) by the same
branch-name and diff-content (or branch-name and commit-count, for the
revert shape) signature `main-gate` itself checks, not merely by
"author is the repo owner" — so if this reminder is ever widened to cover
every PR against `main` rather than only non-owner authors, it still can't
mistake the release pipeline's own automated PRs for ones that need a
nudge.

## If `main`'s history is force-rewritten

`main` may occasionally have its history rewritten (e.g. a deliberate,
operator-approved purge of accumulated large blobs from old promotion
commits). This is a one-shot, `main`-only operation, never routine, and
never something an agent decides to do on its own initiative.

If a local checkout/worktree's `main` ends up non-fast-forward against
`origin/main` after one of these (`git fetch` reporting diverged history,
or a push to `main` rejected for a reason that isn't the ordinary gate
checks above): **don't merge, rebase, or try to reconcile the two
histories.** `main` is a generated artifact (wholesale-replaced every
promotion anyway) — just discard the local `main` and recreate it from the
new one:

```bash
git fetch origin main
git checkout main && git reset --hard origin/main
# or, for a worktree whose own branch merely based off the old main:
git rebase --onto origin/main <old-main-tip> <your-branch>
```

**`dev` is never affected** — it forked long before any such rewrite and
has its own independent, untouched history; only checkouts that track
`main` directly need this. Nothing downstream that actually *consumes*
this repo needs to know or care either: `copilot plugin install`/`update`
(both the direct-repo and marketplace paths) and `worktree-manager`'s own
self-updater fetch **by branch name**, never a pinned commit SHA — so they
transparently pick up whatever is currently on `main`, rewritten or not.

### Ancestry/compare checks across the rewrite boundary fail by design — that's not corruption

Any SHA-based ancestry check spanning the rewrite — `git merge-base
--is-ancestor <pre-rewrite-sha> origin/main`, a GitHub `compare/<old>...
<new>` API call, or a PR-merge-ancestry lookup for a PR merged before the
rewrite — **genuinely has no common ancestor** across that boundary and
correctly fails (locally: a non-zero exit with no useful message; via the
API: `404 No common ancestor between <sha> and <sha>`). This is expected
for every pre-rewrite commit, not a sign the rewrite dropped history or
that your checkout is broken — `git filter-repo` rewrites every commit's
**ID** (each commit's SHA depends on its parent's SHA, so once the root
commit's ID changes, every descendant's ID changes too, all the way to the
tip), so no pre-rewrite commit SHA appears anywhere in the rewritten line.
(A commit's **tree** ID is a different thing and does *not* automatically
change along with it — a tree ID depends only on its own entries, so a
commit whose tree was untouched by the rewrite keeps the same tree ID even
though its own commit ID changed.)

To check whether a specific pre-rewrite change (e.g. a PR merged shortly
before a rewrite) actually survived, don't try to re-derive ancestry across
the boundary — check the rewritten `main`'s **content** directly instead,
by file:

```bash
gh api "repos/<owner>/<repo>/contents/<path>?ref=main" --jq '.content' \
  | base64 -d | grep '<expected string from that change>'
```

(or just read the file at that ref with any `gh`/API content call). A
history rewrite like this is only ever verified content-identical at the
rewritten **tip** (its tree, as a whole, matches the pre-rewrite tip's tree
byte-for-byte) — it is NOT content-identical commit-by-commit throughout
history: the whole point is deliberately stripping specific oversized
blobs from every historical commit that carried one, so a commit that only
ever touched a since-stripped blob no longer resolves that blob's content.
Every file that survives to the rewritten tip, though, is exactly what it
was. The one documented rewrite to date is the 2026-10-04 purge recorded in
the repo README banner and
[`efforts/done/main-history-rewrite`](../efforts/done/main-history-rewrite/README.md);
if you hit this exact "no common ancestor" symptom, check there first for
the exact old→new SHA pair before assuming something new is wrong.

## Release & Versioning

### Marketplace architecture

This repo is a **Copilot CLI plugin marketplace** — a GitHub-hosted
registry of plugins that machines install via `copilot plugin marketplace
add ThomasMichon/copilot-extensions`. The marketplace catalog lives at
`.github/plugin/marketplace.json` and lists every plugin with its current
version. The Copilot CLI reads this file to determine available updates.

> **Deploy with `<repo> update` for any runtime plugin — never hand-run
> `copilot plugin update` there.** `copilot plugin update` on its own
> refreshes only a plugin's *payload* (cached source + skills) — it does
> **not** rebuild a runtime
> (venv/binstubs/service), and if the version wasn't bumped it silently
> no-ops ("already at latest"). A payload-only plugin (skills/hooks/agents
> only, no venv/binstub/service) has no runtime to miss, so
> `copilot plugin update` genuinely does fully deploy that class on its
> own (see
> [docs/install-contract.md § What the marketplace vendors](install-contract.md#what-the-marketplace-vendors-copied-vs-loaded)).
> For a runtime plugin, or for a repo-wide update across every plugin
> regardless of class, use the one unified flow: **`<repo>
> update`** (`agent-worktrees update`, or any repo binstub such as
> `dotfiles update`). It refreshes **every** registered plugin's payload,
> rebuilds **every** runtime, and fast-forwards the anchor checkouts — in a
> single command, per machine. See
> [docs/install-contract.md → Plugin update ≠ runtime install](install-contract.md#plugin-update--runtime-install).

### Version scheme

PEP 440-compatible versioning: `MAJOR.MINOR.PATCH[-devN]`.

- **Patch** bumps (`1.0.1 -> 1.0.2`) — bug fixes, small improvements,
  new skills/docs that don't change runtime behavior.
  > Only a change **inside a plugin folder** (its `src/`, `skills/`, or its
  > own `docs/`) ships in that plugin's payload and needs a bump. A
  > **repo-root** `docs/` change is not vendored into any plugin and needs
  > **no** bump — see
  > [install-contract.md § What the marketplace vendors](install-contract.md#what-the-marketplace-vendors-copied-vs-loaded).
- **Minor** bumps (`1.0.x -> 1.1.0`) — new features, behavioral changes,
  new CLI subcommands. **Only when the maintainer decides.**
- **Major** bumps (`1.x -> 2.0`) — breaking changes. **Only when the
  maintainer decides.**

### Contributing a change: add a changefile, don't hand-pick a version

**A version number is never hand-edited.** Instead, once per touched
plugin, run:

```bash
python tools/changefile.py add --plugin <name> --type patch --comment "<summary>"
# one PR touching two plugins with one shared reason:
python tools/changefile.py add \
  --plugin agent-worktrees --type patch \
  --plugin agent-bridge --type dev \
  --comment "Shared fix for Y"
python tools/changefile.py list   # see what's pending
```

Default to **`patch`** (or `dev` for an iterative fixup within an
already-in-flight patch). Do **not** request `minor`/`major` unless the
maintainer explicitly says so. Multiple changefiles may target the same
plugin (e.g. two different PRs merged close together); whichever carries
the biggest bump type wins when they're all consumed together
(`tools/accumulate_bumps.py`'s `highest_bump`) — no need to coordinate
with another PR author over the exact number.

This closes a changefile's "PR at PR-time" side of the story; consuming it
into a real version number is this pipeline's concern, not the
contributor's — see [Promotion: dev → main](#promotion-dev--main) for what
actually happens between a merge to `dev` and a real version landing on
`main`.

### Where the mechanically-applied bump lands (reference only)

Each plugin has its own version triplet. The promotion pipeline's
`tools/accumulate_bumps.py` is what actually writes these, consuming
whatever changefiles are pending; nothing here is hand-edited directly.

> **Mechanical shortcut — release/recovery tooling only, not for an ordinary
> contributor PR:** `python tools/accumulate_bumps.py --from-diff
> origin/dev --apply` bumps exactly what `check-version-bump.py` requires
> for a branch — every touched plugin (all three files plus literal
> `__version__` fallbacks), every plugin that vendors a changed lib, and
> the lib itself in all its copies — each only when it is not already
> ahead of `origin/dev`. Use `origin/dev` here, not `origin/main`: `main`
> is a disjoint, wholesale-regenerated promotion artifact (see
> `tools/promote_release.py`'s own docstring) with no real shared ancestry
> to an ordinary branch except the repo's original fork point — and a
> deliberate `main` history rewrite (this doc's own "If main's history is
> force-rewritten" section) severs even that, making `origin/main` a
> permanently unrelated base this tool now explicitly refuses rather than
> silently computing a wrong/misleading bump set. The one case that
> legitimately wants `origin/main` as the base is a true recovery check
> run directly against a specific already-promoted `main` commit (e.g.
> auditing exactly what a past promotion bumped) — pass that commit's SHA
> explicitly rather than the branch name `origin/main`, since the branch
> itself is just whatever the most recent promotion happens to be. This
> writes version manifests **directly**, the
> same way the promotion pipeline itself does, and does **not** consume or
> even look at pending changefiles — using it on an ordinary `dev` PR
> bypasses the changefile workflow above entirely. It exists for the
> promotion pipeline's own use and for manual release-recovery scenarios,
> not as a contributor-facing alternative to adding a changefile.
> `--dry-run` shows the plan.

> **General rule (applies to every plugin, present and future).** For a
> plugin `<p>`: bump `plugins/<p>/plugin.json` (`version`),
> `plugins/<p>/pyproject.toml` (`[project].version`, runtime plugins
> only), and `<p>`'s entry in `.github/plugin/marketplace.json` (found **by
> name**, not a hardcoded index). **agent-worktrees** additionally bumps
> `metadata.version`; adding a new plugin appends a `plugins[]` entry and
> bumps `metadata.version`.
>
> **Keep any in-package `__version__` in sync.** A runtime plugin that
> exposes a Python `__version__` must bump it to match the
> `pyproject.toml` version in the **same** commit — it is a *fourth* file
> for that plugin, easy to miss because the marketplace doesn't read it.
> Registered metadata-only standalone contracts are the explicit exception:
> `services/chat` resolves the existing distribution/project version instead
> of embedding a Python literal. Its version guard requires the declared
> resolver assignment, keeping admitted unit code unchanged during allocation.
>
> **Enforced by `tools/check-version-consistency.py`** (pre-push): fails
> if any plugin's `plugin.json` / `pyproject.toml` / `marketplace.json`
> versions disagree.
>
> **Enforced by `tools/check-changefile-presence.py`** (pre-push + CI,
> PR-diff scoped, against `dev`): fails the push/PR if a plugin's content
> changed **without** a pending changefile naming it. A change to **any
> file under `plugins/<p>/`** requires a changefile for `<p>`; a change to
> a shared, vendored `libs/<lib>/` requires one for **every** plugin that
> vendors it. Repo-root files not vendored into any plugin (`tools/`,
> `.github/`, repo-root `docs/`, `CONTRIBUTING.md`, `README.md`) need no
> changefile. **Also exempt, even under `plugins/<p>/`** (delegated to
> `tools/check-version-bump.py`'s own ignore list): build/venv/cache
> artifacts (`build/`, `dist/`, `.venv*/`, `.test-venvs/`, `__pycache__/`,
> `.pytest_cache/`, `.ruff_cache/`, `*.pyc`/`*.pyo`) and dev-hygiene files
> that never ship (`.gitignore`) — none of these change the runtime
> payload, so none force a bump or need a changefile.
>
> **`worktree-manager` follows the same rule, with a narrower footprint.**
> It is a top-level, out-of-plugin consumer tree with no `plugin.json` and
> no marketplace entry at all — its release version lives directly in its
> own `pyproject.toml` (`[project].version`), with its `src/*/__init__.py`
> `__version__` fallback as the "fourth file" equivalent. Any non-ignored
> change under `worktree-manager/` (or to a shared lib it consumes, as a
> real vendored copy or a `uv`-editable canonical-reference pointer)
> requires a changefile naming it the same way a plugin's own content
> change does (`python tools/changefile.py add --plugin worktree-manager
> --type patch --comment "..."` — the `--plugin` flag name is historical;
> it accepts any recognized consumer identifier).
> Registered standalone consumers such as **`fleet-manager`** follow this
> same release-metadata rule: changes to their shipped content require a
> pending changefile naming that consumer. Use its registered name with
> `--plugin`; absence from the marketplace plugin roster is not an exemption.
>
> **Shared-library release versions also use changefiles.** Address the
> canonical project explicitly, for example
> `python tools/changefile.py add --plugin libs/agent-procutil --type dev --comment "Release shared process helpers"`.
> Promotion seeds its version from the last shipped Main, applies the
> requested bump to canonical and physical copies, and then materializes
> editable pointers. This is separate from consumer changefiles: source
> changes still require the consumer entries described above. Do not
> hand-edit library manifests or weaken a consumer's minimum dependency.
>
> **Before editing a shared lib, find every consumer first — two commands,
> not one.** `python tools/check-vendored-libs-sync.py --list` enumerates
> every REAL, physical vendored copy (`plugins/*/libs/*` and a standalone
> consumer's own top-level `libs/*`), but a lib consumed *solely* through a
> `uv`-editable canonical-reference pointer (no local copy at all) is
> invisible to it. `python tools/check-version-bump.py --list` additionally
> names those pointer-only consumers — check both before assuming you've
> found every plugin a shared-lib change reaches.

**All version files for a plugin must be bumped together in the same
commit.** If any file is out of sync: a stale `plugin.json` makes
`copilot plugin update` report "already at latest" even when new code is
available; a stale `marketplace.json` shows the old version to machines
checking for updates; a stale `pyproject.toml` makes runtime `--version`
output wrong.

### When to add a changefile

After a set of changes is committed and ready to push — one changefile per
PR is fine; don't add one on every commit. Changefiles remove the old
hand-bump coordination problem entirely: each PR just declares its own
intent (`patch`/`minor`/`major`/`dev`), several changefiles for the same
plugin can coexist peacefully, and `tools/accumulate_bumps.py` merges them
(biggest bump wins) into one real version only when the promotion pipeline
actually consumes them.

## See also

- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — contributor process: opening a
  PR, waiting for a review verdict, documentation-impact requirements.
- [`docs/install-contract.md`](install-contract.md) — what the marketplace
  vendors and the plugin-update vs. runtime-install distinction.
- [`docs/patterns/mutable-dev-slot.md`](patterns/mutable-dev-slot.md) —
  immutable source-hashed development generations and legacy migration;
  direct merged Dev deployment remains in #7028/#7029.
