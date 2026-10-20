# Gateway role tunnels - inception record

Back to [the canonical effort](README.md).

These selected operator excerpts capture the portable request. They are not a
complete transcript. Private repository or host identifiers are replaced only
where marked; deployment-specific identities and access policies remain with
the adopter. Assistant design choices are separately labelled in the plan.

## Repository-owned desired state

The private repository name is replaced by `[owning repository]` below.

> I expect the Gateway to be controlled kubernetes-style: the owning repo ([owning repository] in this case) specifies the API routes and service configurations we expect to have, assigns roles and key sources, and thus gives the Gateway its manifest of what to expect. The integrated services deploy and then reach out, and Gateway is thus epxecting them. For Gateway, it should generally be a matter of live-updating its config, and not forcing as whole redeployment. The config would say "Service X is at /_api/x, its connection key signature is ____" or whatever.

The surrounding direction requires authenticated outbound service connections
and a unified protected API/UX surface. A configured signature or key reference
is an identity binding, not permission for a service to invent its own namespace.
The cryptographic mechanism and schema are implementation recommendations, not
specified by this example.

## Portable core and thin wrappers

> Right. Upstream, the Gateway core, configuration language, base container spec, and Cloudflare adapter can live. We'll make a separate repo to host the Home Assistant bit. By goal is that the Gateway is nearly-entirely live-config driven, so that it rarely if ever needs a redeployment even as other services come and go.

The operator subsequently selected an existing Komodo/Periphery-capable Linux
host for initial development and explicitly deferred Home Assistant integration
and private app-repository distribution. Its private machine identity is not
needed by this portable campaign. Actual container-manager readiness remains
something to establish, not an instruction to claim an unimplemented deploy API.

## Execution authorization

> Great. Start planning some initial phases for the unified (reusable) Gateway and then build them

Scope confirmation:

```text
effort_slug=gateway-role-tunnels
```

Live infrastructure changes retain their ordinary exact approval gates. Planning
and implementation do not themselves authorize DNS, edge policies, credentials,
firewall changes, production service replacement or a second Gateway authority.

## Independent transport and Gateway authentication

Public-safe summary of the operator's subsequent clarification; the private
reference deployment context is omitted:

Tunnel transport does not directly authenticate a caller to the Fleet. The
Gateway owns its application authentication independently. The same separation
should support Cloudflare Tunnel/Zero Trust and Authelia, with Entra as another
Gateway identity-provider option.

Implementation boundary (agent-recommended within the existing initial scope):
the portable ingress slice remains transport-only; real provider interoperability
is separately tracked.
