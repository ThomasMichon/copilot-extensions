---
name: agent-worktrees-repos
description: >
  Manage the repos registry — catalog of known repositories, source roots,
  local checkout paths, and per-repo management class (reference / singleton
  / worktree). Use when asked to find, add, clone, migrate, status-check, or
  sync repos, or when deciding how to safely edit a repo. Trigger
  phrases include:
  - 'find repo'
  - 'where is repo'
  - 'clone repo'
  - 'register repo'
  - 'list repos'
  - 'repo status'
  - 'sync repos'
  - 'pull repos'
  - 'source root'
  - 'srcroot'
  - 'repos registry'
  - 'git-repos'
  - 'migrate registry'
  - 'add repo'
  - 'remove repo'
---

# Agent Worktrees Repos Registry

Manage the repos registry at `~/.agent-worktrees/repos.yaml` — the
**canonical** catalog of known repositories across platforms. This
registry supersedes the legacy `~/.git-repos` file; import an existing
one with `agent-worktrees repos migrate`.

## Repo Classes — How a Checkout Is Edited

Every repo has a management **class** that says how the facility
interacts with its local checkout. This is the single most important
field: it determines whether (and how) you may edit the repo.

| Class | Editing model | Use for |
|-------|---------------|---------|
| **reference** | Read-only. Tracked only for path resolution, cloning, and indexing (VEI). **Never edited locally.** | Upstream deps, consumer repos, indexed mirrors |
| **singleton** | Editable as a **single anchor checkout**, no worktree isolation. One flow at a time. | Repos where worktrees are overkill or unsupported |
| **worktree** | Full agent-worktrees lifecycle: **concurrent-flow safe**, edits/stages/commits isolated in per-task worktrees until the final push. | Owned/contributed repos edited by multiple agent flows (e.g. `copilot-extensions`, your control-harness repo) |

**Why this matters:** multiple agent flows editing the same anchor
checkout collide on working-tree state, staging, and commits. A
**worktree**-class repo avoids this — each task gets an isolated git
worktree, and changes only converge at push time. Editing a
worktree-class repo's anchor directly is a bug.

> Legacy mapping: the old `type: project` becomes `worktree`, and
> `type: repo` becomes `reference`. Both still load.

### Class is *not* role, locus, or delegate

`class` is one of **three orthogonal axes** that describe a repo. They are easy
to conflate — a phrase like "worktree / owner / delegate" wrongly mixes all
three into one list — so keep them straight:

| Axis | Field · home | Answers | Values |
|------|--------------|---------|--------|
| **class** | `class` · repos registry (this skill) | *How is the checkout edited?* | `reference` \| `singleton` \| `worktree` |
| **role** | `role` · `related.yaml` ([`agent-worktrees-related`](../agent-worktrees-related/SKILL.md)) | *What is this repo to another repo?* | `product` \| `dependency` \| `consumer` \| `tooling` \| `docs` \| `sibling` |
| **locus + delegate** | `locus` / `delegate` · `related.yaml` | *Where does work happen, and who is handed it?* | locus `local` \| `machine:<k>` \| `codespace` \| `container`; delegate `agent-bridge` \| `agent-codespaces` \| `agent-containers` \| `none` |

The two words that most often get mistaken for classes are **not** classes:

- **"owner" / "direct-push, no PR"** is a *landing policy*, not a class. A repo
  you own is typically `class: worktree` (still edited via isolated worktrees)
  with PR mode **off**, so `finalize` pushes straight to the default branch; a
  repo you *contribute* to is the same class with PR mode **on**. Class governs
  *edit isolation*; the `pr:` config governs *how work lands* — see
  [worktree-lifecycle.md § Landing the change](../../docs/worktree-lifecycle.md#3-landing-the-change).
- **"delegate"** is the *locus/handoff* axis (`delegate.via` in `related.yaml`) —
  hand the work to another machine / CodeSpace / container's agent — orthogonal
  to how the checkout is classed.

A single repo is described by all three axes at once. Example: `copilot-extensions`
is **class** `worktree` (edit in isolated worktrees), **role** `tooling` (what it
is to a control repo), **locus** `local` + **delegate** `none` (worked in place),
under an owner / direct-push **landing policy** (no PR). "worktree", "tooling",
and "owner" are three different answers, not one field.

## Editing a Worktree-Class Repo (Collision-Free)

For repos classed **worktree** (the default for any repo you contribute
to), do **not** edit the anchor checkout. Use the agent-worktrees
lifecycle so concurrent flows stay isolated:

```bash
# One-time: adopt the repo as an agent-worktrees project
agent-worktrees register <repo>          # or: repos add <name> <path> --class worktree

# Per task: create an isolated worktree, edit there, push, finalize
#   Agents/automation: create the worktree WITHOUT launching a session. This
#   prints the worktree path; cd into it and edit in your CURRENT session.
agent-worktrees create                   # prints id + path (add --json for a plan)
#   Interactive (human at a terminal) only: launch a fresh muxed session in a
#   new worktree. Refused without a TTY -- never use it from a tool call.
<repo> --new
# ... edit, commit inside the worktree path ...
<repo> push-changes                      # push to the remote default branch
<repo> finalize                          # validate on upstream + clean up the worktree
```

Edits, stages, and commits live in the per-task worktree and only reach
the shared remote on `push-changes`. This is what prevents two parallel
flows from clobbering each other mid-edit.

**Singleton** repos: edit the anchor directly (only one flow at a time).
**Reference** repos: never edit — read, clone, or index only.

## CLI Commands

All commands are accessed via `agent-worktrees repos <subcommand>`:

```
repos list [--class reference|singleton|worktree] [--json]
repos find <name>
repos add <name> <path> [--class C] [--remote URL]
                        [--default-branch B] [--tags a,b] [--contributing PATH]
repos remove <name>
repos clone <remote> [--name N] [--target PATH]
repos srcroot [--set PATH] [--platform windows|wsl|linux]
repos migrate [--default-class reference|singleton|worktree]
repos status [--tag T] [--class C] [--json]
repos sync [--tag T] [--class C]
```

## Common Workflows

### Migrate the legacy ~/.git-repos registry

Run once per machine/environment to import an existing `~/.git-repos`
into `repos.yaml`. Adopted projects (in `projects.yaml`) are classified
as **worktree**; everything else defaults to **singleton** (override with
`--default-class`). The legacy file is left in place — remove it after
verifying.

```bash
agent-worktrees repos migrate
agent-worktrees repos list          # verify, then reclassify as needed
```

Reclassify any entry by re-adding it with the right class:

```bash
agent-worktrees repos add copilot-extensions D:\Src\copilot-extensions --class worktree
```

### Set up source roots

```bash
agent-worktrees repos srcroot --set D:\Src --platform windows
agent-worktrees repos srcroot --set ~/src --platform wsl
```

### Register an existing repo

```bash
agent-worktrees repos add my-lib D:\Src\my-lib --class reference \
  --remote https://github.com/org/my-lib.git
```

### Find where a repo is checked out

```bash
agent-worktrees repos find my-project
# → D:\Src\my-project
```

If the repo has no local path but has a remote, suggest cloning it.

### Check status / sync across repos (git hygiene)

```bash
agent-worktrees repos status                 # branch, dirty, ahead/behind
agent-worktrees repos sync --tag facility    # fetch + ff-merge (skips dirty)
```

`sync` only fast-forwards the default branch and **skips** any repo whose
working tree is dirty or whose checkout is on a non-default branch — it
never force-updates or creates merge commits.

## Data File

The registry lives at `~/.agent-worktrees/repos.yaml`. Full annotated example:
[`references/repos.yaml`](references/repos.yaml). At a glance:

```yaml
srcroot:
  windows: D:\Src
  wsl: ~/src
repos:
  copilot-extensions:
    class: worktree                # reference | singleton | worktree
    remote: "https://github.com/ThomasMichon/copilot-extensions.git"
    default_branch: main
    windows: D:\Src\copilot-extensions
    wsl: ~/src/copilot-extensions
```

### Schema

| Field | Description |
|-------|-------------|
| `class` | `reference` \| `singleton` \| `worktree` (see above) |
| `remote` | Git remote URL |
| `default_branch` | Branch `status`/`sync` track (default: current) |
| `tags` | Filter tags for batch ops (`facility`, `work`, …) |
| `contributing` | Path to CONTRIBUTING.md — read before editing |
| `windows`/`wsl`/`linux` | Per-platform checkout paths |

## Integration Points

- **Adopt flow**: reads `srcroot` to suggest clone locations for WSL
- **WSL provision**: uses `srcroot.wsl` for clone targets
- **Worktree lifecycle**: `worktree`-class repos are adopted as
  `projects.yaml` projects; the registry is the broader catalog
- **ACP bridge**: queries the registry to find local checkouts
- **`projects.yaml`**: remains authoritative for adopted projects;
  `repos.yaml` is the superset catalog with class + hygiene metadata
