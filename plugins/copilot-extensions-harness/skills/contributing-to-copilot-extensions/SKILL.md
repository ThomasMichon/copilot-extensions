---
name: contributing-to-copilot-extensions
description: >
  How to make a change to a copilot-extensions plugin and land it correctly --
  repo layout, the PR-required worktree flow, the MANDATORY version bump (the
  single most common mistake), the test + install-contract gates, deploy after
  merge, and the source-of-truth rules. Also covers the design-guidance layers a
  change reconciles to: the repo's visions (intent) and architecture patterns
  (how we build). Use when editing, fixing, or extending any plugin in the
  copilot-extensions suite (agent-worktrees, agent-bridge, agent-mcp,
  agent-logger, agent-dispatch, context-handoff, efforts, visions, harness-*).
  Trigger phrases include:
  - 'change a copilot-extensions plugin'
  - 'fix agent-worktrees'
  - 'edit a plugin'
  - 'plugin architecture pattern'
  - 'reconcile to the vision'
  - 'fix the installer'
  - 'bump the plugin version'
  - 'push a plugin update'
  - 'contribute to copilot-extensions'
---

# Contributing to copilot-extensions

The authoritative, versioned rules live in the repo's own **`CONTRIBUTING.md`**,
**`AGENTS.md`**, **`TESTING.md`**, **`docs/install-contract.md`**, and
**`docs/architecture.md`** — read and respect them for the current detail (they
are the repo's own root docs, not carried by this plugin). This skill is the
operator's map: what to touch, in what order, and the gotchas that bite.

**Two guidance layers a change reconciles to** (design work, not just the
mechanical flow):

- **Visions** ([`visions/`](visions/README.md)) — the *intent*, the standing
  what-should-be. Before an architectural/behavioral change, reconcile it (the
  three kinds): it **closes** a stated vision item (cite it), **extends** the
  vision (revise the vision first), or is **below-altitude** (say so, proceed).
  Never silently contradict or bypass a vision. This binds **every** contributor —
  including an agent driving from a downstream control repo.
- **Patterns** ([`docs/patterns/`](docs/patterns/README.md)) — *how we build it
  here*: plugin shapes, design principles, binding invariants, and focused pattern
  docs (endpoint discovery, service supervision, à-la-carte independence,
  cross-platform parity). Honor them; extend the library when you establish a new
  reusable convention.

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
  libs/<lib>/                 # shared libs vendored into consuming venvs (ssh-manager, credential-relay, zdd)
  .github/plugin/marketplace.json   # the catalog — versions live here too
  tools/                      # repo-level guards (check-install-contract.py, reset.*)
```

**Payload vs runtime.** A *payload-only* plugin ships skills/hooks/extensions
(no venv) — enabling it is the whole install. A *runtime* plugin also ships a
venv + `~/.local/bin` binstub (and sometimes a service), deployed by its own
installer. Know which kind you are changing.

## The flow

0. **Reconcile (architectural/behavioral changes).** Before building, reconcile
   the change to the repo's **visions** (close / extend / below-altitude — see
   above) and check it against the **patterns** (`docs/patterns/`) and their
   binding **invariants**. A below-altitude fix (lint, typo, dependency bump)
   skips this with a word; a design change owes the reconcile. This is a guide,
   not a gate.
1. **Isolate.** This is a worktree-class, **PR-required** repo — never edit the
   anchor checkout and never push directly to `main`. Create a worktree with
   `copilot-extensions create`, edit and commit there, then land through the
   repo's `pr-self-merge` flow.
2. **Edit in the repo, never the deployed copy.** The repo is the source of
   truth. Do **not** edit `~/.copilot/installed-plugins/...` (overwritten on
   update) or a runtime dir (`~/.agent-*/lib`, service venvs).
3. **Test.** Run `pytest` from the changed runtime plugin's dir
   (`plugins/<plugin>/`). agent-worktrees has no suite yet — verify worktree ops
   end-to-end. Lint touched Python with `ruff check --select F,E9`. Respect the
   repo's `TESTING.md` for how to run the suites and the opt-in e2e smoke tests.
   **Clean-room validation (install/bootstrap/provision/behavior changes).** When a
   change affects how a plugin installs, bootstraps, self-provisions, or behaves on
   a fresh machine, **run or extend the relevant clean-room scenario when
   practical** — the disposable fresh-box rig (`tools/clean-room/`) turns those
   flows into a hard PASS/FAIL instead of a field belief. See the
   **`validating-in-clean-room`** skill (run / evaluate / author more) and the
   [clean-room-validation vision](../../../../visions/clean-room-validation/README.md).
   This is a **norm, not a blocking gate** — a below-altitude fix skips it with a
   word; a change to a provisioning hook, installer, binstub, or readiness signal
   owes it.
4. **Install-contract gate (runtime plugins).** Run
   `python tools/check-install-contract.py` — it must report **zero
   violations**.
5. **BUMP THE VERSION — mandatory, same commit.** This is the mistake that
   silently swallows changes: the marketplace detects updates by comparing
   versions, so an unbumped plugin change makes every machine report "already at
   latest" and skip your change after merge. For the plugin you touched, bump
   **together**:
   - `plugins/<plugin>/plugin.json` → `version`
   - `plugins/<plugin>/pyproject.toml` → `[project].version` (runtime plugins)
   - `.github/plugin/marketplace.json` → that plugin's `plugins[N].version`
   - agent-worktrees only: also `marketplace.json` `metadata.version` **and**
     `plugins[0].version`. Adding a **new** plugin is a catalog change — bump
     `metadata.version` too.

   Default bump is **patch with a `-devN` suffix** (e.g. `1.3.1` → `1.3.2-dev1`);
   never bump minor/major unless the maintainer asks. The exact per-plugin file
   table is in `CONTRIBUTING.md` — follow it; entries drift, so trust the repo.
6. **Open/update the PR.** Use `copilot-extensions create-pr` to squash the
   worktree, push `pr/<slug>`, and open the GitHub PR (the repo config has
   `auto_open: true`). If review feedback requires more commits in the same
   worktree, use `copilot-extensions push-changes` to update the PR head — never
   push a worktree branch or `main` by hand.
7. **Self-merge and finalize.** This repo's effective profile is
   **`pr-self-merge`**: the GitHub ruleset blocks direct pushes and requests a
   non-blocking Copilot review, but the submitter is authorized to merge. After
   the PR is ready, run `copilot-extensions pr-merge <PR> --now` (or the same
   verb through `agent-worktrees`) and then `copilot-extensions finalize`.
8. **Deploy with `<repo> update` — one unified command.** Merging only *primes*
   the change; deploy it on each target machine (over SSH for remotes) with the
   repo's update binstub: **`<repo> update`** (e.g. `agent-worktrees update`, or
   any repo binstub such as `dotfiles update`). This single flow does
   everything: it refreshes the marketplace catalog, updates **every** registered
   plugin's payload — runtime AND payload-only (`efforts`, `visions`,
   `context-handoff`, `customizing-copilot`, `harness-*`) — rebuilds **every**
   runtime (agent-worktrees, agent-bridge, agent-codespaces, …), fast-forwards
   the anchor checkouts, and redeploys binstubs/profiles. **Do NOT hand-run
   `copilot plugin update`, or a per-plugin `scripts/install.* update` /
   `scripts/init.*`** — the unified `update` orchestrates those internally, and
   running them piecemeal is the wrong path (and, if the version wasn't bumped,
   the payload refresh silently no-ops — it *looks* like it worked while your
   change never lands). The per-plugin installers exist only for isolated local
   testing / recovery, not the normal flow.

## The fix-path bridge (a review flagged an external plugin)

When `reviewing-customizations` (the `customizing-copilot` plugin) reviews a
consumer harness with `--from-settings`, a **trigger collision** — or any
finding — that lands on a plugin from **this** suite is *outside that repo's
control*: it can't be fixed in the consumer repo, only here. This skill is the
**fix path** that review points at (via the `<repo>-harness → contributing-to-<repo>`
bridge). When you arrive here from such a finding:

1. **Confirm it's a copilot-extensions plugin.** The review tags each collision
   owner `skill [marketplace/plugin]`; a `[copilot-extensions/<plugin>]` origin
   (or a `source:` of `github.com/ThomasMichon/copilot-extensions`) is ours.
2. **Reproduce against the repo source, never the installed payload.** Resolve
   the anchor and read the offending skill/agent in `plugins/<plugin>/…` — do
   **not** inspect or edit `~/.copilot/installed-plugins/…` (overwritten on
   update).
3. **Fix it through the normal flow above** — worktree, edit, **bump the
   version**, gates, PR/self-merge, deploy. A trigger collision is usually
   resolved by sharpening or de-duplicating the phrase in the owning skill's
   `description` / `Trigger phrases include:` list.
4. **Can't/shouldn't fix it now?** File a GitHub issue on
   `ThomasMichon/copilot-extensions` describing the collision (both skills, the
   shared phrase) in generic, world-readable terms (see *Coordinating concurrent
   drivers* below) so it's tracked for a maintainer.

The consumer repo's own options (an in-repo authority-override skill that
reclaims the phrase, or disabling the plugin there) live on the *consumer* side
and are documented by `customizing-copilot:reviewing-customizations`; **this** skill covers the
upstream half — landing the real fix in the plugin.

## What NOT to do

- **Don't open/update a PR without the required version bump.** (See step 5.
  This is the one.)
- **Don't edit installed/deployed copies** to "fix fast" — fix the repo source,
  bump, PR/self-merge, deploy.
- **Don't hand-run `copilot plugin update` or a per-plugin `scripts/install.*` /
  `scripts/init.*`** — always deploy with the unified **`<repo> update`**
  (`agent-worktrees update`). One flow updates every plugin's payload AND
  rebuilds every runtime; the per-plugin installers are internals / local-test /
  recovery only. Running them by hand is easy to get wrong (wrong plugin, missed
  runtime, a version-unbumped no-op that looks like success).
- **Don't copy source into a runtime dir** — it bypasses versioning and leaves
  other machines stale.

## Coordinating concurrent drivers (public repo)

`copilot-extensions` is **public** and may be driven from **more than one
private control repo at once** (for example a personal control repo and a work
control repo). Everyone lands through the same PR-required `main`. Two
disciplines keep them from colliding — and keep private context off the public
face.

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

### Serial, single-writer merges

Treat `main` as a single-writer lane:

- Land one coherent change, then the next — avoid parallel in-flight PR merges from
  different worktrees or drivers.
- **Rebase/update before PR publication or merge and re-check the version bump.**
  A concurrent merge may have already consumed your `-devN`; if the marketplace
  version moved under you, bump again on top of theirs (never reuse a version
  another merge took).
- If you pull and find another driver touched the same plugin, reconcile before
  updating/merging your PR rather than force-landing.

### Sanitization — keep private context off the public face

Everything that lands here is **world-readable**: commits, issues, code
comments, docs, `AGENTS.md`. Never put downstream-private material in them.

- **No** employer/multi-machine system names, internal service or host names, topology
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
`TESTING.md` (running the suites), `docs/install-contract.md` (the runtime-plugin
contract), `docs/architecture.md` (payload/runtime split, ports), `docs/patterns/`
(how we build — shapes, principles, invariants, focused patterns), `visions/` (the
standing what-should-be). To work the repo as a good citizen from another control
repo, pair this with the `agent-worktrees:working-cross-repo` skill.
