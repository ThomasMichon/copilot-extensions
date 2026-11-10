# Explicit native selected-thread resolution

Back to the [CLI parity inventory](cli-parity-inventory.md) and
[canonical effort](README.md). Related lifecycle acceptance remains owned by
[#7051](https://github.com/ThomasMichon/copilot-extensions/issues/7051) and its
[normalized contract](../pr-lifecycle-conformance/contract.md).

**Contract reviewed in #11714; finite SOURCE implementation merged in #11728.**
This finite
ADO-first slice advances the PR-domain
[provider-neutral surface](../../../visions/plugins/agent-pull-requests/README.md#first-class-provider-neutral-pr-surface)
and Worktrees'
[foreign-repository operations](../../../visions/plugins/agent-worktrees/pull-requests/README.md#foreign-repo-pr-operations).
It follows the existing
[version-skew pattern](../../../docs/patterns/service-contract-evolution.md).
It neither replaces the lifecycle owner nor qualifies complete resolution,
reopening, installed or HTTP parity.

## Baseline and boundary

Worktrees' tracked `pr-status --resolve-threads` selects its active tracked PR
and calls the provider's bulk resolver. Explicit `pr-status --repo/--pr`
rejects that switch. `pr-watch threads` and the dedicated native `threads`
command are read-only and remain so.

The legacy provider signature is not a selected-thread contract. GitHub ignores
the integer `thread_ids`, whose values are display indexes, and resolves
unresolved node IDs from its first 100 threads without pagination metadata.
Its nested comments are also bounded. Gitea explicitly refuses resolution.
ADO can close selected native integer IDs, but returns an aggregate string
without candidate admission or a structured per-operation outcome. Do not
silently reinterpret or advertise these methods as the new capability.

| Surface | Proposed additive form |
|---------|------------------------|
| Worktrees owner | `pr-thread-resolve --repo <registered-target> --number <positive-number> --thread <native-positive-id> --expected-candidate <fingerprint> --expected-thread <fingerprint> --operation-id <key> --json` |
| Dedicated native client | `resolve-thread` with the same target, selection and expectation arguments, plus required `--worktrees-command <selected-runtime>` |
| Support discovery | Optional `thread-resolve` entry in the existing `agent-worktrees.pr-author-capabilities`, version 1, operation map |

Reuse the existing explicit author-command admission, target binding and
optional delivered worker-policy selection. A claimant worktree is required;
a target checkout is not. Target policy selects provider, endpoint and
internally brokered credentials, never the caller's default PR or an arbitrary
provider/host/token override. Strictly reject unknown arguments, tracked
selectors, invalid IDs/fingerprints/keys and bulk/all-thread selection.
Preserve all old syntax, defaults, outputs and tracked claim ownership.

Only the selected enabled ADO binding may advertise support after its source
controls pass. Absent, false, malformed or unavailable support is not permission
to try a write. GitHub, Gitea and other providers remain unsupported by this
optional operation; legacy operations remain independently usable.
Capability reporting grants neither effect authority nor reviewer privileges.

## Evidence and admission

Extend the existing ADO thread report additively with `resolution_candidate`:
the canonical endpoint, project/target-repository/PR identity, actual source
repository and ref, target ref, head/base/common commits, current source
iteration, and a versioned fingerprint over that full tuple. Reuse the native
candidate/iteration readers and existing canonical digest helper. The
fingerprint version defines the tuple and serialization; it is not a package
version. Do not use a head-only fingerprint for different source/base authority.
Missing or ambiguous native identity/iteration data is unavailable evidence,
not a fabricated same-repository or current-candidate default.

The report brackets acquisition with candidate observations. An incomplete
collection, malformed/duplicate thread identity, changed candidate, missing
pagination evidence or unavailable nested comment evidence cannot supply a
resolution expectation. Preserve the baseline report's existing completeness
and head-change fields; add explicit resolution-candidate availability/change
evidence rather than strengthening old fields silently. Legacy readers may
ignore the additive fields; new callers cannot infer them from exit 0.
The existing ADO `thread_fingerprint` supplies the exact native thread revision,
including comments, authors, timestamps, status and iteration context.

The mutation owner resolves the actual authenticated principal through its
existing native identity boundary and evaluates effective native
read/PR-contribution permission at the target repository and branch, reusing
the existing permission evaluator. Preserve applicable target policy and
operator authority; possession of a thread ID or a capability is insufficient.
Native denial or unavailable identity/permission evidence refuses before
dispatch. This is discussion state, not a review vote: neither infer an
approval nor invent a rule limiting resolution to the comment's author.

Before writing, obtain an authoritative active same-target PR and complete
addressable discussion evidence. Require the exact requested candidate and
thread fingerprints, one live discussion with active status, valid native
iteration association and the resolved actor's action authority. Deleted,
system/vote-only, pending, already-closed or otherwise non-active threads
refuse without mutation. Recheck candidate, selected thread and permission
immediately before dispatch; never fall back to another ID or all threads.

## Single effect, recovery and outcome

Compose the existing ADO remediation owner and status-only native PATCH.
The sole intended effect is `status: closed` on the selected thread. It must
not reply, edit bodies, resolve siblings, reopen, vote, request reviewers,
release holds, grant consent, merge, push source, settle claims or finalize.
No new forge client, watcher, service, policy store or credential flow is added.

Reuse the existing locked consumed-before-write remediation ledger. Bind the
operation key to target, actor, action, full candidate, thread ID/revision and
requested status. A conflicting key refuses; persist intent before dispatch.
An in-flight, unknown or previously consumed key must never issue another
PATCH automatically, including after process interruption. Reconciliation
retains the original identity and performs reads only. A changed principal or
candidate cannot silently turn the original operation into a new one.

ADO's status PATCH supplies no atomic candidate/thread compare-and-swap.
Advertise only **preflight plus readback**, never a conditional-write guarantee.
Another participant can change the PR or discussion after the final check.
Require an authoritative same-target write response and subsequent candidate
and thread readback for confirmation. The native thread must be closed, its
content/identity unchanged except for status and its native thread-update
timestamp, and the original candidate still current. Comment bodies, authors
and comment-update timestamps may not change under that exception.
Readback is observation of the requested state, not exclusive causal
attribution or proof that no later participant edit can occur.

Return `agent-worktrees.pr-thread-resolve`, version 1, with exact requested
repo/number/thread, resolved provider/actor when known, operation ID,
expected/observed candidate and thread evidence, a stable reason code,
diagnostic and `actions.resolution.status`:

| Status | Meaning |
|--------|---------|
| `not-attempted` | Input, support, claimant, policy, identity, freshness or applicability refused before dispatch |
| `confirmed` | The single write's authoritative response and same-target readback meet the conditions above |
| `failed` | An authoritative response establishes rejection without an ambiguous send outcome |
| `unknown` | A write may have applied, or response/readback/candidate/thread evidence cannot confirm it |

Only `confirmed` yields `ok: true` and exit 0. Preserve any known result identity
and independent observations on every failure. A timeout, malformed response,
lost output, changed candidate or late completion after dispatch stays unknown.
Later observing a closed thread alone does not prove this request completed:
retain the original unknown outcome and block automatic replay, rather than
attribute another participant's action to this operation. Recovery does not
post a synthetic marker comment. Stronger cross-venue mutation attribution
and provider-conditional guarantees remain open under #7051.

The native client preserves valid owner envelopes, additive fields, nonzero
exits and original selected identity through the existing bounded native
process/cleanup primitive. Malformed or lost output after dispatch is unknown,
not a retry instruction or empty success. There is no alternate executable,
raw forge route, bulk resolver or effectful compatibility fallback.

## Expansion and qualification

Expand the Worktrees/provider owner, additive report evidence and selected
runtime capability first. Then enable the thin native client conditionally.
Old callers on new owners retain their baseline; new callers on old or
rolled-back owners refuse the optional feature before mutation. Capability
fetch failure does not invalidate unrelated legacy operations. No form is
deprecated here and no HTTP/report-read mutation is introduced.

- [x] Qualify the actual Worktrees parser, claimant/target/policy admission,
  native principal and permission owner with synthetic ADO transport responses.
  Prove exactly the selected status-only PATCH, unchanged independent PR state,
  and explicit unsupported GitHub/Gitea behavior without their bulk resolvers.
- [x] Exercise the actual frontend-to-owner-to-provider composition and a real
  process seam. Cover strict invalid syntax, additive report/result fields,
  mixed versions, old defaults and selected-runtime rollback; argv assertions
  alone do not prove semantic parity.
- [x] Refuse incomplete pagination/nested reads, duplicate/missing IDs, deleted or
  non-active targets, invalid iteration, actor/policy denial and source/base/head
  or selected-thread movement before dispatch. Include same-head base/ref and
  source-repository changes so head-only checks cannot pass.
- [x] Inject authoritative rejection, lost response, timeout, malformed/late
  response, changed readback and crash/restart on both sides of dispatch.
  Prove honest unknown outcomes, conflicting-key refusal, read-only consumed-key
  recovery and no second PATCH or synthetic comment, including concurrent callers.
- [x] Record isolated source/platform controls separately from native Windows,
  installed/provider-account/HTTP qualification and full three-provider
  lifecycle acceptance. Keep the parent Plan and Validation Plan open.

The [source implementation journal](journal/2026/11.09%20Selected%20native%20resolution%20implementation.md)
records the focused Linux controls and reviewed delivery. Native
Windows, installed/provider-account/HTTP and complete lifecycle qualification
remain open under their existing owners; no live thread was changed.

## Selected native reopening proposal

**Refinement reviewed in #11741 under #7051; finite source implementation
merged in #11748.** Continue
the same provider-neutral lifecycle intent and existing owner, rather than
treating the delivered close-only operation as reopening support.
The [native ADO thread-update contract](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/pull-request-threads/update?view=azure-devops-rest-7.1)
includes `active` separately from `fixed`, `wontFix`, `closed`, `byDesign`
and `pending`. The current remediation status allowlist does not include
`active`; neither `pr-thread-resolve` nor its native client may reinterpret
their close-only contract.

Add optional `operations.thread-reopen: true` to the same target-qualified
author-capabilities version-1 response, initially for enabled ADO bindings
only. Proposed Worktrees `pr-thread-reopen` and native `reopen-thread` take
the same strict repo/number/native-thread, expected-candidate/expected-thread,
operation-ID and optional delivered-policy selectors as resolution.
The native client also requires its exact `--worktrees-command`.
Unsupported GitHub/Gitea, absent/malformed support and old/rolled-back receivers
refuse before dispatch, without a raw forge or bulk fallback.

Compose the merged complete candidate/discussion reader, actual authenticated
principal, repository/target-branch permission evaluator, remediation transport
and consumed-before-write ledger. Current base freshness remains independent
of the historical iteration target. No new credential, policy, storage,
watcher or service owner is introduced. Capability and native contribution
permission still do not supply operator effect authority.

Only an exact live discussion in native `fixed`, `wontFix`, `closed` or
`byDesign` state qualifies, with valid iteration association. Already-active,
pending, unknown, deleted, system/vote-only, incomplete or stale evidence
refuses without mutation. Preserve full candidate/thread and principal/
permission rechecks immediately before dispatch.
The sole intended effect is `{"status":"active"}` on that selected thread.
Do not reply, edit comments, recreate deleted data, resolve siblings, reopen
the PR itself, vote, release holds, grant consent, merge, publish source or
settle claims.

The ledger binds the reopening action and requested active state with the
same target, principal, full candidate and thread revision. The caller supplies
a new intentional operation ID; reopening cannot reinterpret, clear or replay
a consumed resolution operation. Idempotency remains action-scoped under the
existing ledger, not a new global cross-command or cross-installation guarantee.
Conflicting keys refuse, intent is durable before dispatch, and consumed/
interrupted/unknown reopening keys reconcile read-only with no second PATCH.

Return `agent-worktrees.pr-thread-reopen`, version 1, retaining the same selected
identity, operation ID, provider/principal, expected/observed evidence,
reason/error fields and `actions.reopening.status`.
Only `confirmed` has `ok: true` and exit 0; `not-attempted`, authoritative
`failed` and `unknown` exit nonzero. The existing resolution schema/action
and all old syntax/defaults/outputs keep their meanings.
Confirmation requires an authoritative same-thread response and same-candidate
active readback with no change except status/native thread-update timestamp.
Comment bodies, authors and comment timestamps remain unchanged.

This remains preflight plus readback, not atomic CAS or exclusive causal proof.
Lost, truncated, malformed, timed-out, late or changed response/readback stays
unknown. Later observing an active thread cannot upgrade that original
unknown outcome. Likewise, a stored confirmed result describes the original
operation, not a guarantee the thread stayed active; later observations remain
independent. No marker comment, retry or effectful compatibility path is added.

### Reopening source controls after review

- [x] Qualify the actual owner parser, claimant/target/delivered policy,
  native principal and effective permission owner. Prove exactly the selected
  status-only active PATCH and unchanged PR, sibling, comment and vote state.
- [x] Exercise the actual native frontend/owner/provider composition and a real
  process seam. Cover strict invalid syntax, additive result fields, missing/
  malformed/old/rolled-back support and unchanged close-only/legacy behavior.
- [x] Admit each supported closed/resolved native state and refuse active,
  pending, deleted, system/vote-only, incomplete/duplicate/unaddressable,
  invalid-iteration and stale target/principal/permission evidence.
  Include source/base/ref/repository/thread changes and stable target-only advance.
- [x] Inject authoritative rejection, truncated/lost/malformed/late response,
  readback movement and crash on both sides of dispatch. Prove conflicting-key
  refusal, concurrent same-ledger ownership, original-outcome read-only recovery
  and no second PATCH or synthetic comment, including later re-closure.
- [x] Record isolated source controls separately from native Windows,
  installed/provider-account/HTTP, cross-venue attribution and complete
  three-provider lifecycle acceptance. Preserve both parent completion gates.

The [implementation journal](journal/2026/11.09%20Selected%20native%20reopening%20implementation.md)
records 414 isolated Worktrees controls and 193 frontend controls with five
existing platform skips. These qualify the finite source leaf, not installed
support, provider-account effects or the parent lifecycle acceptance.
