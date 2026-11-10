# Native unpublished-creation cancellation

Back to the [CLI parity inventory](cli-parity-inventory.md) and
[canonical effort](README.md).

**Contract reviewed in #11651; finite native source merged in #11653.
Installed/provider/HTTP acceptance remains open.**
This closes part of the PR-domain
[provider-neutral surface](../../../visions/plugins/agent-pull-requests/README.md#first-class-provider-neutral-pr-surface)
through the existing Worktrees creation owner. It follows
[version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md)
and preserves the [source-custody boundary](author-publication.md#ownership-and-exclusions).
It does not qualify remote abandonment, HTTP mutation or installed adoption.

## Separate the two existing operations

Worktrees `pr-abandon` has distinct paths:

| Selected path | Existing owner and effects |
|---------------|----------------------------|
| Tracked remote PR | `pr_abandon_ops.abandon_pr`: explicit operator-approved closure, best-effort reason comment and abandoned-claim settlement |
| Exact unpublished creation | `pr_creation_cancel.cancel_creation`: qualified local reservation cancellation, retained reason/actor/revision receipt, no provider closure or claim release |

Both require a nonblank reason and explicit confirmation after actual operator
approval. The flag is not an authorization primitive. Local cancellation also
requires an immutable creation ID and exact expected revision. It proves local
head, remote head and provider PR-head absence through existing owners before
committing. Legacy, published, publishing or uncertain attempts remain retained.
An already-closed remote PR is not an unpublished creation.

Remote close cannot be relabeled equivalent to this finite local operation.
It retains its own reason-comment preparation/publication and partial-effect
obligations under the
[comment contract](comment-identity.md). Do not decorate legacy provider
methods, discard a reason comment, add a second effect ledger or release a PR
claim to make a thin frontend appear complete. The combined close/comment error
classification prerequisite merged in #11649; it does not supply structured
per-action outcomes or native abandonment parity.

## Smallest compatible native boundary

Add a native-only command:

```text
cancel-creation --worktree <exact-owner-worktree-id> --creation <immutable-pr-id> --expected-revision <nonnegative-integer> --reason <nonblank-text> [--confirm] --worktrees-command <selected-runtime> --json
```

This is deliberately worktree/creation-addressed, not repository/PR-addressed.
It runs in the owning project's existing admitted configuration context.
There is no target-repository checkout requirement, but this slice does not
claim arbitrary-CWD operation or remote source access. Require the exact full
worktree ID; an inferred current worktree, abbreviated selector or different
record cannot satisfy it. Reject `--repo`, `--number`, remote `--pr` selection,
`--config`, worker-policy selection and unknown options rather than silently
ignoring or reinterpreting them.

Keep the Worktrees command, arguments, defaults, warning and result unchanged.
Do not add another cancellation algorithm, project resolver, storage owner,
credential path or forge transport to the native client.

Before any record/discovery read, reject missing/invalid selectors, a negative
or non-integer revision and a blank reason. Without `--confirm`, return a
nonzero, explicit confirmation refusal without invoking Worktrees. Its
diagnostic must retain the actual-operator-approval requirement and alternatives:
retain/reconcile useful or uncertain work rather than treating a rebase,
conflict or delay as grounds to abandon. This frontend refusal is not a claim
that the legacy warning was executed. No automatic confirmation is permitted.

## Discovery and one owner dispatch

After explicit confirmation, query the **same selected Worktrees runtime**
using the existing read-only form:

```text
pr-status <exact-owner-worktree-id> --all --no-live --json
```

Require a valid, same-worktree response without an error, the existing
`creation_cancellation_contract` value
`agent-worktrees.pr-creation-cancellation.v1`, and one unambiguous matching
creation entry. Inspect `prs`, not only the active PR. Do not select another
attempt or borrow a same-number PR. This declaration identifies the actual
receiver's supported contract, not the package version or a newer payload.
Discovery performs no live reconciliation or native provider read; it is not
proof that cancellation is currently eligible.

Only then dispatch once to that same receiver:

```text
pr-abandon <exact-owner-worktree-id> --creation <immutable-pr-id> --expected-revision <original-revision> --reason <original-reason> --confirm --json
```

The Worktrees owner retains all current project, record-incarnation, reservation,
revision, source/publication, endpoint, absence and lock checks. Discovery does
not reserve a revision or make the later transaction atomic. Preserve the
original supplied revision, including original-receipt recovery after an
already-confirmed cancellation; do not substitute today's revision from status.
Missing/prior/rolled-back support refuses this optional frontend before
cancellation dispatch without disabling other native or legacy commands.
Never probe an effectful new form and replay an older form on a parser error.

## Results, uncertainty and custody

Preserve the owner's result and actionable diagnostics. The frontend may add
an explicitly frontend-owned `frontend.cancellation_status` field:

| Status | Meaning |
|--------|---------|
| `not-attempted` | Local admission or read-only discovery refused before cancellation dispatch |
| `confirmed` | The matching v1 owner result confirms local cancellation, including exact original-receipt recovery |
| `unknown` | Cancellation was dispatched but no valid confirming result is available |

Only `confirmed` exits zero. Validate the returned contract and exact worktree
and creation identity, an integer resulting revision greater than the original
expected revision, terminal local
`state: abandoned`, `success: true`, `creation_cancelled: true`, boolean
`already_cancelled`, `provider_closed: false` and `claim_released: false`.
Successful receipt recovery is cancellation confirmation, not evidence that
this invocation created a new transition.

A nonzero subprocess, owner error, timeout, lost response, malformed output or
identity/semantic mismatch after dispatch is not confirmation. The legacy
error envelope alone does not establish whether a local save committed before
its response was lost; preserve that uncertainty and original inputs. Do not
automatically retry, allocate another creation, close a remote PR or use a
different runtime. Reconcile the original owner/attempt before considering an
intentional recovery; recovery uses the same original revision and reason.

Cancellation never changes source bytes, index, HEAD, branches, remotes,
attribution, another creation, resource claims or finalization gates. The owner
may perform its existing absence reads; it never creates/closes a provider PR,
posts a comment, casts a vote, grants consent or merges. This document and a
capability declaration grant none of the required effects or operator approval.

## Delivery and qualification

Review this boundary before implementation. Reuse the already delivered v1
owner and its declaration; only extend that owner first if a concrete missing
binding is found. Add the native frontend conditionally, preserving old callers
on new receivers and explicit pre-effect refusal on old/rolled-back receivers.
No existing command is deprecated here.

- [x] Exercise the actual native parser and selected Worktrees process seam on Linux:
  no-confirm/invalid-input zero dispatch, exact full worktree/creation selection,
  non-active matching attempt, missing/duplicate entries, malformed discovery,
  unsupported receiver and strict unknown-option refusal.
- [x] Compose actual creation persistence/cancellation with the frontend in
  isolated state. Prove exact revision/reason forwarding, owner race and
  published/uncertain refusal, original-receipt recovery, retained audit and
  unchanged source, siblings, claims and ordinary finalization gates.
- [x] Inject pre-dispatch discovery failure, owner refusal, failed persistence,
  nonzero subprocess, lost/malformed/mismatched response and timeout. Preserve
  unknown outcomes after dispatch and prove no retry, fallback or remote close.
- [x] Cover new/current, new/prior and receiver rollback through the selected
  process, plus old/current through an actual owner parser/handler subprocess, including additive result fields and
  an unsupported optional contract without blanket native rejection.
- [ ] Record isolated source and native-platform evidence separately from
  installed/provider/HTTP acceptance. Retain the full remote-abandonment,
  prepared reason-comment and broader CLI parity gates in the parent.

### Source qualification

Current source evidence uses new isolated fixtures, not retained worktrees or
provider state:

| Qualification | Evidence and boundary |
|---------------|-----------------------|
| Native cancellation/routing | 149 passed, five platform skips on Linux; actual native command runner and strict receiver-parser processes, including prior support/rollback and managed-peer admission |
| Worktrees owner composition | 82 passed; actual creation persistence, status and cancellation handlers with synthetic provider absence, plus an old-caller parser/handler subprocess |

These are complementary controls, not proof of installed cross-process
provider operation. Current output/admission controls cover plain-error
recovery diagnostics, complete default success receipts, missing stdout/stderr,
preserved malformed owner fields, original frontend-owned recovery inputs and
blank command-selector rejection before managed-peer selection or discovery.

Exact original-revision recovery, non-active creation selection, retained audit,
source/index/HEAD/refs, sibling attempts, resource claims and the source
finalization precondition are covered. A save-then-lost-response control confirms
the frontend reports `unknown` despite a committed cancellation and dispatches
no automatic recovery. Owner race, published/uncertain reservations and failed
persistence also remain nonzero/unknown after dispatch.

The suites ran inside the existing hardened, network-disconnected test phase.
The [effort journal](README.md#journal) retains review/correction history and the
disposable bootstrap-download adjustment; its pinned archive/hash and all
isolation/cleanup controls remained unchanged. No tracked bootstrap, installer
or runtime installation changed.
Native Windows/PowerShell execution, installed receiver rollout, live provider
absence and full ordinary-finalization adoption remain separate qualification.
