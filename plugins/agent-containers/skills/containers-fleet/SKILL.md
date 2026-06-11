---
name: containers-fleet
description: >-
  Manage a local Docker dev-container fleet and dispatch Copilot agents into
  containers via agent-bridge. Use when asked to "set up containers", "borrow a
  container", "release a container", "container fleet", "dispatch to a
  container", or to run work inside a local odsp-web dev container instead of a
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
agent-containers up odsp-web --count 3   # create/top-up to 3 warm containers
agent-containers fleet                   # list members + lease status
```

Containers are kept warm (stopped, not destroyed). `down` stops them, `start`
restarts them, `rm` removes them.

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
agent-bridge send container:odsp-web-1 "run the unit tests in packages/foo"
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
