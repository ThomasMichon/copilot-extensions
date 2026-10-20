# Transport-only Cloudflare ingress

Back to the [service-role Gateway](gateway-service-profile.md).

## Independent transport and authentication

The tunnel provides reachability, optionally protected by Cloudflare Zero Trust
Access. Neither reachability nor edge admission authenticates a caller to the
Fleet. The Gateway owns application authentication independently of its tunnel;
Entra and Authelia are intended provider options, not required tunnel components.
The current runtime supports the documented common JWT/DPoP profile and explicit
caller grants. It does not yet implement either provider's login/token flow or
translate an Access assertion into a Fleet caller.

Every routed request still needs its actual Fleet access token and method/path
proof. Service connectors separately prove their assigned role. Access JWT,
email, `Forwarded` and `X-Forwarded-*` headers are not enrollment, caller identity,
route permission or administration authority and are not sent to the backend.
An ordinary logged-in browser without Fleet credentials therefore still refuses;
this adapter is not a completed browser-login feature.

## Explicit public origin, verified private origin

Keep `origin` as the private TLS endpoint and add `external_origin` to the
version-1 listener document:

```json
{
  "schema": "fleet-manager.gateway-service-listener",
  "version": 1,
  "fleet": "example-fleet",
  "gateway": "example-gateway",
  "host": "127.0.0.1",
  "port": 8443,
  "origin": "https://localhost:8443",
  "certificate": "/config/origin.pem",
  "key": "/config/origin-key.pem",
  "remote": false,
  "external_origin": "https://gateway.example.com"
}
```

The public origin has no path, credentials, query or fragment. Its authority is
canonical lowercase DNS or canonical IP notation; omit the default `:443`, and
do not use a zero port, trailing dot or wildcard. Generic proxy mode supports an
explicit non-default public port; the Cloudflare renderer requires a DNS hostname
and the default HTTPS port.

The configured public origin, never request forwarding metadata, is the PoP
target for both service admission and callers. All incoming HTTP and WebSocket
requests in this mode must present exactly one `Host` matching its authority,
including a non-default port when selected. The private listener still requires
TLS, exact certificate SAN/port agreement and explicit remote-bind consent.
Changing either bootstrap origin requires a controlled restart. The SDK's
returned `Serving.origin` continues to describe the actual private listener;
connectors and callers instead select the external origin.

Host validation is **not** proxy authentication or private-origin isolation:
any client can send that Host. The adopter must restrict the origin to the
tunnel's network/process boundary, provision trusted TLS on both legs and apply
any chosen edge policy. A direct client with valid Fleet credentials and the
public Host is not rejected merely because it bypassed the edge. Do not expose
the private listener on a public network or claim mandatory Access enforcement
from this configuration alone.

## Render an owner-managed ingress fragment

With the optional `service-gateway` dependencies installed:

```text
python -m fleet_manager.gateway_cloudflare --tls-config listener.json --ca-pool /config/origin-ca.pem
```

This reads/validates the listener and certificate and prints a JSON-compatible
YAML fragment containing only `ingress`. The CA path names the file as seen by
cloudflared, not necessarily the renderer's filesystem; the renderer does not
read it. Omit `--ca-pool` only when cloudflared's default trust store validates the
origin chain. The fragment contains no certificate private-key reference:

```json
{
  "ingress": [
    {
      "hostname": "gateway.example.com",
      "service": "https://localhost:8443",
      "originRequest": {
        "originServerName": "localhost",
        "httpHostHeader": "gateway.example.com",
        "caPool": "/config/origin-ca.pem",
        "noTLSVerify": false,
        "http2Origin": false,
        "disableChunkedEncoding": false
      }
    },
    {"service": "http_status:404"}
  ]
}
```

Merge this fragment into the tunnel owner's supported locally managed
configuration, retaining that owner's tunnel and credential custody.
`originServerName` verifies the private certificate independently of the public
Host. HTTP/1.1 and chunking preserve the service WebSocket channel and bounded
response streaming; paths and opaque queries must be forwarded without rewrite.
Do not add a cache rule that overrides protected responses' `no-store`.
Tunnel replacement can drop WebSockets: connectors reconnect under their
bounded retry policy, and lost mutations are not silently retried.

The module neither installs/runs cloudflared nor creates an account, tunnel,
DNS record, Access policy, key or network rule. It prints no tunnel credentials
and writes no files. The owner validates the merged configuration with its
supported cloudflared version before activation. Network-separated cloudflared
must be able to resolve/reach the private origin and read its selected CA file;
the loopback example assumes both processes share that network boundary.

## Qualification boundary

`tests/test_gateway_service_ingress.py` covers strict listener/renderer/CLI
behavior and a synthetic dual-TLS HTTP/WebSocket proxy with real Fleet proofs.
It verifies two outbound services, API/HTML/query and unfinished SSE, refusal of
private-origin proofs, wrong/duplicate Host and forwarded-only identity.
Both TLS hops verify their configured certificates; no live provider is involved.
The direct-TLS portfolio remains the compatibility baseline.

This is not vendor-account or deployed Cloudflare/Access/Entra/Authelia evidence.
Native ARM64, provider login, origin network isolation and live adopter rollout
remain separately qualified gates in the
[adoption-readiness record](gateway-adoption-readiness.md).

Field semantics follow Cloudflare's
[origin parameters](https://developers.cloudflare.com/tunnel/reference/origin-parameters/)
and [locally managed configuration](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/configuration-file/).
