# Standalone PR watch service

`agent-pull-requests-service` hosts the existing PR replay store, Gateway handler,
connector and native observation cycle. It runs without Copilot, marketplace
hooks or an interactive client session. It is not a deployment controller,
credential issuer, second registrar, arbitrary callback runner or native-daemon
installer. Native PR commands remain independent of this distribution.

Install its wheel and explicit dependencies through the normal seven-day
dependency policy. The native snapshot executable is a separately selected
Worktrees installation/artifact, addressed by the trusted host configuration;
this service's Python dependency resolver must not select its installation or
substitute an unrelated published package. Released wheels have ordinary versioned
dependencies, not paths escaping into a plugin checkout. Repository development
tests select the same checkout's PR and Fleet sources explicitly:

```text
python tools/run-standalone-tests.py services/pr --prepare --timeout 180
```

## Explicit host inputs

The trusted launch owner supplies an absolute JSON configuration reference:

```json
{
  "schema": "agent-pull-requests.service",
  "version": 1,
  "root": "/data/pr-watch",
  "namespace": "installation-a",
  "fleet": "fleet-a",
  "gateway": "gateway-a",
  "slot": "pr-watch",
  "gateway_origin": "https://gateway.example.test",
  "proof_key_file": "/run/secrets/service-proof.pem",
  "access_token_file": "/run/secrets/service-access",
  "gateway_ca": "/run/config/gateway-ca.pem",
  "bindings": [
    {
      "principal": "caller-a",
      "target": "repository-a",
      "consumer": "dispatch-a",
      "account": "account-a",
      "repo": "example/repository"
    }
  ],
  "observations": [
    {
      "account": "account-a",
      "repo": "example/repository",
      "command": "/usr/local/bin/agent-worktrees",
      "credential_command": [
        "/usr/local/bin/fleet-manager",
        "credential",
        "--config", "/run/config/credential-worker.json",
        "--source", "forge-reader",
        "--account", "account-a",
        "--forge", "{\"provider\":\"github\",\"api_base\":\"https://api.github.com\",\"repository\":\"example/repository\",\"permission\":\"observe\",\"authentication\":\"bearer\"}"
      ]
    }
  ],
  "interval": 5,
  "operation_seconds": 60,
  "drain_timeout": 60
}
```

Use the credential worker's actual
[`ForgeScope` contract](../../fleet-manager/docs/gateway-runtime.md) for the
selected provider; the native snapshot and worker must agree exactly. The
example identifiers are not enrollment, provider-policy or placement grants.
All executables and credential/configuration references are explicit absolute
paths. An optional immutable `prefix` argv selects an attributable interpreter
invocation instead of the native executable. `credential_command` is required:
this host never falls back to a logged-in CLI or another account.
These are trusted deployment inputs, never fields accepted from subscribers.
Do not put tokens or arbitrary secrets in command arguments or configuration.

Configuration is bounded to 1 MiB and 128 entries per binding collection.
Unknown/duplicate fields, ambiguous scope mappings and missing observation
scopes refuse. Interval, operation and drain budgets are integer seconds in
1..300. The operation budget is a local transaction deadline, not token expiry
or a fabricated placement lease.

The explicit launch authorizes only this foreground process's local work.
SIGINT/SIGTERM withdraw later work and signal the existing bounded drain.
Real Gateway TLS/PoP admission authorizes service attachment and each remote
caller's lifetime; the existing typed credential worker authorizes forge reads.
Neither labels nor decoded JWT claims grant these permissions. Service access
tokens are reread on every admission; the external P-256 proof key is loaded
once, so key rotation requires a new connector/process. No credential enters
durable replay state or readiness diagnostics.

## Provision, serve and observe

```text
agent-pull-requests-service provision --config /run/config/pr-service.json
agent-pull-requests-service serve --config /run/config/pr-service.json
agent-pull-requests-service ready --status /data/pr-watch/service-status.json
```

Provision is an explicit first-creation operation and never repairs/replaces
existing state. Serve adopts existing valid state; it does not provision it.
Use a dedicated root, never the native watch owner's state. One host-local
writer lease covers the actual observation/serving work. A failed/timed-out
drain does not release ongoing in-process custody or promise rollback.
An ambiguous durable request reconciles its original request/registration/event
identity; disconnect or revocation is not proof that an in-flight commit failed.
The native snapshot command retains its existing bounded execution contract.

Readiness is a passive local snapshot with at most three seconds' freshness.
Only the held writer publishes it. Missing/stale/future/corrupt snapshots fail
the probe; readiness requires connected serving and a successful observation
cycle with no unavailable/unbound targets. This is not a placement, promotion,
safe-update or cross-host fencing receipt. The host opens no additional
unauthenticated HTTP listener. Its supervisor owns restart and escalation.
CLI failures name the operation and exception class without echoing external
references, provider error details or credential material.

## Independently constructed container

`container/Dockerfile` is an offline wheel-only recipe, not an image published
or activated by this repository. A trusted builder supplies:

- An explicitly approved, digest-pinned Python Linux base.
- `wheels/`, containing the service and complete platform-matching dependency
  closure, built from the approved release and seasoned external artifacts.
- `requirements.lock`, the complete exact-version, SHA-256-hashed closure,
  including `agent-pull-requests-service` and the separately approved
  `agent-worktrees` artifact supplying the configured snapshot command.

Use a **dedicated context containing only those inputs and this Dockerfile**.
Never use a developer checkout, home, credential directory or replay volume as
the context. Installation is network-free, wheel-only and hash-required; image
construction never reads host configuration or credentials. The resulting
image runs as UID/GID 65532, with a read-only image filesystem and one
explicitly provisioned writable persistent volume at `/data`. Mount service
configuration and credential references separately, read-only. No Docker socket
or host source mount is required.

The deployment owner supplies the image digest and configuration/secret/volume
references using the existing
[`container.service` role contract](../../fleet-manager/docs/role-deployment-plans.md);
`container/role-bindings.example.json` shows that namespace, not a new adapter.
The provider projects references only: it neither grants placement nor proves
installation. Supply a stop grace period greater than `drain_timeout`, and
verify actual work settlement before transferring the volume. Never run two
writers or move a live local volume to another host on the strength of its
host-local lock.

Image construction, fresh-container restart/drain, fleet placement,
cross-host fencing, safe update/rollback/interrupted cutover and owned uninstall
require their own real proofs. A wheel install, source fixture, passive status
file or role plan does not satisfy them. The existing serial observation cycle
also does not yet qualify unrelated slow-target fairness.

On Windows a concurrent passive health reader can prevent atomic status-file
replacement. That sharing refusal skips one publication; it does not stop the
watch owner or extend the prior snapshot's expiry. A prolonged refusal therefore
becomes unready, never fabricated healthy evidence.
