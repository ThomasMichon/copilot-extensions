# Delegated machine enrollment and selected continuity

Back to the [service Gateway](gateway-service-profile.md), [operation
journal](operation-ledger.md) and [repository setup
consumer](../../plugins/agent-worktrees/docs/repository-setup.md).

This opt-in source implementation supplies first-touch **caller** enrollment
and authenticated outbound Gateway access. It does not issue OAuth/application
tokens, log in to a sync provider, install a runtime, configure SSH, implement
human SSO or deploy a controller. `jwt-pop-v1`, `enrolled-pop-v1` and their
defaults remain unchanged. An ordinary provider session is not a machine proof.

## Explicit public-key profile

Service-policy version `4` selects `machine-key-v1`. Its `identity` has exactly
`profile`, `audience` and `trust_valid_until`; no issuer/JWKS is inferred.
The existing manifest, public `bindings` and caller grants retain their
meanings. `enrollment` has exactly `mappings`, each with:

```json
{
  "principal": "machine-a",
  "kind": "caller",
  "resource": "service-a",
  "key_source": "machine-a",
  "expires_at": 1900000600
}
```

`resource` is an actual service slot. A static mapping may also select `service`
for an outbound connector. Every mapping must match its configured principal,
key source and slot; enrollment does not grant arbitrary application operations.
An `AdmissionAuthority` must separately opt into `identity_profile="machine-key-v1"`
with `issuer="machine-key-v1"` before accepting this identity for operations.
Default admission profiles do not broaden.

The maintained JOSE verifier accepts only ES256/P-256 and
`fleet-machine-proof+jwt`, bound to the currently published public key.
Proof claims are exactly `sub`, `aud`, `kind`, `resource`, `htm`, `htu`,
`sha256`, `iat`, `nonce` and `jti`. `htu` includes the full canonical external
path and query; `sha256` is the lowercase SHA-256 of the actual buffered body.
The Gateway derives method/target, reads the bounded body once and reuses those
same bytes for dispatch. Forwarding headers cannot choose the proof authority.
Nonce epochs, monotonic time checks, bounded atomic replay tracking and
capacity refusal reuse the existing proof fence. Restart chooses a new nonce.
Adding an unrelated machine keeps the current nonce/replay fence and unchanged
binding grants, so existing connector channels and in-flight requests survive.
Changed/withdrawn bindings invalidate only their own retained work; a changed
audience or trust deadline replaces the proof epoch.

Actual machine-mode discovery is `GET /_fleet/machine-challenge`, returning
exactly `{profile, nonce}`. The legacy challenge's `{nonce}` shape is unchanged.
Machine requests use `Authorization: Fleet-Machine <principal>` and
`Fleet-Machine-Proof`; the principal is not a bearer credential. Mixed proof
headers refuse. `ServiceCaller` selects the mode only with explicit
`machine_principal`, `machine_audience`, `machine_resource` and a local JOSE
private key, without an access-token callback. `ServiceConnector` accepts the
same explicit principal/audience selection for its configured service slot.
Neither client guesses a profile or retries through the old token mode.

## Same-owner enrollment producer

The existing serving authority can select an envelope with schema
`fleet-manager.gateway-enrollment-policy`, version `1`, and exactly `policy`
and `delegations` in addition to those schema/version fields. `policy` is a
complete version-four base service policy. It must already validate; pending
callers are not fabricated as bindings in that base.

Each delegation has exactly `id`, `grant_sha256`, `grant_expires_at` and `scope`.
The hash is of a high-entropy, 43-character base64url grant conveyed through the
selected protected source, not stored in desired policy. The exact scope is:

```json
{
  "source": "source-a",
  "account": "account-a",
  "container": "container-a",
  "harness": "harness-a",
  "gateway": "gateway-a",
  "destination": "https://gateway.example",
  "machine": "machine-a",
  "principal": "new-caller",
  "generation": 1,
  "slots": ["service-a"],
  "methods": ["GET", "POST"],
  "expires_at": 1900000600
}
```

