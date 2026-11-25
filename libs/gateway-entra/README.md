# agent-gateway-entra

> Gateway user-caller driver: Entra trust acquisition for explicitly configured Gateway user callers.

This is a Gateway driver library for the Fleet `user-bearer-v1` caller boundary
described in [Explicit user bearer callers](../../fleet-manager/docs/gateway-user-callers.md).
It keeps the selected Entra application as the authority rather than
creating an issuer, enrolling a machine signing key or replacing user-side
credential refresh. It is independently packageable Python 3.10+ source
(import name `gateway_entra`), not a Gateway server, installer, login client or
daemon. Installing or importing it performs no network or credential operation.

## Install

The package has no third-party runtime dependencies. `EntraUserProvider` also
needs `fleet-manager` with its `runtime-authentication` extra from the same
copilot-extensions revision. Neither package is published to a public index;
install both from the same pinned commit, for example in a test requirements
file:

```text
agent-gateway-entra @ git+https://github.com/ThomasMichon/copilot-extensions@<commit-sha>#subdirectory=libs/gateway-entra
fleet-manager[runtime-authentication] @ git+https://github.com/ThomasMichon/copilot-extensions@<commit-sha>#subdirectory=fleet-manager
```

Pin `<commit-sha>` to a full commit on `dev`; do not use a branch name. This
package does not declare `fleet-manager` as a dependency, so a resolver never
substitutes a same-named package from another index.

## What it does and how to use it

| Python entry point | Result |
|--------------------|--------|
| `EntraApplication(tenant, audience, required_role, required_scope)` | Explicit tenant, application audience, required role and delegated API scope |
| `fetch_trust(application)` | Immutable public RSA JWKS, exact issuer/audience and finite trust expiry |
| `EntraUserProvider(application)` | Pure Fleet `IdentityProvider` registration and strict Entra caller-policy construction |

An embedding owner supplies its existing application configuration:

```python
from gateway_entra import EntraApplication, fetch_trust

application = EntraApplication(
    tenant="<tenant-guid>",
    audience="<application-guid>",
    required_role="GatewayClient",
    required_scope="example.read",
)
snapshot = fetch_trust(application)
```

With a capable Fleet SDK, register that trust through its existing user boundary:

```python
from gateway_entra import EntraUserProvider

provider = EntraUserProvider(application)
provider.publish(snapshot)
user_callers = provider.user_callers(
    mappings=[{"subject": "<user-object-guid>", "principal": "operator-a"}],
    callers=[{"principal": "operator-a", "slots": ["local-control"], "methods": ["POST"]}],
)
```

