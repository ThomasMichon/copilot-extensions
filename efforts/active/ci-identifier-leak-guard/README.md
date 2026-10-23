# CI Identifier Leak Guard

- **Slug:** `ci-identifier-leak-guard`
- **Repo:** copilot-extensions
- **Branch(es):** isolated worktree branch for the reviewed plan PR; follow-on implementation slices via PRs
- **Created:** 2026-09-26
- **Status:** Active
- **Vision:** Below altitude relative to the standing publication-safety / public-artifact-hygiene intent; this effort closes a known enforcement gap in existing tooling. _(agent-recommended)_
- **Source umbrella:** #3923; continuing coordination is this Active ROOT's
  reviewed Plan/Validation/Journal and eligible OPEN bounded-source issues

## Guiding Intent

Make CI the backstop that cannot be bypassed or forgotten, closing the gap the
local-only internal-identifier guard leaves for fork, external, and agent
contributors who do not have the private denylist configured on their machine.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| effort host | Owns the reviewed plan PR and follow-on implementation slices | isolated worktree |
| private-downstream-repo agent | Assembles the private-adopter-context denylist (`<private-input-family-a>`) -- not secret from within private-downstream-repo, only from the public repo -- and pushes it to the repository secret via `gh secret set` | private-downstream-repo session |
| operator's work-context harness agent | Assembles the separate work-context denylist (`<private-input-family-b>`) from a list the operator keeps in their private work-context source location, and pushes it to the repository secret via `gh secret set` | cross-repo/cross-harness collaboration (private-context harness; not named here) |
| repository operator | Confirmed (2026-09-27) the denylists are not secret *from* either agent -- only from the public repo -- so both are agent-assembled/pushed rather than operator-typed; keeps the source lists in private personal source locations (first private source list does not need Vault, no credential material) | — |

## Coordination

The complete #3923 retained finite family continues through this existing Active ROOT's
reviewed Plan, actual global Validation and Journal/open bounded-source process under
the SAME declared and source-specific custodians. Preserve every incumbent alternative,
native/source/resource reservation and independent acceptance gate. Closed source
trackers are historical provenance, never required live claim tokens; no assignment,
claim expiry or new blanket ACK is created.

- **Topology:** reviewed plan PR first, then follow-on implementation PR slices.
- **Host (owns PRs):** effort host.
- **Delegates:** none yet; follow-on slices may split by phase if the implementation grows.
- **Handoff:** each merged slice updates this effort before the next phase starts.
- **Continuing coordination:** this Active ROOT's reviewed Plan/Validation/Journal
  and eligible OPEN bounded-source claims, under the same private-source,
  trusted-CI and branch-policy custodians. After reviewed supersession,
  #3923 and #4339 are historical provenance, not live claim tokens.

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

Private input labels, control-record identifiers and adopter-specific storage
references below are neutral abstractions; their distinct roles, ownership,
conditions and recorded outcomes are unchanged.

Capture the operator's settled design as the implementation contract for the
follow-on phases:

1. **Storage:** the denylist(s) live as GitHub Actions repository secrets on
   `ThomasMichon/copilot-extensions` (for example `<private-input-family-a>` for
   the first private source set and `<private-input-family-b>` or similar for the separate work
   context) — never committed to the repo and never exposed to contributor
   agents. The secret format is newline- or `;`-separated `token|reason`
   entries, where the reason explains why the token is forbidden and what kind
   of generic replacement to use.
2. **Masking/logging boundary:** the script must never print a matched token to
   stdout or any workflow log line. The trusted scan step's log reports only a count
   (for example `3 forbidden identifier(s) found — see the 'identifier leak
   guard' Check Run output for details`) and fails the job. The actual matched
   value, reason, file, line, and column are delivered through the custom
   Check Run's own `output.text` (API-delivered, not a masked log line) --
   *(revised 2026-09-30, see Journal)*: a duplicate Issues-API PR comment was
   originally planned as well, but a `workflow_run`-triggered `GITHUB_TOKEN`
   cannot reliably post one regardless of declared permissions, so that path
   is now best-effort only and the Check Run output is the sole relied-upon
   delivery channel.
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
   signal on both `dev` and `main`. `main` already has ruleset `<main-ruleset-id>`; the
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

- [x] Update and cross-link the public umbrella issue after this plan PR
  merges. _(agent-recommended as an explicit planning/review-gate phase before
  implementation starts.)_
