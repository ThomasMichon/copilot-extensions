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

> The end-to-end narrative — states, the two landing paths, held/follow-up and
> serial-vs-parallel PRs — is in
> [Worktree Lifecycle & Change Management](worktree-lifecycle.md). This table is
> the verb catalog.

| Subcommand | Description |
|------------|-------------|
| `resolve` | Interactive picker -- select or create a worktree, emit JSON launch plan. `--new` creates + launches a **muxed interactive** session (refused without a TTY) |
| `create` | Create a worktree **programmatically** -- no launch, no mux; prints id + path (add `--json`). The path for agents and daemons |
| `push-changes` | Push worktree changes to remote default branch (squash, rebase, push). Aborts if the pre-squash fails (`--allow-unsquashed` to opt into individual commits) |
| `finalize` | Validate the branch's content is on upstream; prune the worktree/branch only when idle (deferred while a session is live) |
| `mark-complete` | Manual recovery -- set tracking status flag only (hidden from help) |
| `cleanup` | List and remove orphaned or finalized worktrees |
| `status` | Show worktree git status |
| `status-segment` | Print a styled status-bar segment for the worktree at the cwd (for a tmux/psmux status line) |
| `status-context` | Print a styled left status-bar segment: machine, environment, and repo:id4 for the worktree at the cwd |
| `status-updater` | Background loop that keeps a session's `@aw_ctx`/`@aw_seg` status vars fresh **off the paint path** (no per-render binstub spawn) |
| `list` | List worktrees from tracking records |
| `handoff` | Manage handoff prompt state on a worktree |

## Pull-request workflow

The `pr-*` family drives PR-gated landing (config `pr.enabled` / `pr.required` —
see [config-reference.md § PR workflow](config-reference.md)). `push-changes`
then targets the *feature* branch, never the default branch. The verbs are
self-describing: `pr-status` prints the active `flow:` profile, and `pr-merge`
refuses (naming the reason) on a repo where no consent label is bound. Full
narrative in [worktree-lifecycle.md § Landing the change](worktree-lifecycle.md).

| Subcommand | Description |
|------------|-------------|
| `create-pr` (alias `pr-create`) | Squash the worktree's commits, publish the PR head branch, and open the PR. Flags: `--title`, `--body`/`--body-file`, `--draft` (open not-ready-for-review), `--new` (force a fresh head branch for a parallel PR), `--no-open` (push only), `--hold` (deprecated alias for `--draft`) |
| `pr-ready` | Move a draft PR **out of draft** — request review |
| `set-pr` | Record PR metadata (`--url`, `--number`) when the PR was opened out of band by a provider sub-agent |
| `pr-status` | Show tracked PR metadata + live verdict / conflict / merge state; prints the `flow:` profile and flags pull-forward once merged |
| `pr-watch` | Block until the PR moves (`wait <repo> <pr> [--until …]`) and wake the caller with a race-proof cursor; `cursor <repo> <pr>` prints the current baseline |
| `pr-merge` | Signal **merge consent** on an approved PR (applies the bound `automerge_label`); the review gate merges when satisfied. `--all` / `--loop` for sweeps |
| `pr-complete` | Reconcile the worktree after its PR merged — fast-forward past the squash-merge (or rebase), dropping the local commits the squash already absorbed |
| `pr` | Namespace grouping the `pr-*` verbs |

`get pr-profile` / `get pr-required` / `get pr-provider` report the repo's PR
disposition (`direct` | `pr-human-merge` | `pr-agent-merge`) so you know which
verbs apply before signing off.



## Status bar segment (tmux / psmux)

`status-segment` prints a **single styled line** classifying the worktree at
the current directory (or `--path`) relative to its upstream default branch.
The launcher wires it into each session's bar **per session** (it does **not**
own your global `~/.tmux.conf` / `~/.psmux.conf`) -- but the bar does **not**
poll this command on its render path. Instead the `status-updater` watcher
calls it *off* the render path and pushes the result into the `@aw_seg` session
option, which the bar reads with zero per-render spawn (see *Off the paint
path* below):

```tmux
set status-interval 15
set status-right '#{@aw_seg} %H:%M '
```

Output (what the watcher stores in `@aw_seg`) is the resolved session title
followed by a colored state block:

| State | Color | Meaning |
|-------|-------|---------|
| `DIRTY` | red | Working tree has uncommitted changes (modified, staged, or untracked) |
| `FINAL` | green | Clean; work landed / fast-forwardable to upstream |
| `UNUSED` | grey | Clean; no commits **and no conversation** since the fork point |
| `CONVO` | teal | Clean; no commits, but the session held conversation turns (annotated with the turn count, e.g. `CONVO 12💬`) |
| `WIP` | amber | Clean; ahead with content not yet on upstream |
| `ORPHAN` | magenta | No merge base with upstream |

A trailing `↑ahead`/`↓behind` tag mirrors the picker's inline sync status. The
`CONVO` state refines `UNUSED` using session turn-count detection: a worktree
with no committed work is only truly *unused* when its session also held zero
turns; once it has held conversation, it renders as `CONVO` with the turn
count (mirroring the picker's `💬` annotation and `cleanup`'s
"conversation-only" preservation). The upstream default branch (`main`/`master`)
is auto-detected per repo, so the segment works regardless of which project the
binstub belongs to.

Flags: `--path PATH` (classify another worktree), `--fetch` (refresh
behind-counts from the remote -- off by default so the poll stays cheap),
`--plain` (no `#[style]` directives), `--no-title` (state block only).

