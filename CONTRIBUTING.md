# Contributing to Copilot Extensions

## Contribution boundary

This marketplace accepts **general-purpose capabilities** that can be used
without a particular person's private state or a particular organization's
internal systems, identity, process, or data.

Not welcome here:

- Personal/operator-specific workflows, machine inventory, private state, or
  experiments whose audience is one harness. Keep those in the adopter's
  private control/knowledge repo.
- Organization-specific workflows, internal systems, or company-bound policy.
  Those belong in that organization's internal marketplace.

A generic engine may live here while an organization-specific policy/config
plugin lives in its internal marketplace. If removing personal and
organizational assumptions would change the capability's purpose, this is not
its destination.

## Contribution flow (PR-required)

**Every change lands through a pull request — direct pushes to `main` are
blocked.** This is enforced on three layers that agree:

1. **Tooling** — `.agent-worktrees/config.yaml` sets `pr.required: true`, so
   `agent-worktrees push-changes` refuses direct-to-`main` and the PR-workflow
   git-hooks block committing to `main` / pushing a worktree branch directly.
2. **Branch policy** — a GitHub repository ruleset ("Default-branch policy:
   PR-required + non-blocking Copilot review") carries a `pull_request` rule (+
   `non_fast_forward`) that blocks direct pushes to `main` server-side, for
   everyone (no bypass).
3. **Review** — the same ruleset's `copilot_code_review` rule auto-requests a
   **non-blocking** Copilot review on every PR (it is a review, not a required
   status check, so it never gates the merge).

### The flow every agent (and human) uses

```bash
copilot-extensions create            # isolated worktree (no mux/session)
#   …edit in the returned worktree path…
#   Complete the documentation-impact review below.
copilot-extensions create-pr         # squashes the worktree, pushes pr/<slug>,
                                     # and (auto_open) opens the GitHub PR
#   → Copilot posts its review on the PR (non-blocking; never a merge gate)
#   …address anything worth addressing, re-run push-changes to update the PR…
#   → merge regardless of remaining low/medium findings -- there is no
#     approval or "clean" verdict to wait for; see the note below.
copilot-extensions pr-merge ThomasMichon/copilot-extensions <#> --now   # MANUAL squash-merge (you own the merge)
copilot-extensions finalize          # clean up the worktree
```

- **Update an open PR** with `copilot-extensions push-changes` (it re-pushes the
  `pr/<slug>` head; it will NOT land on `main`).
- **Merge is deliberately manual.** No auto-merge label is bound (the repo's
  `pr-self-merge` profile authorizes the submitter to merge directly): open the
  PR, give Copilot's review a bounded window of **about 5 minutes** (order of
  minutes, not hours) to land, then squash-merge it yourself with `pr-merge <#>
  --now` (equivalent to a plain `gh pr merge <#> --squash --delete-branch`, but
  it resolves the right account and squashes uniformly). (0 approvals are
  required by policy — a solo owner can't approve their own PR, and Copilot's
  review is advisory.) **As the repo's maintainer, self-merge after that ~5
  minute window regardless of whether a review has posted yet** -- address any
  findings that *did* land in time, but do not extend the wait chasing a
  verdict that may never come; a review arriving after merge can still be
  addressed in a follow-up commit/PR.
- **There is no verdict to wait for.** Copilot's review is always a
  non-blocking comment, never a required approval, no matter how many
  findings it reports or how many rounds you go through. Address genuinely
  valuable findings, explain or dismiss the rest, and merge -- do not leave a
  PR sitting untouched after a review lands "waiting" for a subsequent
  approval or a zero-finding pass that may never come. If a PR has had a
  review and nothing has happened since, that is a stuck PR: merge it or
  explicitly abandon it, don't leave it idle.
- **Do not comment `@copilot review` (or similar) to request a fresh pass.**
  An `@copilot` mention on GitHub does not nudge the `copilot-pull-request-reviewer`
  bot -- it delegates a task to the separate Copilot **cloud coding agent**,
  which will start pushing its own commits directly to your PR branch (it can
  and will act on open review findings, which may or may not be what you
  want, and consumes its own credit budget independent of your session).
  The review bot already re-runs automatically on every push; if you want a
  fresh verdict, just push a commit.
- **Never** `git push origin main` or `push-changes` direct-to-`main`; both the
  tooling and the branch policy reject it. Break-glass (a genuine recovery)
  means temporarily relaxing the ruleset — not routing around it.

### Parent trackers stay open across partial slices

Use `Refs` or `Part of` for an issue that a PR only advances. Do not put a
closing keyword next to that issue number, even in a sentence saying the PR
does *not* close it: GitHub recognizes the keyword/reference pair without
honoring the negation.

Before merging a partial slice, inspect its closing references:

```bash
agent-worktrees repos gh ThomasMichon/copilot-extensions -- pr view <number> --repo ThomasMichon/copilot-extensions --json closingIssuesReferences
```

The result must not contain an unfinished parent tracker. After merge, verify
both the PR's merged state and the parent issue's
expected open state. If accidental closure occurs, remove the closing phrase,
reopen the issue, and record the correction; a null `commit_id` in an issue
closure event is not by itself evidence that an agent called an issue-close API.

### Documentation impact (required before opening a PR)

Assess the final diff and repeat the assessment after material scope or
implementation changes. Apply this review to every change classification,
including bug fixes, compatibility repairs, and below-altitude work.

1. Update the authoritative documentation affected by changes to behavior,
   guarantees, interfaces, configuration, output/error handling, process
   lifecycle, operating procedures, or platform support. Keep already-accurate
   documentation unchanged.
