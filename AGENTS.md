# Copilot Extensions -- Development Guide

Source of truth for the copilot-extensions Copilot CLI plugins. The **canonical
plugin roster** lives in `.github/plugin/marketplace.json` (mirrored, with
descriptions, in `README.md` and `docs/architecture.md`). All ship from this
repo via the Copilot CLI marketplace.

> **This file is the map, not the manual.** It orients you and links out to the
> homes that hold the substance -- read the waypoint you need instead of crawling
> the tree, which is how a large repo stays navigable. It deliberately uses
> **backtick faux-links** (`` `docs/architecture.md` ``) rather than
> `[text](path)` links: Copilot auto-loads real Markdown links from an always-on
> `AGENTS.md` into *every* session, so faux-links keep this a lean map read on
> demand (see the `customizing-copilot:authoring-skills` skill). Don't "fix" them into clickable
> links.

---

## Finding your way around -- start here

| To... | Go to |
|-------|-------|
| Know **which plugins exist** (+ versions) | `.github/plugin/marketplace.json` (source of truth), rendered in `README.md` and `docs/architecture.md` |
| Understand **how the suite works today** (as-is) | `docs/architecture.md` |
| See **how we build plugins here** (reusable design) | `docs/patterns/README.md` |
| Add an **`agent-*` runtime plugin** with commands/services | `docs/patterns/runtime-agent-plugin.md` |
| See **what a subject should ultimately be** (intent) | `visions/README.md` |
| Plan or resume a **stretch of work** | `efforts/README.md` + the **`efforts:planning-efforts`** skill |
| **Make a change and land it correctly** | the **`copilot-extensions-harness:contributing-to-copilot-extensions`** skill (+ *Contribution Rules* below) |
| **Test** a plugin | `TESTING.md` |
| Decide **where config lives** (repo vs machine) | `docs/configuration.md` |
| Turn a repo into an **agent harness** | `docs/harness-runbook.md` + the **`customizing-copilot:building-harnesses`** skill |
| Author a **skill / sub-agent / harness plugin** | the **`authoring-skills`** / **`defining-subagents`** / **`authoring-harness-plugins`** skills |
| **Diagnose** a broken plugin or deploy | the **`copilot-extensions-harness:diagnosing-copilot-extensions`** skill |
| The **deploy / install contract** | `docs/install-contract.md` |

---

## Repository Structure

```
copilot-extensions/
  plugins/<plugin>/            # one dir per plugin — marketplace.json is the roster (see below)
    plugin.json                # manifest (name, version, skills path)
    pyproject.toml             # runtime plugins only: Python package + version
    src/<pkg>/                 # runtime plugins only: Python source
    scripts/                   # runtime plugins only: installers (init.ps1/sh, install.ps1/sh)
    payload-invocation.json    # runtime command declarations
    bin/                       # generated shims (or payload-invocation outputDir)
    skills/                    # plugin-provided skills
    tests/                     # runtime plugins with a suite
    hooks.json | extensions/   # optional: session-start hook / session extension
    docs/                      # plugin docs
  libs/<lib>/                  # shared libs vendored into consuming venvs (ssh-manager, credential-relay, config-migrate, endpoint-rendezvous, versioned-runtime, zdd)
  docs/                        # repo architecture (architecture.md), patterns/, plans/
  visions/                     # standing north-star visions (should-be)
  .github/plugin/marketplace.json   # marketplace catalog — the SINGLE SOURCE OF TRUTH for the plugin roster + versions
  CONTRIBUTING.md              # full versioning and release docs
```

> **The roster is deliberately not enumerated here.** The canonical plugin list
> (and every version) lives in `.github/plugin/marketplace.json`, rendered
> for humans in `docs/architecture.md` and the `README.md` table. Read those for
> *which* plugins exist; this tree shows only the *shape* of a plugin dir.

---

## Plugins and Lifecycles

The suite spans many plugins. The **canonical plugin list, the runtime-vs-
payload split, and the per-plugin lifecycle tables** live in
`docs/architecture.md` (and the `README.md` plugin table) — derived from
`.github/plugin/marketplace.json`, which is the single source of truth.
**Don't re-enumerate the plugin roster here** — that duplicate is exactly what
drifts. Runtime plugins carry generated payload-local agent commands and emit
their exact `argv` through attributable session command glossaries.
`~/.local/bin/agent-*` remains a legacy management compatibility surface during
the installation-cell migration; machine-global command ownership converges on
attributable project binstubs.

