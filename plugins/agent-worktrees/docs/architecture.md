# Agent Worktrees -- Architecture

## Two-Layer Design

```
Plugin layer (Copilot CLI)              Runtime layer (Python CLI)
  plugin.json                             ~/.agent-worktrees/
  hooks.json  -- sessionStart hook          .venv/           Python venv
  skills/     -- skills loaded                lib/agent_worktrees/  Python package
                 into every session           bin/             launch-session, bootstrap-check
                                              projects.yaml    registry of adopted repos
                                              repos.yaml       repos registry + source roots

                                            ~/.{project}/      per-project config + state
                                              config.yaml      repos, machine, launch commands
                                              worktrees/       per-worktree tracking YAML

                                            ~/.local/bin/
                                              {project}        binstub (Windows: .cmd)
                                              agent-worktrees  CLI tool
```

The **plugin** installs via `copilot plugin install` and provides skills
and hooks to every Copilot CLI session. The **runtime** installs via
init scripts (`init.ps1`/`init.sh`) and provides the `agent-worktrees`
CLI, session launchers, and per-project binstubs.

## Installed Layout

After full installation and project registration:

```
~/.agent-worktrees/                 # Shared runtime (one per machine)
  .venv/                            #   Python virtual environment
  lib/agent_worktrees/              #   Python package
  bin/                              #   Shell wrappers
    launch-session.{ps1,cmd,sh}     #     Session launcher
    bootstrap-check.{ps1,sh}        #     Session-start health check
  projects.yaml                     #   Registry of adopted projects
  repos.yaml                        #   Repos catalog + source roots
  deploy-manifest.json              #   Provenance (commit, timestamp)

~/.{project}/                       # Per-project config + state
  config.yaml                       #   Machine, repos, launch commands
  worktrees/                        #   Per-worktree tracking
    {worktree-id}.yaml              #     status: active|pushed|complete|finalized|orphaned

~/.local/bin/                       # Binstubs on PATH
  agent-worktrees{.cmd}             #   CLI tool
  {project}{.cmd}                   #   Project launcher (one per registered repo)
  cleanup-worktrees{.cmd}           #   Bulk worktree cleanup
```

## Session Lifecycle

```
{project}                         # launch binstub
  |
  v
launch-session.{ps1,sh}           # pre-flight update, venv activation
  |
  v
agent-worktrees resolve           # picker UI, worktree creation
  |                                 emits JSON launch plan, exits
  v
Setup script runs                  # tools/setup/setup.{ps1,sh} or config-driven
  |
  v
Copilot CLI session                # your work happens here
  |
  v
Post-exit checks                   # detect completion markers
  |
  +-- status: pushed  --> finalize (validate content on master, cleanup)
  +-- status: active  --> preserve worktree for later resume
```

### Two-Phase Completion

Worktree completion is split into two explicit steps:

**Step 1 -- push-changes** (run by the agent during the session):
1. Squashes commits on the worktree branch
2. Rebases onto `origin/{default_branch}`
3. Validates core files
4. Fast-forward merges into local `{default_branch}`
5. Pushes to origin (with retry on rejection)
6. Updates tracking YAML to `status: pushed`

**Step 2 -- finalize** (run by the agent or post-exit hook):
1. Non-mutating validation that branch content is on upstream
2. Removes the worktree directory and branch
3. Updates tracking YAML to `status: finalized`

On push failure, the worktree is preserved and marked `status: orphaned`.

### Recovery Mode

```bash
my-project -Recovery    # Windows
my-project recovery     # Linux/WSL
```

Skips vault credential loading for debugging broken bootstrap
infrastructure.

## Terminal Integration

| File | Platform | Description |
|------|----------|-------------|
| `session-options.sh` | Linux/WSL | Per-session tmux options the launcher stamps onto each session (status bar + behaviors); replaces a global `~/.tmux.conf` |
| `apply-mux-keybinds.sh` | Linux/WSL | **Opt-in** server-global tmux tuning (keystroke passthrough + `escape-time`); run by the user or a machine-restore flow |
| `session-options.ps1` | Windows | Per-session psmux options the launcher stamps onto each session (status bar + behaviors); replaces a global `~/.psmux.conf` |
| `apply-mux-keybinds.ps1` | Windows | **Opt-in** server-global psmux tuning (keystroke passthrough); run by the user or a machine-restore flow |
| `tabby-template.yaml` | Linux | Tabby terminal profile template |

