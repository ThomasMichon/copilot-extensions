# Execution-scoped, on-demand host resources (version 1)

This optional native capability reuses the native execution's authenticated
provider transport and durable identity. It introduces no permanent service,
listener, remote host-wide credential, or browser policy. It realizes
`session-hosting`'s host-owned mechanics and capability-honest control, and
`agent-bridge`'s durable, frontend-independent ownership.

## Local launch registration

The trusted local launcher adds `--host-resources-file PATH` to the existing
`agent-bridge native start` command. The UTF-8 JSON file is snapshotted before
launch admission:

```json
{
  "schema": "copilot-extensions.native-host-resources",
  "version": 1,
  "resources": {
    "preview": {
      "argv": ["C:\\Tools\\node.exe", "C:\\Tools\\preview-provider.mjs"],
      "config": {"listenPort": 45123},
      "inputSchema": {
        "type": "object",
        "properties": {},
        "additionalProperties": false
      }
    }
  }
}
```

`argv[0]` is an absolute executable, not a shell command. Arguments and `config`
are trusted local launch policy and are never published to the remote descriptor.
At most 8 resource names are accepted. `inputSchema` is a closed object schema:
at most 16 properties of type string, boolean or integer, optional `required`
and primitive `enum`; nested schemas and additional properties are rejected.
Strings are at most 4096 characters; all input is at most 16 KiB. Omitted
`inputSchema` accepts only `{}`.
The first ensure fixes the resource input for that execution lifetime; a later
ensure cannot reinterpret an existing owned resource using different input.

Startup validates/snapshots the definition but runs **no resource provider** and
creates no resource process, profile or resource-state directory. No-resource
launches preserve their existing wire shape and behavior. An applicable resource
capability is negotiated before launch; version-skew refusal never silently
discards registered resources.

The launcher may predeclare an ordinary managed reverse forward before its
destination listens, using existing `--reverse-forward REMOTE:HOST`. Choose
the ports locally and include the fixed mapping in provider config. Forwarding
does not create the destination service. Version 1 does not let remote requests
choose or add commands, profiles, paths, ports or forwarding destinations.

## Native-session descriptor and callable

Remote native startup sets `AGENT_BRIDGE_NATIVE_RESOURCES` to a private file
containing a **non-secret capability descriptor**, not a running-resource claim:

```json
{
  "schema": "copilot-extensions.native-host-resource-capability",
  "version": 1,
  "capability": "native-host-resources-v1",
  "executionId": "EXECUTION",
  "generation": "GENERATION",
  "resources": {
    "preview": {
      "ensureCommand": ["<official-remote-python>", "-m", "agent_bridge", "native", "resource", "ensure", "preview", "--json"],
      "inputSchema": {"type": "object", "properties": {}, "additionalProperties": false}
    }
  }
}
```

Run the descriptor's exact `ensureCommand` from the real native session.
`agent-bridge native resource descriptor --json` also returns that descriptor.
`ensure` optionally accepts `--request-id KEY`, `--input-file PATH`, and
`--timeout SECONDS` (1-120, default 120). Without a request ID it generates one;
retries with an explicit ID must retain the same resource and input. No local
terminal intervention, new Copilot session or presentation attachment is needed.
At most 256 distinct request IDs are retained per execution. A completed
successful ID replays its recorded result; reuse a new ID when requesting a fresh
ensure check. An identical failed request can be retried using its original ID.

The helper verifies execution/generation, the real registered session and its
process ancestry, durably records the request on the venue, and waits for a
bounded reply. The controller consumes pending requests through its existing
native status poll, invokes only the registered provider, and returns the result
over the same identity-bound provider control channel. No remote request carries
host provider configuration or a host-wide bearer. Request timeout preserves the
request; it does not cancel the native execution or create a replacement.

Success is JSON:

```json
{
  "schema": "copilot-extensions.native-host-resource-result",
  "version": 1,
  "executionId": "EXECUTION",
  "generation": "GENERATION",
  "sessionId": "REAL_SESSION",
  "requestId": "REQUEST",
  "resource": "preview",
  "state": "ready",
  "value": {"endpoint": "http://127.0.0.1:45123"}
}
```

`value` is provider-owned public data; consumers must validate their domain
schema and actual service identity. A capability is not readiness. Invalid,
foreign, stale, unregistered or stopping execution requests fail explicitly.
Resource failure does not change ordinary native coding readiness.

## Local provider protocol

The controller invokes the registered argv in the native launch owner's local
directory. It sends **one bounded JSON request on stdin** (not command-line
interpolation) and expects one JSON response on stdout. Stderr is not forwarded
to the native session. Calls are bounded to 90 seconds and serialized across
controller generations by an OS-owned per-resource lock. There is no provider
process until ensure is requested.
Longer-lived resource children must not retain the provider's stdin/stdout
request/reply pipes; the provider returns one complete JSON response and exits.

Request:

```json
{
  "schema": "copilot-extensions.native-host-resource-provider",
  "version": 1,
  "operation": "ensure",
  "executionId": "EXECUTION",
  "generation": "GENERATION",
  "resource": "preview",
  "operationId": "STABLE_SCOPE_ID",
  "stateDir": "<controller-owned-resource-directory>",
  "config": {"listenPort": 45123},
  "input": {},
  "previous": null
}
```

`operationId` and `stateDir` remain stable for this execution/generation/resource,
including controller restart and lost acknowledgements. `previous` is the last
opaque provider receipt, or null when no reply was durably received.

Ensure response:

```json
{
  "schema": "copilot-extensions.native-host-resource-provider",
  "version": 1,
  "executionId": "EXECUTION",
  "generation": "GENERATION",
  "resource": "preview",
  "operationId": "STABLE_SCOPE_ID",
  "ok": true,
  "owned": true,
  "value": {"endpoint": "http://127.0.0.1:45123"},
  "receipt": {"providerOwnedIdentity": "opaque"}
}
```

The provider **must journal ownership in `stateDir` before side effects**, make
ensure idempotent under the stable scope, and reconcile that journal even when
`previous` is null. It must verify actual process/service identity before reuse;
PID or port alone is not ownership. Concurrent requests, worker loss, host restart
and retry must not duplicate a resource or adopt another scope's resource.
`owned: true` means this scope owns it, including an earlier creation recovered
after restart; it does not mean "created by this invocation."

On generation-bound native stop, the controller sends `operation: "release"`
with the same scope/config/stateDir and last receipt. Successful release returns
the same identity envelope with `ok: true, released: true`. A never-requested
resource receives no release call. A proven unowned resource is not released.
An attempted ensure with a lost reply receives release so the provider can
reconcile its durable journal and remove **only resources it created**.
Release must be idempotent, including after a lost acknowledgement. Failed
cleanup remains a durable obligation and prevents reporting clean native stop.
Frontend detach and controller redeploy do **not** release resources.

## Capability and compatibility

`native capabilities --json` advertises `hostResources: "native-host-resources-v1"`.
The optional launch field is `hostResources` containing the file's JSON object.
The provider/remote host advertise the same capability on their existing native
capability responses. No browser, product, model or organization behavior is
implemented by this primitive.
