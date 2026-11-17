# Bounded selected PR populations

`pr-watch population --contract --json` is passive discovery for
`agent-worktrees.pr-population/v1`. It neither reads configuration nor acquires
credentials. This additive native read closes the selected-population driver
slice of the [PR vision](../../../visions/plugins/agent-pull-requests/README.md).
It does not register subscriptions, run a service, or change existing inventory,
snapshot, review, or lifecycle behavior.

```text
<project> pr-watch population Project/repository --config <absolute-config> --credential-command <JSON-argv> --filter-file <absolute-expression-file> --max-pages 4 --max-pulls 200 --seconds 60 --json
```

The selected file must contain exactly one matching `repos.<name>.pr` binding
with explicit `provider`, `api_base`, and `repository`. There is no registry,
layered configuration, token-command, environment-token or ambient login
fallback. The common `agent-pr-filters` parser validates the bounded JSON
expression, driver capabilities, and every repository predicate before any
credential or provider call. The filter narrows only this exact repository.
The credential command uses the existing absolute JSON argv contract and returns
a fresh typed `observe` credential for every request. Its provider, endpoint,
repository, permission and expiry must agree. Secrets travel on curl stdin,
not argv; curl configuration, URL glob expansion and redirects are disabled.

## Compatibility and caller custody

Consumers discover this contract from their selected executable using
`population --contract --json`, not from package-version equality or source
presence. Older executables lack this additive verb: a new caller must report
unsupported selection rather than downgrade to unfiltered inventory. Existing
inventory/snapshot callers retain their old syntax and result contracts on the
new executable. Upgrade is provider-first; there is no persisted population
state to migrate. Rolling back the provider requires withdrawing new invocations
or retaining a compatible executable, not broadening their meaning.

Only an absolute `--filter-file` is accepted; there is no inline JSON option.
The common parser bounds the UTF-8 envelope to 32768 bytes. The CLI closes its
read handle but neither creates nor deletes the file, checks its permissions,
or grants file custody. Callers create a private immutable file, protect it from
replacement for the native invocation, and clean up their owned file after
the contained command has settled. Filter identities are private caller data;
the file must not carry credentials.

## Initial driver semantics

Only `azure-devops` is supported for population. GitHub/Gitea retain their
existing inventory commands but explicitly refuse this command. ADO uses the
repository's native active-pull-request list, with `creatorId` or `reviewerId`
pushdown where equivalent. `any` unions selected branches; conjunction chooses
a narrowing cover and evaluates all residual predicates with the common
library. A branch without an identity restriction permits bounded listing of
this repository, never organization-wide discovery. Duplicate PR numbers across
branches coalesce. Page, pull and request limits are shared across branches,
not multiplied by their count.
The descriptor's `acquisition` declaration exposes the shared pure admission
planner's identity and query-cover constraints. `query_rotation: offset/v1`
additionally permits `--query-offset <0..63>` (default zero) to rotate the
bounded cover before reading pages. A continuing owner advances that offset
between cycles to avoid starving later author/reviewer queries. An older
caller retains its default ordering; a caller requiring rotation must select
the capability before passing the new argument.

* `from.users`: canonical lowercase provider `IdentityRef.id` UUIDs, not names,
  email addresses, login aliases or inferred membership.
* `assigned.users`: native reviewer user UUIDs, not work-item ownership.
* `repo`: the exact admitted `Project/repository` slug.
* `age`: creation age from timezone-qualified `creationDate`; one captured cycle
  instant drives evaluation, so time alone can change a match.
* `tags`: native PR label names, read through the separate labels endpoint under
  the same request/time budget. Explicit complete empty labels mean `[]`;
  missing, malformed or unavailable labels mean `null`.
* `publication`: native boolean `isDraft`, normalized to `draft`/`published`.

Native project/repository response names are checked with ADO's case-insensitive
name semantics; facts retain the exact admitted slug for common filter evaluation.

**Group predicates are explicitly unsupported**, including reviewer group IDs:
this driver has no membership-evidence owner and does not fabricate expansion.
Group selectors refuse before credential acquisition. A group or malformed
reviewer record makes reviewer evidence unknown rather than inventing a complete
user roster. **Activity age is explicitly unsupported**: the list endpoint has
no verified last-activity field. `activity_at` remains `null`; closed dates or
head changes are not substituted.

## Result and finite bounds

Results use `schema: agent-worktrees.pr-population`, `version: 1`, with provider,
endpoint, repo, filter revision, outcome, pages, selected limits, safe error code,
and bounded `pulls: [{number, facts, match}]`. Facts contain only `repo`, `from`,
`assigned`, `created_at`, `activity_at`, `tags`, and `publication`; absent
evidence remains `null`. Match is `match`, `nonmatch`, or `unknown`. Candidates
remain in the output even when unknown or nonmatching. Raw descriptions, source
and credentials are never exported.

Defaults are 4 total list pages, 200 unique PRs, and 60 seconds. Hard caps are
20 list pages, 1000 unique PRs, 64 total native requests/query branches, 50 rows
per page, 128 reviewers/labels per PR, 1 MiB body/output JSON and 8192 stderr
bytes. Provider capture includes at most eight additional HTTP-status bytes.
Credential capture retains its existing 64 KiB/15-second bounds within the
remaining cycle deadline. Existing owned-process capture supplies headless
Windows creation and joined cleanup.

Only an empty final list page in every branch establishes complete discovery;
available metadata is also required for a complete result. Failed first-page
reads are unavailable. Later failure, repeated/incomplete pages, missing facts,
or a cap preserve acquired candidates with partial coverage. Incomplete
acquisition never proves membership departure or PR closure. No retries occur
after credential loss.

The contained `test_pr_population.py`, `test_pr_inventory.py`, and
`test_pr_credential_command.py` controls qualify source semantics and native
process seams using synthetic data, not installed-runtime or live-provider
acceptance. This slice does not claim complete group/activity semantics,
subscription custody or service integration.