The grant deadline and machine-binding expiry are distinct. An expired bootstrap
grant cannot enroll; it does not by itself revoke a still-current admitted key.
Removing/changing the owner-authenticated delegation withdraws its published
binding. Expiry, current desired grants and request checkpoints still apply.
Discovery alone never provides enrollment authority.

`GET /_fleet/enrollment-challenge` advertises
`delegated-machine-enrollment-v1` and an enrollment nonce. `POST /_fleet/enroll`
accepts exactly `{intent, delegation, scope, public_key}` plus the
`Fleet-Enrollment-Grant` and `Fleet-Machine-Proof` headers, over authenticated
TLS. The public key is a minimal public P-256 JWK; no private key is uploaded.
The machine proves possession over the exact request, with kind `caller` and
resource `enrollment`. Grant, source/account/container/harness/destination,
machine, principal, generation, capabilities and expiry must match the
currently selected owner declaration before admission.

The command is genuinely `machine.enroll`, with an operation parameter
`enrollment` containing the canonical public binding digest. This is not
`workspace.allocate` or a new enrollment database. Raw grants, proofs and
private keys are absent from journal intents, bindings, receipts and diagnostics.
The serving owner composes desired grants with admitted public bindings and
validates the complete prospective policy **inside admission's transaction**.
Unpublishable keys, generated identifiers, aggregate limits and conflicts roll
back intent, claim, outbox and audit together.
Final owner/revision/trust-deadline eligibility also runs inside the publication
transaction, before accepted floors or withdrawal markers commit. Journal-writer
serialization is separate from the short serving-state lock; SQLite contention
must not block synchronous request authentication or the event loop.

Use the existing operation journal explicitly:

```text
fleet-manager serve --role gateway --service-policy enrollment-policy.json \
  --tls-config listener.json --operation-journal /private/operations.sqlite \
  --operation-authority authority-a --operation-installation installation-a
```

The selected journal directory/file must be ordinary and owner-private.
`OperationLedger(..., enable_enrollment=True)` explicitly migrates known schema
four to five in place, retaining existing operations/claims and adding
enrollment claims and accepted-policy floors. The default stays schema four
and refuses schema five. Old writers must reconcile through their lifecycle
owner; no downgrade, second database or automatic deletion is supplied.
Generic `admit` cannot bypass the enrollment transaction.

Machine/principal/key claims are exclusive within this Gateway. Repeating the
same operation and public binding returns that original admission; a new
operation or replacement key cannot silently take its place. Complete valid
publication retains revision/digest floors in this same journal, after matching
the serving fleet/Gateway. Rejected candidates cannot advance those floors.
Delayed/conflicting source copies and restarts cannot restore revoked policy.
Withdrawal also persists the machine binding's retirement revision; copying its
old delegation into a higher source revision cannot revive that generation.
Published revisions reserve 1,024 enrollment increments per configured base
revision; configured revisions are bounded before composition.

Only bindings in the actually installed context receive correlated success
receipts. Reload/startup reconciles receipt-less exact admitted bindings after
publication, even when their original bootstrap grant has expired. A lost reply
therefore resumes the original operation, not a new identity. Revoked or
superseded bindings are not falsely settled as newly published.
Receipt-storage failure does not undo or conceal an installed publication:
the transport receives the installed context and reports
`enrollment-settlement-pending` in status/logs. A later reload retries settlement
against that same exact installed context; it does not invent a new operation.
Transport adoption is generation-ordered even when concurrent reload workers
complete in reverse order; an older result cannot restore stale routing/status.
Claims/floors are retained; there is no automatic rotation, reclamation or
re-enrollment under another generation. Such recovery requires its owner's
explicit future recovery contract.

## Selected local continuity consumer