2. Reconcile architectural changes with their governing vision and patterns.
   Revise a vision when intended behavior or guarantees change; put
   implementation details in architecture or operating documentation.
3. Include a **Documentation impact** statement in the PR description, linking
   the documentation updated or explaining why existing documentation remains
   accurate and complete.

Reviewers confirm that the statement and documentation match the final diff.
Treat a missing assessment or inaccurate affected documentation as unfinished
work.

## Release & Versioning

### Marketplace architecture

This repo is a **Copilot CLI plugin marketplace** — a GitHub-hosted
registry of plugins that machines install via `copilot plugin marketplace
add ThomasMichon/copilot-extensions`. The marketplace catalog lives at
`.github/plugin/marketplace.json` and lists every plugin with its current
version. The Copilot CLI reads this file to determine available updates.

> **Deploy with `<repo> update` — never hand-run `copilot plugin update`.**
> `copilot plugin update` on its own refreshes only a plugin's *payload* (cached
> source + skills) — it does **not** rebuild a runtime (venv/binstubs/service),
> and if the version wasn't bumped it silently no-ops ("already at latest"). Do
> not chase that gap with per-plugin installers by hand; use the one unified
> flow: **`<repo> update`** (`agent-worktrees update`, or any repo binstub such
> as `dotfiles update`). It refreshes **every** registered plugin's payload
> (invoking the plugin manager for you), rebuilds **every** runtime
> (agent-worktrees, agent-bridge, agent-codespaces, …), and fast-forwards the
> anchor checkouts — in a single command, per machine. The per-plugin
> `scripts/install.*` / `scripts/init.*` documented below are the internals it
> runs for you (and a local-testing / recovery path), **not** the normal deploy
> path. See
> [docs/install-contract.md → Plugin update ≠ runtime install](docs/install-contract.md#plugin-update--runtime-install).

### Version scheme

Agent Worktrees follows [PEP 440](https://peps.python.org/pep-0440/)
compatible versioning:

```
MAJOR.MINOR.PATCH[-devN]
```

- **Patch** bumps (`1.0.1 -> 1.0.2`) — bug fixes, small improvements,
  new skills/docs that don't change runtime behavior.
  > Only a change **inside a plugin folder** (its `src/`, `skills/`, or its own
  > `docs/`) ships in that plugin's payload and needs a bump. A **repo-root**
  > `docs/` change (this repo's `docs/`, `CONTRIBUTING.md`, `README.md`) is not
  > vendored into any plugin and needs **no** bump — see
  > [install-contract.md § What the marketplace vendors](docs/install-contract.md#what-the-marketplace-vendors-copied-vs-loaded).
- **Minor** bumps (`1.0.x -> 1.1.0`) — new features, behavioral changes,
  new CLI subcommands. **Only when the maintainer decides.**
- **Major** bumps (`1.x -> 2.0`) — breaking changes. **Only when the
  maintainer decides.**

### Default: bump patch with `-devN`

When committing changes that warrant a version bump, use the **patch**
level with a `-devN` suffix:

```
1.0.1 -> 1.0.2-dev1 -> 1.0.2-dev2 -> ... -> 1.0.2 (release)
```

Do **not** bump minor or major versions unless explicitly instructed.

### Where the version lives (ALL THREE must be bumped together)

Each plugin has its own version triplet. Bump only the files for the
plugin you changed.

> **General rule (applies to every plugin, present and future).** For a plugin
> `<p>`: bump `plugins/<p>/plugin.json` (`version`), `plugins/<p>/pyproject.toml`
> (`[project].version`, runtime plugins only — payload-only plugins have none),
> and `<p>`'s entry in `.github/plugin/marketplace.json` (find it **by name**,
> not a hardcoded index). **agent-worktrees** additionally bumps
> `metadata.version`; **adding a new plugin** appends a `plugins[]` entry and
> bumps `metadata.version`. The per-plugin tables below are concrete examples for
> the original plugins — the same rule covers agent-logger, agent-dispatch,
> context-handoff, efforts, visions, customizing-copilot,
> copilot-extensions-harness, and anything added later.
>
> **Keep any in-package `__version__` in sync.** A runtime plugin that exposes a
> Python `__version__` (e.g. `agent-dispatch`'s `src/agent_dispatch/__init__.py`,
> surfaced by `--version` and the coordinator's `/health`) must bump it to match
> the `pyproject.toml` version in the **same** commit — it is a *fourth* file for
> that plugin, easy to miss because the marketplace doesn't read it. A stale
> `__version__` makes a correctly-deployed runtime misreport its own version.
>
> **Enforced by `tools/check-version-consistency.py`** (pre-push): it fails the
> push if any plugin's `plugin.json` / `pyproject.toml` / `marketplace.json`
> versions disagree — the guard added after #65 bumped only `pyproject.toml` and
> wedged the Picker's "Update available" indicator into a permanent loop.
>
> **Enforced by `tools/check-version-bump.py`** (pre-push + CI, PR-diff scoped):
> it fails the push/PR if a plugin's content changed **without** a version bump.
> A change to **any file under `plugins/<p>/`** (its `src/`, `skills/`,
> `agents/`, own `docs/`, tests, manifests) requires bumping `<p>`; a change to a
> **shared, vendored `libs/<lib>/`** requires bumping **every** plugin that
> vendors it (a lib change reaches every consumer's payload — see
> `check-vendored-libs-sync.py`). This closes the silent stale-deploy gap where
> new code ships under an unchanged version and the version-gated runtime install
> never redeploys it (dotfiles #1025). Repo-root files not vendored into any
> plugin (`tools/`, `.github/`, repo-root `docs/`, `CONTRIBUTING.md`,
> `README.md`) need no bump. Build artifacts under a plugin are ignored.
>
> **Before editing a shared lib, find every REAL copy first: `python
> tools/check-vendored-libs-sync.py --list`.** A shared lib such as
> `ssh-manager` is vendored **per consuming plugin**, at
> `plugins/<plugin>/libs/<lib>/` — each copy is installed and imported
> independently (`[tool.uv.sources] <lib> = { path = "libs/<lib>" }` in that
> plugin's own `pyproject.toml`). Some repos also carry a legacy top-level
> `libs/<lib>/` directory alongside these — it is easy to mistake for "the"
> source since it sits next to the lib's own `tests/`, but `--list` only
> enumerates the `plugins/*/libs/*` copies it keeps in sync; a top-level
> `libs/<lib>/src` that isn't one of the listed copies is **not consumed by any
> plugin at runtime**, and editing it silently does nothing. Edit every listed
> copy identically (or edit one and copy it to the rest byte-for-byte), then
> re-run `check-vendored-libs-sync.py` to confirm — it fails loudly on drift
> between copies, but it cannot warn you about editing an unlisted, unvendored
> directory.

**agent-worktrees:**

| File | Field | Purpose |
|------|-------|---------|
| `plugins/agent-worktrees/plugin.json` | `version` | Copilot CLI reads this to detect updates via `copilot plugin update` |
| `plugins/agent-worktrees/pyproject.toml` | `version` under `[project]` | Python package version at runtime; shown in `--version` output |
| `.github/plugin/marketplace.json` | `metadata.version` AND `plugins[0].version` | Marketplace catalog; Copilot CLI reads this from GitHub to check for updates |

**agent-bridge:**

| File | Field | Purpose |
|------|-------|---------|
| `plugins/agent-bridge/plugin.json` | `version` | Plugin version for marketplace detection |
| `plugins/agent-bridge/pyproject.toml` | `version` under `[project]` | Python package version; shown in `agent-bridge version` output |
| `.github/plugin/marketplace.json` | `plugins[1].version` | Marketplace catalog entry for agent-bridge |

**agent-codespaces:**

| File | Field | Purpose |
|------|-------|---------|
| `plugins/agent-codespaces/plugin.json` | `version` | Plugin version for marketplace detection |
| `plugins/agent-codespaces/pyproject.toml` | `version` under `[project]` | Python package version; shown in `agent-codespaces version` output |
| `.github/plugin/marketplace.json` | `plugins[2].version` | Marketplace catalog entry for agent-codespaces |

**agent-containers:**

| File | Field | Purpose |
|------|-------|---------|
| `plugins/agent-containers/plugin.json` | `version` | Plugin version for marketplace detection |
| `plugins/agent-containers/pyproject.toml` | `version` under `[project]` | Python package version; shown in `agent-containers version` output |
| `.github/plugin/marketplace.json` | `plugins[3].version` | Marketplace catalog entry for agent-containers |

**agent-mcp:**

| File | Field | Purpose |
|------|-------|---------|
| `plugins/agent-mcp/plugin.json` | `version` | Plugin version for marketplace detection |
| `plugins/agent-mcp/pyproject.toml` | `version` under `[project]` | Python package version; shown in `agent-mcp status` output |
| `.github/plugin/marketplace.json` | `plugins[4].version` | Marketplace catalog entry for agent-mcp |

**All version files for a plugin must be bumped together in the same commit.** If any
file is out of sync:

- Stale `plugin.json` — `copilot plugin update` reports "already at
  latest" even when new code is available.
- Stale `marketplace.json` — the marketplace registry shows the old
  version; machines checking for updates won't see the new version.
- Stale `pyproject.toml` — runtime `--version` output is wrong.

### When to bump

- After a set of changes is committed and ready to push.
- Before pushing to GitHub — the push is the "release."
- One bump per push is fine; don't bump on every commit.
- **On a hot plugin with concurrent agents** (several agents landing PRs to the
  same plugin within minutes of each other — `agent-worktrees` is the frequent
  case), the version you read is stale the moment another PR merges. Don't
  precompute the bump early and carry it through several commits: re-fetch
  `origin/main` and set the version to *(current main's version) + 1*
  immediately before your final push, and again after any rebase the
  `check-version-bump` CI gate forces on you. A collision here isn't
  data loss — `check-version-bump`/`check-version-consistency` catch it every
  time and force a quick re-bump — but re-checking right before push avoids
  the wasted round trip.

## Deploying: one command — `<repo> update`

**The canonical deploy is a single unified command: `<repo> update`**
(`agent-worktrees update`, or any repo binstub, e.g. `dotfiles update`). Run it
on each target machine (over SSH for remotes) after pushing. In one flow it:

- refreshes the marketplace catalog once, then updates **every** registered
  plugin's payload — runtime **and** payload-only (`efforts`, `visions`,
  `context-handoff`, `customizing-copilot`, `harness-*`) — by calling the plugin
  manager for you (`_update_registered_plugins`);
- rebuilds **every** runtime (agent-worktrees, agent-bridge, agent-codespaces,
  agent-containers, …) into a fresh versioned slot and cuts over;
- fast-forwards each managed repo's **anchor checkout** so in-repo config lands
  with the plugin;
- redeploys binstubs, Windows Terminal profiles, and shortcuts.

**Do not deploy plugin-by-plugin by hand.** Hand-running `copilot plugin update`
or a per-plugin `scripts/install.* update` / `scripts/init.*` is the wrong path:
it is easy to update one plugin and miss its runtime (or a sibling), and a
push without a version bump makes the payload refresh a silent no-op that *looks*
successful. The per-plugin "Deploying Agent X" sections below document the
**internals** `<repo> update` runs for you — plus the **local-testing /
recovery** path (running an installer from a local checkout before pushing).
They are not the normal deploy step.

> Prerequisite, every time: **bump the version** (see *Where the version lives*).
> `<repo> update` is version-gated — an un-bumped change deploys nothing.

> **`<repo> update` does not validate an unmerged worktree's changes.** Per
> [install-contract.md § Source = where the installer runs
> from](docs/install-contract.md#source--where-the-installer-runs-from-no-flag),
> a machine's installed footprint remembers a `source.kind` (`marketplace` or
> `local`) from wherever its installer last ran, and `update`/`agent-worktrees
> update --force` **keeps pulling from that same source** — for a normal,
> already-onboarded machine that's `marketplace` (resolving `main`, typically
> via the registered **anchor checkout**, not any feature worktree). Running
> `agent-worktrees update --force` from inside a worktree with uncommitted or
> unmerged commits will silently redeploy the **unchanged** marketplace/anchor
> code — not your edit — and a version bump alone does not fix this, since
> there is nothing on `main` yet to bump to. To validate a real change **before
> merging**, run that specific plugin's own installer directly from the
> worktree (see each plugin's own "Local Testing"/"Install / Update" section
> below, e.g. `cd plugins/agent-codespaces; ./scripts/install.ps1 update`) —
> this switches that machine's footprint to `source.kind = local`, pointed at
> your worktree, until you run the unified `<repo> update` again (which flips
> it back to `marketplace`). Treat this as throwaway pre-merge validation, not
> a persistent local-dev mode: remember to run the unified `update` again after
> merging so the machine returns to tracking the marketplace normally.

## Deploying Agent Worktrees

Agent Worktrees is deployed from the `copilot-extensions` GitHub repo,
not from your project monorepo. Your project repo may contain a
parallel `worktree-manager` service that shares code but deploys
independently.

### The Deployment Pipeline

Changes follow this exact sequence — no shortcuts:

1. **Commit** changes in `plugins/agent-worktrees/`
2. **Bump the version** in all three files (see "Where the version lives")
3. **Push** to `main` on GitHub: `git push origin main`
4. **Update on each machine** via `agent-worktrees update`
   (over SSH for remote machines)

The update command runs `copilot plugin update` to pull the latest
plugin from the marketplace, then executes the platform-specific
installer which deploys the package, regenerates `_build_info.py`
with the real commit hash, and refreshes instruction files.

### What NOT to Do

**Never copy source files directly into the deployed runtime directory
(`~/.agent-worktrees/lib/`).** This bypasses:

- Version tracking (`_build_info.py` won't reflect the real version)
- The installer's own setup steps (venv sync, wrapper generation,
  instruction file deployment, post-install hooks)
- Other machines — they won't get the update
- Rollback safety — there's no commit to revert to

If you need to test a change locally before pushing, use the installer
from the local checkout:

```powershell
# Windows — from the copilot-extensions checkout
cd plugins\agent-worktrees
.\scripts\install.ps1 update
```

```bash
# Linux/WSL — from the copilot-extensions checkout
cd plugins/agent-worktrees
./scripts/install.sh update
```

This runs the real installer against the local source, so the full
pipeline executes (build info, venv, wrappers, instructions) — just
from a local commit instead of a pushed one.

## Deploying Agent Bridge

Agent Bridge is a persistent HTTP service (not a per-session plugin).
It deploys via its **own installer scripts** in
`plugins/agent-bridge/scripts/`, not the Copilot CLI marketplace update
flow.

### The Deployment Pipeline

1. **Commit** changes in `plugins/agent-bridge/`
2. **Bump the version** in all three files (see "Where the version lives")
3. **Push** to `main` on GitHub: `git push origin main`
4. **Update on each machine** via the installer (see below)

The installer resolves the local checkout via `~/.git-repos`, installs
agent-bridge into a venv, deploys layered config, and restarts the
service. Project binstubs (e.g. `my-project services agent-bridge
update`) can also dispatch to the installer.

### Platform-Specific Deployment

| Platform | Installer | Service manager | Install location |
|----------|-----------|----------------|-----------------|
| Linux/WSL | `install.sh` | systemd | `/opt/agent-bridge/` |
| Windows | `install.ps1` | Scheduled task + PID file | `~/.agent-bridge/` |
| macOS | Planned | -- | -- |

### Local Testing

```powershell
# Windows
pwsh -File plugins\agent-bridge\scripts\install.ps1 install
```

```bash
# Linux/WSL
bash plugins/agent-bridge/scripts/install.sh install
```

### Keeping worktree-manager in sync

When fixing bugs or adding features that apply to both codebases:

1. Apply the fix in **both** `copilot-extensions` (agent-worktrees) and
   your project repo (worktree-manager)
2. Push copilot-extensions to GitHub
3. Push your project repo to its origin

The two codebases are forked — they share structure and much of the code,
but are not automatically synchronized.

## Deploying Agent Codespaces

Agent Codespaces is a session plugin with a CLI binstub. It provides the
`codespace:<name>` namespace resolver for agent-bridge and a standalone
`agent-codespaces` CLI for SSH transport, credential relay, and lifecycle
management.

### The Deployment Pipeline

1. **Commit** changes in `plugins/agent-codespaces/`
2. **Bump the version** in all three files (see "Where the version lives")
3. **Push** to `main` on GitHub: `git push origin main`
4. **Update on each machine** via the installer

### Install / Update

```powershell
# Windows -- from the copilot-extensions checkout
cd plugins\agent-codespaces
.\scripts\install.ps1 install    # first time
.\scripts\install.ps1 update    # subsequent updates
```

```bash
# Linux/WSL -- from the copilot-extensions checkout
cd plugins/agent-codespaces
bash scripts/install.sh install
bash scripts/install.sh update
```

The installer creates a venv at `~/.agent-codespaces/`, deploys the
package and ssh-manager dependency, and places a binstub in
`~/.local/bin/`.

### Bootstrap (init)

For first-time setup on a new machine, the `init` scripts handle
everything including prerequisite checks:

```powershell
# Windows
pwsh -File plugins\agent-codespaces\scripts\init.ps1
```

```bash
# Linux/WSL
bash plugins/agent-codespaces/scripts/init.sh
```

### Version Files

Bump all three files for agent-codespaces before pushing (same rule as
other plugins):

| File | Field |
|------|-------|
| `plugins/agent-codespaces/plugin.json` | `version` |
| `plugins/agent-codespaces/pyproject.toml` | `version` under `[project]` |
| `.github/plugin/marketplace.json` | `plugins[2].version` |

## Deploying Agent Containers

Agent Containers is a CLI plugin with an `~/.agent-containers` runtime. It
provides the `container:<name>` namespace resolver for agent-bridge (installed
as a sibling package into the bridge venv) and a standalone `agent-containers`
CLI for local Docker dev-container fleet and lease management.

### The Deployment Pipeline

1. **Commit** changes in `plugins/agent-containers/`
2. **Bump the version** in all three files (see "Where the version lives")
3. **Push** to `main` on GitHub: `git push origin main`
4. **Update on each machine** by re-running the init script

### Install / Update

The plugin ships only `init` scripts (no separate `install`); re-running `init`
with `--force` / `-Force` redeploys the runtime.

```powershell
# Windows -- from the copilot-extensions checkout
pwsh -File plugins\agent-containers\scripts\init.ps1            # first time
pwsh -File plugins\agent-containers\scripts\init.ps1 -Force     # redeploy
```

```bash
# Linux/WSL -- from the copilot-extensions checkout
bash plugins/agent-containers/scripts/init.sh                   # first time
bash plugins/agent-containers/scripts/init.sh --force           # redeploy
```

The init script creates a venv at `~/.agent-containers/` and places a binstub in
`~/.local/bin/`. So the bridge picks up the `container:` resolver, install
agent-containers **before** (re)running the agent-bridge installer.

## Deploying Agent MCP

Agent MCP is a standalone CLI plugin with an `~/.agent-mcp` runtime. Unlike the
other plugins it has **no** agent-bridge integration — an agent invokes the
`agent-mcp` binstub directly from its `mcp-servers` config to wrap an upstream
MCP server.

### The Deployment Pipeline

1. **Commit** changes in `plugins/agent-mcp/`
2. **Bump the version** in all three files (see "Where the version lives")
3. **Push** to `main` on GitHub: `git push origin main`
4. **Update on each machine** by re-running the init script

### Install / Update

Like agent-containers, agent-mcp ships only `init` scripts; re-run with
`--force` / `-Force` to redeploy.

```powershell
# Windows
pwsh -File plugins\agent-mcp\scripts\init.ps1            # first time
pwsh -File plugins\agent-mcp\scripts\init.ps1 -Force     # redeploy
```

```bash
# Linux/WSL
bash plugins/agent-mcp/scripts/init.sh                   # first time
bash plugins/agent-mcp/scripts/init.sh --force           # redeploy
```

The init script creates a venv at `~/.agent-mcp/` and places the `agent-mcp`
binstub in `~/.local/bin/`.

## Code Style

- Python 3.10+, type hints encouraged
- **Linter: [ruff](https://docs.astral.sh/ruff/).** Each plugin configures its
  own `[tool.ruff]` in `pyproject.toml`. Run the full pass with `ruff check .`
  (and `ruff format` for formatting). The repo carries pre-existing style debt,
  so the committed `pre-commit` hook lints only **staged** files and only the
  high-signal `F` (pyflakes) + `E9` (syntax) rule groups — fix those as you go.
- Docstrings for public functions
- **Componentization: a 1,000-line hard cap per source module**
  (`tools/check-module-size.py`). A single module growing without bound is a
  real failure mode this repo hit in practice (`agent-dispatch`'s `queue.py`
  reached ~7,200 lines with no guard catching it) — a 1,000-line file is
  already a lot to hold in your head at once; split by responsibility (an
  adapter, an evaluator, a policy table) well before that, not after. Dozens
  of pre-existing files exceed the cap by a wide margin (some by an order of
  magnitude), so a **shrink-only baseline**
  (`tools/module-size-baseline.json`) grandfathers each one in at its current
  size as a temporary ceiling — the guard still fails if a baselined file
  grows even one line further, or if any non-baselined file newly crosses the
  cap. Shrinking a file is always fine and never itself a failure. Widening a
  baselined ceiling in the ordinary case is a **manual, reviewed edit** to
  the JSON, never something a plain refresh does silently — bare
  `--refresh-baseline` only lowers or removes entries, it never raises one.
  The one exception is the opt-in `--refresh-baseline --allow-widen` flag,
  restricted by convention to a scheduled/post-merge run against `main`
  (`.github/workflows/module-size-baseline-widen.yml`), which additionally
  ratchets a grown file's ceiling up to its current size and opens its own
  small, reviewable PR — never something a PR branch's own CI run applies to
  its own diff. A separate `--changed-since REF` flag scopes the ordinary
  (non-widening) check to files this branch's own commits actually touch
  (used by CI on `pull_request` events) — this repo's high concurrent-PR
  volume otherwise let one already-merged PR's growth in a shared,
  already-baselined module fail every *other* PR's guard until the widen job
  caught up, even ones that never opened that file; `--changed-since` fixes
  the attribution, not the underlying growth. When the diff itself touches
  `tools/module-size-baseline.json`, scope also includes every baseline
  entry the diff itself added, changed, or removed — a baseline edit could
  otherwise mismatch a file's actual size for an entry a diff's own file
  list wouldn't name, so that entry is always checked. This still never
  falls back to a fully unscoped, whole-tree sweep: a file whose own source
  *and* baseline entry the diff never touches is unrelated organic drift,
  not this PR's responsibility to fix. Test files (`tests/`, `test_*.py`,
  `conftest.py`) are exempt — `TESTING.md` already directs splitting those by
  behavioral contract, not arbitrary line count, a different rule for a
  different failure mode.
  - **The cap is a backstop, not a target.** Treat "a couple of related
    classes/functions per module" as the working ceiling in normal
    development, and split proactively as a module grows toward it — waiting
    for `check-module-size.py` to fail is already too late; by then the
    module has usually accreted several unrelated responsibilities that are
    now entangled and harder to separate than if each had landed in its own
    file from the start.
  - **CLI/registration surfaces are a named recurring shape, not a special
    case.** A large `__main__.py` (or any command/route/handler registry) is
    almost always several independent subcommands sharing one dispatch table,
    not one cohesive module. Split it into one module per subcommand (or
    cohesive subcommand family) plus a thin registrar that only imports and
    wires them — `agent-dispatch`'s extraction of `producers_cli.py`,
    `recipes_cli.py`, and `supervise_cli.py` out of its `__main__.py` is the
    model to follow for any other CLI that's grown the same way.
  - **This is a language-agnostic discipline**, not a Python-only rule. The
    same "one cohesive responsibility, split proactively, no giant CLI
    registration blob" standard applies to `.sh`, `.ps1`, and `.ts` sources
    even though `tools/check-module-size.py` currently only scans tracked
    `*.py` files — extending the guard to other extensions is tracked
    separately (see the `module-componentization-discipline` effort); do not
    treat the tool's current Python-only scope as license to let a large
    shell/PowerShell/TypeScript file grow unchecked in the meantime.
  - **How to actually do a split safely:** see the
    `customizing-copilot:componentizing-modules` skill (a runbook for
    identifying seams, extracting them, and re-validating — including the
    `--refresh-baseline` step once a baselined file shrinks below its prior
    ceiling). Use `python tools/rank-module-size.py` to find which
    already-grandfathered files are the biggest offenders (it folds identical
    vendored copies — e.g. the `installation-context`/`versioned-runtime`
    sync targets — into one row so the ranking reflects distinct real work,
    not duplicated line counts).
  - **A scheduled watchdog surfaces organic drift proactively**
    (`.github/workflows/module-health-watchdog.yml`,
    `tools/module-health-watchdog.py`, daily): no single PR is ever blamed
    for a module that grew past its cap/ceiling one small, individually
    reasonable contribution at a time — the watchdog finds the single worst
    offender (already-over-cap files always outrank merely-near-cap ones)
    and files (or leaves alone, if one is already open) a
    `needs-decomposition`-labeled tracking issue naming it, for a dedicated
    decomposition pass rather than diffuse pressure on whichever future PR
    happens to touch the file next.

### Git Hooks

The repo ships git hooks under `tools/hooks/`:

- **`pre-commit`** — on staged files: `ruff check --select F,E9` on Python
  (unused imports/vars, undefined names, syntax errors), and
  `tools/check-skills.py` on any staged `SKILL.md` (frontmatter validity, `name`
  rules, and the **1024-char `description` limit** the Copilot CLI enforces —
  over it, the loader silently drops the skill).
- **`pre-push`** — runs the repo-wide guards: `tools/check-install-contract.py`
  (the [install contract](docs/install-contract.md)),
  `tools/check-no-internal-identifiers.py`, `tools/check-vendored-libs-sync.py`,
  `tools/check-headless-launch.py`, `tools/check-skills.py`,
  `tools/check-docs-consistency.py`, `tools/check-runbook-references.py`,
  `tools/check-version-consistency.py` (every plugin's version identical across
  `plugin.json` / `pyproject.toml` / its `marketplace.json` entry — a one-file
  bump wedges the Picker's update indicator), `tools/check-feed-neutrality.py`
  (no config/Dockerfile/install-script/CI-workflow file may hardcode a public
  package-feed URL as the only usable endpoint — this repo runs on machines
  whose default feed is network-blocked and replaced with an internal mirror),
  and `tools/check-module-size.py` (the 1,000-line-per-module cap and
  shrink-only baseline described above).

CI also runs `tools/check-marketplace-isolation.py` in report-only mode. It
inventories legacy unqualified runtime roots, generic global plugin commands,
PATH-based sibling launches, fixed lifecycle identities, and operative bare
commands while the marketplace-installation-cell migration is active. Do not
enable `--strict` until the producing phases in #1096 have removed the baseline.

### Test Portfolio Discipline

Treat required pull-request CI as a fast, change-scoped contract gate. Do not
add exhaustive cross-products or repeated subprocess setup directly to that
lane. Subprocess-heavy suites must provide a focused smoke lane selected by
markers and path gating, with the complete portfolio retained in a scheduled or
manually dispatched workflow. Prefer broad canonical implementation coverage
plus representative adapter checks at real divergence seams; never pool the
process boundaries that a concurrency or lifecycle test exists to verify.

`TESTING.md` is the canonical source for the portfolio invariants, runner
mechanics, and the current smoke/exhaustive split.

### Windows Background-Launch Review

Any change that adds or modifies a background subprocess, scheduled launcher,
health probe, transport, or daemon must classify the launch using
[`windows-background-process-launch`](docs/patterns/windows-background-process-launch.md)
and reuse its shared primitive. `CREATE_NEW_CONSOLE` plus `SW_HIDE` is not a
headless mechanism: Windows Default Terminal may still display and focus it.
The user's configured default terminal is never a correctness dependency.

Review requires evidence at the real divergence seam, not only a mocked
`creationflags` assertion:

1. Start the path from a windowless parent such as `pythonw.exe` or the actual
   service launcher.
2. Exercise at least one real console-subsystem descendant; for SSH, include the
   configured `ProxyCommand` path when present.
3. Observe at least two periodic cycles and assert zero visible top-level
   windows, zero Default Terminal/`OpenConsole` acquisitions, and zero foreground
   transitions.
4. Exercise timeout/cancellation and confirm the complete child tree is reaped.
5. Verify local targets stay local so a same-machine health check cannot create
   avoidable SSH/process churn.

Keep this live Windows check focused; the required CI guard remains static and
fast.

### Ephemeral Process Reaping

Launching a background/detached process invisibly (the section above) is only
half the contract. Any change that adds or modifies a detached process, a
per-worktree/per-task helper, or anything else that must outlive its parent
invocation must also state **how it gets reaped** — classify it against
[`ephemeral-process-reaping`](docs/patterns/ephemeral-process-reaping.md) and
answer, in the PR description or a code comment at the reap site:

1. What is the real liveness signal for the unit this process serves (a
   worktree's mux + PID liveness, a service's lease file, ...) — not "a hook
   fired"?
2. Where is the polling-based reap that requires no cooperation from the
   dying process — a hook-only reap is a fast path, never the only path.
   Reuse an existing bounded sweep (e.g. `session_catalog.py`'s resident
   reconciler) rather than adding a new poller.
3. Is the reap idempotent and silent on an already-dead target?
4. Confirm it is **not** implemented inside a preservation-oriented lifecycle
   command (`finalize`/`cleanup`) — those must not also own process teardown.

A detached process with a described launch path but no described reap path is
an incomplete change, not a follow-up: #2265 and #2269/#2270 are what an
"it'll get cleaned up somehow" assumption costs in practice (a machine-wide
process/window leak discovered only once it made a laptop's fans and keyboard
noticeably hot).

They are **not active until wired** per clone (git does not auto-enable a
committed hooks dir). Run the helper once per checkout:

```bash
tools/setup-hooks.sh          # macOS / Linux / Git Bash / WSL
tools\setup-hooks.ps1         # Windows PowerShell
# equivalent to: git config core.hooksPath tools/hooks
```

Bypass in a pinch with `git commit/push --no-verify` (discouraged). The
install-contract check fails until every runtime plugin's installer conforms —
see the contract doc for the rules.

## Gotchas

### The mux status bar must never compute on the render path

**Rule: nothing in a tmux/psmux `status-left` / `status-right` may spawn a
process per render.** No `#(agent-worktrees …)`, no `#(cat …)`, no `#()` that
shells out. The bar may read only precomputed values — the `#{@aw_ctx}` /
`#{@aw_seg}` user options plus `%H:%M`-style strftime. A detached
`status-updater` watcher computes the segments **off** the render path and
pushes them in via `set-option`.

**Why (the regression this exists to prevent).** tmux runs `#()` jobs
asynchronously and caches them between `status-interval` ticks, so it *mostly*
hides the cost. **psmux repaints synchronously** — it re-runs every `#()` in the
status line on each repaint, in the render/keystroke path. A bar that shelled
out to the (Python, cold-starting) `agent-worktrees` CLI cost ~600 ms per
repaint there; under Copilot's high-framerate TUI that turned keystroke echo and
re-render to molasses on Windows (worse under the double-ConPTY stack), while a
no-mux session stayed snappy. The fix moved the compute into one common
`status-updater` watcher feeding `#{@aw_*}` vars. See the *Off the paint path*
section of
[`plugins/agent-worktrees/docs/cli-reference.md`](plugins/agent-worktrees/docs/cli-reference.md).

**If you touch `terminal/psmux.conf`, `terminal/session-options.sh`, or a
launcher status path:** keep the bar on `#{@aw_*}` vars; keep the compute in the
shared cross-platform `status-updater` watcher (do **not** re-introduce a
per-mux shell writer or a render-path `#()`); the guard tests in
`plugins/agent-worktrees/tests/test_terminal_decoupling.py` (assert no
`#(agent-worktrees` / `#(cat` in the bar) will fail if you regress. Verified
mechanisms: psmux 3.3.6 and tmux 3.4 both support session-scoped `set-option -t`
(isolated per session) and `#{@user-option}` expansion.

### Hot-patching a deployed venv for fast pre-merge iteration

**A cached `.test-venvs/<platform>/<plugin>` venv installs a vendored
path-dependency lib (e.g. `agent-ssh-manager`) as a normal, non-editable
copy** — not an editable link. Editing `plugins/<p>/libs/<lib>/src/...` does
**not** change what that venv imports until you rebuild it. Two ways to see a
fresh edit without a full rebuild:

- **Fast unit-test iteration:** prepend the vendored copy's `src/` to
  `PYTHONPATH` so it shadows the stale installed copy, e.g. (PowerShell):
  `$env:PYTHONPATH = "plugins\<p>\libs\<lib>\src"` before invoking the venv's
  `python.exe -m pytest`. This is also how to run a shared lib's **own** test
  suite (`libs/<lib>/tests/`) against one specific vendored copy — that suite
  is not part of any plugin's `tests/` dir, so `tools/run-plugin-tests.py`
  never runs it; point `PYTHONPATH` at the copy you want to validate and
  invoke pytest against the shared lib's own `tests/` directory directly.
- **`--reinstall`:** `python tools/run-plugin-tests.py <plugin> --reinstall`
  forces a real rebuild from the current vendored source, for a true
  integration-level check of that plugin's own suite.

**Validating against the actual deployed CLI a human/agent would invoke** (not
just the test venv) needs one more step beyond the *local installer* gotcha
above (see *`<repo> update` does not validate an unmerged worktree's
changes*): even a `source.kind = local` install rebuilds from a **checkout on
disk**, so it still requires committing (or at least saving) your edit and
re-running that plugin's installer to pick it up.

**`agent-codespaces` has adopted the mutable-dev-slot pattern
(`docs/patterns/mutable-dev-slot.md`, #3376) as the preferred path** for this:
from your worktree, run `pwsh -File plugins\agent-codespaces\scripts\install.ps1
dev` (`./scripts/install.sh dev` on POSIX). This claims a protected, mutable
`versions/dev` slot, builds it as an **editable** install against your
checkout, and activates it -- a plain source edit is then reflected by the
deployed `agent-codespaces` CLI immediately, no rebuild needed; re-run `dev`
only when you change a dependency. Release when done with the DEPLOYED CLI's
own verb: `agent-codespaces dev-release` (works even without a checkout
present -- see the design doc's "Runtime accessibility" section), which
restores the machine to whatever version was active before you claimed dev
mode. `agent-worktrees finalize` warns (never silently releases) if your
worktree still holds a live dev-slot claim when you try to retire it.

For any plugin that has **not yet** adopted this pattern, or when you need the
fastest possible iteration loop on a live bug **before** even a dev-slot claim
is worth setting up, it remains acceptable to hot-patch the **already-deployed**
runtime's files directly (e.g. on Windows,
`~/.agent-codespaces/versions/<version>/Lib/site-packages/ssh_manager/*.py`) —
but treat this as strictly throwaway: it is silently overwritten by the next
real install/update, must never be treated as "shipped," and the actual fix
still needs to land through the normal commit → PR → merge → deploy flow
before you consider it done. Re-verify against a real (non-hot-patched)
deploy once your PR lands.

### Windows `ProxyCommand` bridge: a peer-gone-away read is not a failure

**If you touch `libs/ssh-manager` (any vendored copy)'s `proxy.py` or
`process.py`:** two Windows-only behaviors are load-bearing, not incidental,
and a "obvious" cleanup can silently reintroduce either:

- A read from a bridged loopback socket/pipe (`_pump()`) can raise
  `ConnectionResetError` (`WinError 64`, "the specified network name is no
  longer available") when the peer closes, instead of the POSIX-style empty
  read at EOF. This is a normal Windows ProactorEventLoop signal for "the
  other side is gone," not an error condition — treat it as clean end-of-stream,
  not something to log as a failure or propagate.
- The ambient background watcher that reaps a per-command proxy's spawned
  process (`run_process_cleanup`'s caller in `_watch_process`) must bound how
  long **it** waits for that cleanup (`timeout=` a modest ceiling, e.g. 10s),
  even though the underlying cleanup itself stays `shield()`-protected and
  keeps running to completion in the background. Without that bound, a slow
  remote process-tree kill (`taskkill /T /F`, observed 100+ seconds under
  endpoint-protection scanning) blocks the **entire hosting CLI process's
  exit** — `asyncio.run()`'s own shutdown sequence waits for every
  outstanding task, including a shielded one, so an unbounded wait here isn't
  contained to one code path; it stalls the whole invocation even after the
  real command's result was already returned to the caller.

Both were root-caused live diagnosing `agent-codespaces ssh` reliability
(ThomasMichon/copilot-extensions#3323, fixed in #3340) — differential
diagnosis (bare `gh codespace ssh` vs the wrapped command, `--no-relay` to
rule out the credential-relay prelude, replaying the exact `ssh` invocation
by hand) is what isolated these two behaviors from red herrings (a
misdiagnosed "ADO feed-token export hang", a misdiagnosed "network/VPN
outage" that a bare `gh` call disproved). Re-run that diagnosis shape —
strip layers one at a time against a known-good baseline — before assuming a
new hang/slowdown in this path is a repeat of either of these two fixed
causes.

## Commit Messages

- Descriptive, imperative mood: "Fix Unicode crash on cp1252 consoles"
- Reference this repo's GitHub issue numbers where applicable: "Fix #372: …"
- Include `Co-authored-by` trailer for Copilot-assisted commits
