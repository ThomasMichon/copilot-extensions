# CI/CD Pipelines

This is the mechanical reference for how a change actually moves through
this repo: the automated gates a pull request passes before it merges to
`dev`, how `dev` promotes to `main`, how to preview a pending release
without waiting for a real promotion, and the complete list of scheduled
and reactive automation this repo runs. `CONTRIBUTING.md` owns contributor
*process* (how to open a PR, the review-verdict waiting loop, documentation
requirements); this doc owns the *infrastructure* those steps run on top
of — link here rather than restating any of it.

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
no-dependency install accepts `ARTIFACT_VALIDATION_INDEX_URL` as an explicit
feed override, defaulting to the public tooling index on public runners.

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
payload hash from that candidate data. Candidate-supplied hashes and
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
`promote_release.py --python-artifacts-dir <dir>`
recomputes composition and refuses missing/corrupt/mis-targeted artifacts
before pushing a candidate, including repeating the archive-content checks.
A no-content-change or paused run builds and
publishes nothing.

Only after the exact candidate PR really merges does publication recheck its
actual parent/content tree and project set and revalidate archive contents.
Assets live under the deterministic
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
same version. An unshipped or different-payload tag is rejected.
Its release PAT and verification token are scoped to the final
publisher step, not exposed to checkout, Python setup or artifact-download
actions. Publication is its own job so a partial upload can be retried with
**Re-run failed jobs**, reusing the successful signer and exact merge
outputs rather than repeating promotion. It also enters the failure reporter.
Transfer artifacts expire after two days; durable release-asset
retention remains a separately tracked policy.

These first-party sets are **not yet an installer fast path**. Third-party
closure/admission, verified shared-installer consumption and retention remain
open in `efforts/active/governed-python-artifact-promotion/README.md`. No
published bundle contains third-party runtime dependency bytes or certifies a
consumer's governed feed.

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

The automatic fallback gives native/manual review **five minutes from the
triggering PR event's `updated_at` timestamp**, without API polling. Queue time
counts toward that grace: a run queued for five minutes does not occupy a runner
for another five. Invalid/future event timestamps fail before requesting review.
After any remaining grace, it checks the live PR once. It skips closed/draft/retargeted PRs,
pending Copilot reviews, completed reviews of the current diff, and requests
still inside their five-minute grace period. Completed reviews must be newer
than the latest base-retarget/reopen event. Identity checks accept the observed
`Copilot` and `copilot-pull-request-reviewer[bot]` aliases only for API users
typed `Bot`; the review write retains the canonical app login. A run superseded by a newer push
leaves that push's grace period to its own queued run. Per-PR serialization keeps
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

### Everyone else

