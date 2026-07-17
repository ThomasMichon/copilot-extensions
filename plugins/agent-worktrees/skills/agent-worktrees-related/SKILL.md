---
name: agent-worktrees-related
description: >
  Manage a repo's per-project "related repos" index -- the directional,
  committed list of OTHER repos relevant to the current one, with their role,
  locus (where work happens), delegation target, and a narrative doc. Use when
  asked to link/unlink a related repo, list or show related repos, set the
  primary repo, scaffold or open a related repo's narrative, or when you are
  asked to work on another repo from here and it is not yet linked. Trigger
  phrases include:
  - 'link repo'
  - 'link a related repo'
  - 'add a reference to <repo>'
  - 'related repo'
  - 'related repos'
  - 'list related'
  - 'which repos are related'
  - 'cross-repo index'
  - 'set the primary repo'
  - 'default project repo'
  - 'related narrative'
  - 'unlink repo'
---

# Agent Worktrees -- Related Repos

A **control-plane** repo (e.g. a dotfiles/harness repo) coordinates work across
several OTHER repos. This skill manages the **directional, per-project** index
of those repos -- *from the current repo's point of view* -- committed in-repo
at `<repo>/.agent-worktrees/related.yaml`, with a plain-markdown narrative per
related repo under `<repo>/.agent-worktrees/related/<name>.md`.

It complements (does **not** duplicate) the **global** repos registry
(`agent-worktrees repos`, `~/.agent-worktrees/repos.yaml`): related entries are
**keyed by global-registry names** and add only **relationship** (role,
summary, doc), **locus** (where to work), and **delegate** (how to hand off).
Checkout paths, class, and remote still resolve from the global registry --
never restate them here.

> **Role / locus / delegate here are *not* the registry `class`.** The registry's
> `class` (`reference` / `singleton` / `worktree`) is the **editing model** — a
> third, orthogonal axis. Don't collapse "worktree / owner / delegate" into one
> taxonomy: `worktree` is a class, "owner" is a landing policy, and `delegate` is
> a locus/handoff. See
> [`agent-worktrees-repos` § Class is not role, locus, or delegate](../agent-worktrees-repos/SKILL.md).

> To actually *work* on a related repo as a good citizen (honor class, locus,
> and delegation), use the **`working-cross-repo`** skill, which builds on
> `related resolve`.

## The model

Full annotated example: [`references/related.yaml`](references/related.yaml).
At a glance:

```yaml
# <repo>/.agent-worktrees/related.yaml
primary: odsp-web                 # the default/primary related repo
related:
  odsp-web:
    role: product                 # product|dependency|consumer|tooling|docs|sibling
    summary: "Primary product monorepo we ship changes to."
    locus:
      preferred: codespace        # local | machine:<key> | codespace | container
      codespace: { repo: org/odsp-web-codespaces,
                   workspace_folder: /workspaces/odsp-web }   # cloud: any machine
      container: { repo: org/odsp-web-codespaces,
                   workspace_folder: /workspaces/odsp-web,
                   machines: [dev6] }                         # local fleet: dev6 only
    delegate: { via: agent-codespaces }
```

- **`role`** -- what the repo is to this one (free-form; common values above).
- **`locus`** -- *where work actually happens*: `local`, `machine:<key>`,
  `codespace`, or `container`; `machines:` lists the boxes a *local* checkout is
  available on (the per-machine availability the per-*platform* global registry
  can't express).
  - **`codespace:`** -- GitHub CodeSpace hints (`repo` / `machine` / `location`
    / `workspace_folder`). CodeSpaces run in the cloud, so they work from **any**
    machine.
  - **`container:`** -- a local Docker dev-container fleet (`repo` /
    `workspace_folder` + a `machines:` list scoping it to the fleet hosts). A
    container fleet is **local**, so `machines:` restricts where it can run
    (e.g. `[dev6]`). `workspace_folder` is the checkout path the venue lands in
    (often *not* the venue `repo` name).
- **`delegate.via`** -- how to hand work to the agent that owns the repo:
  `agent-bridge`, `agent-codespaces`, `agent-containers`, or `none`.
- **`primary`** -- the default repo (used by `related resolve` with no name).

## CLI

All commands take `[--repo PATH]` to target a specific checkout; the default is
the git repo containing the current directory.

```
related list [--role R] [--json]         List related repos (+ the primary)
related show <name> [--json]             Show one entry + global-registry context
related add <name> [opts]                Link a repo + scaffold its narrative
related remove <name>                    Unlink (leaves the narrative doc)
related doc <name>                       Print (scaffold if missing) the narrative
related primary [<name>]                 Show or set the primary
related resolve [<name>]                 How to work on it from here (see working-cross-repo)
```

`add` options: `--role R`, `--summary S`, `--doc PATH`, `--delegate D`,
`--locus L`, `--machines a,b`, `--primary`, `--no-scaffold`; for a codespace
locus `--cs-repo R --cs-machine M --cs-location L --cs-workspace DIR`; and for a
container locus `--container-repo R --container-workspace DIR
--container-machines a,b`.

## When to link a repo

- **Explicit** -- the user asks to "link", "add a related repo", "track repo X
  here". Run `related add X ...`.
- **Implicit** -- you are asked to make a change in repo **B** from repo **A**,
  and **B is not yet in A's `related.yaml`**. Offer to link it first
  (`related add B`), so the relationship and its narrative are captured for next
  time, then proceed via `working-cross-repo`.

A linked name should exist in the **global** registry (`repos add <name> ...`);
`related add` warns when it doesn't but still records the link.

## Narrative docs

`related add` scaffolds `related/<name>.md` -- a short narrative *from this
repo's POV*: why the repo matters here, how to make a change, and its rules.
The template bakes in the **never-hardcode-a-path** rule (resolve checkouts with
`agent-worktrees repos find <name>`). Fill in the TODO sections; the docs are
committed alongside `related.yaml`.

## Common workflows

```bash
# Link the product repo, preferred via a CodeSpace, and make it primary
related add odsp-web --role product --primary \
  --locus codespace --cs-repo org/odsp-web-codespaces \
  --cs-machine largePremiumLinux256gb --cs-location EastUs \
  --delegate agent-codespaces

# Link a tooling repo that only lives on some machines
related add copilot-extensions --role tooling \
  --locus machine:dev6 --machines dev6,cloud1 --delegate agent-bridge

related list                 # review the index + the primary
related show odsp-web        # entry + [class] path remote from the registry
related doc odsp-web         # open/scaffold the narrative to fill in
```
