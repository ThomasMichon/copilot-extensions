---
name: contributing-to-copilot-extensions
description: >
  How to make a change to a copilot-extensions plugin and land it correctly --
  repo layout, the worktree contribution flow, the MANDATORY version bump (the
  single most common mistake), the test + install-contract gates, deploy after
  push, and the source-of-truth rules. Use when editing, fixing, or extending
  any plugin in the copilot-extensions suite (agent-worktrees, agent-bridge,
  agent-codespaces, agent-containers, agent-mcp, agent-logger, agent-dispatch,
  context-handoff, efforts, visions, customizing-copilot, harness-*).
  Trigger phrases include:
  - 'change a copilot-extensions plugin'
  - 'fix agent-worktrees'
  - 'fix the agent-bridge plugin'
  - 'edit a plugin'
  - 'plugin code change'
  - 'fix the installer'
  - 'bump the plugin version'
  - 'push a plugin update'
  - 'contribute to copilot-extensions'
  - 'ssh-manager'
---

# Contributing to copilot-extensions

The authoritative, versioned rules live in the repo's own **`CONTRIBUTING.md`**,
**`AGENTS.md`**, **`docs/install-contract.md`**, and **`docs/architecture.md`** —
read them for the current detail. This skill is the operator's map: what to
touch, in what order, and the gotchas that bite.

Resolve the local checkout before anything else — its path varies by machine.
Do not hardcode it.

## Repo layout

```
copilot-extensions/
  plugins/<plugin>/           # one dir per plugin
    plugin.json               # manifest (name, version, skills path)
    pyproject.toml            # runtime plugins only (Python package + version)
    src/<pkg>/                # runtime plugins only
    scripts/                  # installers (init.* / install.*) for runtime plugins
    skills/                   # plugin-provided skills
    tests/                    # runtime plugins with a suite
    docs/                     # plugin docs
  libs/ssh-manager/           # shared SSH lib (imported by bridge + codespaces)
  .github/plugin/marketplace.json   # the catalog — versions live here too
  tools/                      # repo-level guards (check-install-contract.py, reset.*)
```

**Payload vs runtime.** A *payload-only* plugin ships skills/hooks/extensions
(no venv) — enabling it is the whole install. A *runtime* plugin also ships a
venv + `~/.local/bin` binstub (and sometimes a service), deployed by its own
installer. Know which kind you are changing.

## The flow

1. **Isolate.** This is a worktree-class repo — never edit the anchor checkout.
   Create a worktree, edit and commit there. (Owners push directly to `main`, no
   PR. Without push access, use a fork + PR — the target repo's policy, not
   your control repo's.)
2. **Edit in the repo, never the deployed copy.** The repo is the source of
   truth. Do **not** edit `~/.copilot/installed-plugins/...` (overwritten on
   update) or a runtime dir (`~/.agent-*/lib`, service venvs).
3. **Test.** Run `pytest` from the changed runtime plugin's dir
   (`plugins/<plugin>/`). agent-worktrees has no suite yet — verify worktree ops
   end-to-end. Lint touched Python with `ruff check --select F,E9`.
4. **Install-contract gate (runtime plugins).** Run
   `python tools/check-install-contract.py` — it must report **zero
   violations**.
5. **BUMP THE VERSION — mandatory, same commit.** This is the mistake that
   silently swallows changes: the marketplace detects updates by comparing
   versions, so a push without a bump makes every machine report "already at
   latest" and skip your change. For the plugin you touched, bump **together**:
   - `plugins/<plugin>/plugin.json` → `version`
   - `plugins/<plugin>/pyproject.toml` → `[project].version` (runtime plugins)
   - `.github/plugin/marketplace.json` → that plugin's `plugins[N].version`
   - agent-worktrees only: also `marketplace.json` `metadata.version` **and**
     `plugins[0].version`. Adding a **new** plugin is a catalog change — bump
     `metadata.version` too.

   Default bump is **patch with a `-devN` suffix** (e.g. `1.3.1` → `1.3.2-dev1`);
   never bump minor/major unless the maintainer asks. The exact per-plugin file
   table is in `CONTRIBUTING.md` — follow it; entries drift, so trust the repo.
6. **Push** to `main` (owner) / open the PR (fork).
7. **Deploy** on each target machine — pushing only *primes* it. The update path
   is per-plugin: `agent-worktrees update`; `agent-bridge` / `agent-codespaces`
   `scripts/install.* update`; `agent-containers` / `agent-mcp` / `agent-dispatch`
   re-run `scripts/init.*` (with `-Force`/`--force`); `agent-logger` via its
   installer. Payload-only plugins (`efforts`, `visions`, `context-handoff`,
   `customizing-copilot`, `harness-*`) refresh via `copilot plugin update` +
   session restart.

## What NOT to do

- **Don't push without a version bump.** (See step 5. This is the one.)
- **Don't edit installed/deployed copies** to "fix fast" — fix the repo source,
  bump, push, deploy.
- **Don't mix deployment paths** — agent-worktrees updates via the marketplace +
  its installer; agent-bridge via its own installer. They are different
  pipelines.
- **Don't copy source into a runtime dir** — it bypasses versioning and leaves
  other machines stale.

## Coordinating concurrent drivers (public repo)

`copilot-extensions` is **public** and may be driven from **more than one
private control repo at once** (for example a personal control repo and a work
control repo). Both push to the same `main`. Two disciplines keep them from
colliding — and keep private context off the public face.

### Claim work with a public GitHub issue

Before starting a stretch of work, **file (or find) a GitHub issue on
`ThomasMichon/copilot-extensions`** and note that you're taking it. The issue is
the shared, neutral coordination token every driver — and any outside
contributor — can see; it's how you avoid two agents building the same thing or
racing the same files.

- Search open issues first; if one already covers it, comment/assign rather than
  open a duplicate.
- Write it in **generic-tool language** (see Sanitization below).
- Link the issue from your *private* effort/plan — the public issue coordinates,
  the private effort carries the "why".

### Serial, single-writer pushes

Owners push directly to `main`, so treat `main` as a single-writer lane:

- Land one coherent change, then the next — avoid parallel in-flight pushes from
  different worktrees or drivers.
- **Rebase before push and re-check the version bump.** A concurrent push may
  have already consumed your `-devN`; if the marketplace version moved under you,
  bump again on top of theirs (never reuse a version another push took).
- If you pull and find the other driver touched the same plugin, reconcile
  before pushing rather than force-landing.

### Sanitization — keep private context off the public face

Everything that lands here is **world-readable**: commits, issues, code
comments, docs, `AGENTS.md`. Never put downstream-private material in them.

- **No** employer/facility names, internal service or host names, topology
  details, persona/role-play machinery, private URLs, or the specific downstream
  reason a change is wanted.
- **Do** describe changes in self-contained, general-purpose terms — as if for a
  stranger who has only this repo ("add a `--json` flag to `list`", *not* "so the
  internal dashboard can parse it").
- The proprietary "why" lives in the **driver's private effort/plan**, which
  *links to* the public issue. The public artifact stays generic; the private
  artifact stays private.

When in doubt, write the issue/commit as if you were an unaffiliated open-source
contributor — because to a reader, you are.

## Reference

`CONTRIBUTING.md` (versioning + release), `AGENTS.md` (dev guide),
`docs/install-contract.md` (the runtime-plugin contract),
`docs/architecture.md` (payload/runtime split, ports). To work the repo as a
good citizen from another control repo, pair this with the `working-cross-repo`
skill.
