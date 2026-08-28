# Reliable agent-mcp transport and fallback

Use one reviewed agent-mcp bridge config to expose two equivalent surfaces:

1. the primary Copilot `mcp-servers` catalog;
2. a materialized CLI fleet used when that catalog fails to load.

The fallback is reliable only when both surfaces preserve the same auth source,
identity, tool filter, and authorization boundary.

This reference owns only agent-mcp's bridge and fallback mechanics. Author the
surrounding agent, its domain-service ownership, bounded execution contract,
`## MCP Readiness` section, and anti-self-delegation guard with
**`customizing-copilot:defining-subagents`**.

Use the exact `argv[0]` from the agent-mcp session command catalog for every
shell operation below. Replace `<agent-mcp catalog argv[0]>` with that path;
in PowerShell invoke it as `& "<agent-mcp catalog argv[0]>" <args>`. Never
search `PATH` for a same-named command. If session-start hooks did not publish
the catalog, use the compatibility readiness path from the **`agent-mcp`**
skill before continuing.

## Bridge and agent-mcp wiring

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

Add this transport stanza to the agent frontmatter produced by
`customizing-copilot:defining-subagents`:

```yaml
mcp-servers:
  service-mcp:
    type: stdio
    command: agent-mcp # marketplace-isolation: allow mcp-server-startup
    args: [bridge, --config, .github/agents/service.mcp.yaml]
    tools: ["*"]
```

In the authoring skill's required `## MCP Readiness` section, include these
agent-mcp-specific transport steps:

```markdown
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
```

The fallback is not permission to call the product API directly.

Top-level `tools:` is the authorization boundary shared by bridge and CLI
calls. Decorator stacks are not applied by the payload-local command's
`call`/`materialize` operations.
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
<agent-mcp catalog argv[0]> materialize \
  "$ROOT/.github/agents/service.mcp.yaml" \
  --server-name service

cd "$ROOT"
printf '%s' '{}' |
  "$HOME/.agent-mcp/materialized/service/bin/service_read" --no-serve
```

PowerShell:

```powershell
$root = git rev-parse --show-toplevel
& "<agent-mcp catalog argv[0]>" materialize `
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

The payload-local agent-mcp catalog likewise names a `.cmd` on Windows to
preserve stdio. Pass structured call arguments with `--request-file`; do not
place raw JSON or metacharacter-bearing paths inline on its CMD command line.

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

Materialized stubs intentionally invoke the global management wrapper because
their generated fleet can outlive the session catalog. <!-- marketplace-isolation: allow materialized-stub-management -->
That compatibility wrapper attaches to the optional warmth daemon when its
endpoint is available and otherwise starts a stateless one-shot session. The
resident daemon keeps one warm session per bridge identity, reducing npm/uvx
launch and protocol negotiation cost:

```bash
<agent-mcp catalog argv[0]> serve
```

Warmth is an optimization, not the default for identity-sensitive fallback.
Warm sessions reopen when the effective config/overlay changes, and every call
rechecks the freshly loaded top-level tool filter. An external credential may
still rotate behind an unchanged config, so use `--no-serve` for
identity-sensitive readiness and fallback calls unless the credential lifecycle
explicitly evicts the warm session. A fleet can rescue a Copilot-side MCP
registration/session failure; it cannot rescue an upstream that does not answer
agent-mcp's own negotiation.

Until materialized fleets gain attributable management context, their global
wrapper lookup can still select a different installation cell through ambient
`PATH`. Treat the fleet as a legacy compatibility boundary, not as
cross-cell-safe invocation.

## Deployment and drift

The payload-local command's `materialize` operation is intentionally
mechanical: it plates the upstream catalog into `index.md`, per-tool sidecars,
`manifest.json`, and platform stubs.
A repository or plugin that relies on standing fleets should own an idempotent
deploy step that:

- materializes every declared bridge identity;
- records the agent-mcp version, platform, stable config reference, and config
  digest of the **effective post-overlay config**;
- re-materializes on any of those changes;
- treats per-fleet network/auth failures independently;
- validates that each MCP-backed agent names an identity-matched fallback.

At agent runtime, a fleet is definitely stale when its expected stub is missing
or `manifest.json.generated_by` differs from
`<agent-mcp catalog argv[0]> --version`. Detecting bridge
config/schema/overlay drift requires the deploy-owned effective-config digest
above;
agent-mcp's base manifest does not currently record one.

Never let a low-privilege agent select an admin fleet merely because both expose
the same tool names.