- [x] Author this effort README and add it to the active-effort index.
- [x] Submit the effort itself as a PR, give the advisory Copilot review a
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
  `<private-input-family-a>`/`<private-input-family-b>` secret-backed denylist.
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
- [x] After operator confirmation, add `identifier leak guard` to the required
  status checks on `main` ruleset `<main-ruleset-id>` and `dev` ruleset `<dev-ruleset-id>`.

### Phase 4 - Provision the secret-backed denylists

_(Revised 2026-09-27: the operator will not personally type these secrets --
see the Journal entry below. Both are agent-assembled/pushed instead of
"operator only.")_

- [x] A **private-downstream-repo agent** assembles the `<private-input-family-a>` list
  (private-adopter-context identifiers -- machine names, internal hosts, personal
  names, etc.; not secret from within private-downstream-repo, only from the public
  repo) and pushes it to the `ThomasMichon/copilot-extensions` repository
  secret via `gh secret set`.
- [x] Collaborate with the operator's **private-context work harness agent** to
  assemble the separate `<private-input-family-b>` list from a source list the
  operator keeps in their private work-context source location, and push it to the repository secret
  via `gh secret set`. **Done 2026-10-01** -- landed and rotated across the
  `identifier-leak-guard-scrub` effort/PR #4858 (see that effort's own
  Journal); confirmed present via `gh secret list`.
- [x] Document the expected secret format and repository-administration step
  near the workflow/tooling docs touched by the implementation.

### Phase 5 - Centralized, pluggable cross-repo sweep (generalizes Phase 4's manual provisioning)

Phase 4 proved the concept but left `<private-input-family-b>` provisioning as a
fully manual, hand-copied step from a single private source file. This phase
replaces that with a discoverable, pluggable convention any locally
registered repo can participate in -- both as a *source* of blocklist terms
and as a *target* repo enforcing them, scoped by its own declared
audience-exposure tier.

- [x] Add `RepoEntry.visibility` (`private`/`internal`/`public`) to the
  `agent-worktrees` repos registry, plus `repos set-visibility`/
  `repos add --visibility`. Unset/unknown resolves to `public` (maximal
  enforcement) rather than silently under-enforcing an unclassified repo.
- [x] Add `identifier_blocklist.py`: discovers every locally registered
  repo's `.identifier-blocklist/block-for-<tier>.yaml`, scoped to a target
  repo's resolved visibility (`internal` applies to internal+public targets;
  `public` applies to public targets only; no `block-for-private` tier since
  nothing is more exposed than private).
- [x] Add the `identifiers sweep [--repo NAME] [--json]` CLI surface
  (`identifier_blocklist_cli.py`), wired into `__main__`'s dispatch table.
- [x] Wire `tools/check-no-internal-identifiers.py` to consume the live
  sweep as a fourth, best-effort identifier source (`agent-worktrees
  identifiers sweep --format json`, invoked with this repo as cwd so it
  auto-resolves as the sweep target) -- silently absent anywhere
  `agent-worktrees` isn't installed/registered, opt-out via
  `COPILOT_EXTENSIONS_DISABLE_LIVE_SWEEP=1`. The JSON format (rather than
  the CLI's own default `ci` text format) is used so a parse failure in one
  peer repo's blocklist still surfaces whatever entries DID parse
  successfully. This automatically covers the
  pre-push git hook AND `create-pr`/`push-changes` (both trigger the same
  `core.hooksPath` hook via their underlying `git push`) with no separate
  wiring.
- [x] Document the convention end-to-end in
  [`docs/identifier-blocklist.md`](../../../docs/identifier-blocklist.md),
  including the YAML entry schema (`token`/`kind`/`whole_word`/
  `case_sensitive`/`reason`) and the regenerated `<private-input-family-b>`
  provisioning recipe (now sourced from a live sweep instead of a hand-copied
  file).
- [ ] Migrate the harness repo carrying the current `<private-input-family-b>`
  source list (private, not named here) to a `.identifier-blocklist/
  block-for-public.yaml` at its own anchor root, in the new YAML schema, and
  set its registered `visibility: public`. Retires the old pipe-delimited
  `.txt` convention that effort's own history section already documents as
  superseded.
- [ ] Re-provision `<private-input-family-b>` from the live sweep's output (rather
  than the old hand-copied file) once the migration above lands, confirming
  parity with the current secret content.

<!-- backlog-reconciliation:7556:transfer:3923:to:efforts/active/ci-identifier-leak-guard/README.md -->

