# Optional Plugins Setup -- Codespaces, Containers, MCP

Detailed init/adopt steps for the optional / standalone copilot-extensions
plugins. See [SKILL.md](../SKILL.md) for the overview and the core
agent-worktrees + agent-bridge flow.

## Contents
- Agent-Codespaces Init (section 5)
- Agent-Codespaces Adopt (section 6)
- Agent-Containers Init (section 7)
- Agent-MCP Init (section 8)

---
## 5. Agent-Codespaces Init

Install the agent-codespaces runtime (CLI binstub + `~/.agent-codespaces`
home). The credential relay itself runs inside the agent-bridge service, but
this step gives you the standalone `agent-codespaces` CLI and is the canonical
owner of the `~/.local/bin/agent-codespaces` binstub.

```powershell
# Windows
pwsh -NoProfile -ExecutionPolicy Bypass -File "$acDir\scripts\init.ps1"
```

```bash
# Linux/WSL
bash "$ac_dir/scripts/init.sh"
```

### Verify

```bash
agent-codespaces version
agent-codespaces status      # shows runtime, gh CLI, ssh
```

`gh` must be authenticated (`gh auth login`) for CodeSpace operations.

---

## 6. Agent-Codespaces Adopt

Register the repo so agent-codespaces reads `codespaces.yaml` live (CodeSpace
defaults + credential-relay policy). Run **from inside the repo**.

```bash
cd /path/to/repo
agent-codespaces config adopt
agent-codespaces config validate
agent-codespaces config show
```

If the repo has no `codespaces.yaml`, create one first — see the
`codespaces-setup` skill for the format (defaults, credential sources, per-repo
overrides).

### Verify relay + bridge integration

No registration step is needed: when agent-codespaces is installed, the
agent-bridge service imports it as a sibling and **auto-registers the live
`codespace:` namespace resolver** at startup, so CodeSpaces are addressable as
`codespace:<name>` (raw or friendly) on demand.

```bash
# CodeSpaces should already appear here -- no `bridge register` required.
agent-bridge agents          # look for codespace:<name> entries
```

If `agent-bridge agents` shows no codespace entries and the bridge install
WARNED about a missing sibling, re-run the agent-bridge installer **after** the
agent-codespaces plugin is installed (section 0) so the service venv picks up
the `agent_codespaces` package. (`agent-codespaces bridge register` exists but
only POSTs a static `cs-*` snapshot with a TTL — it is optional and superseded
by the resolver; see the `codespaces-lifecycle` skill.)

---

## 7. Agent-Containers Init

Install the agent-containers runtime (CLI binstub + `~/.agent-containers`
home). The `container:` namespace resolver runs inside the agent-bridge
service (installed as a sibling import); this step gives you the standalone
`agent-containers` CLI for fleet/lease management and owns the
`~/.local/bin/agent-containers` binstub.

```powershell
# Windows
pwsh -NoProfile -ExecutionPolicy Bypass -File "$anDir\scripts\init.ps1"
```

```bash
# Linux/WSL
bash "$an_dir/scripts/init.sh"
```

### What It Creates

```
~/.agent-containers/
  .venv/                   Python venv with the agent_containers package
  deploy-manifest.json

~/.local/bin/
  agent-containers[.cmd]   Binstub
```

### Verify

```bash
agent-containers version
agent-containers fleet       # lists local dev containers + lease status
```

Docker (Docker Desktop WSL2 backend) must be running for fleet operations.
The `container:` resolver in agent-bridge forwards the host `gh auth token`
into containers, so `gh` must be authenticated for dispatched agents to work.

---

## 8. Agent-MCP Init (optional, standalone)

Install the agent-mcp runtime (CLI binstub + `~/.agent-mcp` home). agent-mcp is
**not** part of the bridge mesh — it has no `codespace:` / `container:`-style
resolver and the bridge does not import it. An agent wraps an upstream MCP by
pointing an `mcp-servers` entry at the `agent-mcp` binstub. Install it only if
you need to bridge an authenticated MCP server.

```powershell
# Windows
pwsh -NoProfile -ExecutionPolicy Bypass -File "$amDir\scripts\init.ps1"
```

```bash
# Linux/WSL
bash "$am_dir/scripts/init.sh"
```

### What It Creates

```
~/.agent-mcp/
  .venv/                   Python venv with the agent_mcp package
  deploy-manifest.json

~/.local/bin/
  agent-mcp[.cmd]          Binstub
```

You create `~/.agent-mcp/bridges/<name>.yaml` config files yourself (or pass
`--config <path>`); init does not create the `bridges/` directory.

### Verify

```bash
agent-mcp status            # prerequisites + available bridges
```

Define a bridge under `~/.agent-mcp/bridges/<name>.yaml` (or pass `--config`),
then validate it with `agent-mcp validate <name>`. See the `agent-mcp` skill for
the config format and how to wire it into an agent's `mcp-servers`.
