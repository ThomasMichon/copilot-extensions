# Protected review executor: source embedding

This realizes the protected-operation portion of the
[reviewed submission contract](../../../efforts/active/pr-review-workspaces/submission-slice.md)
over the same `ReplayStore`. It adds no ledger, HTTP/auth client, issuer, service,
watcher, queue or review-policy engine. It is a library interface, not a shipped
operational command or evidence that an installed owner supports review effects.

## Existing protected write-context factory

The actual domain owner first applies its normal authentication, role, input,
repository and review-consent gates. Its context manager pins **one actual current
write credential and transport** for the selected PR scope, then constructs
`agent_worktrees.providers.azure_owned_review.AdoReviewContext`:

```python
context = AdoReviewContext(
    selected_scope,
    request=existing_protected_request,
    credential_binding=existing_current_private_material_reference,
    checkpoint=existing_current_owner_check,
    review_check=existing_action_relevant_review_policy_check,
)
```

These callables come from that existing protected owner; never deserialize them
or manufacture a write context from caller role/issuer labels or an observer
secret. The private material reference must be immutable for its generation,
not a mutable token container. It stays inside the owner and is never returned
in intent, result, cache or receipt JSON.

`request(method, url, body, query=..., expected_binding=..., remaining=...)`
is the owner's existing backend operation seam. **At actual dispatch** it must
enforce the same actual pinned material reference and normal permission/input
gates, including renewal/account-switch detection. A separate observer identity
read does not qualify a writer. The same callback handles identity/candidate
reads and selected writes; the SDK adds no raw transport or login. Existing
HTTPS/no-redirect, finite deadline, pagination and wire-budget enforcement remain
with that transport; the shared native `AdoReader` supplies collection behavior.

`review_check(operation, action, current, context=...)` is the existing caller's
action-relevant policy guard. It can opt into fresh
`context.read_decision_inputs()` statuses and **policy evaluations** through the
same pinned transport. Configuration definitions are not evaluations; incomplete
inputs stay incomplete. The SDK does not require all policies/merge checks green
when the caller's actual review policy does not require them.

Replies are `NativeReviewResponse(status, body, continuation)`, parsed by the
existing owner. No raw header/token is requested. ADO identity is always
`connectionData.authorizedUser.id`, never `authenticatedUser.id` or an Entra ID.
The SDK checks actual native repository/PR IDs and latest source iteration/common
evidence, preserving incompleteness rather than guessing.

## Finite execution

```python
from agent_pull_requests.review_executor import execute_review

result = execute_review(
    existing_store, selected_intent, reviewed_generation, source_attachment,
    context_factory=existing_pinned_write_context_manager,
    checkpoint=existing_current_owner_check,
    expires_at=current_request_expiry,
    budget_seconds=owner_review_io_budget,
)
```

The intent must match the immutable generation and ready source-manifest identity/
revision and complete source/discussion evidence. Policy completeness or merge
readiness is **not** an unconditional review gate. Existing caller policy checks
run on current action-relevant inputs; hashes and context objects do not grant
authority.

Execution persists selected intent before effects, checks actual current identity/
revision/discussion and target ownership, durably fences an action as unknown,
then repeats current-owner and candidate checks after persistence before sending.
Backend IO runs outside the shared store lock. Changed credentials, source,
discussion/comment ownership or decision inputs stop stale selection without
refreshing intent or resetting original observation clocks.

Native writes serialize only selected comment/reply/owned-comment content or the
one selected principal's vote. Other reviewers and all completion-setting
presence/values are untouched. Response verification distinguishes known IDs from
a complete matching action reply: partial or contradictory responses retain IDs
as unknown, not a fabricated confirmation.
The store's conservative `confirm=False` disposition keeps all known native IDs
without upgrading an unverified response into a confirmed effect.

Confirmed own receipts may advance only their exactly selected semantic thread/
comment changes. Unrelated replies, edits, deletion, ownership or status changes
stop remaining actions. Server last-update timestamps are accommodated only on
the explicitly changed owned targets; stricter audit/freshness policy stays with
the existing caller guard. Source/metadata generations are never rewritten.
For an already confirmed vote response, one new native `VoteUpdate` system thread
with the same native voter/result may be included per confirmed vote. Other
voters/results, extra events or user comments are not silently ignored. This
expected-delta handling is never used to confirm an unknown vote.
Vote identity is resolved from the raw native identity map by the existing
observation parser before normalization discards optional fields. Conflicting
legacy/indexed identities are unavailable; both numeric and string native system
comment representations are supported.

There is no atomic multi-action or head-CAS guarantee. Post-effect readback can
report candidate drift while retaining a confirmed native effect; no compensating
vote, rollback or duplicate comment is inferred.

## Unknown outcomes and native attribution

Once a write path is entered, transport loss, malformed/partial response or
owner loss preserves its original unknown attempt and stops later actions.
If a bounded native write reply arrives after the work deadline while the owner
still permits settlement, its known IDs are persisted unconfirmed; no success
or subsequent write is permitted. Malformed response authors likewise fail
verification without discarding otherwise known native IDs.
Actual owner denials/cancellation propagate through the existing path. A denial
is not proof an already-admitted effect or atomic store commit was absent.

Unknown new-thread comments can be reconciled only through a complete fresh
native thread read containing the exact operation/action marker, selected content,
native author and IDs. The marker is a native thread property, not an alteration
of caller-selected text or proof of server-side deduplication. Missing/stripped
markers, ambiguous matches, partial pages or modified content leave unknown.
Actual backend support must be qualified by its owner; source fake fixtures do
not establish production property behavior.

The initial native adapter does **not** attribute unknown reply/edit/vote events
from their current values. They remain unknown with no send/replay. A richer
existing audit owner may supply an independently qualified effect-specific seam
in a later supported contract; do not wrap equal votes/content as proof.

`status: complete` means the operation's selected receipts are confirmed, including
retained results on replay—not a fresh eligibility, merge, deployment or live
review qualification. Results carry original operation/revision, action states,
entered-write count, stage, measured elapsed time and explicit non-atomicity.

## Source validation and remaining adoption

The tests use the actual generation builder, atomic `ReplayStore`, native request
serializers/shared reader and synthetic protected material/transport. They prove
pre-send persistence, same-material dispatch checks, source/discussion drift,
owned targets, cancellation/owner loss, partial IDs, read-only marked attribution
and non-replay of unattributable current values. They make no real backend calls,
service/bootstrap changes or installed permission/adoption claims.

Actual pinned-context adoption, live ADO end-to-end review, source fetching,
serving qualification and separately authorized rollout remain unresolved in the
original effort. Do not install, invent a command or switch backend to close them.
