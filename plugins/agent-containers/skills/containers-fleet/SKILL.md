---
name: containers-fleet
description: >-
  Manage a local Docker dev-container fleet and dispatch Copilot agents into
  containers via agent-bridge. Use when asked to "set up containers", "borrow a
  container", "release a container", "container fleet", "dispatch to a
  container", or to run work inside a local Docker dev container instead of a
  CodeSpace.
---

# Containers Fleet

`agent-containers` manages a persistent fleet of local Docker dev containers
and brokers exclusive *leases* so an effort can borrow one without two parallel
worktrees driving the same container. Containers are reached over `docker exec`
(Docker Desktop WSL2 backend) and run a Copilot ACP agent addressable via
agent-bridge as `container:<name>`.

## Provision a fleet

Define the fleet in `containers.yaml`, then:

```bash
agent-containers up myrepo --count 3      # create/top-up to 3 warm containers
agent-containers fleet                   # list members + lease status
```

Containers are kept warm (stopped, not destroyed). `down` stops them, `start`
restarts them, `rm` removes them.

## Configuration (`containers.yaml`)

Looked up from `$AGENT_CONTAINERS_CONFIG`, `./containers.yaml`, or
`~/.agent-containers/containers.yaml`. A fleet built from a devcontainer spec:

```yaml
# Optional: reproduce a designated dotfiles repo inside every fleet container,
# Codespaces-style (bind-mount read-only, copy to target, run install.sh).
# Per-user / optional — omit `repo` to skip entirely.
dotfiles:
  repo: D:/Src/dotfiles            # host path to the dotfiles repo
  target: /workspaces/.codespaces/.persistedshare/dotfiles   # default
  install_command: bash install.sh # run in `target` as the remote user; "" skips

fleets:
  odsp-web:
    repo: odsp-microsoft/odsp-web
    devcontainer_path: D:/Src/odsp-web-codespaces   # dir holding .devcontainer/
    # Needed when the spec is NOT at the default
    # .devcontainer/devcontainer.json — passed to the devcontainer CLI as
    # --config. Relative paths resolve against devcontainer_path.
    devcontainer_config: .devcontainer/docker/devcontainer.json
    size: 1
```

- `devcontainer_config` lets `up` build a **nested** devcontainer spec (e.g.
  odsp-web-codespaces' local-Docker spec under `.devcontainer/docker/`) instead
  of the repo's default top-level config.
- `dotfiles` materialises a host repo at `target` and runs its `install.sh`
  (skill symlinks, instructions, etc.) — the host repo is copied in with
  `docker cp` (read-only on the host side), so `install.sh` never mutates the
  host checkout. The install step is best-effort (a failure is logged, never
  aborts `up`).

## Borrow / release (effort owns a container)

```bash
name=$(agent-containers borrow my-effort)   # lease a free container
# ... dispatch work to container:$name ...
agent-containers release my-effort          # free it when done
```

Leases are **advisory** and persist across processes until `release` or TTL
(default 24h). `borrow` will not hand out a container already leased to another
effort; re-borrowing for the same effort is idempotent.

## Dispatch work

```bash
agent-bridge send container:myrepo-1 "run the unit tests in packages/foo"
```

The resolver launches `copilot --acp --stdio --allow-all-tools` inside the
container, forwarding the host `gh auth token` as `GH_TOKEN` so the in-container
Copilot CLI is authenticated headlessly.

## Notes

- **Model A** (default): the repo is cloned *inside* the container on create.
  Model B (mount a dedicated WSL2-native standalone clone) is a future option —
  never mount a shared git worktree (branch-exclusivity + dangling-gitdir hazard).
- Discovery recognises fleet members by the `agent-containers.fleet` label, a
  `devcontainer.local_folder` label, or a configured image-name prefix.
- Runtime state (leases) lives in `~/.agent-containers/leases.json`.
