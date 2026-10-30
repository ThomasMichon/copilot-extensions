# Offline review generations (first slice)

This source entry point implements the first bounded
[PR review workspace slice](../../../efforts/active/pr-review-workspaces/read-generation-slice.md).
It normalizes already-collected ADO evidence and compares immutable generations.
It **does not acquire evidence, authenticate, attach source, submit a review,
evaluate eligibility, persist a cache or start a service**. Existing PR commands,
native snapshots and watches are unchanged.

An authorized transport owns acquisition, pagination, completeness declarations
and account provenance. These bytes cannot prove that a live read occurred or
grant permission to post. Do not copy credentials, headers or diagnostic payloads
into the input. Store real evidence only in the caller's private workspace.

## Exact source invocation

From the desired source checkout, select its package explicitly:

```powershell
$env:PYTHONPATH = Join-Path $PWD 'plugins\agent-pull-requests\src'
python -m agent_pull_requests.review_workspace_cli capabilities
python -m agent_pull_requests.review_workspace_cli load --evidence plugins\agent-pull-requests\docs\review-evidence.example.json
python -m agent_pull_requests.review_workspace_cli sync --evidence evidence.json --previous generation.json
```

On POSIX set `PYTHONPATH="$PWD/plugins/agent-pull-requests/src"` for the same
module invocation. Use the actual chosen checkout/ref, not the installed shim.
The module uses only the standard library. No installer or runtime selection
is involved. `--help` and `capabilities` disclose this executing implementation's
support, not a package-version guess about a different installed runtime.

`load` accepts `--evidence -` for stdin. `sync` accepts a previous **generation**
(the `generation` member of a prior result), not the enclosing sync result.
Neither writes a file. The caller chooses any output paths; stdout is one JSON
value. Exit codes: `0` normalized (possibly incomplete), `1` input/contract
failure with a JSON error, `2` strict CLI syntax error on stderr. Incompleteness
is not an input failure and is never review readiness.

## Version-1 input

The [synthetic example](review-evidence.example.json) is executable without
network or credentials. The envelope is:

- `schema: agent-pull-requests.review-evidence`, integer `version: 1`.
- `scope`: exactly `provider: azure-devops`, HTTPS `endpoint`, `project_id`,
  `repository_id`, `principal_id` and positive integer PR `number`. IDs, not
  display names, separate namespaces. This declared principal is not an
  authentication assertion. Endpoint trailing slashes are canonicalized;
  other aliases are deliberately not conflated.
- `sections`: any of `pull_request`, `iterations`, `changes`, `discussions`,
  `reviewers`, `policies`. Unknown section names fail rather than disappear.
- Every supplied section has `data`, boolean `complete`, string-list `errors`
  and timezone-bearing ISO-8601 `observed_from` / `observed_to`. A complete
  section cannot carry errors. The transport must mark partial/failed paging
  incomplete, retaining known records. Omitted sections are unavailable with
  `data: null`; an explicitly complete empty list is different.
- `pull_request.data` is an ADO PR object. When complete, its `pullRequestId`,
  `repository.id` and `repository.project.id` must match scope. Supplied
  mismatching identity always fails, even for partial evidence.
- Other data sections contain bounded lists of native ADO records: iteration
  objects, iteration change entries, all-status threads including comments,
  reviewer objects, and policy evaluations respectively. `changes.revision`
  requires `head_sha`, `iteration` and authoritative `common_sha`.

The adapter selects domain fields rather than retaining arbitrary response
headers or raw diagnostic objects. Completion values preserve presence/null/
false, as do votes, deleted comments and iteration contexts. Selected
provider vote-event properties retain their native `CodeReviewThreadType`,
`CodeReviewVoteResult` and `CodeReviewVotedByTfId` presence/wrappers; the adapter
does not interpret them as review authorization. Other unsupported
provider fields are not evidence of false values. Records are deterministically
ordered; comments retain provider order. A blob's type or path is not a binary/
text classification or proof that source was prepared.

## Output, freshness and synchronization

`agent-pull-requests.review-sync` version 1 contains `previous_id`, a
`generation`, `changed_sections`, `invalidations`, operation `duration_ms`,
`acquisition: caller_supplied` and `effects: none`.

Evidence input is capped at 8 MiB. Generation inputs/outputs have a separate
16 MiB bound to accommodate normalization metadata, so a valid generated
baseline can round-trip through `sync`. The CLI emits compact JSON; caller-added
whitespace still counts against its raw input limit.

The generation is `agent-pull-requests.review-generation` version 1. Its `id`
hashes canonical scope, selected normalized data, revision, completeness and
observation intervals. It is an integrity/revision key, not provenance or
authority. Previous input must have a valid ID and exactly the same scope.
Generations do not assert an atomic forge snapshot or expiration-based freshness.
Consumers retain the per-section observation intervals and apply their own
action-specific freshness requirements.

`revision` records the PR head, newest observed iteration head, current source iteration, iteration
target, authoritative common commit and current PR target. Missing facts remain
null. The newest iteration must agree with the PR metadata head before it
establishes a current range; a historical matching iteration cannot hide a
newer conflicting head. Changes bound to another head/iteration/common commit remain visible but
become incomplete with `revision_mismatch`. Source attachment always reports
`unsupported` and the currently observed head; old source is never relabeled.
`complete` means all supplied required sections and revision evidence are
complete, **not** permission or source readiness.

Observation-time-only refresh creates a new generation but no material delta.
Replies, edits, deletions and thread disposition invalidate discussion decisions
without invalidating source analysis. Changed or unknown head/iteration/common
range invalidates source analysis, inline anchors and review decisions.
Target-only movement refreshes review decisions without discarding the unchanged
authoritative source range. These are conservative analysis hints, not policy.

## Compatibility and remaining work

Supported evidence/generation contract versions are exactly `[1]`, independently
of package version. Additive fields inside supported native ADO objects may be
ignored; required schema/version, scope and section names are strict. Existing
command syntax/output is untouched. Older installed peers do not implement this
new module; callers must select an implementation advertising this capability.
Rollback to one without it is explicitly unavailable, never an ambient-client
fallback or an attempted review write.

Later slices own authorized live acquisition, service-backed atomic cache/CAS,
source-workspace attachment and durable explicit submission/reconciliation.
No cache journal or live review authority is introduced here.
