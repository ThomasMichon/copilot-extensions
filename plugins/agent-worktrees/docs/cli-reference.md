# Agent Worktrees -- CLI Reference

```bash
agent-worktrees <subcommand> [options]
```

## Session Lifecycle

| Subcommand | Description |
|------------|-------------|
| `resolve` | Interactive picker -- select or create a worktree, emit JSON launch plan |
| `create` | Create a new worktree non-interactively |
| `mark-complete` | Mark a worktree as complete (triggers finalization on exit) |
| `finalize` | Squash-merge a completed worktree back to the default branch |
| `cleanup` | List and remove orphaned or finalized worktrees |
| `status` | Show worktree git status |
| `list` | List worktrees from tracking records |
| `handoff` | Manage handoff prompt state on a worktree |

## Installation & Config

| Subcommand | Description |
|------------|-------------|
| `install` | Full deploy: runtime + project config + binstubs + terminal profiles |
| `register` | Register a new project (create config + binstub without full reinstall) |
| `uninstall` | Remove worktree manager |
| `update` | Re-deploy runtime from repo source + refresh marketplace plugin |
| `install-status` | Show installation and deployment status |
| `deploy-instructions` | Deploy `machine.instructions.md` from `machines.yaml` |
| `get` | Query config values (e.g., `agent-worktrees get repo-dir`) |

## Services, Repos & Validation

| Subcommand | Description |
|------------|-------------|
| `services` | Service discovery, staleness checks, passthrough to installers |
| `repos` | Repos registry -- list, find, add, clone, srcroot management |
| `validate` | Validate core infrastructure files |
| `pre-launch` | Check bootstrap staleness (JSON output, for launch wrappers) |

## Development

| Subcommand | Description |
|------------|-------------|
| `dev` | Dev venv and test runner |
| `--version` | Print installed version |

---

## Installer Actions

The `install.ps1` and `install.sh` scripts support these lifecycle
actions:

| Action | Description |
|--------|-------------|
| `install` | Full deploy: runtime, binstub, config, terminal profiles, manifest |
| `uninstall` | Remove runtime and binstub (`--remove-config` for config too) |
| `status` | Check deployed runtime, config, PATH, worktrees, provenance |
| `update` | Re-deploy runtime + binstub, refresh marketplace plugin |
| `update-config` | Regenerate config.yaml (`--force` to overwrite) |

### Installer Flags

| Flag | Platform | Description |
|------|----------|-------------|
| `-ProjectName` / `--project-name` | Both | Project name (auto-detected from repo) |
| `-Force` / `--force` | Both | Overwrite config without confirmation |
| `-RemoveConfig` / `--remove-config` | Both | On uninstall: also delete config and metadata |
| `-Machine` / `--machine` | Windows | Machine name (auto-detected) |

### Programmatic Install (Outside Copilot)

```powershell
# Windows -- from the copilot-extensions checkout
cd C:\Src\copilot-extensions\plugins\agent-worktrees
.\scripts\install.ps1 install -ProjectName my-project
```

```bash
# Linux/WSL
cd ~/src/copilot-extensions/plugins/agent-worktrees
bash scripts/install.sh install --project-name my-project
```

### Remote Deployment

```bash
ssh my-machine "cd ~/src/copilot-extensions/plugins/agent-worktrees && bash scripts/install.sh update"
```

---

## Config Reference

`~/.{project}/config.yaml`:

```yaml
srcroot: C:\Data\Src              # or ~/src on Linux
machine: my-machine
platform: windows                 # windows | wsl | linux
repo_name: my-project

repos:
  my-project:
    anchor: C:\Data\Src\my-project
    worktree_root: C:\Data\Src\.worktrees\my-project
    default_branch: main
    remote: origin
```