> agent-bridge sources the `codespace:` / `container:` namespaces from a
> **filesystem provider registry** — each provider drops a manifest into
> `~/.agent-bridge/providers.d/` on session start and the daemon drives that
> provider's binstub **over a process boundary** (it does **not** import the
> `agent_codespaces` / `agent_containers` packages into its venv). It does
> **not** own their binstubs — those belong to `~/.agent-codespaces` and
> `~/.agent-containers` respectively. (The credential relay itself is host-side,
> run in-process by the bridge from the vendored `credential-relay` lib.)
> agent-mcp is standalone: it has no bridge resolver and is invoked directly from
> an agent's `mcp-servers` config.

---

## Visions — the standing north star

This repo carries **visions** under `visions/README.md`: the durable
*what-should-be* for its plugins, services, and shared systems. A vision is
**pure should-be**, **intent-level** (not a spec), and **revised in place** (Git
is the history) — it never lists gaps or status.

The construct chain: a **vision** states the target; **efforts are carved from
its delta vs. reality** (diff the vision against the reality docs/code, file the
misalignments as **GitHub issues** that cite the vision item, group them into an
effort); a **doc** records what actually *is*; an **issue** tracks a discrete
to-do.

- **Route standing intent to a vision.** When you capture the north star for a
  system/service/tool — what it should ultimately be — put it in `visions/…`,
  not in an architecture doc's "goals" prose. Keep "what is" (docs) separate
  from "what should be" (visions).
- **Visions are a source of new work.** The vision→reality delta is a backlog
  generator: diffing a vision is a first-class way to find issues and efforts.
- **Don't edit a vision to record progress.** It changes only when the *intent*
  changes; delta-closure state lives in the issues/efforts.

See `visions/README.md` for the local conventions (organization, issue/effort
linkage), the `visions:envisioning` skill for standing intent, and the
`efforts:planning-efforts` skill for the campaigns carved from it.

### Efforts — active campaigns and coordination

Stretches of planned work live under `efforts/active/<slug>/`, governed by the
`efforts:planning-efforts` skill and the local addendum in `efforts/README.md`.
Start or resume a substantial implementation campaign there rather than creating
a standalone plan document. GitHub issues remain the discrete tracking and
coordination tokens; the effort connects those issues to phased implementation,
participants, validation, and an append-only journal.

### Architecture patterns — how we build it

Between the vision (*what should be*) and the code (*what is*) sits the
**patterns** layer: `docs/patterns/README.md` — the prescriptive, reusable design
conventions for building plugins and plugin services here (plugin shapes,
numbered **design principles**, binding **design invariants**, and focused
pattern docs: endpoint discovery, service supervision, à-la-carte independence,
cross-platform parity). `docs/patterns/` is the **map**; `docs/install-contract.md`
is the established deploy-contract pattern it links.

**Reconcile an architectural change to both layers.** Before adding or altering
architecture/behavior: reconcile to the relevant **vision** (close / extend /
below-altitude) *and* check it against the **patterns** and their invariants. A
below-altitude change (lint, typo, dependency bump) needs neither; a design change
owes both. Guide, not gate.

The layered model: **vision** (`visions/`, should-be) → **patterns**
(`docs/patterns/`, how-we-build) → **architecture** (`docs/architecture.md`,
as-is) → **contribution** (this file + the harness skills, how-to-land).

---

## Contribution Rules

> **The full landing procedure is the `contributing-to-copilot-extensions`
> skill** — repo layout, the worktree contribution flow, the mandatory version
> bump, the test + install-contract gates, deploy-after-merge, and the
> source-of-truth rules. This section is the always-on summary; that skill is the
> step-by-step, and `diagnosing-copilot-extensions` covers a broken plugin or
> deploy.

### Test Portfolio

Required pull-request CI must remain a fast, change-scoped contract gate; do not
grow it into the exhaustive portfolio. New subprocess-heavy matrices need a
focused smoke lane, path gating, and an explicit scheduled/manual full lane.
Preserve real process boundaries for concurrency tests. `TESTING.md` owns the
detailed portfolio invariants and execution mechanics.

### Windows Background Process Launches

Background work must remain invisible when launched from a consoleless parent.
Use the launch-kind matrix in
`docs/patterns/windows-background-process-launch.md`; never rely on the user's
default-terminal setting or combine `CREATE_NEW_CONSOLE` with `SW_HIDE`.
Required CI runs `tools/check-headless-launch.py`. Reviewers require a real
Windows regression for launch-path changes: exercise a console descendant from
a windowless parent and observe zero visible windows and foreground transitions
across at least two periodic cycles.

