# agent-containers

Local Docker dev-container **fleet manager**, **lease broker**, and
optional agent-bridge **`container:` namespace provider**.

Manages a persistent fleet of local dev containers (Docker Desktop WSL2
backend), brokers *advisory* exclusive leases so an effort can borrow a
container without two parallel worktrees driving the same one, and lets
agent-bridge dispatch a Copilot agent into a container over `docker exec` when
agent-bridge is installed. The CLI and binstub are owned by this plugin and work
standalone; without agent-bridge only bridge addressing (`container:<name>`) is
unavailable.

## Usage path

1. Enable the plugin, then run the cheap stamp action if the binstub is not
   already on PATH:
   - Windows: `pwsh -NoProfile -ExecutionPolicy Bypass -File <plugin>\scripts\init.ps1 stamp`
   - POSIX: `bash <plugin>/scripts/init.sh stamp`
2. First use self-provisions the runtime under `~/.agent-containers/` (or
   `%USERPROFILE%\.agent-containers\`) if the versioned venv is not built yet.
3. Define a fleet in `containers.yaml`, run `agent-containers up <fleet>`, borrow
   with `agent-containers borrow <effort>`, and release when done.
4. If agent-bridge is present, the session-start hook also drops a provider
   manifest into `~/.agent-bridge/providers.d/`; bridge then discovers
   `container:<name>` on demand.

## Concepts

- **Fleet** — a named pool of long-lived dev containers built from one
  devcontainer spec. Kept warm (stopped, not destroyed) between uses.
- **Lease / borrow** — an *effort* (a logical unit of work) borrows a
  container for the duration of its work, then releases it. Leases persist
  across CLI invocations and agent dispatches; they expire only on explicit
  `release` or after a TTL (default 24h). Enforcement is **advisory** — the
  resolver logs but does not block cross-effort dispatch.
- **`container:` resolver** — `agent-bridge send container:<name> "..."`
  is served through agent-bridge's declarative provider registry. The
  `agent-containers` binstub implements `namespace-list`,
  `namespace-resolve`, `namespace-ensure-ready`, and `relay-profile`; bridge
  shells out to those commands rather than requiring this package in the bridge
  venv. Resolution spawns `agent-containers exec --stdio <name>`, whose wrapper
  runs `docker exec -i -e GH_TOKEN -u <user> <name> bash -lc "copilot --acp ..."`.
  The wrapper fetches the host `gh auth token` at spawn time and injects it via
  the process environment (referenced by name in argv). Because the token is
  fetched inside the wrapper, it is **never** placed in the SpawnTarget that
  agent-bridge persists to its SQLite DB, nor in any log.

## CLI

```
agent-containers fleet [--json]      # list fleet containers + lease status
agent-containers up <fleet>          # provision/top-up a fleet to its size
agent-containers down <fleet>        # stop (keep warm)
agent-containers start <fleet>       # start stopped containers
agent-containers rm <fleet> [--force] # remove (destructive)
agent-containers borrow <effort> [--fleet <fleet>] [--container <name>]
                                      # lease a free/specific container -> prints name
agent-containers release <target>    # release by container or effort name
agent-containers leases              # show active leases
agent-containers exec <name>         # run the ACP launch command (testing)
agent-containers config-migrate      # stamp/migrate machine-local containers.yaml
agent-containers version             # show version
```

Bridge-facing commands (`namespace-*`, `relay-profile`) are implementation
seams for agent-bridge and are not normally invoked by humans.

## Configuration

`containers.yaml` (looked up via `$AGENT_CONTAINERS_CONFIG`, `./containers.yaml`,
or `~/.agent-containers/containers.yaml`, then an optional agent-worktrees
knowledge-overlay fallback when that binstub exists and the current harness is
bound to external state). Built-in defaults target a generic VS Code dev
container (`exec_user: vscode`, `workspace_folder: /workspace`,
`image_prefixes: ["vsc-"]`); point them at a real repo in your own
`containers.yaml`. The overlay lookup is additive and fail-open; agent-worktrees
is not required for standalone use.

```yaml
exec_user: vscode
workspace_folder: /workspaces/myrepo
forward_gh_token: true
image_prefixes:
  - vsc-myrepo-                  # narrow discovery to your devcontainer image
fleets:
  myrepo:
    repo: your-org/your-repo
    devcontainer_path: D:/Src/myrepo-devcontainer
    size: 3
    code_model: clone   # Model A: repo cloned inside the container
```

## Discovery

Containers are recognised as fleet members (in priority order) by:
1. the `agent-containers.fleet` label (set by `up`),
2. a `devcontainer.local_folder` label (VS Code / devcontainer CLI), or
3. an image-name prefix from `image_prefixes`.

## Installation

The plugin owns its runtime and binstub:

- `scripts/init.ps1` (Windows) / `scripts/init.sh` (POSIX) build a versioned
  runtime under `~/.agent-containers/versions/<version>/` and publish it via
  `current-version` (Windows binstubs resolve the marker; POSIX also publishes
  `.venv` as the stable link).
- The same installers support a cheap `stamp` action that writes the
  self-provisioning binstub and payload marker (Windows also records a payload
  snapshot) without building the venv; first CLI use runs `provision`.
- `hooks.json` runs `bootstrap-check` at session start and
  `register-bridge-provider` after it. `bootstrap-check` reconciles version drift
  for provisioned runtimes; on Windows it also performs the first cheap stamp.
  If the binstub is absent elsewhere, run the stamp command above. The bridge
  provider hook writes `~/.agent-bridge/providers.d/agent-containers.json` when
  the binstub exists. If agent-bridge is missing, this registration is harmless
  and the CLI remains usable.

Docker is required for fleet operations (`fleet`, `up`, `borrow`, `exec`, etc.),
not for stamping the binstub.

## Runtime state

- `~/.agent-containers/leases.json` — lease records, guarded by an exclusive
  lock file; corrupt/unreadable state is treated as empty.
- `~/.agent-containers/relay-tokens.json` — per-container credential-relay
  secrets used by the bridge-owned relay.
- `~/.agent-containers/containers.yaml` — optional machine-local config; only
  this copy is eagerly schema-stamped/migrated by `config-migrate`.
- `~/.agent-containers/deploy-manifest.json`, `current-version`, `versions/` —
  runtime deployment metadata and versioned venv slots.

## Troubleshooting

There is no `agent-containers doctor` subcommand today. Use the narrow checks the
CLI exposes:

- `agent-containers version` — proves the binstub and runtime import.
- `agent-containers fleet --json` — proves Docker is reachable and discovery
  works; Docker errors come from `docker version` / `docker ps`.
- `agent-containers leases` then `agent-containers release <container-or-effort>`
  — inspect and clear advisory leases. Forgotten leases expire after the 24h TTL.
- `agent-containers namespace-list` — checks the bridge-facing provider CLI; if
  `agent-bridge send container:<name>` cannot see containers, verify
  `~/.agent-bridge/providers.d/agent-containers.json` exists and points at the
  absolute binstub.
- `agent-containers config-migrate` — stamps/migrates only the machine-local
  `~/.agent-containers/containers.yaml`; repo/cwd configs are read-only.
