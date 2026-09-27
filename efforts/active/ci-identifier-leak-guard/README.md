# CI Identifier Leak Guard

- **Slug:** `ci-identifier-leak-guard`
- **Repo:** copilot-extensions
- **Branch(es):** isolated worktree branch for the reviewed plan PR; follow-on implementation slices via PRs
- **Created:** 2026-09-26
- **Status:** Active
- **Vision:** Below altitude relative to the standing publication-safety / public-artifact-hygiene intent; this effort closes a known enforcement gap in existing tooling. _(agent-recommended)_
- **Umbrella issue:** #3923

## Guiding Intent

Make CI the backstop that cannot be bypassed or forgotten, closing the gap the
local-only internal-identifier guard leaves for fork, external, and agent
contributors who do not have the private denylist configured on their machine.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| effort host | Owns the reviewed plan PR and follow-on implementation slices | isolated worktree |
| repository operator | Provisions the secret-backed denylist values without routing the raw values through an agent | GitHub repository settings / `gh secret set` |

## Coordination

- **Topology:** reviewed plan PR first, then follow-on implementation PR slices.
- **Host (owns PRs):** effort host.
- **Delegates:** none yet; follow-on slices may split by phase if the implementation grows.
- **Handoff:** each merged slice updates this effort before the next phase starts.
- **Public coordination token:** #3923.

## Context

The repository already has a local pre-push guard,
[`tools/check-no-internal-identifiers.py`](../../../tools/check-no-internal-identifiers.py),
that blocks known forbidden internal identifiers when the contributor has a
private denylist configured on their machine. That protects the operator's own
machines, but it does not protect fork contributors, external contributors, or
agents running on a machine without that private configuration.

