# Optional Plugins Setup -- Codespaces, Containers, MCP

Use the exact `argv[0]` from each plugin's session command catalog for
interactive checks and configuration below. Replace
`<agent-bridge catalog argv[0]>`, `<agent-codespaces catalog argv[0]>`,
`<agent-containers catalog argv[0]>`, and `<agent-mcp catalog argv[0]>` with
their raw published paths, quoting each at its shell call site. Installer and
service launchers remain explicit
management boundaries.

Detailed init/adopt steps for the optional / standalone copilot-extensions
plugins. See [SKILL.md](../SKILL.md) for the overview and the core worktree and
bridge flow.

## Contents
- Codespaces plugin init (section 5)
- Codespaces plugin adopt (section 6)
- Container plugin init (section 7)
- MCP plugin init (section 8)

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
<agent-codespaces catalog argv[0]> version
<agent-codespaces catalog argv[0]> status      # shows runtime, gh CLI, ssh
```

`gh` must be authenticated (`gh auth login`) for CodeSpace operations.

---

## 6. Agent-Codespaces Adopt

**Most repos need no config.** agent-codespaces works out of the box on standard
CodeSpaces — machine/location defaults, the `/workspaces/<basename>` checkout,
and the git-credential relay (github.com + ADO) are all convention-derived. Add
a supplementary `.agent-codespaces/config.yaml` (and adopt it) **only** when a
repo deviates (a split `*-codespaces` repo, a pinned devcontainer, an ADO host,
provision hooks). Run **from inside the repo**.

```bash
cd /path/to/repo
<agent-codespaces catalog argv[0]> config init       # scaffold .agent-codespaces/config.yaml (+ auto-adopt)
<agent-codespaces catalog argv[0]> config validate
<agent-codespaces catalog argv[0]> config show
```

For a convention-matching repo, skip the above entirely — see the
`agent-codespaces:codespaces-setup` skill for when supplementary config is warranted and its
format. A legacy repo-root `codespaces.yaml` is still read (relocate it with
`<agent-codespaces catalog argv[0]> config migrate`).

### Verify relay + bridge integration

No registration step is needed: when agent-codespaces is installed, the
the bridge service imports it as a sibling and **auto-registers the live
`codespace:` namespace resolver** at startup, so CodeSpaces are addressable as
`codespace:<name>` (raw or friendly) on demand.

```bash
# CodeSpaces should already appear here -- no `bridge register` required.
<agent-bridge catalog argv[0]> agents          # look for codespace:<name> entries
```

If the payload-local `agents` output shows no codespace entries and the bridge install
WARNED about a missing sibling, re-run the agent-bridge installer **after** the
the CodeSpaces plugin is installed (section 0) so the service venv picks up
the `agent_codespaces` package.
(`<agent-codespaces catalog argv[0]> bridge register` exists but
only POSTs a static `cs-*` snapshot with a TTL — it is optional and superseded
by the resolver; see the `agent-codespaces:codespaces-lifecycle` skill.)

---

## 7. Agent-Containers Init

Install the agent-containers runtime (CLI binstub + `~/.agent-containers`
home). agent-containers registers the `container:` namespace with the
bridge daemon via a `~/.agent-bridge/providers.d/` manifest (the daemon
drives the `agent-containers` binstub over a process boundary, not a venv
import); this step gives you the standalone
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
<agent-containers catalog argv[0]> version
<agent-containers catalog argv[0]> fleet       # lists local dev containers + lease status
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
<agent-mcp catalog argv[0]> status            # prerequisites + available bridges
```

Define a bridge under `~/.agent-mcp/bridges/<name>.yaml` (or pass `--config`),
then validate it with `<agent-mcp catalog argv[0]> validate <name>`. See the `agent-mcp:agent-mcp` skill for
the config format and how to wire it into an agent's `mcp-servers`.