### Direct complete CI-backstop and inherited finite provisioning acceptance from #3923

#### Continuing Coordination

Retain the SAME existing Active ROOT, every original Plan/Validation/Journal row and
all4339 inheritance. The current effort host, two existing private-input/source
custodians, trusted scanner/reporting, fork/contributor approval, CI-control and
branch-policy owners keep their scope and nonexpiring claims. No credential/list/source
inventory is moved, read, re-provisioned or reassigned here. Before administrative3923
closure, reconcile the ROOT's operative public-coordination pointer:3923 and4339 become
historical provenance, NOT closed live claim tokens. Continue this ROOT
Plan/Validation/Journal and actual eligible OPEN bounded-source/CI coordination under
those same owners; do not manufacture a new writer or universal ACK ceremony.

#### Unchecked Plan

- [ ] Preserve the original CI backstop for fork/external/contributor paths without
  privately configured local guards. Explicitly map original required coverage to
  current accepted admission/approval constraints and owner-scoped Main versus retained
  CI-completion producers; do not infer all paths from one safe caller or silently
  weaken the original outcome.
- [ ] Reuse the existing scanner and BOTH existing private input families,
  private/uncommitted and assembled/provisioned only through their established owners.
  Credit the actual public provisioning/rotation and trusted-load/scan/report receipts;
  no repetition merely to tick a new tracker.
- [ ] Preserve trusted default-branch/pinned tooling and exact PR/head resolution.
  Candidate changed paths/content remain inert Git/API data, never executed with private
  inputs. Retain lossless filename transport, stale-head fences and independently
  trusted reporting; no untrusted findings artifact grants authority.
- [ ] Preserve count-only workflow logs and redacted ordinary artifacts. Useful complete
  findings follow the EXISTING approved trusted custom-result/API reporting contract,
  with best-effort comments separate from the authoritative result. No raw values,
  private source/secret names or denylist copies belong in this intake or its examples.
- [ ] Preserve actual head-bound custom-result success/failure/required-check semantics
  and all setup/load/fetch/scan/report failures. A genuinely operational unconfigured
  neutral/no-op outcome is NOT a configured enforced scan or permission to make a failed
  result green. Required-policy changes remain separately authorized owner actions.
- [ ] Carry ALL four4339 rows: finite two-family provisioning; meaningful safe-canary
  negative control; accepted fork/contributor/required-result coverage and migration;
  changed-path versus separate historical cleanup/custody. Preserve same
  source/candidate/trusted-reporter identity and no later failure-to-success conversion.
  Use suitable existing safe negative evidence where sufficient, including the recorded
  actual failure witness; no new live seeded test is authorized or universally imposed.
- [ ] Resolve the existing Phase5 private-source format migration under its same source
  custodian, retaining registered-repository visibility/tier/precedence semantics and
  current local behavior. Do not inspect a private source to claim its status from this
  public research.
- [ ] Resolve existing sweep-derived re-provision/parity follow-through through its
  established owner OR transfer every residual explicitly to a named accepted tracked
  objective. Earlier provisioning is complete at its evidenced scope; unchecked later
  migration does not retroactively negate it or prove current absence.
- [ ] Keep the local developer path, changed-path scan and separately tracked historical
  full-tree cleanup. No global privacy/history-erasure attestation, new
  scanner/registry, source/list publication, branch mutation, workflow activation or
  claim expiry is introduced.

#### Unchecked Validation Plan

- [ ] Record the exact accepted contributor/fork/owner/base/approval and
  required-custom-result matrix through the existing trusted producers; distinguish
  declared, configured, actually scanned/reported and required/enforced evidence.
  Unknown current policy remains unknown, not a silent waiver.
- [ ] Preserve causal positive and negative controls for inert-head safety, filenames,
  stale events, malformed/missing reports, infrastructure failure, configured clean scan
  and genuinely unconfigured neutrality under current bounds. Research executes none of
  these controls.
- [ ] Credit and bind suitable existing provisioning and safe-canary failure evidence to
  its actual source/head/trusted report. If a finite qualification remains unproved,
  leave it explicitly owned rather than run a secret/credential/security probe, repeat
  completed provisioning or declare ordinary clean PASS a rejection proof.
- [ ] Retain both private-source migration and re-provision/parity outcomes or explicit
  accepted tracked dispositions, same custodians and whole original ROOT/4339 scope.
  Keep source, hosted/control, required-policy and provisioning facts distinct.

