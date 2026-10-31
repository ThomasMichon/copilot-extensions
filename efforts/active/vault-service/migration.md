# Vault Service - Proposed Extraction and Adopter Migration

Back to [the effort](README.md), [architecture](architecture.md) and
[V9 acceptance](validation.md#acceptance-obligations).
**Agent-recommended plan, not permission to extract code, copy credentials,
change an installation or retire an existing owner.**

## Extraction before adoption

Prefer adapting or extracting compatible capabilities over a blank rewrite:
existing local credential custody, provider invocation, typed pull routes,
scoped Gateway admission, contributed UX and proven coordination behavior.
For each candidate, establish source provenance/licensing, public portability,
maintaining owner, dependency closure and behavioral compatibility before
moving it into a shared service. Unavailable provenance is not permission to
copy; a generic contract can be designed independently without importing
unqualified source.

Queue durability, deduplication, conditional updates, attempt fencing, expiry,
presence and consumer-driven outcome handling are useful extraction targets.
They are not a claim that the public Gateway already provides durable Vault
transactions or that an external implementation can be transplanted unchanged.
Do not publish adopter-specific source paths, deployment identifiers or policy.

Adopt providers independently. An existing credential-store extension can remain
a physical-custody backend under an explicit Vault lifecycle contract. A
credential-pull adapter can remain a compatibility route without becoming an
issuer, durable coordinator or permission authority. Qualify both directions
of compatibility before changing any existing consumer's selected owner.

## Per-adopter cutover gates

All gates require the adopter's named owner and explicit scoped permission.
Plan review is not that permission.

1. **Inventory and consent.** Identify current transaction, credential, native
   execution and deployment owners and their consumers through supported
   interfaces. Record which responsibilities would move and which remain.
   Include pending/coalesced requests, expiry, cancelled and uncertain effects,
   protected history, provider grants, credentials/handles and session bindings.
   Inspect without launching authentication or reading secrets into the UX.
2. **Prepare and qualify.** Build the selected local or central distribution
   through existing installer/deployment owners. Use synthetic isolated fixtures
   for crash, duplicate, withdrawal and rollback qualification first. Establish
   data compatibility, protected storage, restoration bounds and supported
   native providers. Preparation does not activate a second writer or copy
   nonexportable credentials.
3. **Stage a reversible handover.** Old owner retains mutation authority while
   a non-serving candidate validates an owner-authorized transfer of non-secret
   transaction/consumer history and protected custody references. Any secret
   movement requires its own explicit provider/custody authorization; otherwise
   retain native handles or report an unsupported migration. Reconcile accepted
   and uncertain operations instead of silently abandoning or recreating them.
4. **Transfer at the effect boundary.** The existing owner quiesces new mutations
   within the selected scope and transfers responsibility through its supported
   handoff. Exclude old/superseded writers before the new owner mutates. Keep
   consumer request identities, attempt generations, cancellation and readiness
   relationships. Native operations remain with their correct device/user
   session; ongoing interaction must be reconciled, not blindly relaunched.
5. **Verify real consumer readiness.** Redirect admitted consumers to the
   selected authority, verify correctly bound access/use, observe pending
   transactions and reconnect clients. Service health, a route acknowledgement,
   imported metadata or provider approval alone is insufficient. Compare
   denied/expired/revoked and coalesced-consumer behavior as well as success.
6. **Retire only verified scope.** Remove old admission/registrations only after
   serving and custody continuity is confirmed. Retain protected history under
   the agreed retention and owner policy. Data destruction, credential
   redistribution and retirement of unrelated services require separate consent.
   Keep #8729 and this effort open until their full acceptance gate is met.

## Failure, rollback and partition

Before authority transfer, abort leaves the original serving owner intact and
the candidate non-serving. After transfer, recovery first determines which
owner can legitimately mutate; a timeout cannot authorize both sides.
Prefer compatible checkpointed handover, with bounded cold recovery when
necessary under the existing service replacement owner.

Rollback is an explicit compatible authority transfer, not a binary selection
over an unreadable ledger. Qualify reversing newly accepted requests, current
provider bindings and protected history before enabling the old writer.
If safe reversal cannot be established, preserve one current authority and
recover forward or hold for operator resolution; do not resurrect completed
attempts, erase new history or invoke authentication to manufacture recovery.

Federated ownership transfers need both services' admitted trust/ownership
contract. During a partition, local authority may serve only its own still-valid
grants within declared offline/revocation bounds. An unreachable remote owner
does not become abandoned, and cached observations do not grant credential
custody or permission to take over its transaction.

## Remaining admission

P0 must choose owners and resolve D1/D2/D5/D6 for actual migration contracts;
V2/V4/V6/V8/V9 must qualify their respective boundaries. Generic migration
fixtures precede any live adopter. Each deployment/account/native interaction
requires later explicit authorization, and its actual result remains separate
from source implementation, plan acceptance and release publication.
