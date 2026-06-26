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
| `push-changes` | Push worktree changes to remote default branch (squash, rebase, push). Aborts if the pre-squash fails (`--allow-unsquashed` to opt into individual commits) |
| `finalize` | Validate the branch's content is on upstream; prune the worktree/branch only when idle (deferred while a session is live) |
| `mark-complete` | Manual recovery -- set tracking status flag only (hidden from help) |
| `cleanup` | List and remove orphaned or finalized worktrees |
| `status` | Show worktree git status |
| `status-segment` | Print a styled status-bar segment for the worktree at the cwd (for a tmux/psmux status line) |
| `list` | List worktrees from tracking records |
| `handoff` | Manage handoff prompt state on a worktree |


## Status bar segment (tmux / psmux)

`status-segment` prints a **single styled line** classifying the worktree at
the current directory (or `--path`) relative to its upstream default branch,
for polling from a multiplexer status line. The worktree's tmux/psmux config
(deployed by the installer) wires it up automatically:

```tmux
set -g status-interval 15
set -g status-right '#(agent-worktrees status-segment) %H:%M '
```

The `#()` job runs in the pane's current directory, so the segment classifies
the worktree the pane is actually in. Output is the resolved session title
followed by a colored state block:

| State | Color | Meaning |
|-------|-------|---------|
| `DIRTY` | red | Uncommitted changes, or commits ahead of upstream |
| `FINAL` | green | Clean; work landed / fast-forwardable to upstream |
| `UNUSED` | grey | Clean; no work since the fork point |
| `WIP` | amber | Clean; ahead with content not yet on upstream |
| `ORPHAN` | magenta | No merge base with upstream |

A trailing `↑ahead`/`↓behind` tag mirrors the picker's inline sync status. The
upstream default branch (`main`/`master`) is auto-detected per repo, so the
segment works regardless of which project the binstub belongs to.

Flags: `--path PATH` (classify another worktree), `--fetch` (refresh
behind-counts from the remote -- off by default so the poll stays cheap),
`--plain` (no `#[style]` directives), `--no-title` (state block only).


## Keeping worktrees current

The picker keeps idle worktrees aligned with the default branch, fast-forward
only -- it never rebases, merges, or discards local commits.

- **Inline sync status.** Each worktree row shows its relationship to the
  default branch: `↓N` (behind by N, i.e. stale), `↑N` (ahead by N local
  commits), or `↑A↓B` (diverged). Aligned worktrees show nothing.
- **Auto-fast-forward on resume.** Resuming a *clean* worktree that is
  strictly behind upstream fast-forwards it before the session and setup
  script run, so they see an up-to-date tree. A worktree with uncommitted
  changes or local commits (ahead/diverged) is left untouched. Disable
  per-invocation with `--no-fast-forward`, or globally with
  `auto_fast_forward: false` in `config.yaml`.
- **System menu -> Update stale worktrees.** Fetches once, then fast-forwards
  a single selected eligible worktree or all eligible worktrees in a batch.
  Only clean, strictly-behind worktrees with no local commits are eligible.

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
| `reconcile-plugins` | Reconcile repo-adopted plugin payloads + gated runtimes (JSON output, for launch wrappers) |

### Repo-adopted plugin reconciliation (`reconcile-plugins`)

On an interactive launch, the launcher reconciles the anchor repo's
`.github/copilot/settings.json` `enabledPlugins`: for each
`<name>@copilot-extensions` it ensures the **payload** is installed (throttled
refresh) and the **runtime** matches the installed payload version, per the
plugin's `runtimeScope` (`none` | `universal` | `machine-gated`) and a facility
machine gate (`external-repos.yaml` `deploy_machines`). It is local and
version-keyed, so an unchanged re-launch does ~no work. Runs only after the
direct-dispatch boundary (plain subcommands never trigger it); opt out with
`WORKTREE_NO_RECONCILE=1`. See `docs/install-contract.md` § "Automatic
reconciliation at launch" for the full policy. Headless `copilot -p` launches do
**not** reconcile (repo settings aren't merged there).

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

> **Full reference:** [config-reference.md](config-reference.md) documents
> **every** option — top-level keys, all per-repo keys, the `pr:` workflow
> block, the in-repo `.agent-worktrees.yaml` overlay, backend profiles, and
> the platform-keyed hook maps. The example below is just the common subset.

`~/.{project}/config.yaml`:

```yaml
srcroot: C:\Data\Src              # or ~/src on Linux
machine: my-machine
platform: windows                 # windows | wsl | linux
repo_name: my-project
auto_fast_forward: true           # auto-FF a stale clean worktree on resume (default true)

repos:
  my-project:
    anchor: C:\Data\Src\my-project
    # worktree_root is optional; it defaults to a sibling
    # <anchor>.worktrees folder (here C:\Data\Src\my-project.worktrees),
    # matching the Copilot CLI's /worktree layout. Set it only to override.
    default_branch: main
    remote: origin
```
