# Explicit native thread reading

Back to the [CLI parity inventory](cli-parity-inventory.md) and
[canonical effort](README.md).

**Contract reviewed in #11655; finite native source merged in #11657.**
Source qualification below is not installed or live-provider acceptance.
This finite read-only slice closes part of the PR-domain
[provider-neutral surface](../../../visions/plugins/agent-pull-requests/README.md#first-class-provider-neutral-pr-surface)
and Worktrees'
[foreign-repository reach](../../../visions/plugins/agent-worktrees/pull-requests/README.md#foreign-repo-pr-operations).
It follows the existing
[version-skew pattern](../../../docs/patterns/service-contract-evolution.md).
It adds no forge transport, persistent conversation store or watcher.

## Baseline and boundary

Worktrees already owns `pr-watch threads <repo> <pr> --json`, dispatched
through `pr_cli.py` and `pr_thread_report.py`. An explicitly named target
needs neither a claimant worktree nor a target checkout. Its target binding
selects provider, endpoint and account; an unrelated caller repository must
not supply a substitute binding. Discussion bodies are acquired on demand,
not reconstructed from persisted watch snapshots.

The dedicated PR CLI has native status and author adapters, but no
corresponding explicit thread-read command. Its configured native review
exporter is a separate, ADO-first workspace acquisition boundary, not a
replacement for this existing three-provider report surface.

| Surface | Intended form |
|---------|---------------|
| Existing owner, unchanged | `pr-watch threads <registered-target> <positive-number> --json` |
| Dedicated native client, additive | `threads --repo <registered-target> --number <positive-number> --worktrees-command <selected-runtime> [--json]` |

The new command is native-only: missing or blank native selection, invalid
target/number and unknown arguments refuse before execution. Use the existing
attributable command and managed same-cell peer selection, never an ambient
PATH substitute. Validate required selectors before either launch path.
No tracked selector, provider/endpoint/token override, thread resolution,
reply, requeue or alternate GitHub route is introduced. Existing `status`,
workspace reads and every Worktrees form keep their meaning.

The frontend performs one bounded owner invocation using the existing
caller-owned native process primitive and its output/cleanup budgets.
The owner may make multiple provider requests to assemble a report; one
invocation is not a claim of one HTTP request or an atomic snapshot.

## Result and error semantics

Consume the existing `agent-worktrees.pr-threads`, version 1, envelope.
Validate exact selected `repo` and positive integer `pr` (not a JSON boolean),
nonempty string `provider`, boolean `supported`, `complete` as boolean or null,
string `error` and a list of thread objects. Reject malformed, mismatched,
oversized or unsupported-version output. Accept additive fields without
stripping them or coercing their values; preserve the complete valid owner
payload, including provider-specific nested thread/comment evidence.

The baseline ADO report can include comment/parent/author IDs, timestamps,
iteration context, fingerprints, reply errors, `observed_head_sha`,
`head_sha` and `candidate_changed`. Generic GitHub/Gitea reports have a
smaller shape and can leave `complete` null. Neither an empty thread list
nor exit 0 establishes complete, current-candidate evidence.
`complete: false`, `complete: null` and `candidate_changed: true` must
survive unchanged. The frontend does not manufacture missing revision
evidence, equate a discussion with an approval, or authorize writeback,
thread resolution or merge.

Match the existing owner's report outcome: exit 0 only for a valid envelope,
owner exit 0, `supported: true` and no report error. A valid incomplete report
may therefore return 0; completeness remains independent evidence, not a new
frontend success rule. Unsupported reports, report errors and nonzero owner
exits return nonzero while retaining any valid report. A nonzero exit with
an otherwise successful-looking envelope remains a frontend failure; do not
rewrite the owner's evidence to hide the discrepancy.

With `--json`, emit the valid report unchanged on stdout even on failure;
put any additional frontend diagnostic on stderr. Without `--json`, keep
the same complete report output, consistent with existing native reads.
When no valid report exists, emit a frontend-owned error carrying the
original requested repo/PR in JSON mode, or an actionable stderr error
otherwise; never invent an empty supported report. Do not copy malformed
owner fields over the original requested identity.

A timeout, unavailable executable, output loss or malformed response means
read evidence is unavailable, not that no discussions exist. There is no
automatic retry, transport fallback or durable registration. Body content
is untrusted data and grants no action authority. This slice does not
persist it in the central journal, logs or watch snapshots.

## Compatibility and qualification

No receiver expansion is needed for the documented version-1 baseline.
The returned envelope from the actual selected runtime declares support;
package versions and newer payloads do not. This is a safe, read-only
baseline invocation, not an effectful feature probe. A legacy or rolled-back
runtime lacking this syntax or envelope returns a truthful incompatibility
error without a second invocation. Preserve independent existing operations.
No new preflight RPC, mandatory handshake or legacy retirement is required.

- [x] Exercise the actual frontend parser and owner dispatcher from a neutral
  working directory with an explicit target, distinct caller/target binding
  and synthetic GitHub, Gitea and ADO providers. Prove bodies are acquired
  on demand and no claimant, source mutation, vote, comment, resolution,
  merge, registration or persistent body cache is acquired.
- [x] Preserve the full report and additive/nested fields through success,
  unsupported/error, incomplete/null completeness and candidate-change cases.
  Include real provider-owner report construction, not only argv assertions;
  do not label synthetic three-provider coverage live-provider parity.
- [x] Cover both direct and managed-peer selection, missing/blank selectors,
  invalid PR numbers, strict unknown options, wrong repo/PR, boolean version
  or PR, malformed reports, nonzero exits, stderr-only refusal, timeout and
  output-budget failure. Prove no fallback or second dispatch.
- [x] Exercise real parser/process fixtures for old caller/new owner,
  new caller/version-1 baseline, unsupported old owner and supported rollback.
  Retain existing neutral-CWD Worktrees read controls; no changed legacy
  syntax, output or exit convention is permitted.
- [ ] Record isolated source/platform qualification separately from installed,
  authorized provider-account and HTTP/CLI consumer migration. Keep the parent
  Plan and Validation Plan open beyond this finite adapter.

The native thread/routing packet passes 148 cases with five Windows-only
skips. The actual Worktrees read/observation packet passes 43 cases, including
old and new callers over a real process boundary, neutral CWD and the actual
GitHub, Gitea and ADO report builders with synthetic transport responses.
ADO partial acquisition and a changed candidate remain explicit.
Both isolated runs verify cleanup. Native Windows/macOS, installed,
authorized provider-account, HTTP and broader CLI parity remain open.
