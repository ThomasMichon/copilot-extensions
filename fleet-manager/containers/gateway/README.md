# Standalone Gateway image

This specification packages the exact local `fleet-manager` wheel with its
`service-gateway` extra. It does not install Copilot, plugins, a harness,
an enrollment provider or adopter inventory. The same installed extra exposes
the outbound `ServiceConnector` SDK for services that embed it; there is no
connector executable or installer API.

The build context is the repository root. `Dockerfile.dockerignore` admits only
package metadata/direct Python sources and the five canonical
`libs/versioned-runtime` build inputs. `setup.py` selects `standalone_build.py`,
which adds the four canonical runtime resource modules to the wheel. No Git
directory, tests, test environment, host HOME or synthetic keys enter the image.
The multi-stage Python 3.13 slim build uses uv 0.11.7 with a seven-day cutoff
for both isolated wheel build dependencies and extra/transitive dependencies.
The runtime contains the noneditable wheel environment, not uv or its sources.

## Runtime contract

The default foreground command is the existing supported CLI:

```text
fleet-manager serve --role gateway --service-policy /config/policy.json --tls-config /config/listener.json
```

Provision `/config` and externally referenced TLS files read-only.
`/state` is the declared writable HOME/work directory, not a claim that the
process-local accepted policy revision is durably fenced across restart.
The default UID/GID is `10001:10001`; an operator may select a different
non-root UID with appropriate file ownership. Use a read-only root filesystem
and explicit bounded `/tmp` and `/state` storage. There are no automatic
volumes, published ports, host mounts or network setup in this image.

The image deliberately has **no healthcheck**: thin-client diagnostics cannot
prove listener readiness, role availability or current policy revision.
Deployments must select their own probes; the isolated witness independently
verifies the TLS challenge and authenticated service requests.
See the [service profile](../../docs/gateway-service-profile.md)
for policy, TLS, trust and restart limitations.
The installed [Cloudflare fragment renderer](../../docs/gateway-cloudflare.md)
supports opt-in external-origin binding; cloudflared and identity-provider login
are not included in the image.

A service image may derive from this base, add its own application, override
the entrypoint, and embed the installed SDK with an async token refresh callback
and explicit read-only proof-key/token/CA references. The
[witness fixtures](../../../tools/clean-room/gateway-role-tunnels/README.md)
demonstrate that composition separately; they are not production image content.

## Qualification boundary

`linux/amd64` and `linux/arm64` are intended build targets, not an assertion that
either was built or run by adding this specification. The explicit witness
currently requires an existing native amd64 Linux engine and records the image's
inspected architecture. ARM64 build, emulation and native execution remain
separate evidence. Do not install privileged emulation to make that gate pass.
This is not Cloudflare compatibility, managed installation or live deployment.
The [adoption-readiness record](../../docs/gateway-adoption-readiness.md)
separates the completed native amd64 proof from open architecture, provider,
lifecycle and live-activation gates.