Anyone who hasn't been invited as a collaborator at all **gets no
automatic CI, no automatic Copilot review, and no agentic-workflow
support**, only the strictest built-in fork-PR-approval gate (Settings →
Actions → General → "Fork pull request workflows" → **"Require approval
for all outside collaborators"**), which already blocks every Actions run
(including CI) from starting until a Maintainer manually approves it, plus
the trusted review job's own collaborator check (above) for review. A
Maintainer can still manually approve a run or request a review for an
outside PR at their discretion — this only removes the automatic path for
someone the repo owner never invited.

**This also means an unapproved fork PR against `main` gets zero automated
feedback**, including from `main-gate` (below) — GitHub holds the *entire*
`ci.yml` run, every job included, in `action_required` until a maintainer
approves it. `.github/workflows/base-branch-reminder.yml` exists
specifically to close that gap: see
[If you opened a PR against `main` by mistake](#if-you-opened-a-pr-against-main-by-mistake).

## Automated workflows reference

Every workflow under `.github/workflows/`, what triggers it, and what it
does. Grouped by role; see each file's own header comment for full
rationale and any known limitations.

**PR-related gating** (not all run on every PR — `ci.yml` runs on every
PR; the review and lockdown workflows target only `dev`; the reminder
targets only `main`; `trusted-ci.yml` is path-scoped and opt-in):

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | `pull_request`, `push` (`main`/`dev`), `workflow_dispatch` | The fast smoke-CI suite (guards, lint, per-plugin tests) that gates every PR. Contains `main-gate` (rejects a non-promotion PR against `main`) and the `PR gate` fixed-name required-check aggregate, including native Windows archive-source/chronicler smoke when `agent-logger` is selected. |
| `workflow-lockdown-guard.yml` | `pull_request_target` → `dev` | One trusted run hosts the unchanged required `workflow-lockdown-guard` check and the Maintainer-only review fallback, with independent jobs and job-scoped review permissions/concurrency. Workflow and check names stay unchanged; no PR code executes. |
| `base-branch-reminder.yml` | `pull_request_target` → `main` | Posts a one-time comment asking a non-owner author to retarget a PR against `main` to `dev` (see [If you opened a PR against `main` by mistake](#if-you-opened-a-pr-against-main-by-mistake)). Never checks out or executes PR code. |
| `trusted-ci.yml` | `pull_request_target`, paths-scoped to `.github/workflows/trusted-ci.yml`/`libs/**`/`agent-bridge`/`tools/**` | Opt-in (`vars.TRUSTED_SELF_HOSTED_CI`), collaborator-gated self-hosted CI lane for paths too sensitive/expensive for the standard runner pool; includes its own file so a change to the gate itself also re-runs it. |

**Reactive, post-CI (triggered by `ci.yml` completing):**

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `promote-trigger.yml` | `workflow_run: ["CI"]` | One trusted invocation hosts independent dev-ancestry dispatch, fork-safe identifier scanning and committed-dev module baseline maintenance. Baseline PRs target dev only; pinned trusted tooling reads the completed dev input as data, with isolated Python, credential-free checkouts and baseline-only publication. Success/failure completions can widen existing ceilings without admitting new offenders; stale inputs skip. Automation never lowers or removes ceilings; tightening remains deliberate manual work. A stale higher ceiling is tolerated under ordinary review rather than exact-baseline equality or global strict-base CI. The release PAT is confined to PR publication. No extra verification dispatch is needed because that PAT triggers ordinary PR CI/review. |

**The promotion pipeline (dev → main):**

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `validate-and-promote.yml` | `repository_dispatch: [dev-advanced]`, `workflow_dispatch` | The pipeline itself: `gate` (confirm the dispatch really reflects `dev`'s current tip) → full validation suite → `promote` (open a generated `release/promote-<run id>` PR against `main`, auto-merge via `APERTURE_RELEASE_TOKEN`). See [Promotion: dev → main](#promotion-dev--main). |
| `purge-consumed-changefiles.yml` | `schedule` (daily), `repository_dispatch: [purge-changefiles-requested]` | Standalone daily (or on-demand) companion to the promotion pipeline: opens the "clear consumed changefiles on dev" housekeeping PR for whatever changefiles a promotion has already consumed by then -- pure housekeeping, not per-promotion and never load-bearing for promotion correctness. |

**Scheduled sweeps and watchdogs** (`stale-branch-sweep.yml` acts on
already-merged PRs' branches):

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `module-health-watchdog.yml` | `schedule` (daily), `workflow_dispatch` | Proactively finds the single worst module-size-ceiling offender (organic drift no PR-time gate catches) and files/updates one tracking issue for it. |
| `stale-branch-sweep.yml` | `schedule` (weekly), `workflow_dispatch` | Deletes merged-PR branches still lingering on `origin` — the automated form of the one-time 3000+-branch cleanup. |
| `installation-context-full.yml` | `schedule` (daily), `workflow_dispatch` | Full-sweep variant of the installation-context contract smoke check `ci.yml` runs per-PR. |
| `context-handoff-exhaustive.yml` | `schedule` (weekly), `workflow_dispatch` | The real-git/child-process/lock-race exhaustive suite for `context-handoff`'s exhaustive test tree — deliberately its own workflow so its `schedule` trigger doesn't apply to the rest of `ci.yml`'s matrix. |
| `crash-diagnostics-stress.yml` | `schedule` (daily), `workflow_dispatch` | Stress-tests `context-handoff` crash-diagnostics paths. |
| `agent-dispatch-pwsh-race-exhaustive.yml` | `schedule` (weekly), `workflow_dispatch` | Runs `agent-dispatch`'s multi-process `pwsh` mutex-race tests (self-skipped by default in `ci.yml`'s smoke lane) on both `ubuntu-latest` and `windows-latest` — deliberately its own workflow for the same reason as `context-handoff-exhaustive.yml`. |
| `payload-cwd-contract.yml` | Path-filtered non-dev `pull_request`, `schedule` (weekly), `workflow_dispatch` | Proves installer/watchdog payload replaceability and HOME CWD on Linux and Windows. Relevant dev PRs run the identical platform smoke in CI's existing native runner jobs and required aggregate, avoiding one workflow and two check records; non-dev PRs retain this standalone smoke, and scheduled/manual runs retain the full inherited-staging-flag matrix. |
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

### Expect roughly 10-20 minutes, and know what to watch

Budget on the order of **10-20 minutes** from a green `dev` merge to a real
`main` release: `CI` on `dev` (a few minutes) → `Validation Gate` →
`Promote` opens a generated `release/promote-<run id>` candidate PR
against `main` → that candidate PR's own fast `main source gate` check →
auto-merge (via the pipeline's own `APERTURE_RELEASE_TOKEN` — a personal
fine-grained PAT minted under the maintainer's own account, not the
default `GITHUB_TOKEN`/Actions-bot identity). To confirm a change actually shipped rather than
assuming the `dev` merge itself was the release:

The promotion job retains the workflow's exclusive concurrency slot while
waiting for its exact generated PR to merge. Enabling auto-merge alone is not
delivery: the waiter verifies the PR identity and reports a bounded timeout,
closed-unmerged state or provider error as a failure. The release tag uses that
PR's actual merge commit, even if `main` advances again before tagging.

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
it can take up to a day to appear.

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
(see the `main-gate` job in `ci.yml`), and `main-gate`
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

### If you opened a PR against `main` by mistake

`main-gate` (above) only ever runs as part of `ci.yml`, a plain
`pull_request`-triggered workflow — for a first-time or otherwise
unapproved external contributor, GitHub holds that *entire* workflow run
in `action_required` until a maintainer manually approves it, so such a
contributor can get zero automated feedback at all.
`.github/workflows/base-branch-reminder.yml` closes that specific gap: a
narrow, `pull_request_target`-triggered job (not subject to the
fork-approval gate) posts a one-time comment asking the author to retarget
to `dev`, for any PR against `main` not authored by the repo owner. It
never checks out or executes the PR's own code and never runs tests — its
only effect is that one comment, so it adds no capability a
non-collaborator didn't already have.

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
  running unmerged code against the real deployed CLI.