The Gateway owner supplies `provider.identity_provider` as
`ServiceAuthority(user_identity_provider=...)` and `user_callers` in its complete
version-5 policy. This uses the common `user-bearer-v1` signature verifier, not a
provider-created authenticated identity. It requires a scalar `tid`, list-valued
`roles` containing the selected application role, and an explicitly mapped
scalar user `oid`. It also requires the selected scalar `scp` claim, which
[Entra includes only in user access tokens](https://learn.microsoft.com/en-us/entra/identity-platform/access-token-claims-reference).
Signed ID tokens can share the same audience, tenant, user and roles but cannot
meet this delegated access-token requirement. `required_scope` is the existing
approved API permission, not permission to create a scope or grant consent.
This bounded profile accepts one exact delegated scope; multi-scope token
strings explicitly refuse rather than being rewritten or broadly admitted.
Older SDKs without explicit claim shapes refuse
`unsupported-user-profile`; they never fall back to machine enrollment.

`publish` validates and selects public trust but does not publish Gateway policy.
The hosting owner must atomically select the matching snapshot reference and
complete policy at an admitted new revision. A changed reference refuses stale
preparation; an invalid candidate leaves the previous bounded serving verifier
with its existing owner. Refresh does not extend a stored snapshot's expiry.

The placeholder identifiers must be replaced with the selected tenant's and
API application's canonical GUIDs. This profile supports only v2 access tokens,
whose `aud` is the API's application (client) ID GUID, not its App ID URI.
`api://<application-guid>` audiences and v1 issuers are unsupported.
The existing API registration must select
[`api.requestedAccessTokenVersion: 2`](https://learn.microsoft.com/en-us/graph/api/resources/apiapplication?view=graph-rest-1.0).
Using a v2 token-acquisition endpoint alone does not select the access-token
version; the resource registration controls it. This adapter does not change
that registration.

Acquisition uses verified HTTPS at the fixed tenant-specific
`login.microsoftonline.com` discovery-key endpoint, refuses redirects, bounds
the response and key set, rejects private keys and unsupported key operations,
and emits only canonical public RSA verification fields. One caller-contained,
headless child bounds DNS, connection, headers and slow-progress response reads
by the total acquisition timeout, then settles on success or refusal. A
child-local hard deadline is armed before reading input, so parent termination
cannot leave an unbounded acquisition worker. Normal completion cancels and
joins that worker's deadline thread. It is not
a daemon, a general URL fetcher or an independent credential process.
Trust expires at most 300 seconds after acquisition began. Refresh is explicit and belongs outside
pure verifier/provider callbacks.

## What this driver provides and what it does not

**Provides:** configured public-cloud Entra application selection and bounded
public-key acquisition. The returned snapshot is public trust input, not an
authenticated caller, service identity or operation permit.

**Does not provide:** access-token acquisition, app registration, consent,
credential storage, user refresh pumps, background discovery, route grants,
Gateway installation, serving-host selection or deployment. It does not
translate an ID token, unrelated resource token, cookie, forwarded identity,
OS account or health observation into Fleet identity.

**Assumes:** an already selected Entra application, a trusted configuration
owner and normal verified HTTPS reachability. Sovereign-cloud endpoints are not
supported by this public-cloud adapter.

The common Fleet caller verifier must still verify each token's RSA signature,
issuer, exact audience, lifetime, selected tenant and required application role,
selected delegated access-token scope, then resolve the configured user mapping
and current route/operation grants.
`fetch_trust` performs none of that caller admission. Existing machine/service
proof profiles remain separate and do not accept this snapshot as a caller.

## Dependencies and validation

The trust-acquisition package has no third-party runtime dependencies.
`EntraUserProvider` additionally requires the Fleet SDK (`fleet-manager`) with
`user-bearer-v1`, explicit scalar/list claim requirements and its
`runtime-authentication` extra. A serving Gateway takes both packages from its
reviewed artifact closure through its normal lifecycle owner, not by importing
repository checkouts in a persistent service.

Run the focused package tests from the repository root:

```powershell
tools/run-tests gateway-entra
```

The runner installs this package with its `dev` extra and the repository's
`fleet-manager[runtime-authentication]` source in an isolated uv Python 3.11
environment.
Real synthetic RSA/common-caller tests verify the constructed
provider and scalar-tenant/list-role constraints, literal user mapping, current
grant withdrawal and snapshot refusal. No SDK identity or cryptography is mocked.

Synthetic response tests cover bounded acquisition, duplicate/private keys,
unselected algorithms, redirects, unavailable transport and clock changes.
A real tenant-key endpoint check proves public signing-trust acquisition only:
it is not actual token/provider interoperability, user authentication or
deployment acceptance.

## What's in this package

- `src/gateway_entra/trust.py`: application selection and explicit public-trust acquisition.
- `src/gateway_entra/user_provider.py`: pure common-verifier registration and typed Entra caller constraints.
- `src/gateway_entra/__init__.py`: independently packageable Python API.
- `tests/test_trust.py`: focused acquisition/refusal contracts.
- `tests/test_user_provider.py`: real common-verifier provider and caller-policy contracts.
- `pyproject.toml`: source-package and test configuration.

The public API is the `gateway_entra` package exports plus `gateway_entra.trust.fetch_trust`.
`gateway_entra.trust._public_keys` is private. Existing consumer tests import it,
so it keeps its name and behavior in this release, but it is not a stable API.

## Troubleshooting, contributing and issues

`invalid-application` means the configured tenant, audience or role is outside
the supported application shape. `public-trust-unavailable` means the selected
HTTPS endpoint could not supply trust; retain the existing finite snapshot only
while its independently checked lifetime remains valid. `invalid-public-trust`
or `invalid-clock` is a refusal, not permission to extend expiry or substitute
another issuer.

Contribute through this repository's [contribution policy](../../CONTRIBUTING.md)
and file defects in its issue tracker. Application selection, private policy and
deployment evidence belong to the adopting operator, not this repository.

Standing intent: [Entra Gateway user authority](../../visions/machine-fleet/gateway/entra/README.md).