### Branch and Publication

This repo is **PR-required** and uses the `pr-self-merge` profile. Work in an
isolated worktree, publish with `copilot-extensions create-pr`, give the
non-blocking Copilot review a chance to report, then merge with
`copilot-extensions pr-merge <PR> --now` and finalize. Direct pushes to `main`
are blocked by tooling and repository policy.

### Coordinating Across Control Repos

This repo is public and may be driven from **multiple downstream/control repos**
at once. Two rules keep them from colliding and keep private context off the
public face:

- **Claim work with a GitHub issue** before starting a stretch -- search open
  issues first, then take or comment on one. It's the shared token other drivers
  and outside contributors can see. Run those operations through
  `agent-worktrees repos gh ThomasMichon/copilot-extensions -- ...`; verify the
  scoped login and never switch the global active `gh` account. If no authorized
  public identity is available, continue locally under the downstream issue's
  claim plus its deduplicated dispatch task, keep all downstream context private,
  and repeat the public search before publication. The full fallback is in the
  contribution skill.
- **Keep every public artifact generic.** Commits, issues, and docs are
  world-readable -- write them self-contained, with no downstream-private names,
  systems, or context. The proprietary "why" stays in the driver's own private
  planning, which links to the public issue.
- **PR metadata is public too, including hidden HTML comments.** This repo keeps
  `pr.source_attribution: false`; never publish raw machine, worktree, or session
  identifiers in a PR title, body, commit message, label, or generated marker.
  Closed-circuit repos may opt in to source attribution in their own config.

PR merges to `main` are single-writer: update from `origin/main` before
publication or merge and re-check the version bump in case a concurrent merge
already consumed it.

<!-- visions:cross-repo-sequencing:start -->
When a vision revision in this review-gated repository also drives a directly
published change in a related repository with no PR review, land the
vision-update PR first. Only completion markers follow the related-repository
implementation; all intent belongs in the earlier reviewed PR.
<!-- visions:cross-repo-sequencing:end -->

### Version Bump -- Required For Every Plugin Change

**Every PR that changes a plugin must include a version bump** for each plugin
changed. The marketplace detects updates by comparing versions; skip the bump
and machines report "already at latest" and silently ignore the change.

For **each plugin `<p>` you touched**, bump these **in the same commit**:

| File | Field | When |
|------|-------|------|
| `plugins/<p>/plugin.json` | `version` | always |
| `plugins/<p>/pyproject.toml` | `version` under `[project]` | runtime plugins only (payload-only plugins have no `pyproject.toml`) |
| `.github/plugin/marketplace.json` | the `version` on `<p>`'s entry in `plugins[]` (find it **by name**, not a hardcoded index) | always |

Two extra rules for the marketplace catalog:

- **agent-worktrees** additionally bumps `metadata.version` (the catalog's own
  version).
- **Adding a new plugin** appends a `plugins[]` entry **and** bumps
  `metadata.version`.

Default bump: **patch with a `-devN` suffix** (e.g., `1.3.1` -> `1.3.2-dev1`).
Do not bump minor or major unless the maintainer explicitly requests it.
See `CONTRIBUTING.md` for the full versioning scheme. (The
`tools/check-docs-consistency.py` guard keeps the plugin lists/counts in the
docs honest; run it before publishing doc changes.)

### Test Before PR Publication

> **Full testing guide: `TESTING.md`** — the runner reference, the
> lint/contract gates, and the **opt-in end-to-end smoke tests** (real-infra,
> caller-supplied targets, skipped by default).

Run a plugin's suite **on demand** with the turn-key runner (builds/reuses a
cached dev venv per plugin under `.test-venvs/`, git-ignored; uses `uv`, so
vendored `[tool.uv.sources]` path deps resolve):

```bash
python tools/run-plugin-tests.py agent-worktrees        # one plugin, full suite
python tools/run-plugin-tests.py --changed              # plugins changed vs origin/main
python tools/run-plugin-tests.py --all                  # every plugin with a suite
python tools/run-plugin-tests.py agent-worktrees --guards  # just the fast guards
python tools/run-plugin-tests.py agent-worktrees -k picker  # filter
```

Fast structural/contract checks are marked `@pytest.mark.guard` (marketplace +
picker integrity: overlay-registry, palette, shipped-manifest contract, key
canonicalization, F3 binding invariants) so `--guards` runs them in
sub-second-per-plugin. Copilot review is non-blocking, so run the relevant suite
yourself before publishing a plugin change.