A trusted harness's version-two setup declaration names
`local-projection-v1` and one explicit absolute/home-relative source reference;
the [engine contract](../../plugins/agent-worktrees/docs/repository-setup.md)
owns selection and trust. The engine uses the Fleet SDK in **its own selected
interpreter**, with Python isolation, not an executable discovered on `PATH`.
Its state namespace hashes the canonical harness repository URL and selected
context name, remaining stable across checkout moves and source revisions while
separating different repositories that reuse the same context name.
The existing construction/release owner must supply the Fleet SDK and its
service-Gateway dependency closure in that selected engine environment.
This source slice does not add a cross-project path dependency to the plugin's
library-only installation graph or introduce another installer. Missing/older support
refuses only the optional context stage and retains completed Manager/core work.

The private source JSON has exactly `schema: "fleet-manager.continuity-projection"`,
`version: 1`, `provider: "local-projection-v1"`, `authority`, `audience`,
`delegation` (the exact public declaration above), `grant` and `gateway_ca`.
`gateway_ca` is null for platform trust or an explicit absolute CA reference.
Do not put this file in the harness repository or copy machine-bound state into
it. The selected projection is already authenticated/protected by its source
owner; this implementation adds no cloud login, OneDrive client or Git sync.
Shared/unowned files and symlink/reparse traversal refuse without permission
repair. A plaintext exported source must retain equivalent private custody.
Custody checks cover the opened reader's ownership/mode or native ACL, not just
a pathname that could be replaced between validation and reading.

The actual Fleet executable reports its implemented contracts with
`capabilities`. `bootstrap --source <absolute-file> --harness <context-name>
--state-root <private-directory>` checks existing readiness; `--apply` permits
first touch. No grant or private key travels in argv or the environment.
Receiver capability discovery precedes local key creation.
The single-winner private state file retains the unique key, selected source
digest and original operation; interrupted/corrupt/conflicting state refuses,
never rotates automatically. State is separate from the projection, repository
and session-managed folders.

An existing key first attempts current authenticated outbound access.
Only a missing connection and explicit apply may submit the original enrollment.
`POST /_fleet/machine-connect` verifies current key, principal and selected slot,
without invoking a backend or granting an application method.
The consumer reports ready only after this actual authenticated exchange.
Capability, admission, publication and connection remain separate outcomes;
none enables SSH, a provider profile, human SSO or a lifecycle effect.

## Qualification and release boundary

Focused controls cover real TLS caller/connector requests, changed body/query
refusal, proof replay/capacity, exact delegation scope, no credential echo,
prospective-publication rollback, rejected-owner floors, original-operation
restart recovery and source rollback refusal. A POSIX source qualification
uses real Manager -> engine -> isolated Fleet SDK processes, with a synthetic
grant and actual TLS enrollment/connect receiver. A conflicting `fleet-manager`
on `PATH` is not selected.

The full/manual `client_distribution_full` lane in
`tests/test_continuity_installed.py` builds a fresh noneditable
`Fleet[service-gateway]` SDK closure using the maintained seasoned build path.
An isolated `-I -S` process installs a checkout-read audit guard before package
imports, admits only that runtime's site directories, checks every installed
distribution's metadata/file inventory and noneditable location, and compares
the installed Fleet source bytes. An intentional checkout import must refuse.
Both finite CLI and normal Manager -> engine -> SDK scenarios enroll against
the real synthetic TLS receiver, restart that receiver at the same origin with
its original journal, reconnect with the unchanged machine-state bytes and
retain exactly one enrollment and operation. A conflicting source still refuses.
The Manager and engine drivers themselves remain source fixtures using their
prepared engine dependencies; those dependencies are not admitted into the
guarded SDK process. This qualifies the Linux installed **SDK**, not an
installed Manager/engine product or a released construction path.

These results are **not** native Windows/macOS provider qualification, approved
live enrollment, normal human navigation or managed deployment/soak evidence.
Providers expand first; callers activate
only for the selected declaration/capability. No old profile or declaration
generation is retired. A source merge does not install the optional SDK or
change any running Gateway.
