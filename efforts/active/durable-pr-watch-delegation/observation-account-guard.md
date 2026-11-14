# Optional native observation account guard

Back to the [canonical effort](README.md) and
[repository cache milestone](repository-cache-events-m1.md).
Tracking: #12536 under #6007. This is an **agent-recommended, review-first
contract**, not executing capability or credential/deployment authority.

## Gap and existing owners

The typed observation owner checks provider, HTTPS endpoint, repository,
purpose and material-use expiry. Those selected bindings do not prove which
native authenticated actor answered. A caller selecting an expected Gitea
account needs actual provider evidence, not a copied scope field, new verifier
callback or another HTTP client.

Reuse Worktrees' `pr_inventory_cli`, `pr_credential_command`,
`ObservationCredentials`, `observation_inventory`, `gitea_snapshot` and the
existing typed Gitea GET/direct HTTP owners. Preserve native process containment,
captured credential output, current result shapes and the foreground service's
original lifecycle. No cache, issuer, broker or observation controller is added.

This closes the existing account-qualified observation requirement. It does
not make the caller's labels authoritative, downscope an opaque credential,
enable a writer or qualify installed adoption.

## Proposed optional operation

Add `--expected-account <login>` to explicitly typed native inventory and
snapshot operations. The option is proposed here; do not invoke it until the
selected executing provider advertises support.

The guarded selection requires the existing typed credential command and
explicit `--http-profile direct-v1`. Legacy token selection, omitted required
typed inputs and unsupported providers refuse before material acquisition.
Calls without the new option retain their existing defaults and do not add a
verification request.

Initial implementation supports Gitea, whose authenticated `GET /user` returns
the current login. GitHub App installation identities and ADO are not assumed
to follow that contract. A guarded request for an unsupported provider refuses
before credential acquisition or another native/provider fallback.

Before the first repository/PR read, the actual Gitea provider performs one
bounded authenticated actor GET through the same selected typed observation
and direct HTTP owners. Require HTTP success and one complete, unambiguous
nonempty login matching the expected account under Gitea's case-insensitive
login semantics. Missing, malformed, duplicate or wrong actor evidence refuses
the entire guarded operation. Never convert that denial into a partial
successful inventory or snapshot under the configured account label.

The GET must use the selected endpoint and credential binding. Preserve
endpoint/TLS/redirect restrictions and actual expiry/authorization checks.
Do not print raw credential, authorization header, response body or command
diagnostics to establish identity. Successful repository reads remain necessary;
actor equality alone grants no repository access, review or merge permission.

## Executing discovery and compatibility

Extend the existing side-effect-free inventory declaration with an optional
`observation_account_guard` object:

```json
{
  "version": 1,
  "option": "--expected-account",
  "providers": ["gitea"],
  "http_profiles": ["direct-v1"],
  "inventory_extra_requests": 1,
  "snapshot_within_credential_budget": true
}
```

This declaration describes supported execution, not authenticated identity or
permission. Discovery selects no account, repository or credential and performs
no forge request. Reject attempts to combine an account selection with
contract-only discovery rather than echoing it as verified support.

Existing inventory declaration fields, ordinary inventory/snapshot result
schemas, stored state and typed credential envelopes are unchanged. Existing
readers retain their supported optional-metadata behavior. New callers check the
selected executable's actual declaration before choosing the new syntax;
missing/rolled-back support makes that guarded lane unavailable without a
write probe, ambient account or fallback to an unguarded operation.

## Bound accounting

The actor GET consumes the same native credential/acquisition accounting as
the repository reads. It is not a hidden second send behind an acquisition.
The selected direct profile's existing one-send bound remains unchanged.

Guarded inventory has at most the selected page count plus one actor request.
Thus a four-page caller reserves at most five native GETs under the qualified
direct profile. Preserve its existing page/member, byte and absolute-deadline
bounds; the verification request does not count as an inventory page.

For snapshots the actor request is **inside** the existing maximum of 64
acquisitions and any selected lower `--max-credential-requests` ceiling.
Exhaustion after actor verification refuses before further material acquisition
or repository reads. Do not silently add an uncharged request or raise the cap.
Original outer reservations, deadlines, unknown outcomes and real cleanup
remain with their existing owners.

## Delivery and qualification

1. Review this contract before changing native parser/provider execution.
2. Implement the opt-in source path through the owners above, with unchanged
   old callers and guarded old/new provider refusal.
3. Qualify actual parser-to-provider-to-typed/direct composition using contained
   synthetic credentials and responses: correct/wrong actor, malformed or
   duplicate identity, HTTP authorization loss, lower request ceilings,
   verification-only exhaustion, original deadlines and value-free failures.
4. Prove discovery performs no acquisition, unsupported selections refuse before
   acquisition, unguarded operations perform no actor GET, inventory accounting
   adds one and snapshot accounting stays inside its original/lower ceiling.
5. Record source, release, selected installation and actual provider/adopter
   evidence separately. A fixture, declaration or merged source is not live
   account qualification or completion of the full parent effort.

No credential creation, token export, live forge operation, new listener,
deployment, source/resource release or parent completion is authorized here.