**Per-plugin coverage** — what each plugin's suite exercises — lives in
`TESTING.md` § *Per-plugin coverage*, not here (that enumeration drifts as
plugins are added). The largest is **agent-worktrees**: a ~1400-test suite
covering worktree lifecycle, the status/tracking model, PR flow, and the Textual
**Picker** (with a real-framework `pilot.press` keyboard harness).

### Deploy After Merge

After the PR merges to `main`, deploy on each target machine. **Payload-only plugins**
(skills / hooks / extensions — e.g. efforts, visions, context-handoff, agent-ssh,
customizing-copilot, copilot-extensions-harness) need only `copilot plugin
update` — no runtime installer. **Runtime plugins** additionally run their own
installer; the examples below are illustrative, with the
`copilot-extensions-harness:contributing-to-copilot-extensions` skill and each plugin's `scripts/` as the
authority:

```bash
# agent-worktrees -- via the update subcommand
agent-worktrees update

# agent-bridge -- via your project's service framework or the installer
# directly from the local checkout:
cd plugins/agent-bridge
./scripts/install.sh update    # Linux/WSL
.\scripts\install.ps1 update   # Windows

# agent-codespaces -- via its installer
cd plugins/agent-codespaces
./scripts/install.sh update    # Linux/WSL
.\scripts\install.ps1 update   # Windows

# agent-containers / agent-mcp -- re-run init (no separate installer)
cd plugins/agent-containers     # or plugins/agent-mcp
./scripts/init.sh --force       # Linux/WSL
.\scripts\init.ps1 -Force       # Windows
```

### Local Testing (Before PR Publication)

Run the installer from the local checkout to deploy your uncommitted
changes through the real pipeline:

```powershell
# Windows -- agent-worktrees
cd plugins\agent-worktrees
.\scripts\install.ps1 update

# Windows -- agent-bridge
cd plugins\agent-bridge
.\scripts\install.ps1 update
```

```bash
# Linux/WSL -- agent-worktrees
cd plugins/agent-worktrees
./scripts/install.sh update

# Linux/WSL -- agent-bridge
cd plugins/agent-bridge
./scripts/install.sh update
```

---

## Code Standards

- **Python 3.10+**, type hints encouraged
- **uv** for all dependency operations -- never bare `pip`
- Docstrings for public functions
- Commit messages: imperative mood, descriptive
  ("Fix Unicode crash on cp1252 consoles")
- Include `Co-authored-by` trailer for Copilot-assisted commits

---

## What NOT to Do

- **Do not copy source files into the runtime directory**
  (`~/.agent-worktrees/lib/`, `~/.agent-bridge/venv/`). This bypasses
  version tracking, the installer pipeline, and leaves other machines
  on the old version. Always commit, bump, publish through a PR, merge, then update.
- **Do not publish a plugin change without a version bump.** Machines will silently ignore
  the update.
- **Do not edit installed plugin copies** under
  `~/.copilot/installed-plugins/`. The marketplace overwrites them on
  update. Fix the source here instead.
- **Do not mix up deployment paths.** agent-worktrees deploys via the
  marketplace + its own installer. agent-bridge deploys via its own
  installer (or a project service framework that wraps it). They are
  different pipelines.

---

## Key Files

Global entry points. **Per-plugin files follow the shape shown, for any plugin
`<p>` in the marketplace roster** — that roster is the source of truth, so this
table is deliberately not enumerated per plugin (the enumeration is exactly what
drifted as the suite grew):

| What | Where |
|------|-------|
| Marketplace catalog (roster + versions) | `.github/plugin/marketplace.json` |
| Repo architecture overview | `docs/architecture.md` |
| Design patterns / invariants | `docs/patterns/README.md` |
| Visions (intent) | `visions/README.md` |
| Per-plugin manifest | `plugins/<p>/plugin.json` |
| Per-plugin Python source (runtime plugins) | `plugins/<p>/src/<pkg>/` |
| Per-plugin tests | `plugins/<p>/tests/` |
| Per-plugin skills | `plugins/<p>/skills/` |
| Per-plugin installers | `plugins/<p>/scripts/` (`init.*` and/or `install.*`; payload-only plugins may have none) |
| Session-start hooks | `plugins/<p>/hooks.json` (e.g. `agent-worktrees`) |
| Shared libs (vendored into venvs) | `libs/<lib>/` |
