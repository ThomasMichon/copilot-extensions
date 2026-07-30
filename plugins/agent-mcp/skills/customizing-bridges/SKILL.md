---
name: customizing-bridges
description: >-
  Customize or tune an EXISTING agent-mcp bridge on this machine -- change which
  upstream tools are exposed, add/remove decorators (filter, defer, code-mode,
  rename, storage, transform, gate), or tweak headers / auth / resource /
  timeout / upstream -- WITHOUT editing the committed or plugin-shipped config,
  by writing a machine-local override overlay (`~/.agent-mcp/overrides/<id>.yaml`,
  deep-merged at load). Use when asked to "customize a bridge", "override an mcp
  bridge config", "change which tools <bridge> exposes", "expose all/more tools",
  "add a decorator to a bridge", "make a bridge lean / defer / code-mode",
  "per-machine / per-host mcp override", or "tune @spark / @ado-data / an
  agent-mcp bridge without editing the plugin". To *create* a bridge from
  scratch (transport, auth, a sub-agent), use the `agent-mcp` skill instead.
---

# customizing-bridges

Tune an **existing** agent-mcp bridge on this one machine, without touching the
shared config. agent-mcp loads a bridge from its committed config -- an in-repo
`--config`, a named `~/.agent-mcp/bridges/<name>.*`, or a **plugin-shipped** one
-- and, right before use, **deep-merges a machine-local overlay** from
`~/.agent-mcp/overrides/<id>.{yaml,yml,json}` on top of it. So a single host can
vary *any* field (tools, decorators, headers, auth, upstream URL) with a small
overlay file -- no editing the shared file, no forking the plugin, no env vars.
No overlay file → the config is used unchanged.

> **This skill is for *tuning* a bridge. To *create* one** (pick a transport,
> wire auth, stand up a sub-agent), use the **`agent-mcp`** skill -- it also
> holds the full auth-kind and decorator reference this skill points back to.

## 1. Find the overlay id

The overlay filename is keyed by the config's **id**: its explicit top-level
`id:` if present, else the config **file stem with a trailing `.mcp` stripped**:

| Config | id | Overlay file |
|--------|----|--------------|
| `spark.mcp.yaml` | `spark` | `~/.agent-mcp/overrides/spark.yaml` |
| `ado.mcp.yaml` | `ado` | `~/.agent-mcp/overrides/ado.yaml` |
| named bridge `foo.yaml` | `foo` | `~/.agent-mcp/overrides/foo.yaml` |

`agent-mcp status` lists the known named + plugin-shipped bridges and their
config paths. `AGENT_MCP_HOME` (default `~/.agent-mcp`) relocates the whole tree.

## 2. Write the overlay -- know the merge rules

Put **only the fields you want to change** in the overlay. The merge is a
recursive deep-merge with one rule that trips people up:

- **Mappings merge recursively** -- keys only in the base survive; overlay keys
  win. So a `headers:` overlay *adds/replaces individual header keys*, leaving
  the rest intact.
- **Lists and scalars replace wholesale** -- an overlay list **fully restates**
  the base list (it does **not** append). To change one entry of `decorators:`,
  `tools.deny`, `auth` (list form), etc., **restate the whole list**.

See [`references/override-recipes.yaml`](references/override-recipes.yaml) for
copy-paste overlays. The common ones:

### Expose all / more upstream tools (header-driven upstreams)
Some upstreams gate their advertised catalog on a request header. Add it:
```yaml
# ~/.agent-mcp/overrides/spark.yaml -- request the full Spark catalog
headers:
  X-Toolset-Domain: "*"
```

### Hide specific tools from the agent (filter decorator)
```yaml
# restate the FULL decorators list -- lists replace, not append
decorators:
  - type: filter
    deny: ["learn", "run_code", "*_delete", "*_admin"]
```

### Shrink a big catalog to a lean surface
```yaml
decorators:
  - type: defer          # find_tool / execute_tool over the catalog
    mode: lazy           #   lazy (default) | eager | meta_only
  # or:  - type: code-mode  { tool: run_code }   # needs Node on PATH
```

### Retarget the upstream / tenant, tweak auth or timeout
```yaml
server:
  url: https://other-farm.example.com/mcp
auth:
  resource: <guid>
timeout: 120
headers:
  X-FDROUTEKEYHEADER: "b@mysite@<tenantId>"
```

## 3. Verify + apply

- **Validate the merged result:** `agent-mcp validate <name-or-config>` loads
  through the same overlay merge, so it schema-checks the *effective* config.
- **Restart the consumer:** the bridge re-reads config on launch, so start a new
  session / re-invoke the sub-agent for the overlay to take effect.

## Boundaries

- **Machine-local, not shared.** The overlay lives under `~/.agent-mcp/`, never a
  checkout -- ideal for per-host secrets, tenant route keys, or experiments. To
  change the default **for everyone**, edit the committed config (the `agent-mcp`
  skill / the owning plugin), not an overlay.
- **You can overlay a plugin bridge without editing the plugin** -- that is the
  point: the plugin ships the safe default; your host varies it.
- **Restate lists you touch.** Because lists replace, a `decorators:` or
  `tools:` overlay must be the complete list you want, not a fragment.

## See also

- **`agent-mcp`** skill -- create a bridge; auth kinds; the full decorator-stack
  reference (`filter` / `rename` / `defer` / `code-mode` / `storage` /
  `transform` / `gate`).
- Plugin README → *Decorator stack*.