Receiving gate: a NEW ordinary reviewed ACTUAL MERGE must deliver this ENTIRE direct
unchecked Plan AND Validation/continuing Coordination, including the real global
Validation entry and operative-pointer reconciliation. FULL CURRENT Active ROOT
Plan/Validation/Coordination/Journal and actual live claims must be read back BEFORE3923
supersession. Old creation/review, a title/link/child-only note, an unmerged amendment
or this proposal is not delivery. If any original/4339 clause, custodian or coordination
condition cannot be preserved, RETAIN #3923. No scanner, canary, credential/list/secret
inventory, branch policy, workflow, source or runtime effect occurs here.

## Validation Plan

- [ ] Resolve EVERY direct3923 and inherited4339 Plan/Validation clause above at its
  original finite scope, preserving whole incumbent ROOT, same private-source/CI/control
  owners, existing alternatives and actual continuing coordination. Administrative
  consolidation is not implemented coverage, privacy attestation or permission to access
  private inputs.

- [x] Open a clean scratch PR and confirm the ordinary `CI` workflow runs
  first, then the trusted `workflow_run` follow-up runs, and then a custom
  Check Run named `identifier leak guard` appears on the PR head SHA.
- [x] If `<private-input-family-a>` / `<private-input-family-b>` exist, open a scratch
  PR that deliberately reintroduces a known-safe fabricated placeholder test
  token and confirm the trusted Check Run reports `failure` naming the
  matched value and reason in its own output. If the secrets are absent,
  record that this failure-path validation remains blocked on Phase 4 secret
  provisioning.
- [x] Confirm the clean scratch PR's **misconfiguration** path produces no
  identifier-feedback comment and logs only the configuration gap (not any raw
  matched value).
- [x] Once the denylist secrets exist, confirm a genuinely clean denylist-backed
  scan produces no raw matched values in the workflow log and no failure
  feedback comment.
- [x] Inspect the branch-protection/ruleset configuration for `main` and `dev`
  and prepare the exact before/after diff to require the custom Check Run name,
  without applying it yet.

## Proposal

_Pending._

## Journal

### 2026-10-23 - Direct retained acceptance from #3923

- Retained the complete #3923 acceptance directly in this ROOT Plan, its actual global
  Validation and continuing Coordination. Every incumbent obligation, historical
  outcome, alternative and custodian remains; neutral historical privacy/coordination
  corrections change no substantive gate. New normal review, actual merge and complete
  current readback precede source supersession. The effort stays Active; no
  implementation, tests, native result or rollout is claimed.

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

### 2026-09-26 - Required check registered (operator-confirmed)
- Operator confirmed the exact before/after diff and the mutation was applied
  directly via the GitHub API (fetch-then-PUT full ruleset payload, preserving
  every pre-existing rule):
  - `main` ruleset `<main-ruleset-id>`: required status checks
    `["main source gate"]` -> `["main source gate", "identifier leak guard"]`.
  - `dev` ruleset `<dev-ruleset-id>`: added a new `required_status_checks` rule with
    `["identifier leak guard"]` (previously had no such rule).
- Phase 3 is now fully complete. Remaining work is entirely Phase 4
  (operator-only secret provisioning) plus the two validation items gated on
  those secrets existing.

### 2026-09-27 - Interim non-blocking fix: misconfigured reports neutral, not failure
- Neither `main` (ruleset `<main-ruleset-id>`) nor `dev` (rulesets `<dev-ruleset-id>`/`<additional-dev-ruleset-id>`)
  currently list `identifier leak guard` as a required status check -- Phase 3's
  last checkbox (adding it to required checks) is correctly still unchecked --
  but the misconfigured-secrets path reported `conclusion: "failure"` on every
  PR's check-run regardless, which downstream tooling (and PR authors) reading
  the status-check rollup were reasonably treating as a real red X across
  essentially every open PR while Phase 4 (secret provisioning) remains
  outstanding.
- Changed `identifier-leak-guard.yml`'s misconfigured branch from `failure` to
  `neutral` ("Identifier leak guard not yet configured (in development)") so
  the check surfaces as non-blocking while it's genuinely incomplete, instead
  of looking like a real enforcement failure. Real scan failures (forbidden
  identifiers found, or a genuine step error) still report `failure` and post
  PR feedback exactly as before -- only the "not configured yet" case changed.
