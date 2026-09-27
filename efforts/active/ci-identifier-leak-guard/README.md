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
   stdout or any workflow log line. The trusted scan step's log reports only a count
   (for example `3 forbidden identifier(s) found — see PR review comments for details`)
   and fails the job. The actual matched value, reason, file, line, and column
   are delivered through GitHub API-posted PR feedback (review comment or issue
   comment) from the trusted workflow, because Actions log masking would make
   log-line findings unusable.
3. **Fork-safe trusted follow-up:** keep the existing lightweight
   `pull_request` CI workflow for fast no-secrets validation only; do **not**
   attempt identifier scanning there, because fork PRs receive no repository
   secrets and repo `vars.*` would be public text, not a safe denylist channel.
   A separate trusted `workflow_run` workflow reacts to that CI completion,
   loads its own YAML from the default branch, resolves the triggering PR's
   real head SHA/PR metadata, and then reads the PR head's changed file
   contents strictly as inert git/API data -- never by checking out or
   executing the fork head.
4. **Required status check:** the trusted `workflow_run` path creates an
   explicit Check Run on the PR head SHA with a stable name (for example
   `identifier leak guard`) and `success`/`failure` conclusion. *That* custom
   check -- not the untrusted CI job's own status and not the trusted
   workflow's native run status -- becomes the required branch-protection
   signal on both `dev` and `main`. `main` already has ruleset `18553911`; the
   implementation phase must inspect its current required-check list and the
   corresponding `dev` protection/ruleset, then prepare the exact mutation
   needed to add the new custom check there without applying it until the
   operator explicitly approves the admin change.
5. **Existing local tool stays:** the local developer-experience path in
   `tools/check-no-internal-identifiers.py` remains in place. The CI backstop
   layers on top of the same scan logic, adding a structured-output mode (for
   example `--json-out <path>`) and a way to load `token|reason` pairs from the
   secret-backed format alongside the current local env/config input format.

_(superseded by the 2026-09-26 design correction)_ The earlier artifact-handoff
idea is kept here for history only; the corrected design no longer depends on
an untrusted findings artifact at all.

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

### Phase 2 - Add the trusted workflow-run scan/report path

- [x] Remove the structurally-broken secret-backed identifier scan attempt from
  the untrusted `pull_request` CI lane; keep that workflow only for fast
  no-secrets validation.
- [x] Extend `tools/check-no-internal-identifiers.py` so the trusted follow-up
  can scan a PR head's changed file contents as passive git data while still
  emitting the Phase 1 redacted JSON artifact shape.
- [x] Author a `workflow_run` follow-up that runs from the default branch's own
  workflow definition, never checks out or executes the PR head, resolves the
  triggering PR/head metadata, reads the changed file contents as inert data
  only, and runs the trusted scanner with the merged
  `FORBIDDEN_IDS_FACILITY`/`FORBIDDEN_IDS_WORK` secret-backed denylist.
- [x] Create a custom Check Run on the PR head SHA with a stable required-check
  name and a success/failure conclusion derived from the trusted scan.
- [x] Post failure feedback back to the PR via API-delivered text that may name
  the matched placeholder/identifier and reason, while keeping the workflow log
  itself free of raw matched values or denylist dumps.

### Phase 3 - Register the new required check and close the loop

- [x] Validate the merged trigger chain end-to-end on a scratch PR: CI runs
  first, then the trusted `workflow_run` workflow, then the custom Check Run
  appears on the PR head SHA.
- [x] If the repository secrets are present, validate the failure path with a
  fabricated placeholder test token; otherwise validate the success/plumbing
  path only and record that full failure-path validation remains blocked on
  Phase 4 secret provisioning.
- [x] Inspect the current branch-protection/ruleset configuration for `main`
  and `dev`, prepare the exact before/after required-check diff for the new
  custom Check Run name, and stop for explicit operator confirmation before any
  mutating admin call.
- [ ] After operator confirmation, add `identifier leak guard` to the required
  status checks on `main` ruleset `18553911` and `dev` ruleset `23904550`.

### Phase 4 - Provision the secret-backed denylists

