# Protected owner-read embedding

The generic source embedding implements the
[reviewed owner-adapter contract](../../../efforts/active/pr-review-workspaces/owner-read-adapter.md).
It is **not an installed ADO operation, authorizer, issuer or standalone backend**.
An existing ADO owner must explicitly adopt it inside that owner's normal
authenticated, authorized and input-validated operation. Credentials stay there.
Source availability or a schema label does not establish operational adoption.
Calling from an agent or separate process does not introduce another permission
ceremony, issuer, role or universal provenance verifier. Reuse the already
authorized owner's normal context. The work/payload limits here are the selected
API's IO contract, not limits on context-isolated delegation.

## Exact source entry points

- Protected owner component:
  `agent_pull_requests.owner_read.OwnerReadHandler.read(request)`.
- Trusted binding returned by that owner's resolver:
  `agent_pull_requests.owner_read.OwnerReadBinding`.
- Existing-provider orchestration, without credential export:
  `agent_worktrees.providers.azure_owned_evidence.acquire_owned_evidence`.
- Provider binding:
  `agent_worktrees.providers.azure_owned_evidence.AdoReadOwner`.
- Offline inspector, **no backend read**:
  `python -B -m agent_pull_requests.owner_read_cli`.

The components use callbacks supplied by the existing owner after its gates.
They do not load a resolver or callbacks from caller JSON, a plugin label,
environment fallback or a password helper.

## Request contract

`agent-pull-requests.owner-read-request`, integer version `1`, requires exactly
`binding_ref`, positive integer `number` and finite positive `budget_seconds`;
`expected_principal` is an optional additional equality guard. Unknown fields
are rejected, including endpoints, account/role claims, commands and credentials.
The request is at most 8 KiB; it does not carry an entire previous generation.

This [complete synthetic example](owner-read-request.example.json) is executable
through the offline inspector:

```powershell
$env:PYTHONPATH = Join-Path $PWD 'plugins\agent-pull-requests\src'
python -B -m agent_pull_requests.owner_read_cli contract
python -B -m agent_pull_requests.owner_read_cli validate-request `
  --request plugins\agent-pull-requests\docs\owner-read-request.example.json
```

The inspector reports `operational_owner: false` or
`owner_adoption: not_established`, with `backend_access: false`. Passing it is
syntax validation, not authorization or a live-read result.

## How an existing owner adopts it

1. Keep the existing transport, authenticated request context, role/policy
   checks, endpoint/account selection and argument guards. Expose one fixed
   evidence operation there, not a new token-export endpoint or service.
2. Its authorized resolver receives `(binding_ref, remaining)` and returns an
   `OwnerReadBinding` **only** for a target allowed to that current caller.
   The binding supplies canonical provider/endpoint/project/repository/effective
   principal scope (without PR number), an owner trace `context_id`, a live
   `checkpoint()` and `collect(number, remaining)`. Those data fields and the
   Python type do not themselves authenticate anything.
   The checkpoint enforces the current context or raises; it returns `None`,
   never a caller-provided authorization boolean or label.
   Resolve the current authorized request context, not a cached permission or
   a context reconstructed from a saved receipt.
3. Configure `OwnerReadHandler` with that resolver and the owner's actual
   `max_work_seconds` / `max_payload_bytes`, reserving enclosing transport
   overhead. These limits are mandatory owner inputs, not caller-issued permits.
   The handler uses the smaller requested/owner work budget.
4. The collector can call `acquire_owned_evidence` with an `AdoReadOwner` from
   the same existing context. Its `reader_factory(number, remaining, checkpoint)`
   supplies the owner's already authorized native reader: `identity`, `pr`,
   `iterations`, `iteration_changes`, `threads`, `evaluations`. Reuse the
   existing native reader/SDK pagination; do not build a second HTTP client.
   The factory must apply the supplied work budget and live checkpoint to every
   physical request/page, not just collection entry. Keep normal backend auth
   and input guards on those calls.
   An unavailable collection returns explicit incomplete native collection
   evidence, not a complete empty list. Missing effective identity or canonical
   PR identity refuses the operation.
5. Qualification must prove that actual owner, not merely a synthetic callback,
   enforces caller/account/target and lifetime across pages. Until that exists,
   the operational lane remains unavailable. An installed secret helper or
   Azure login is not a substitute.

The source wiring is:

```python
from agent_pull_requests.owner_read import OwnerReadBinding, OwnerReadHandler
from agent_worktrees.providers.azure_owned_evidence import acquire_owned_evidence