- This is a stopgap for the development window only. Once Phase 4 provisions
  `<private-input-family-a>`/`<private-input-family-b>`, `configured` becomes true and
  this neutral branch never fires again; the check should still not be added
  to required status checks until Phase 3's validation-with-a-real-secret step
  is complete.

### 2026-09-27 - Phase 4 revised: secret provisioning delegated to agents, not the operator

- A separate consuming-repo session independently raised the same
  "misconfigured -> failure" concern above and asked to disable it; that
  conversation converged on the same neutral-conclusion fix (landed just
  earlier the same day in #4343/PR-of-record above), then surfaced the actual
  remaining blocker: the operator has no way to originate the raw denylist
  values themselves and won't be doing so.
- Operator correction to the original Phase 4 "operator only" plan: they will
  not personally type these secrets. Both are **agent-assembled and
  agent-pushed** instead, because the content is not secret from within either
  source context -- only from the public repo:
  - `<private-input-family-a>` -- an private-downstream-repo agent assembles this list and
    pushes it via `gh secret set`. Storage doesn't need Vault (no credential
    material, just names/identifiers), and can live in the operator's
    private personal source location.
  - `<private-input-family-b>` -- needs collaboration with the operator's
    private-context work harness agent, sourced from a separate list the
    operator keeps on their **work-context** private source location, then pushed the same way.
  See Participants and the revised Phase 4 above. Exact list contents,
  private source paths, and which agent actually runs `gh secret set` are not yet
  settled -- this entry captures the operator's stated approach, not a
  completed plan.

### 2026-09-29 - `<private-input-family-a>` provisioned

- Storage location changed again from the 2026-09-27 entry's private source-location plan: the
  source list now lives as a plain, versioned asset in the private downstream
  private downstream repository, rather than a private personal source path -- that
  repo already documents every one of these identifiers openly on its own
  side; they're secret only *from this public repo*, not from that one -- so
  keeping the source alongside the private adopter docs it's derived from (rather
  than an unversioned private source-location file) makes the denylist reviewable, diffable,
  and easy to keep in sync as that roster changes.
- The downstream agent assembled the `token|reason` list and pushed it to the
  `<private-input-family-a>` repository secret via `gh secret set` (account-
  routed through `agent-worktrees repos gh`, never a bare `gh`).
  `gh secret list --repo ThomasMichon/copilot-extensions` confirms the secret
  is present.
- Deliberately excluded from the list: generic English/mythological words used
  as individual device codenames on the downstream side, and single-word
  machine names that also double as this repo's own generic example persona/
  voice-kernel content, as opposed to the compound `<device>.internal.example`
  hostname form, which is unambiguous. Both classes would produce
  disproportionate false-positive risk against ordinary public content for a
  denylist whose whole purpose is precision, not maximal recall.
- **Real bug found and fixed the same day this list was assembled:** the
  first push accidentally included the source asset's own header comment
  lines as literal denylist entries, because the CI-mode loader
  (`_load_ci_identifiers`) skips blank entries but -- unlike the local
  single-identifier loader -- has no equivalent skip for `#`-prefixed
  comment lines. A stray bare `#` line (used for paragraph spacing) became a
  one-character forbidden token that matched almost any Markdown heading,
  and the loaded prose comments became giant literal substrings. This
  surfaced immediately as 37 false-positive matches on this very PR's own
  diff. Re-pushed a corrected, comment-free secret within ~19 minutes;
  confirmed no other open PR's `identifier leak guard` check ran during that
  window. The downstream source asset's header now states this constraint
  explicitly and its push instructions filter comments/blank lines before
  piping to `gh secret set`.
- Left for `<private-input-family-b>`: unchanged, still the operator's separate
  work-context harness agent's task, sourced from their private work-context source location.
