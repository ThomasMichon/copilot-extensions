# copilot-extensions

A [Copilot CLI](https://docs.github.com/copilot/how-tos/use-copilot-agents/use-copilot-cli)
plugin suite that gives every session its **own isolated git worktree** and lets
your agents **talk to each other** — across worktrees, across machines, and into
**GitHub Codespaces** and **local dev containers** — with credentials forwarded
securely along the way. The **agent-mcp** plugin wraps authenticated **MCP
servers** so those same host credentials reach your tools.

Plugins, one marketplace. Install what you need; they compose.

| Plugin | Type | What it gives you |
|--------|------|-------------------|
| [agent-worktrees](plugins/agent-worktrees/) | Session tool | Each Copilot CLI session runs in its own git worktree — no branch conflicts, no stale state. Install this first. |
| [agent-bridge](plugins/agent-bridge/) | Persistent service | Send prompts to agents on other machines (or other worktrees) over an always-on local service + SSH mesh. |
| [agent-codespaces](plugins/agent-codespaces/) | CLI + relay | Create/manage GitHub Codespaces, address them as bridge agents (`codespace:<name>`), and forward git/GitHub/Azure credentials into them. |
| [agent-containers](plugins/agent-containers/) | CLI + resolver | Manage a fleet of local Docker dev containers, borrow/release them per effort, and address them as bridge agents (`container:<name>`). |
| [agent-mcp](plugins/agent-mcp/) | MCP bridge | Wrap an upstream MCP server (HTTP or stdio) as a local stdio MCP and inject host credentials (Entra/`az`, `gh`, git-credential, env). Standalone — used directly from an agent's `mcp-servers` config. |
| [efforts](plugins/efforts/) | Planning skills | Plan a stretch of work as an **effort** — a folder with a README-as-shared-contract (premise + plan + journal) that humans and agents coordinate through. The executor plugins above bind its participant seam. |
| [visions](plugins/visions/) | Planning skills | Keep a persistent **vision** — a north-star statement of what a system should ultimately be — and derive efforts from the delta between vision and reality. Payload-only — no runtime to install. |
| [agent-logger](plugins/agent-logger/) | Session logging | Turn raw Copilot sessions into structured Markdown logs — a segmenter, a voice-neutral log-writer agent, and a `session-sync` step that pushes session data to a configurable target (local / OneDrive / SSH / ingest). Personality is injected by the host, never built in. |
| [context-handoff](plugins/context-handoff/) | Extension + skill | Watch the context window via a session extension and, before it fills, compose a continuation prompt so a fresh session can resume the work. Payload-only — no runtime to install. |
| [agent-dispatch](plugins/agent-dispatch/) | Task queue + coordinator | Coordinate multiple agents through a single-writer leased task queue (atomic claim, capability routing, lease recovery) instead of racing through `origin/master` pushes. Per-host coordinator, CLI, and MCP tools. |
| [customizing-copilot](plugins/customizing-copilot/) | Customizing the CLI | Teach an agent how to customize and extend the Copilot CLI — authoring skills, defining sub-agents, registering MCP servers, installing plugins, building a control-harness, reviewing customizations, and authoring `harness-<repo>` plugins. Seven focused skills. Payload-only — no runtime to install. |
| [harness-copilot-extensions](plugins/harness-copilot-extensions/) | Operator harness | The portable, owner-authored skills to work *on* this suite — **contribute** changes and **diagnose** the deployed runtimes. Enable it in any control repo instead of hand-writing a per-repo narrative. Reference implementation of the `harness-<repo>` standard. Payload-only. |
| [wsl-setup](plugins/wsl-setup/) | Environment setup | Set up and troubleshoot WSL2 as a reachable, persistent service host — pick the networking mode (NAT + localhostForwarding vs mirrored), diagnose corp-network egress + host↔WSL loopback failures, and keep a distro alive for a hosted listener (e.g. sshd behind a Dev Tunnel). Ships a windowless keepalive helper. |

All support **Windows** and **Linux/WSL** (macOS planned).

---

## Architecture at a glance

Twelve plugins, one marketplace. **Seven ship a runtime** (a `uv`-built venv under
`~/.agent-*` + a `~/.local/bin` binstub, deployed by the plugin's own
installer); **five are payload-only** — `efforts` (skills), `visions` (skills),
`context-handoff` (a session extension), `customizing-copilot` (skills), and
`harness-copilot-extensions` (skills) need no install beyond enabling the plugin.
Everything installs **from the marketplace** and runs
**from local install paths** — no git checkout required at runtime.

```mermaid
flowchart TB
    MP["GitHub marketplace<br/>ThomasMichon/copilot-extensions"]
    subgraph IP["~/.copilot/installed-plugins/copilot-extensions/"]
      AW["agent-worktrees<br/>skills + sessionStart hook"]
      AB["agent-bridge<br/>service source + libs/ssh-manager"]
      AC["agent-codespaces<br/>CLI + credential relay"]
      AN["agent-containers<br/>CLI + container: resolver"]
      AM["agent-mcp<br/>MCP bridge CLI"]
      AL["agent-logger<br/>session-sync + log writer"]
      PO["efforts · visions · context-handoff<br/>customizing-copilot · harness-copilot-extensions<br/>(payload-only: skills / extension)"]
    end
    subgraph RT["Local runtimes — ~/.* + ~/.local/bin"]
      RW["~/.agent-worktrees<br/>agent-worktrees"]
      RB["~/.agent-bridge<br/>service :9280 Win / :9281 WSL"]
      RC["~/.agent-codespaces<br/>agent-codespaces"]
      RN["~/.agent-containers<br/>agent-containers"]
      RM["~/.agent-mcp<br/>agent-mcp"]
      RL["~/.agent-logger<br/>session-sync task + digests"]
    end
    MP -->|copilot plugin install| AW
    MP -->|copilot plugin install| AB
    MP -->|copilot plugin install| AC
    MP -->|copilot plugin install| AN
    MP -->|copilot plugin install| AM
    MP -->|copilot plugin install| AL
    MP -->|copilot plugin install| PO
    AW -->|init.ps1 / init.sh| RW
    AB -->|install.ps1 / install.sh| RB
    AC -->|init.ps1 / init.sh| RC
    AN -->|init.ps1 / init.sh| RN
    AM -->|init.ps1 / init.sh| RM
    AL -->|install.ps1 / install.sh| RL
    AC -.->|codespace resolver + relay| RB
    AN -.->|container resolver| RB
```

Each runtime plugin is itself a **Python package** (its `src/` plus vendored
`libs/`); the installer creates the venv with `uv venv` and installs the package
with `uv pip install <plugin_dir>`. See
[Quick Start](#quick-start) and [Architecture overview](docs/architecture.md)
for the payload-vs-runtime split.


How the pieces relate at run time:

```mermaid
flowchart LR
    subgraph Yours["Your machine"]
      direction TB
      CLI["Copilot CLI session"]
      WT["agent-worktrees<br/>per-session worktree"]
      BR["agent-bridge<br/>service"]
      CS["agent-codespaces<br/>+ credential relay :9857"]
      CN["agent-containers<br/>local dev-container fleet"]
      MCP["agent-mcp<br/>MCP bridge (host creds)"]
      CLI --> WT
      CLI -->|agent-bridge send| BR
      CLI -.->|mcp-servers: agent-mcp| MCP
      BR --> CS
      BR --> CN
    end
    BR -->|SSH| OM["Other machines<br/>dev box, WSL, server"]
    CS -->|SSH + gh| GH["GitHub Codespaces"]
    CS -.->|forwards git / gh / az creds| GH
    CN -->|docker exec| DC["Local dev containers"]
    CN -.->|forwards gh token| DC
```

---

## Quick Start

> Goal: from a fresh machine to *"send a prompt to my CodeSpace and get work
> done"* in a handful of steps. New to this? Read
> [Concepts](#concepts-the-control-harness-repo) first.

### Prerequisites

- **Copilot CLI** (`copilot` on PATH) · **Python 3.10+** · **Git 2.15+**
- **gh CLI**, authenticated (`gh auth login`) — for agent-codespaces and agent-containers
- **Docker** (Docker Desktop WSL2 backend) — for agent-containers only
- **uv** (bootstrapped automatically by the init scripts if missing)

### 1. Install the plugins

Install agent-worktrees first; add the others as you need them. The bridge
installer imports agent-codespaces and agent-containers for their `codespace:` /
`container:` resolvers, so install those **before** agent-bridge.

```bash
copilot plugin marketplace add ThomasMichon/copilot-extensions
copilot plugin install agent-worktrees@copilot-extensions
copilot plugin install agent-codespaces@copilot-extensions
copilot plugin install agent-containers@copilot-extensions
copilot plugin install agent-bridge@copilot-extensions
copilot plugin install agent-mcp@copilot-extensions      # optional, standalone
copilot plugin install agent-logger@copilot-extensions   # optional — session logging
copilot plugin install efforts@copilot-extensions        # optional — planning skills (no runtime)
copilot plugin install context-handoff@copilot-extensions # optional — context-window handoff (no runtime)
copilot plugin install customizing-copilot@copilot-extensions # optional — how to customize the CLI (no runtime)
```

Each `copilot plugin install` only vendors the plugin's **payload** (source,
skills, hooks, extensions). The seven runtime plugins (every plugin except the
payload-only `efforts`, `visions`, `context-handoff`, `customizing-copilot`, and
`harness-copilot-extensions`) then need their runtime deployed once — that's Step 2,
which runs each installer to build a `uv` venv under `~/.agent-*` and drop a
binstub in `~/.local/bin`.

> **Recommended: register at repo scope instead of globally.** Set
> `"experimental": true` in `~/.copilot/settings.json`, then declare the
> marketplace + `enabledPlugins` in your control repo's committed
> `.github/copilot/settings.json`. Copilot vendors the payloads when a session
> runs in that repo (agent-worktrees may need a session restart to take effect),
> Step 2 deploys the runtimes, and every subsequent launch via the
> binstub/terminal profile runs `agent-worktrees reconcile-plugins` to keep the
> payloads and runtimes fresh automatically. See
> [`copilot-extensions-setup`](plugins/agent-worktrees/skills/copilot-extensions-setup/SKILL.md)
> § 0 and [install-contract.md](docs/install-contract.md).

### 2. Bootstrap the runtimes

Start a Copilot CLI session and say **"set up copilot extensions"** — the
[`copilot-extensions-setup`](plugins/agent-worktrees/skills/copilot-extensions-setup/SKILL.md)
skill runs each installer so the runtimes land under `~/.agent-*` with binstubs
in `~/.local/bin`. (Prefer to do it by hand? See each plugin's Getting Started,
linked below.)

Verify:

```bash
agent-worktrees --version
agent-bridge version && agent-bridge status
agent-codespaces version
```

### 3. Adopt your control-harness repo

Adopt your control repo (see [Concepts](#concepts-the-control-harness-repo)) so
worktrees, topology, and Codespaces all read from one place:

```bash
cd /path/to/my-control-harness
agent-worktrees register my-control-harness          # worktree sessions + binstub
agent-bridge config adopt --repo . --profile my-control-harness
agent-codespaces config adopt
```

### 4. First send — local, then CodeSpace

```bash
# Talk to a local agent (no SSH needed)
agent-bridge send local "Print the working directory and git branch."

# Talk to a CodeSpace through the bridge (auto-starts it; creds forwarded)
agent-codespaces bridge register
agent-bridge send "codespace:<name>" "Run: pwd && git rev-parse --abbrev-ref HEAD && gh auth status"
```

---

## Concepts: the control-harness repo

A **control-harness repo** is your own repo (a dotfiles-style "hub") that drives
the whole system. In examples it's called `my-control-harness`. It:

- is **adopted by agent-worktrees** (gets a project binstub + worktree root),
- holds the **topology** the bridge reads — `machines.yaml` (machines + SSH) and
  `acp-agents.json` (agents), plus `codespaces.yaml` (Codespace defaults +
  credential-relay policy) and `containers.yaml` (local dev-container fleet
  defaults), and
- doubles as the **Codespaces dotfiles repo**, so the same repo provisions each
  CodeSpace.

One repo, one source of truth, the mesh plugins reading from it. (agent-mcp is
standalone — its bridge configs are per-agent files, preferably in-repo via
`--config` for repo-scoped agents, or under `~/.agent-mcp/bridges/` for personal
ones; not the control repo.)

> **Building or auditing a harness?** Point an agent at the
> [Control-Harness Runbook](docs/harness-runbook.md) — an opinionated,
> phase-by-phase procedure for turning a repo into an effective agent harness
> with these plugins. It works from a fresh folder ("make me a control repo like
> this"), on an existing repo ("build out my harness"), or as an audit ("make
> sure my repo follows best practices").

---

## Usage flow: a CodeSpace session end-to-end

```mermaid
sequenceDiagram
    participant You as Copilot CLI
    participant Bridge as agent-bridge
    participant CS as agent-codespaces
    participant Space as CodeSpace
    You->>Bridge: agent-bridge send "codespace:my-space" "..."
    Bridge->>CS: resolve codespace:my-space
    CS->>Space: gh codespace start (if Shutdown) + SSH (-R 9857)
    Bridge->>Space: spawn copilot --acp over SSH
    Space-->>CS: git / gh credential request to :9857
    CS-->>Space: token (from GCM / gh auth)
    Space-->>Bridge: streamed response
    Bridge-->>You: response
```

The credential relay (port **9857**) means the CodeSpace authenticates to GitHub
and Azure DevOps using **your host's** credentials — no PATs baked into the
CodeSpace.

---

## Updating

```bash
# Pull the latest plugin from the marketplace…
copilot plugin update agent-worktrees@copilot-extensions

# …or update the plugin + runtime in one step
agent-worktrees update
```

agent-worktrees also auto-updates its runtime on session launch. agent-bridge
and agent-codespaces update via their installers (`scripts/install.* update`).
agent-containers and agent-mcp re-run their `scripts/init.*` (with `-Force` /
`--force`) to redeploy the runtime.

## Uninstalling / baseline reset

The installer-based plugins (agent-worktrees, agent-bridge, agent-codespaces)
provide an `uninstall` action that stops their **own managed processes** before
removing files — agent-bridge stops the daemon + credential relay, and
agent-codespaces closes its SSH ControlMaster connections — so no manual
process-killing is needed:

```bash
scripts/install.sh uninstall          # per-plugin (add --purge / --remove-config to wipe config)
```

agent-containers and agent-mcp are init-only (no installer): remove them by
deleting `~/.agent-containers` / `~/.agent-mcp` and their `~/.local/bin`
binstubs.

To return a machine to a clean baseline in one step (stops everything, removes
the installer-based runtimes, binstubs, the service/scheduled task, and config)
use the repo-level reset tool — it's idempotent and works even if the CLIs are
broken:

```powershell
# Windows
pwsh -File tools\reset.ps1                       # prompts; add -Yes to skip
pwsh -File tools\reset.ps1 -Yes -RemovePlugins   # also `copilot plugin uninstall`
```
```bash
# Linux/WSL
bash tools/reset.sh                              # prompts; add --yes to skip
bash tools/reset.sh --yes --remove-plugins
```

> The reset tool currently targets the installer-based runtimes
> (`~/.agent-worktrees`, `~/.agent-bridge`, `~/.agent-codespaces`); remove
> `~/.agent-containers` and `~/.agent-mcp` manually until it covers them.

Your source repos and their `.worktrees` content are never touched.

---

## Documentation

### Guides & component breakdowns

| Document | What's inside |
|----------|---------------|
| [Control-Harness Runbook](docs/harness-runbook.md) | Opinionated, phase-by-phase procedure for building/extending/auditing an agent harness with these plugins |
| [Plugin consolidation](docs/plans/plugin-consolidation.md) | Discussion: whether to collapse the multi-plugin suite into fewer plugins, with decision criteria |
| [Architecture overview](docs/architecture.md) | How the plugins fit together: install topology, runtimes, ports, credential relay |
| [Rollout plan](docs/plans/rollout-readiness.md) | Onboarding-readiness plan and fixes |
| [Fresh dev box validation](docs/plans/fresh-devbox-validation.md) | Step-by-step validation on a clean machine |

### Agent Worktrees

| Document | Description |
|----------|-------------|
| [README](plugins/agent-worktrees/README.md) | Plugin overview |
| [Getting Started](plugins/agent-worktrees/docs/getting-started.md) | Install, adopt a repo, launch sessions |
| [Architecture](plugins/agent-worktrees/docs/architecture.md) | Plugin/runtime layers, session lifecycle |
| [CLI Reference](plugins/agent-worktrees/docs/cli-reference.md) | Commands, installer actions, config format |

### Agent Bridge

| Document | Description |
|----------|-------------|
| [README](plugins/agent-bridge/README.md) | Plugin overview |
| [Getting Started](plugins/agent-bridge/docs/getting-started.md) | Install, configure, start the service |
| [Architecture](plugins/agent-bridge/docs/architecture.md) | Service design, API reference, deployment |
| [Machine Configuration](plugins/agent-bridge/docs/machine-config.md) | Topology — `machines.yaml`, `acp-agents.json` |

### Agent Codespaces

| Document | Description |
|----------|-------------|
| [README](plugins/agent-codespaces/README.md) | Plugin overview, CLI reference, config format |
| [codespaces-setup](plugins/agent-codespaces/skills/codespaces-setup/SKILL.md) | First-time setup, adoption, credential relay config |
| [codespaces-lifecycle](plugins/agent-codespaces/skills/codespaces-lifecycle/SKILL.md) | Day-to-day ops — SSH, listing, bridge integration |

### Agent Containers

| Document | Description |
|----------|-------------|
| [README](plugins/agent-containers/README.md) | Plugin overview, CLI reference, config format, discovery |
| [containers-fleet](plugins/agent-containers/skills/containers-fleet/SKILL.md) | Fleet provisioning, borrow/release leases, `container:` dispatch |

### Agent MCP

| Document | Description |
|----------|-------------|
| [README](plugins/agent-mcp/README.md) | Plugin overview, bridge config format, auth kinds, CLI |
| [agent-mcp](plugins/agent-mcp/skills/agent-mcp/SKILL.md) | Defining a bridge, wiring it into an agent's `mcp-servers` |

### Efforts

| Document | Description |
|----------|-------------|
| [README](plugins/efforts/README.md) | Plugin overview, the skill-governs-pattern + repo-addendum model |
| [planning-efforts](plugins/efforts/skills/planning-efforts/SKILL.md) | Start, plan, resume, archive efforts |
| [reference guide](plugins/efforts/skills/planning-efforts/references/efforts.md) | Full effort schema, lifecycle, participants seam |
| [efforts-setup](plugins/efforts/skills/efforts-setup/SKILL.md) | Adopt efforts in a repo: scaffold + write the addendum |

### Visions

| Document | Description |
|----------|-------------|
| [README](plugins/visions/README.md) | Plugin overview, the north-star model, skill-governs + repo-addendum |
| [envisioning](plugins/visions/skills/envisioning/SKILL.md) | Create/revise a vision, derive the delta into efforts |
| [visions-setup](plugins/visions/skills/visions-setup/SKILL.md) | Adopt visions in a repo: scaffold + write the addendum |

### Agent Logger

| Document | Description |
|----------|-------------|
| [README](plugins/agent-logger/README.md) | Plugin overview, pipeline pieces, design principles |
| [log-session](plugins/agent-logger/skills/log-session/SKILL.md) | Write a log for one session on demand |
| [process-backlog](plugins/agent-logger/skills/process-backlog/SKILL.md) | Batch-log a backlog of unlogged sessions locally |
| [session-sync-setup](plugins/agent-logger/skills/session-sync-setup/SKILL.md) | Configure + deploy session-sync (target, schedule) |

### Context Handoff

| Document | Description |
|----------|-------------|
| [README](plugins/context-handoff/README.md) | Plugin overview, why an extension, no-install delivery |
| [context-handoff](plugins/context-handoff/skills/context-handoff/SKILL.md) | The `/handoff` continuation-prompt workflow |
| [context-handoff-setup](plugins/context-handoff/skills/context-handoff-setup/SKILL.md) | Enable the plugin extension in a repo |

### Customizing Copilot

| Document | Description |
|----------|-------------|
| [README](plugins/customizing-copilot/README.md) | Plugin overview, the seven skills, no-install delivery |
| [authoring-skills](plugins/customizing-copilot/skills/authoring-skills/SKILL.md) | SKILL.md format, folder convention, validation, hooks, custom instructions |
| [defining-subagents](plugins/customizing-copilot/skills/defining-subagents/SKILL.md) | Custom agents: `.agent.md`, tool aliases, MCP ownership, anti-recursion |
| [registering-mcp-servers](plugins/customizing-copilot/skills/registering-mcp-servers/SKILL.md) | MCP registration hierarchy, config formats, writing a server |
| [installing-plugins](plugins/customizing-copilot/skills/installing-plugins/SKILL.md) | Repo `settings.json` registration, experimental mode, payload-vs-runtime |
| [building-harnesses](plugins/customizing-copilot/skills/building-harnesses/SKILL.md) | In-session entry to the Control-Harness Runbook (greenfield / brownfield / audit) |
| [reviewing-customizations](plugins/customizing-copilot/skills/reviewing-customizations/SKILL.md) | Review a harness's skills, sub-agents, `AGENTS.md`, hooks, MCP configs |
| [authoring-harness-plugins](plugins/customizing-copilot/skills/authoring-harness-plugins/SKILL.md) | The `harness-<repo>` standard: ship operator skills for a repo |

### Agent Dispatch

| Document | Description |
|----------|-------------|
| [README](plugins/agent-dispatch/README.md) | Plugin overview, the leased queue engine, coordinator, CLI, MCP tools |

### Harness (copilot-extensions)

| Document | Description |
|----------|-------------|
| [README](plugins/harness-copilot-extensions/README.md) | Operator harness overview + the `harness-<repo>` standard |
| [contributing-to-copilot-extensions](plugins/harness-copilot-extensions/skills/contributing-to-copilot-extensions/SKILL.md) | Change + land work in a plugin: flow, the mandatory version bump, gates, deploy |
| [diagnosing-copilot-extensions](plugins/harness-copilot-extensions/skills/diagnosing-copilot-extensions/SKILL.md) | Symptom → cause → action for deployed plugins, key paths, baseline reset |

### Contributing

| Document | Description |
|----------|-------------|
| [CONTRIBUTING](CONTRIBUTING.md) | Versioning, release workflow, deployment pipeline |
| [AGENTS](AGENTS.md) | Repo development guide |

## License

[MIT](LICENSE)