### Machine-readable state: `list --json --classify`

`list --json --classify` enriches each worktree record with its git-derived
classification (`state`, `ahead`, `behind`, `dirty`) so a consumer -- notably
the multi-machine picker -- gets canonical state per machine (a remote's own
state travels in its `list` output over SSH; the local picker cannot
git-classify a remote worktree). Classification is **opt-in** because it costs
~5 git calls per worktree; a bare `list --json` stays fast.

The emitted `state` draws from the **same `WorktreeState` vocabulary the status
bar uses**, including the session-derived `convo` (a clean, commit-less
worktree whose session held conversation turns -- the lowercase data-contract
form of the bar's teal `CONVO` block). Centralized in
`git_ops.refine_state_with_session` so the bar and the picker can never drift
apart. Without `--classify`, records carry no `state` key.

### Left segment: worktree identity

`status-context` prints the **left** side of the bar -- the worktree's
identity rather than its git state. The launcher applies it **per session**
alongside the right segment:

```tmux
set status-left-length 100
set status-left '#{@aw_ctx} '
```

It renders three fields:

| Field | Style | Source | Example |
|-------|-------|--------|---------|
| Machine | Black, bold | Tracking record `machine` (else live host detection) | `lambda-core` |
| Environment | Badge: white on an OS-keyed background (win=blue, wsl=purple, linux=orange) | Platform short code, matching the worktree id | `win` |
| Repo : id4 | Black | Record `repo` + the worktree id's 4-char suffix | `copilot-extensions:8e45` |

Like the right segment, the watcher classifies the worktree by `--path` and
stores the result in `@aw_ctx` (once -- identity is static for a session).
Outside a tracked worktree it falls back to live machine/platform detection and
omits the `repo:id4` field. Flags: `--path PATH`, `--plain` (no `#[style]`
directives).

### Off the paint path: `status-updater` (psmux + tmux)

Polling `#(agent-worktrees status-segment)` directly from the bar is tolerable
on **tmux**, which runs `#()` jobs asynchronously and caches the result between
`status-interval` ticks. **psmux** (Windows) does not: it runs `#()`
synchronously **in the render path**, so a ~600 ms binstub spawn (two fresh
PowerShell processes for the two segments) fired on every repaint. Under
Copilot's high-framerate TUI that made muxed sessions sluggish on lambda-core
and unusable on slower hosts.

`status-updater` is the **single, cross-platform watcher** that fixes this on
both muxes. The bar reads **precomputed session options** instead of spawning:

```tmux
set status-left  '#{@aw_ctx} '          # identity  (machine | env | repo:id4)
set status-right '#{@aw_seg} %H:%M '     # disposition block + live clock
```

The launcher spawns one detached updater per session (psmux via
`launch-session.ps1`, tmux via `launch-session.sh`):

```text
agent-worktrees status-updater --session wt-<id> --mux <psmux|tmux> --path <worktree>
```

It renders **in-process** (paying Python import once, never re-spawning the
binstub), pushes the static identity into `@aw_ctx` once, and refreshes the
dynamic disposition into `@aw_seg` every `--interval` seconds (default 15) via
the cheap native `set-option` verb -- exiting on its own when `has-session`
shows the session is gone. Between updates the bar does **zero** process work;
the mux only re-runs the strftime `%H:%M` clock. Non-worktree sessions leave
the vars unset and render a blank bar. The launcher may (re)spawn the updater
on every attach/join: an `@aw_updater` token elects a single live instance, so
older ones self-retire (the cross-platform equivalent of the old `flock`).

Flags: `--session` (required), `--mux {psmux,tmux}` (default: auto-detect),
`--path PATH` (worktree to classify), `--interval N` (seconds, min 2).



agent-worktrees does **not** deploy, overwrite, or delete your global
`~/.tmux.conf`. The launcher applies the bar and session behaviors with
`tmux set -t <session>` (session-scoped, no `-g`) when it creates or rejoins a
worktree session, so your personal tmux config and any ad-hoc tmux sessions
sharing the same server are left untouched. The single source of truth is the
deployed `~/.agent-worktrees/bin/session-options.sh`.

Settings that **cannot** be session-scoped -- server-global `escape-time` and
the keystroke-passthrough root key table -- are **not** applied automatically
(they would leak onto every session on the server). They live in the opt-in
`~/.agent-worktrees/bin/apply-mux-keybinds.sh`. Run it once per machine, or wire
it into a machine-restore flow, if you want that behavior: it persists a
clearly-marked managed block in `~/.tmux.conf` (so it survives server restarts)
**and** applies to any running server. The installer never touches
`~/.tmux.conf` -- only this script does, and only when you elect to run it
(`--no-persist` tunes the running server without writing the file; deleting the
marked block removes the settings).

> Both tmux (Linux/WSL) and psmux (Windows) are configured **per session** by
> the launcher: `session-options.{sh,ps1}` stamps the bar + behaviors with
> `set -t` (no `-g`), and the server-global keystroke passthrough lives in the
> opt-in `apply-mux-keybinds.{sh,ps1}`. agent-worktrees no longer owns
> `~/.tmux.conf` or `~/.psmux.conf`.


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
| `update` | Re-deploy runtime from repo source + refresh marketplace plugin, then fast-forward the managed repo anchor(s) so in-repo config bindings deploy alongside the plugin (`--no-anchor-sync` to skip) |
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
