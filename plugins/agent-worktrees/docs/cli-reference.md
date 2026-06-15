# Agent Worktrees -- CLI Reference

```bash
agent-worktrees <subcommand> [options]
```

## CLI mode (no project binstub)

The generic `agent-worktrees` command works without a project binstub. If
no project context is set it prints the command catalog and a recommended
next step rather than erroring. Target a project explicitly with
`--project <name>` (or `-p <name>`):

```bash
agent-worktrees --project my-control-harness worktree list
agent-worktrees -p copilot-extensions worktree create --json
```

Running a project binstub bare (e.g. `my-control-harness`) still launches the
interactive picker.

## Headless projects (CLI-only)

Adopt an external repo as a **headless** project to drive its worktree
lifecycle from another session without ever launching Copilot inside it:

```bash
agent-worktrees register copilot-extensions \
    --repo-dir ~/src/copilot-extensions --headless
```

A headless project records `headless: true` in its `config.yaml`. Running
its binstub bare lists worktrees and the available commands instead of
launching an interactive session:

```bash
copilot-extensions                      # lists worktrees + usage (no launch)
copilot-extensions worktree create      # create; print id + dir
copilot-extensions worktree push <id>   # squash + rebase + push
copilot-extensions worktree finalize <id>
```

This collapses the manual `git worktree add -> edit -> squash -> rebase ->
push -> remove` ritual into the same lifecycle commands, driven from your
existing (e.g. `my-control-harness`) session.

## Worktree namespace

`worktree` groups the non-launching lifecycle verbs as a discoverable
alias over the top-level commands -- none of these launch Copilot. Use it
to create and manage worktrees from the CLI (e.g. to drive an external
repo's worktrees without opening a session inside it):

```bash
<project> worktree create [--json]      # create; print id + dir, no launch
<project> worktree list [--json]        # this project's worktrees
<project> worktree status <id>          # git status of a worktree
<project> worktree push <id> [--title]  # squash + rebase + push to default branch
<project> worktree finalize [id]        # validate on upstream, then clean up
<project> worktree cleanup              # remove orphaned/finalized worktrees
```

`worktree create` returns the new worktree's id and directory without
launching into it; it appears as `unused` in the project's picker/list
until it has commits or a live session. The equivalent top-level verbs
(`create`, `list`, `status`, `push-changes`, `finalize`, `cleanup`)
continue to work unchanged.

## Session Lifecycle

| Subcommand | Description |
|------------|-------------|
| `resolve` | Interactive picker -- select or create a worktree, emit JSON launch plan |
| `create` | Create a new worktree non-interactively |
| `push-changes` | Push worktree changes to remote default branch (squash, rebase, push) |
| `finalize` | Validate the branch's content is on upstream; prune the worktree/branch only when idle (deferred while a session is live) |
| `mark-complete` | Manual recovery -- set tracking status flag only (hidden from help) |
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

### Deployment ownership (`extensions.agent-worktrees.auto_update`)

A `service.yaml` may set `extensions.agent-worktrees.auto_update: false` to
declare that another deployer (e.g. VAV) owns the service. agent-worktrees
then **skips it in automatic update/install sweeps** (`services --all update`
/ `--all install`). It still appears in `services list`/`status`, and an
**explicit** `services <name> update` (or `--all update --force`) runs it
regardless. Absent the flag, the service defaults to agent-worktrees
management.

## Development

| Subcommand | Description |
|------------|-------------|
| `dev` | Dev venv and test runner |
| `--version` | Print installed version |

## Diagnostics

| Subcommand | Description |
|------------|-------------|
| `activity` | View the persistent worktree/session lifecycle log |

The launcher and lifecycle code record high-level events -- worktree
created/resumed, session started/ended, Copilot exited, mux
attached/detached, changes pushed, worktree finalized/reaped, and
`finalize_skipped_removal` -- to a machine-global JSONL log at
`~/.agent-worktrees/logs/activity.jsonl`. Unlike the per-PID launcher
setup logs under `$TMPDIR/worktree-setup-logs` (capped at the 10 newest
and wiped on reboot), this log persists across reboots and keeps a
rolling 7-day window, so session-lifecycle anomalies can be reconstructed
after the fact. Every event carries the worktree id and, where known, the
session id.

```bash
agent-worktrees activity                       # full retained log (table)
agent-worktrees activity --since 2d            # last 2 days (2d/12h/30m/ISO)
agent-worktrees activity --worktree-id <id>    # one worktree's lifecycle
agent-worktrees activity --event mux_attached  # one event type
agent-worktrees activity --lines 50 --json     # last 50 events as JSONL
```

`activity-log` (append one event) is an internal hook used by the
launcher and is not intended for direct use.

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
cd <copilot-extensions-checkout>\plugins\agent-worktrees
.\scripts\install.ps1 install -ProjectName my-project
```

```bash
# Linux/WSL
cd <copilot-extensions-checkout>/plugins/agent-worktrees
bash scripts/install.sh install --project-name my-project
```

### Remote Deployment

```bash
ssh my-machine "cd <copilot-extensions-checkout>/plugins/agent-worktrees && bash scripts/install.sh update"
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