- Discovered while assembling this list: a handful of these same personal/
  private adopter identifiers (distinct from the ones the 2026-09-26 sweep in #3910
  already cleaned up) have re-leaked into this repo's own tracked docs post-
  cleanup (mostly under recent `efforts/active/*` READMEs and one plugin
  skill doc). That is a real, separate cleanup item -- filed as
  [#4675](https://github.com/ThomasMichon/copilot-extensions/issues/4675)
  rather than folded into this effort's scope, and not enumerated here per
  this same repo's own leak-guard purpose.

### 2026-09-30 - Failure-path validated for real; a genuine bug found and fixed

- With `<private-input-family-a>` now present, the two remaining
  secret-gated Validation Plan items could finally be exercised for real
  (`configured` becomes `true` from either secret alone -- the workflow
  concatenates both when present, so neither item needed
  `<private-input-family-b>` to be actionable). Added a synthetic, non-real
  canary token to the denylist specifically so this validation (and any
  future re-validation) never needs to risk a real identifier.
- A scratch PR reintroducing that canary confirmed the Check Run correctly
  reports `failure` with the exact match/reason/file/line/col in its
  `output.text` -- but also surfaced that the trusted `workflow_run` token
  cannot post or update an Issues-API PR comment even with `issues: write`
  declared (`403 Resource not accessible by integration`), while the
  identical token's `checks.create` call succeeds. This had never been
  exercised before now, because every prior validation ran on the
  `configured=false` (neutral/misconfigured) branch, which returns before
  reaching the comment-posting code at all.
- Fixed by wrapping the comment-posting step in try/catch: a failure there
  now degrades to a warning instead of an unhandled workflow error, since
  the Check Run's own `output.text` (API-delivered, not a masked log line)
  already satisfies this mechanism's actual design requirement on its own.
  Also corrected the local scanner's own CI-mode message, which pointed
  users at "PR review comments" that may not exist.
- A same-day review correction on the fix above: the Check Run `output.text`
  was itself capped at 50 rendered findings ("...and N more"), with the
  (now best-effort) PR comment as the only channel that ever carried the
  complete list. Since the comment can no longer be relied upon, a PR
  reintroducing more than 50 forbidden identifiers at once would have lost
  match/reason/file/line/column detail for the rest. Replaced the fixed
  50-item cap with a byte-budget-aware truncation against the Check Run
  API's documented 65535-character limit, so the sole relied-upon channel
  stays complete for any realistic finding count.
- Closes Phase 4's last checkbox: the workflow file now documents the
  secret format and the exact provisioning/rotation command directly in
  its own header, and the scanner's docstring documents the CI-mode
  loader's non-comment-skipping behavior.
- Remaining: `<private-input-family-b>` provisioning is still the operator's
  work-context harness agent's own task, unchanged from the 2026-09-27
  entry above.

### 2026-09-30 - `<private-input-family-b>` now actively being built by the work-context harness agent

- Operator confirmed a work-context harness agent is actively building the
  `<private-input-family-b>` denylist and will push it to the repository secret the
  same way `<private-input-family-a>` landed. This is genuinely out of reach
  for this repo's own first-private-source/public-repo session: that harness isn't part
  of any reachable machine mesh or agent-bridge roster here, so it is tracked
  as an external, in-flight handoff rather than driven from this side.
- With this confirmation, the first-private-source/public-repo side of this effort is
  fully complete -- every other Plan and Validation Plan item is already
  checked (see Phase 0-4 and the Validation Plan above). The only remaining
  open item is `<private-input-family-b>` provisioning itself, owned entirely by
  that external agent; this effort stays **Active** (not yet `Done`) until
  that secret lands and the Validation Plan's denylist-backed-scan item can
  be reconfirmed with both secrets present.

### 2026-10-01 - `<private-input-family-b>` landed; Phase 5 opened for a reusable cross-repo mechanism

- The work-context harness agent's `<private-input-family-b>` build-out completed:
  confirmed via `gh secret list` (`<private-input-family-b>` present, rotated
  2026-10-01), alongside the pre-existing cleanup of 46 pre-existing-leak
  files this same expansion surfaced (`#4834`, closed via PR #4858; see the
  now-closed `identifier-leak-guard-scrub` effort for that cleanup's own
  record).
- That whole flow was still fundamentally manual: one operator hand-copied
  one private file's content into a secret by hand, with no discoverable way
  for a *second* repo to contribute its own terms, and no distinction
  between "fully private" and "needs the guard" repos beyond which list
  happened to get pasted where.
- Opened Phase 5 to generalize this into the pluggable, cross-repo mechanism
  `docs/identifier-blocklist.md` now documents: a `visibility` tier per
  registered repo, a `.identifier-blocklist/block-for-<tier>.yaml` convention
  any repo can carry, `agent-worktrees identifiers sweep` to aggregate them,
  and the local guard consuming that sweep as an automatic fourth source
  (never a new requirement -- silently absent wherever `agent-worktrees`
  isn't installed/registered).
- Landed the mechanism itself (registry/CLI/module/docs/tests) this session;
  the harness-repo migration and secret re-provisioning from the live sweep
  remain open (Phase 5's last two checkboxes) -- tracked as the natural next
  slice, not blocking this phase's own PR.

