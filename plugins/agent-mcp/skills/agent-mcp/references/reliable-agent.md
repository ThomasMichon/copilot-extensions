# Reliable MCP-backed sub-agent

Use one reviewed agent-mcp bridge config to expose two equivalent surfaces:

1. the primary Copilot `mcp-servers` catalog;
2. a materialized CLI fleet used when that catalog fails to load.

The fallback is reliable only when both surfaces preserve the same auth source,
identity, tool filter, and authorization boundary.

## Bridge and agent

```yaml
# .github/agents/service.mcp.yaml
id: service
server:
  type: stdio
  npm: "@example/service-mcp"
  args: ["server"]
auth:
  kind: command
  command: ["credential-helper", "get", "service"]
  parse: raw
  target_env: SERVICE_TOKEN
tools:
  allow: ["service_read", "service_write"]
timeout: 120
```

```markdown
---
name: service
description: Manage Example Service.
tools: ["*"]
mcp-servers:
  service-mcp:
    type: stdio
    command: agent-mcp
    args: [bridge, --config, .github/agents/service.mcp.yaml]
    tools: ["*"]
---

## MCP Readiness

1. Probe `service_read`.
2. If the catalog did not load, preserve the exact primary error and use the
   existing `service` materialized fleet.
3. If the fleet is missing or stale, materialize the same bridge config.
4. Verify `manifest.json.bridge` resolves to the same config path used by the
   frontmatter.
5. Probe `service_read` through the stub and verify the expected identity before
   acting.
6. If both surfaces fail, report both errors and stop.
7. If fallback succeeds, include the preserved primary error in the final
   response.

Do NOT use the task tool to spawn another `service` agent.
```

Keep the anti-self-delegation line literal enough for customization scanners.
The fallback is not permission to call the product API directly.

Top-level `tools:` is the authorization boundary shared by bridge and CLI
calls. Decorator stacks are not applied by `agent-mcp call`/`materialize`.
Duplicate static name restrictions in top-level `tools:`. Conditional
authorization (`gate` or argument-dependent redaction) cannot be represented
there and therefore forbids materialized fallback. Shape-only decorators such
as `rename`, `defer`, `code-mode`, `transform`, and `storage` yield a wider raw
fallback catalog; document that surface explicitly.

Likewise, keep identity-affecting values in the shared bridge config or
machine-local overlay. A shell fallback does not inherit variables supplied
only by `mcp-servers.env`.

## Materialize once, invoke repeatedly

Materialize a standing fleet from a stable checkout or a plugin-shipped named
bridge. Avoid recording a disposable linked-worktree path in `manifest.json`;
if development requires one temporarily, re-materialize after the config lands
at its stable location.

POSIX:

```bash
ROOT="$(git rev-parse --show-toplevel)"
agent-mcp materialize \
  "$ROOT/.github/agents/service.mcp.yaml" \
  --server-name service

cd "$ROOT"
printf '%s' '{}' |
  "$HOME/.agent-mcp/materialized/service/bin/service_read" --no-serve
```

PowerShell:

```powershell
$root = git rev-parse --show-toplevel
agent-mcp materialize `
  "$root\.github\agents\service.mcp.yaml" `
  --server-name service `
  --windows

Set-Location $root
$request = Join-Path ([IO.Path]::GetTempPath()) (
  "service-read-{0}.json" -f [guid]::NewGuid()
)
try {
  Set-Content -LiteralPath $request -Value '{}' -NoNewline
  & "$HOME\.agent-mcp\materialized\service\bin\service_read.ps1" `
    --no-serve `
    --request-file $request
} finally {
  Remove-Item -LiteralPath $request -ErrorAction SilentlyContinue
}
```

Use a request file outside the repository for **all** Windows stub calls:

```powershell
& "$HOME\.agent-mcp\materialized\service\bin\service_write.ps1" `
  --no-serve `
  --request-file $requestPath
```

Do not pipe or pass inline JSON through Windows `.ps1`/`.cmd` shims: the
PowerShell shim does not forward pipeline input, and cmd.exe reparses quotes and
metacharacters. Use `.ps1 --request-file`.

## Failure classification

| Failure | Response |
|---|---|
| agent-mcp runtime/binstub is missing or broken | Both surfaces share it; report the provisioning failure and stop |
| Copilot catalog/registration failed, same bridge works by CLI | Report the primary discrepancy, validate the fleet, continue through it |
| Fleet absent or schema/config changed | Re-run `materialize` atomically, then probe |
| One loaded MCP tool has a wrapper/result bug | Use only the agent's separately documented tool-specific fallback |
| Authentication or authorization failed | Fail closed or run the documented ceremony; do not switch identities |
| Static authorization depends only on decorators | Duplicate it in top-level `tools:` or disable fallback |
| Conditional `gate`/argument-dependent authorization | Disable materialized fallback |
| Upstream itself cannot initialize or is unavailable | Both surfaces fail equivalently; report both and stop |

## Warmth and cold starts

Materialized stubs invoke `agent-mcp call`. By default they attach to
`agent-mcp serve` when its endpoint is available and fall back to a stateless
one-shot session otherwise. The resident daemon keeps one warm session per
bridge identity, reducing npm/uvx launch and protocol negotiation cost:

```bash
agent-mcp serve
```

Warmth is an optimization, not the default for identity-sensitive fallback.
Warm sessions reopen when the effective config/overlay changes, and every call
rechecks the freshly loaded top-level tool filter. An external credential may
still rotate behind an unchanged config, so use `--no-serve` for
identity-sensitive readiness and fallback calls unless the credential lifecycle
explicitly evicts the warm session. A fleet can rescue a Copilot-side MCP
registration/session failure; it cannot rescue an upstream that does not answer
agent-mcp's own negotiation.

## Deployment and drift

`agent-mcp materialize` is intentionally mechanical: it plates the upstream
catalog into `index.md`, per-tool sidecars, `manifest.json`, and platform stubs.
A repository or plugin that relies on standing fleets should own an idempotent
deploy step that:

- materializes every declared bridge identity;
- records the agent-mcp version, platform, stable config reference, and config
  digest of the **effective post-overlay config**;
- re-materializes on any of those changes;
- treats per-fleet network/auth failures independently;
- validates that each MCP-backed agent names an identity-matched fallback.

At agent runtime, a fleet is definitely stale when its expected stub is missing
or `manifest.json.generated_by` differs from `agent-mcp --version`. Detecting
bridge config/schema/overlay drift requires the deploy-owned effective-config
digest above;
agent-mcp's base manifest does not currently record one.

Never let a low-privilege agent select an admin fleet merely because both expose
the same tool names.
