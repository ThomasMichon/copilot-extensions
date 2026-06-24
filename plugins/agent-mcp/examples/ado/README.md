# Worked example — adapting the Azure DevOps MCP for agents

A real, end-to-end illustration of the `agent-mcp` **decorator stack**: take one
upstream MCP server (the Azure DevOps remote MCP) and produce several *adapter
variants* of it, each tuned for how an agent should consume it — then hand each
variant to a dedicated read-only agent and compare.

Every file here is runnable and was measured live against
`https://mcp.dev.azure.com/onedrive`.

## The problem

The ADO MCP exposes **37 tools** and a verbose, deeply-nested payload shape:

| | value |
|---|---|
| `tools/list` (upfront, every turn) | **37 tools / 74,498 bytes** |
| `repo_pull_request` action=list (100 PRs) | **51,412 bytes** |
| `search_workitem` (one query) | **7,557 bytes** |

About a third of the tools are mutating (`*_write`, `*_run`, `repo_create_branch`,
`wiki_upsert_page`). For a *read-only* enumeration agent that's both unsafe and
wasteful — the model pays for 37 tool definitions and tens of KB per call it
mostly ignores.

## The base bridge

Every variant starts from the same upstream `server` + `auth` (this is just the
existing `@ado-data` bridge config):

```yaml
server:
  type: http
  url: https://mcp.dev.azure.com/onedrive
auth:
  kind: entra
  resource: 2a72489c-aab2-4b65-b93a-a91edccf33b8   # mcp.dev.azure.com
```

…and layers a `decorators:` stack on top. Every variant also stacks a read-only
`filter` (drop the mutating tools). The five variants in this folder:

| file | stack | idea |
|------|-------|------|
| [`plain.mcp.yaml`](plain.mcp.yaml) | `filter` | the floor: read-only safety only |
| [`defer.mcp.yaml`](defer.mcp.yaml) | `defer` + `filter` | hide the catalog behind `find_tool`/`execute_tool` |
| [`code.mcp.yaml`](code.mcp.yaml) | `code-mode` + `filter` | one typed `run_code` tool; aggregate server-side |
| [`storage.mcp.yaml`](storage.mcp.yaml) | `filter` + `storage` | relay big results through a stream buffer |
| [`transform.mcp.yaml`](transform.mcp.yaml) | `filter` + `transform` | slim list/search rows to key fields |

## Results (measured live)

### Upfront context — `tools/list`

| variant | tools | bytes | vs upstream |
|---------|-------|-------|-------------|
| upstream (no decorators) | 37 | 74,498 | — |
| `plain` | 23 | 41,296 | −45% |
| `defer` | **3** | **1,375** | **−98%** |
| `code` | **3** | **1,349** | **−98%** |
| `storage` | 24 | 41,710 | −44% (adds `read_stream`) |
| `transform` | 23 | 41,296 | −45% |

### Per-call payload returned to the model

`repo_pull_request` list (100 PRs) and `search_workitem`:

| variant | PR list | work-item search | how |
|---------|---------|------------------|-----|
| `plain` | 51,412 | 7,557 | raw |
| `transform` | 31,710 | **1,128 (−85%)** | `pick` key fields per tool (mapped over the array) |
| `storage` | **607** | 3,922 | externalize to a `mcpstream://` handle (+ summary) |
| `code` | **451** | n/a | `run_code` filters/aggregates 100 PRs in Node |
| `defer` | 51,412 (via `execute_tool`) | — | full fidelity, discovered on demand |

### Dedicated agents (real Copilot agent per variant)

Each [`agents/ado-*.agent.md`](agents) wired to one variant, same task
("list the 3 most recent PRs… id + title"), headless:

| agent | result | ADO calls | mechanism |
|-------|--------|-----------|-----------|
| `ado-plain` | ✓ | 1 | direct call |
| `ado-defer` | ✓ | 3 | `find_tool` → `execute_tool` |
| `ado-code` | ✓ | 3 | `find_tool` → `run_code` |
| `ado-storage` | ✓ | 1 | direct (607 B handle + summary) |
| `ado-transform` | ✓ | 1 | direct (slimmed) |

**All five completed the task correctly** — every adapter is agent-usable, with
no change to the upstream server.

## Choosing an adapter

- **`defer` / `code-mode`** cut the *upfront* catalog ~98% — the biggest lever,
  and it scales with tool count. Best for 100+ tool servers and tasks that touch
  few of many tools. They add a discovery round-trip; `code-mode` additionally
  collapses *multi-call compute* into one tiny result (100 PRs → 451 B) at the
  cost of a Node dependency.
- **`transform` / `storage`** are transparent, single-call drop-ins that shrink
  the *payload*. Best bolted onto specific always-too-verbose endpoints.
- **`filter`** is the read-only safety floor every variant builds on.
- Adapters **compose** — pick the stack per server/partner rather than
  one-size-fits-all.

## Run it yourself

Measure any variant (drives the MCP handshake + sample read-only calls):

```bash
# requires `az login` for the Entra token
python examples/ado/probe.py examples/ado/transform.mcp.yaml \
  'PRs:repo_pull_request:{"action":"list","project":"ODSP-Web","repositoryId":"odsp-web"}' \
  'WI:search_workitem:{"searchText":"file picker","project":"ODSP-Web","top":5}'
```

Run a dedicated agent (a fresh Copilot CLI session discovers the agent file):

```bash
copilot --agent ado-transform --allow-all-tools \
  -p 'List the 3 most recent PRs in odsp-web (ODSP-Web): id + title.'
```

> In your own repo, co-locate the bridge config next to the agent (e.g.
> `.github/agents/<name>.mcp.yaml`) and point `--config` at it — exactly the
> pattern these examples use, just under `examples/ado/` here.
