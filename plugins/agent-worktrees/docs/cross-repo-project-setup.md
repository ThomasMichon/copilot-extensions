# Cross-repo setup: which marker for which stated need

Adopting another repo on a machine touches up to **three independent
registries/flags**, and it is easy to set the wrong one (or the right one in
the wrong place) and get a confusing result -- a repo that silently gains a
Windows Terminal entry you didn't want, a binstub that never appears, or a
`reference` repo that gets quietly reclassified to `worktree`. This guide is a
decision table: start from what the **operator actually asked for**, and land
on the exact command.

## The three knobs, precisely

| Knob | Lives in | Set by | Actually governs |
|------|----------|--------|-------------------|
| Repo **class** (`reference` / `singleton` / `worktree` / `knowledge`) | `~/.agent-worktrees/repos.yaml` | `agent-worktrees repos add <name> <path> --class <c>` | Whether the repo supports the worktree lifecycle at all (see `agent-worktrees repos --help` for the four classes). |
| Repo-level **agent exposure** | `~/.agent-worktrees/repos.yaml` (the `agent:` field) | `agent-worktrees repos add <name> <path> --agent \| --no-agent` | **The only thing that actually gates the local self-launch Windows Terminal profile.** `terminal_fragment.collect_local_projects()` reads `agent_exposed` from **`repos.yaml`**, not from `projects.yaml` (see the next row) -- `Build-TerminalFragment`'s Python source of truth (`terminal_fragment.py`) never consults the project-level flag. |
| Project **registration** (is this repo a "project" at all: binstub + optional profile) | `~/.agent-worktrees/projects.yaml` | `agent-worktrees register <name>` / `register-project-entry <name>` | Whether the repo gets a `~/.local/bin/<name>` binstub (via `reconcile-binstubs`/`repair --binstubs`) and is even a *candidate* for a Windows Terminal profile at all. Also carries `headless` (CLI-only lifecycle, never launches Copilot interactively -- see `docs/cli-reference.md` § Headless projects), `display_name`, and `wsl`. |

**The trap:** `register-project-entry --expose-agent`/`--no-expose-agent`
*without* `--repo-dir` only writes `projects.yaml`'s `expose_agent` key --
which the terminal-fragment generator does not read. If you only run that
form, the Windows Terminal profile is unaffected either way. To actually
change whether a project's local launcher appears in the Terminal dropdown,
set the **repo-level** flag: `agent-worktrees repos add <name> <path>
--agent|--no-agent` (this is also what `register-project-entry --repo-dir
<path> --expose-agent|--no-expose-agent` does under the hood, since it calls
`repos.add_repo(..., agent=...)` when a repo dir is given). Prefer the
explicit `repos add` form when you specifically want to change agent
exposure without touching anything else about the project.

**The other trap:** `register-project-entry <name> --repo-dir <path>` on an
already-registered `class: reference` repo silently **upgrades it to `class:
worktree`** (a `reference` class is never preserved through that path --
see `cmd_register_project_entry`). If a repo is deliberately `reference`
(read-only, resolve/clone/index only, never edited -- e.g. a shared
dependency you only need to inspect), do not register it as a project at
all; a plain `repos add --class reference` is sufficient and leaves the
class alone.

## Decision table

Start from the operator's stated need, not from "what flag looks closest":

| Stated need | Command | Notes |
|-------------|---------|-------|
| "I just need to find/clone/inspect this repo's path -- no launcher, no worktrees." | `agent-worktrees repos add <name> <path> --class reference` | Never register it as a project. `related resolve <name>` and `repos find <name>` work without any project registration. |
| "I want to launch/resume interactive Copilot sessions in this repo locally (Picker, `<name>` binstub)." | `agent-worktrees register <name>` (or `register-project-entry <name> --repo-dir <path> --expose-agent`) | Gets a binstub **and** a local self-launch Windows Terminal profile. |
| "Another tool (agent-bridge's `control_plane.project`, agent-dispatch same-cell sibling invocation, a cross-repo dispatch script) needs this repo's binstub, but I never want it cluttering the Windows Terminal dropdown or launching interactively from it." | `agent-worktrees register <name> --repo-dir <path>` then `agent-worktrees repos add <name> <path> --no-agent` | Binstub is deployed and kept in sync by `repair --binstubs`/`reconcile-binstubs` regardless of agent exposure -- only the `repos.yaml` `agent` flag needs to be off. This is the `dotfiles`-as-control-plane-project pattern: real infrastructure (see `docs/machine-config.md` in `agent-bridge`), not a stray registration. |
| "This repo should be driven entirely from another session's CLI -- create/push/finalize worktrees in it, but never open an interactive Copilot session inside it directly." | `agent-worktrees register <name> --repo-dir <path> --headless` | See `docs/cli-reference.md` § Headless projects. Orthogonal to agent exposure: set `--no-agent` on the same repo (per the row above) if you also don't want a Terminal entry for it. |
| "I registered this by mistake / the operator says it should not be a project." | Remove the project entirely: unregister from `projects.yaml`, remove its binstub (`agent-worktrees reconcile-binstubs --remove <name>`), then `repair` to regenerate the Terminal fragment. There is currently no single CLI verb for full project deregistration -- see the *Removing a project* note below. | Don't reach for `--no-agent` alone here if the repo was never supposed to be a project at all; that only suppresses the Terminal profile; the binstub (and the project's presence in `projects.yaml`) remains. |

## Removing a project entirely (no dedicated CLI verb yet)

As of this writing there is no `agent-worktrees unregister <name>` command.
To fully undo a project registration (binstub + `projects.yaml` entry, not
just its Terminal profile):

```bash
agent-worktrees reconcile-binstubs --remove <name>   # supported, ownership-checked
```

then remove the entry from `~/.agent-worktrees/projects.yaml` (there is no
CLI verb; use the public `installer.read_projects_registry()` /
`write_projects_registry()` functions, or hand-edit the file, then run
`agent-worktrees repair --terminal` to regenerate the fragment). If you find
yourself doing this often, that is a signal this gap is worth closing with a
real `agent-worktrees repos remove`/project-deregistration companion --
consider filing or picking up that follow-up rather than reaching for
`write_projects_registry()` by hand every time.

## Worked example: a control-plane repo that should not self-launch

`dotfiles` backs `agent-bridge`'s derived control-plane agent roster
(`machines.yaml` `control_plane: {project: dotfiles}` -- see `agent-bridge`'s
`docs/machine-config.md`), so its binstub is load-bearing infrastructure and
must stay. But an operator may not want it self-launching from the Windows
Terminal dropdown on every machine. The correct sequence is:

```bash
# Keep the project registration (binstub stays; agent-bridge keeps working):
agent-worktrees register dotfiles --repo-dir ~/src/dotfiles

# Turn off ONLY the Terminal self-launch profile -- the repo-level flag,
# not the project-level one:
agent-worktrees repos add dotfiles ~/src/dotfiles --no-agent

# Apply:
agent-worktrees repair   # binstubs unchanged, in-sync; Terminal profile regenerated
```

`worktree-manager repos` will show `dotfiles` as `[worktree·no-agent·pr]` and
still `*` (also a project); `agent-worktrees terminal-fragment --explain`
will show it as `(unmanaged -> default column; no-agent)` with no local-agent
row, while its cross-machine SSH shell rows are unaffected.