The Windows installer generates **Windows Terminal fragments** at
`%LOCALAPPDATA%\Microsoft\Windows Terminal\Fragments\AgentWorktrees\`
with profiles for each registered project (local + remote SSH machines).

## Multiple Projects

Register multiple repos on the same machine. Each gets its own config
directory (`~/.{project}/`) and binstub. The shared runtime is installed
once:

```bash
agent-worktrees register my-app --repo-dir ~/src/my-app
agent-worktrees register dotfiles --repo-dir ~/src/dotfiles
```

## Update Mechanisms

### Pre-Flight Auto-Update

The `launch-session` wrapper checks for new commits on each session
launch. If the anchor repo has changes affecting the worktree manager,
the launcher re-runs the installer automatically.

Skip with `--no-update` or `WORKTREE_NO_UPDATE=1`.

### Plugin Marketplace Update

```bash
copilot plugin update agent-worktrees@copilot-extensions
```

Or use the built-in update command:

```bash
agent-worktrees update
```

### Version Checking

All three version sources must agree:

| File | Purpose |
|------|---------|
| `plugin.json` | Marketplace version detection |
| `pyproject.toml` | Runtime `--version` output |
| `.github/plugin/marketplace.json` | GitHub-hosted marketplace catalog |

See [CONTRIBUTING.md](../../CONTRIBUTING.md) for versioning details.

## Picker Pivot Registry (Cross-Plugin)

The interactive Textual picker (`picker_tui/engine.py`) shows top-level
**pivots** -- Worktrees, Maintenance, Profiles. Another plugin, installed in
its **own separate venv**, can contribute an additional pivot without
agent-worktrees importing its Python. Because each plugin installs standalone,
setuptools entry-points do not cross venvs; a **filesystem manifest registry**
does.

```
~/.agent-worktrees/pivots/<name>.json     # one manifest per contributed pivot
    { "label": "Tasks", "after": "Worktrees",
      "list": ["agent-dispatch", "inbox", "--machine", "{machine}"],
      "entry":   { "id": "id", "title": "title",
                   "worktree": "target_worktree", "badges": ["labels"] },
      "actions": [ { "label": "Abandon", "run": ["agent-dispatch", "abandon",
                     "{task_id}", "--permit"] }, ... ] }
```

- **Discovery** (`picker_tui/pivots.py`): the picker scans the directory at
  startup (and on `r`-refresh), weaving each manifest into the built-in order by
  its `after` hint. `AGENT_WORKTREES_PIVOTS_DIR` overrides the location (tests,
  escape hatch). A missing dir or a malformed manifest is skipped -- never fatal.
- **Data + actions** (`picker_tui/tasks.py`): the `list` command is run as a
  **subprocess** (argv[0] resolved on `PATH`) on a background thread, cached per
  machine, and expected to print a JSON array. `actions` argv templates are run
  the same way. Placeholders (`{machine}`, `{worktree}`, `{id}`/`{task_id}`,
  `{title}`, plus any entry field) are substituted at activation time. Data
  flows **only** through the contributing plugin's CLI -- never a cross-venv
  import -- so the seam stays generic for future pivots (Bridges, Containers, ...).
- **Dispatch is kind-keyed, not index-keyed.** Built-in pivot logic switches on
  the pivot *kind* (`worktrees`/`maintenance`/`profiles`/`registered`), so an
  inserted pivot never renumbers the built-ins.

### Action kinds: external (CLI) vs internal (navigation)

An `actions` entry is one of two shapes:

- **External (default)** -- `{"label": …, "run": [argv…], "confirm": false}`.
  The `run` template is spawned as a subprocess (as above). This is the right
  choice for anything that *does work* (open, abandon, retry, …).
- **Internal (picker navigation)** --
  `{"label": …, "kind": "internal", "verb": "jump-host", "args": ["{worktree}"]}`.
  No subprocess is spawned; the picker handles the `verb` itself against its own
  state. `args` (optional) become the template the handler substitutes. This
  exists because a subprocess **cannot** move the picker's cursor, switch a
  machine tab, or reveal hidden rows -- state a CLI has no handle on.

  Handlers live in `engine.PickerScreen._internal_pivot_action`; the registry is
  intentionally tiny and defensive (an unknown `verb` is a reported failure,
  never a raise). The first verb is:

  - **`jump-host`** -- navigate to the Worktrees view, switch to the host machine
    tab of the worktree named by `args`/`worktree`, reveal hidden if it is a
    bridge/system row, and highlight it (matched by **stable worktree id**, never
    a live list index). The same primitive backs the built-in *Jump to host*
    per-worktree action for bridge/system worktrees (#1424).

**Boundary (deliberate).** Modules contribute a *generic task-list* pivot plus
external/internal actions -- **not** arbitrary custom render surfaces or
in-process Python. The CLI-over-manifest seam is the cross-venv-correct answer to
"each plugin installs in its own venv"; richer per-module rendering is explicitly
out of scope (#1425).