# These are owner-provided callbacks/context, not installed command names.
binding = OwnerReadBinding(
    scope=scope_from_the_existing_authorized_owner,
    context_id=existing_owner_trace_id,
    checkpoint=existing_owner_checkpoint,
    collect=lambda number, remaining: acquire_owned_evidence(
        existing_ado_read_owner, number, budget=remaining(),
    ),
)
handler = OwnerReadHandler(
    existing_authorized_binding_resolver,
    max_work_seconds=existing_owner_work_limit,
    max_payload_bytes=existing_owner_payload_limit,
)
# Invoked only by the existing protected owner operation after its normal gates:
response = handler.read(request)
```

The placeholder callbacks must come from an actual adopted owner, not a caller
wrapper asserting labels. No new issuer, login, role grant, cache copy, runtime
installation or service activation follows from this example. This source task
does not install an operation into another owner's plugin.
Here, adoption means wiring this source interface into that existing authorized
execution path; it is not a new grant of access. A missing callable is an
interface gap, not a denial of already granted backend permission.
`OwnerReadUnavailable` is an interface/current-binding failure, not a
`PermissionError` requesting another grant. Actual permission denials raised by
the existing owner pipeline propagate unchanged.
The current embedding is synchronous. An async owner must use its existing
bounded execution/lifetime mechanism while preserving its normal request context;
this component does not start a helper loop, thread or service to bypass it.

## Principal semantics, including delegation

Scope and `expected_principal` use **only**
`connectionData.authorizedUser.id`: the complete native ADO effective identity.
`authenticatedUser.id` may identify a different transport/delegating actor.
It is not a fallback, is not compared as the effective principal, and is not
converted from an AAD object ID. Missing `authorizedUser.id` refuses even when
`authenticatedUser.id` is present. The existing owner still enforces any
additional delegation policy; this component grants no human-presence or
review-vote authority.

Both credential and owner-reader profiles call the same effective-identity
decoder. Owner acquisition checks actual identity before PR reads and again
after collection; its normal owner pipeline/checkpoints must retain the
authorized account on every intervening physical request/page.

## Response, local sync and bounds

`agent-pull-requests.owner-review-snapshot` version `1` contains:

- `generation`: the existing immutable normalized generation-v1 object;
- `acquisition`: selected timing/completeness/range provenance, never credentials;
- `owner`: profile `owner_executed_review_evidence/v1`, trace context ID and
  `principal_semantics: connectionData.authorizedUser.id`;
- actual applied `work_budget_seconds`, `duration_ms`, measured `payload_bytes`
  and owner-selected `payload_limit_bytes`, plus `effects: none`.

Payload bytes describe this compact JSON object, **not** an enclosing RPC frame.
The existing owner must reserve and enforce its real transport framing limit;
oversized results refuse rather than truncate. A trace ID or receipt is not a
permission grant. A complete generation is not review eligibility, pinned
source, an atomic forge snapshot or permission to submit.

The source handler checkpoints before/after collection, normalization and
publication, verifies scope against the resolved owner and refuses expired,
wrong-principal, wrong-scope, late or oversized results. Work deadlines do not
cancel ownership: the existing owner retains in-flight cleanup/custody.

Keep the returned `generation` privately. Compare a subsequent generation without
another backend read using `review_workspace.compare_generations(current, previous)`
or `python -B -m agent_pull_requests.review_workspace_cli compare
--generation current.json --previous previous.json`. The existing invalidation
semantics are shared, not reimplemented by an ADO interpretation agent.
