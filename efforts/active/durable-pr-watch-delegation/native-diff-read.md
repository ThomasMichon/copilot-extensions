# Explicit native diff reading

Back to the [CLI parity inventory](cli-parity-inventory.md) and
[canonical effort](README.md).

**Contract reviewed in #11659; finite native source merged in #11661.
Source qualification below is not installed or live-provider acceptance.**
This finite read-only slice closes part of the PR-domain
[provider-neutral surface](../../../visions/plugins/agent-pull-requests/README.md#first-class-provider-neutral-pr-surface)
and Worktrees'
[foreign-repository reach](../../../visions/plugins/agent-worktrees/pull-requests/README.md#foreign-repo-pr-operations).
It follows the existing
[version-skew pattern](../../../docs/patterns/service-contract-evolution.md).
Existing provider, workspace, source-control and resource owners remain.

## Baseline and boundary

Worktrees' tracked `pr-diff [worktree]` resolves and reconciles that worktree's
active PR before calling `PRProvider.get_diff`. It is not an explicit
named-target read and must keep its syntax, selection, output and exit
semantics. The provider result has only `diff`, `supported` and `error`;
it supplies no completeness, base/head or candidate-freshness guarantee.
GitHub reads through `gh pr diff`, Gitea through its `.diff` endpoint, and
Azure DevOps explicitly reports unified-diff acquisition unsupported.
The existing GitHub method ignores `api_base`; the new target-qualified
path must not inherit that wrong-endpoint risk.

The dedicated PR CLI has no native diff command. Its configured review
workspace acquisition and retained-source reader are separate contracts:
they bind generations, source references and resource custody, not an
equivalent generic unified-diff response. Do not route this read through a
workspace allocation, invent an ADO patch, or fall back to another forge.

| Surface | Proposed additive form |
|---------|------------------------|
| Worktrees read owner | `pr-watch diff <registered-target> <positive-number> --json` |
| Dedicated native client | `diff --repo <registered-target> --number <positive-number> --worktrees-command <selected-runtime> [--json]` |

Extend the existing one-shot read dispatcher and target-binding resolver,
not the tracked lifecycle resolver. Explicit target/provider/endpoint/account
selection must work from neutral CWD without a claimant or target checkout.
Use the target's configured endpoint, including GitHub Enterprise, rather
than the caller's repository or an unrelated ambient host. Preserve the
existing default-host behavior when no endpoint is configured.
Malformed configured GitHub endpoints refuse rather than borrowing that
default; supplied selected credentials override all public/Enterprise token
aliases used by the same native CLI.
Reject missing/blank selection, option-prefixed repo values, invalid numbers
and unknown arguments before either direct or managed-peer execution.
The dedicated command is native-only; it accepts no provider, endpoint,
credential or workspace override.

The frontend invokes the selected attributable Worktrees runtime exactly
once, using the existing caller-owned process, timeout, output and cleanup
budgets. The owner calls the existing provider diff primitive; no second
transport, watcher, persistent body store, preflight subprocess or retry is
introduced. Required credential failure remains a read failure. No claim,
association, source mutation, comment, review, resolution, merge, durable
subscription or lifecycle reconciliation may occur on this new path.

## Result and error semantics

The new owner emits `schema: agent-worktrees.pr-diff-read`, integer `version: 1`,
the exact selected `repo`, positive integer `pr`, nonempty string `provider`,
boolean `supported`, string `diff`, string `error` and `complete` as boolean
or null. This schema is exclusive to the new explicit-target report; the
tracked command keeps its existing shape (including the generic JSON
formatter's version 1, but no diff-read schema) and is not an interchangeable
report source. JSON booleans are not integer versions or PR numbers.

`complete: true` means the report has evidence that all declared diff
acquisition completed, false means known incomplete acquisition, and null
means completeness is unknown. A successfully obtained partial report may
have false completeness and an empty error; acquisition outcome and
completeness are independent. None of these values establishes candidate
freshness. The existing provider primitive supplies no completeness
evidence: this baseline owner emits null on successful acquisition and false
on error or unsupported acquisition, never true. The frontend nevertheless
accepts all three values so it can retain valid richer version-1 reports
without changing their meaning. Neither empty nor nonempty diff text
establishes complete acquisition or an empty PR.
Explicit acquisition selects strict byte-preserving UTF-8 capture through
the existing provider process owner: CRLF and bare CR remain unchanged,
and invalid UTF-8 fails rather than becoming replacement characters.
Tracked diff calls keep their existing text-normalization default.

The new frontend validates the version, required types and exact target
identity, then preserves the whole valid report, including additive fields
and any future provider-native revision or truncation evidence. It does not
manufacture base/head identity, infer freshness from a separate status
snapshot, upgrade unknown completeness to true, or normalize diff text.
An exit-0 read is not a candidate-bound review artifact, approval,
writeback capability or merge authority.

Return 0 only for a valid report, owner exit 0, `supported: true` and empty
report error. A valid report with false or unknown completeness may still
return 0: completeness and command completion are independent. Unsupported
acquisition, report errors and nonzero owner exits return nonzero while
retaining any valid report. A successful-looking envelope cannot hide a
nonzero owner exit.

Emit the complete valid report on stdout in both frontend modes, including
failure reports; independent frontend diagnostics go to stderr. Malformed,
mismatched, oversized, unavailable or unsupported-version output yields a
frontend-owned error, retaining the original requested identity in JSON
mode, never an invented supported empty diff. Timeouts and lost output mean
evidence unavailable. Diff content is untrusted data and grants no action
authority; do not persist it in watch snapshots or the central journal.

## Compatibility and qualification

Expand the Worktrees receiver first; existing tracked `pr-diff` callers
remain unchanged. The version-1 response from the exact selected runtime
declares the new read support, independently of package versions or newer
payloads. A safe read-only invocation can discover this baseline without
another handshake. A runtime lacking the verb or envelope reports truthful
incompatibility with no fallback or second dispatch; existing operations
remain usable. Migrate ordinary consumers only after receiver qualification;
no legacy retirement is proposed.

- [x] Exercise the actual owner/parser and dedicated frontend across a real
  process boundary from neutral CWD, with distinct caller/target bindings and
  no target checkout or lifecycle/resource writes.
- [x] Use actual GitHub/Gitea/ADO provider methods with synthetic IO.
  Prove configured GitHub endpoint selection despite an unrelated ambient
  host; preserve missing-credential/provider errors and explicit ADO refusal.
- [x] Preserve exact diff bytes as UTF-8 text through JSON encoding, additive
  fields, empty text, incomplete/unknown completeness, valid error reports
  and owner-exit/envelope disagreement. No inferred candidate freshness.
- [x] Cover direct and managed-peer selection, pre-dispatch option-prefix
  refusal, strict unknown options, invalid numbers/types/targets, malformed
  envelopes, missing executable, timeout and output limits; no second call.
- [x] Exercise old tracked caller/new owner, new caller/version-1 owner,
  unsupported old owner and supported rollback through real parser/process
  fixtures. Preserve tracked reconciliation and its existing exit semantics.
- [ ] Record isolated source/platform qualification separately from installed,
  live-provider, HTTP, generation-bound workspace and complete parent parity.
  Keep the canonical Plan and Validation Plan open after finite delivery.

The native diff/routing packet passes 149 cases with five Windows-only
skips. The actual Worktrees owner/read/provider/tracked packet passes 362,
including real subprocess GitHub/Gitea/ADO provider methods with synthetic IO,
configured-versus-ambient GitHub host selection, missing credentials,
native refusal, malformed provider result refusal, unknown report verb
rejection and unchanged tracked parser/reconciliation/output.
Actual provider CLI subprocess capture covers CRLF/bare CR, strict UTF-8
failure and legacy normalization, plus selected Enterprise token aliases
and malformed endpoint refusal; these are synthetic CLI processes, not
live forge requests.
Both isolated runs verify cleanup. Native Windows/macOS, installed,
authorized provider-account, HTTP and broader parent parity remain open.
