# agent-containers

Local Docker dev-container **fleet manager**, **lease broker**, and
agent-bridge **`container:` namespace resolver**.

Manages a persistent fleet of local dev containers (Docker Desktop WSL2
backend), brokers *advisory* exclusive leases so an effort can borrow a
container without two parallel worktrees driving the same one, and lets
agent-bridge dispatch a Copilot agent into a container over `docker exec`.

## Concepts

- **Fleet** — a named pool of long-lived dev containers built from one
  devcontainer spec. Kept warm (stopped, not destroyed) between uses.
- **Lease / borrow** — an *effort* (a logical unit of work) borrows a
  container for the duration of its work, then releases it. Leases persist
  across CLI invocations and agent dispatches; they expire only on explicit
  `release` or after a TTL (default 24h). Enforcement is **advisory** — the
  resolver logs but does not block cross-effort dispatch.
- **`container:` resolver** — `agent-bridge send container:<name> "..."`
  spawns the `agent-containers exec --stdio <name>` transport wrapper, which
  runs `docker exec -i -e GH_TOKEN -u <user> <name> bash -lc "copilot --acp ..."`.
  The wrapper fetches the host `gh auth token` at spawn time and injects it via
  the process environment (referenced by name in argv). Because the token is
  fetched inside the wrapper, it is **never** placed in the SpawnTarget that
  agent-bridge persists to its SQLite DB, nor in any log.

## CLI

```
agent-containers fleet               # list fleet containers + lease status
agent-containers up <fleet>          # provision/top-up a fleet to its size
agent-containers down <fleet>        # stop (keep warm)
agent-containers start <fleet>       # start stopped containers
agent-containers rm <fleet>          # remove (destructive)
agent-containers borrow <effort>     # lease a free container -> prints name
agent-containers release <target>    # release by container or effort name
agent-containers leases              # show active leases
agent-containers exec <name>         # run the ACP launch command (testing)
agent-containers bridge register     # push provider registrations (optional)
```

## Configuration

`containers.yaml` (looked up via `$AGENT_CONTAINERS_CONFIG`, `./containers.yaml`,
or `~/.agent-containers/containers.yaml`). Built-in defaults target the
odsp-web local Docker dev container.

```yaml
exec_user: vscode
workspace_folder: /workspaces/odsp-web
forward_gh_token: true
image_prefixes:
  - vsc-odsp-web-codespaces-
fleets:
  odsp-web:
    repo: odsp-microsoft/odsp-web
    devcontainer_path: D:/Src/odsp-web-codespaces
    size: 3
    code_model: clone   # Model A: repo cloned inside the container
```

## Discovery

Containers are recognised as fleet members (in priority order) by:
1. the `agent-containers.fleet` label (set by `up`),
2. a `devcontainer.local_folder` label (VS Code / devcontainer CLI), or
3. an image-name prefix from `image_prefixes`.

## Installation

Two parts:
1. **Resolver** — installed into the agent-bridge venv as a sibling plugin by
   the agent-bridge installer (provides the `container:` resolver). See
   `_register_namespace_resolvers` in `agent_bridge.agent_registry`.
2. **CLI binstub** — run this plugin's own `scripts/init.ps1` (Windows) or
   `scripts/init.sh` (Linux/WSL) once per machine. It creates
   `~/.agent-containers/.venv` (package installed via `uv pip install`) and the
   `~/.local/bin/agent-containers` binstub. This is the canonical owner of the
   binstub (parallel to agent-codespaces). See the `copilot-extensions-setup`
   skill, section 7.

## Runtime state

`~/.agent-containers/leases.json` (lease records, guarded by an exclusive
lock file).