- [ ] Operator only: add `FORBIDDEN_IDS_FACILITY` and the separate work-context
  secret (for example `FORBIDDEN_IDS_WORK`) through the GitHub UI or
  `gh secret set`; the raw values must not be originated, transited, or stored
  by an agent.
- [ ] Document the expected secret format and repository-administration step
  near the workflow/tooling docs touched by the implementation.

## Validation Plan

- [x] Open a clean scratch PR and confirm the ordinary `CI` workflow runs
  first, then the trusted `workflow_run` follow-up runs, and then a custom
  Check Run named `identifier leak guard` appears on the PR head SHA.
- [x] If `FORBIDDEN_IDS_FACILITY` / `FORBIDDEN_IDS_WORK` exist, open a scratch
  PR that deliberately reintroduces a known-safe fabricated placeholder test
  token and confirm the trusted Check Run reports `failure` plus API-posted PR
  feedback naming the matched value and reason. If the secrets are absent,
  record that this failure-path validation remains blocked on Phase 4 secret
  provisioning.
- [x] Confirm a clean PR produces no raw matched values in the workflow log and
  no failure feedback comment.
- [x] Inspect the branch-protection/ruleset configuration for `main` and `dev`
  and prepare the exact before/after diff to require the custom Check Run name,
  without applying it yet.

## Proposal

_Pending._

## Journal

### 2026-09-26 - Kickoff
- Effort created to capture the settled fork-safe CI backstop design before any
  workflow or scanner implementation begins.

### 2026-09-26 - Phase 1 shipped
- PR #3934 extends `tools/check-no-internal-identifiers.py` with reusable scan
  helpers, `--json-out`, and `--ci`, plus secret-backed
  `COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI` parsing for `token|reason` entries
  without emitting raw tokens or reasons in CI-mode stdout/JSON artifacts.
- Test coverage now exercises legacy default output behavior, merged identifier
  loading, JSON artifact redaction, CI-mode count-only output, and first-match
  column tracking.

### 2026-09-26 - Design correction: Phase 2/3 merged into one trusted check-run path
- The original split was wrong: an untrusted `pull_request` workflow on a fork
  can never hold the real denylist because repository secrets are withheld
  there and repository variables would be public plain text, so there was no
  structurally-sound way for that lane to emit a meaningful required status.
- The corrected design keeps `CI` as an untrusted, no-secrets fast lane only
  and moves all identifier-leak enforcement into one trusted `workflow_run`
  follow-up that loads its YAML from the default branch, reads the PR head only
  as inert data, runs the trusted scanner with the real denylist, and creates a
  custom Check Run on the PR head SHA for branch protection to require.

### 2026-09-26 - Phase 2 shipped
- PR #4002 landed the trusted `workflow_run` implementation:
  `.github/workflows/identifier-leak-guard.yml` now reacts to `CI`
  completions, reads the PR head only as passive data, runs the trusted
  scanner, creates the custom `identifier leak guard` Check Run on the PR head
  SHA, and posts PR feedback through the GitHub API when real findings exist.
- The old secret-backed step was removed from `.github/workflows/ci.yml`, and
  `tools/check-no-internal-identifiers.py` gained the passive-data scan path
  (`--paths-file`, `--git-ref`, trusted-only details output) plus regression
  coverage for the new modes and filename-whitespace preservation.

### 2026-09-26 - Phase 3 validation
- Scratch PR #4062 confirmed the trigger chain on an owner-authored PR path:
  `CI` ran first, then the trusted `Identifier leak guard` workflow fired via
  `workflow_run`, and a custom Check Run named `identifier leak guard` appeared
  on the scratch PR head SHA.
- `gh secret list --repo ThomasMichon/copilot-extensions` returned no
  repository secrets, so the trusted workflow correctly failed closed with the
  Check Run title `Identifier leak guard misconfigured` instead of silently
  passing an unenforced scan. That proves the trigger/report plumbing but means
  full failure-path validation with a fabricated placeholder token remains
  blocked on Phase 4 secret provisioning.
- No identifier-guard PR feedback comment was posted on the clean scratch PR,
  and the check-run output named only the configuration gap -- no raw matched
  values appeared because no denylist-backed scan actually ran.