That gap already leaked in practice: [#3883](https://github.com/ThomasMichon/copilot-extensions/issues/3883)
tracked a sweep over roughly 160 pre-existing leaked occurrences, and
[#3910](https://github.com/ThomasMichon/copilot-extensions/pull/3910) merged the
cleanup. This effort captures the already-decided follow-up: move the same
enforcement into CI so every contributor path hits the backstop.

## Request

Capture the operator's settled design as the implementation contract for the
follow-on phases:

1. **Storage:** the denylist(s) live as GitHub Actions repository secrets on
   `ThomasMichon/copilot-extensions` (for example `FORBIDDEN_IDS_FACILITY` for
   the facility set and `FORBIDDEN_IDS_WORK` or similar for the separate work
   context) — never committed to the repo and never exposed to contributor
   agents. The secret format is newline- or `;`-separated `token|reason`
   entries, where the reason explains why the token is forbidden and what kind
   of generic replacement to use.
2. **Masking/logging boundary:** the script must never print a matched token to
   stdout or any workflow log line. The untrusted job log reports only a count
   (for example `3 forbidden identifier(s) found — see PR review comments for details`)
   and fails the job. The actual matched value, reason, file, line, and column
   are delivered through a GitHub PR review comment posted directly via the
   GitHub API from a trusted workflow, because Actions log masking would make
   log-line findings unusable.
3. **Fork-safe two-workflow split:**  
   a. An **untrusted** `pull_request` workflow runs on the fork/head checkout
   with no secrets and minimal permissions. It extends
   `tools/check-no-internal-identifiers.py` rather than creating a separate
   scanner, uses the `origin/main` diff base to match this repo's push-time
   convention, writes a small structured findings artifact, and fails its own
   job when findings are non-empty so the PR shows a real red required check.  
   b. A **trusted** `workflow_run` workflow runs from the base branch
   definition, does not check out or execute fork code, downloads the artifact,
   and posts the review comment(s). The trusted job re-derives what matched and
   why from the secret-backed denylist it holds, rather than trusting arbitrary
   reason text from the untrusted artifact.
4. **Required status check:** the untrusted workflow job becomes the required
   branch-protection check on both `dev` and `main`. `main` already has ruleset
   `18553911`; the implementation phase must inspect its current required-check
   list and the corresponding `dev` protection/ruleset, then add the new
   untrusted scan job there. The trusted comment-posting workflow is **not** the
   required check.
5. **Existing local tool stays:** the local developer-experience path in
   `tools/check-no-internal-identifiers.py` remains in place. The CI backstop
   layers on top of the same scan logic, adding a structured-output mode (for
   example `--json-out <path>`) and a way to load `token|reason` pairs from the
   secret-backed format alongside the current local env/config input format.

_(agent-recommended)_ The findings artifact should carry only the minimum data
the trusted workflow needs to re-identify the match location and token without
trusting untrusted explanatory prose; keep the review UX aggregated when
multiple findings land on the same PR so repeated failures do not turn into
comment spam.

## Plan

### Phase 0 - Land the reviewed effort plan

- [ ] Update and cross-link the public umbrella issue after this plan PR
  merges. _(agent-recommended as an explicit planning/review-gate phase before
  implementation starts.)_
- [ ] Author this effort README and add it to the active-effort index.
- [ ] Submit the effort itself as a PR, give the advisory Copilot review a
  bounded window, address anything substantively useful, and self-merge.

### Phase 1 - Extend the local guard for CI artifact output

- [x] Refactor `tools/check-no-internal-identifiers.py` so the existing scan
  logic can power both the local guard path and the CI path without duplicating
  matching behavior.
- [x] Add structured findings output (for example `--json-out <path>`) carrying
  the location data the trusted workflow needs.
- [x] Add a `token|reason` loader for secret-backed CI inputs while preserving
  the existing local single-identifier convention and private config path.
- [x] Keep CI-mode stdout/log output count-only so matched values never appear
  in workflow logs.

### Phase 2 - Add the untrusted pull-request scan and required-check registration

- [ ] Author the untrusted `pull_request` workflow with no secrets and minimal
  permissions, running the scan against the `origin/main` diff base.
- [ ] Upload the structured findings artifact for the trusted follow-up
  workflow.
- [ ] Fail the untrusted job when findings are present so the PR itself shows a
  red required check.
- [ ] Inspect the current branch-protection/ruleset configuration for `main`
  and `dev`, then register the untrusted job as the required check in both
  places.

### Phase 3 - Add the trusted comment-posting workflow

- [ ] Author the `workflow_run` follow-up that runs from the base-branch
  workflow definition and never executes fork code.
- [ ] Treat the untrusted artifact as a hint, not authority: independently
  validate each reported location against the PR content/diff from the trusted
  side before posting any review comment. _(agent-recommended)_
- [ ] Download the structured findings artifact and re-derive the matched token
  and reason from the secret-backed denylist held by the trusted workflow.
- [ ] Post review comment feedback that names the actual matched value, explains
  why it is forbidden, and instructs the contributor to replace it with a
  generic equivalent.
- [ ] Validate the triggering run/PR metadata and effective workflow definition
  source against GitHub's actual `workflow_run` semantics before relying on the
  trust boundary. _(agent-recommended)_
- [ ] Keep trusted-workflow logging free of raw secret/denylist dumps even though
  the API-posted review text intentionally names the matched value on the PR.

### Phase 4 - Provision the secret-backed denylists

- [ ] Operator only: add `FORBIDDEN_IDS_FACILITY` and the separate work-context
  secret (for example `FORBIDDEN_IDS_WORK`) through the GitHub UI or
  `gh secret set`; the raw values must not be originated, transited, or stored
  by an agent.
- [ ] Document the expected secret format and repository-administration step
  near the workflow/tooling docs touched by the implementation.

## Validation Plan

- [ ] Open a test PR that deliberately reintroduces a known-safe test token and
  confirm the untrusted scan job fails, the required check blocks merge, and
  the trusted follow-up posts review feedback naming the matched value and its
  reason.
- [ ] Confirm the same behavior on an owner-authored non-fork PR path.
- [ ] Confirm a clean PR passes with zero review comments from the trusted
  workflow.
- [ ] Spot-check the raw untrusted workflow logs to confirm matched values and
  raw denylist contents never appear there.
- [ ] Confirm the trusted workflow never accepts untrusted artifact prose as
  authoritative for the reason text it posts back to the PR. _(agent-recommended)_

## Proposal

_Pending._

## Journal

### 2026-09-26 - Kickoff
- Effort created to capture the settled fork-safe CI backstop design before any
  workflow or scanner implementation begins.

### 2026-09-26 - Phase 1 shipped
- PR TBD extends `tools/check-no-internal-identifiers.py` with reusable scan
  helpers, `--json-out`, and `--ci`, plus secret-backed
  `COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI` parsing for `token|reason` entries
  without emitting raw tokens or reasons in CI-mode stdout/JSON artifacts.
- Test coverage now exercises legacy default output behavior, merged identifier
  loading, JSON artifact redaction, CI-mode count-only output, and first-match
  column tracking.
