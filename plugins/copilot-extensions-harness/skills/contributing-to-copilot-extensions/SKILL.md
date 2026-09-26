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

Before proposing a capability, read the portable
[`contribution-ground-rules.md`](../../references/contribution-ground-rules.md).
`copilot-extensions` accepts organization-neutral, general-purpose work; it
rejects personal needs and organization-specific policy/process.

**Two guidance layers a change reconciles to** (design work, not just the
mechanical flow):

- **Visions** ([`visions/`](../../../../visions/README.md)) — the *intent*, the standing
  what-should-be. Before an architectural/behavioral change, reconcile it (the
  three kinds): it **closes** a stated vision item (cite it), **extends** the
  vision (revise the vision first), or is **below-altitude** (say so, proceed).
  Never silently contradict or bypass a vision. This binds **every** contributor —
  including an agent driving from a downstream control repo.
- **Patterns** ([`docs/patterns/`](../../../../docs/patterns/README.md)) — *how we build it
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
venv + `~/.local/bin` binstub (and sometimes a service), deployed by its own <!-- marketplace-isolation: allow doc-example -->
installer. Know which kind you are changing.

## The flow

0. **Reconcile (architectural/behavioral changes).** Before building, reconcile
   the change to the repo's **visions** (close / extend / below-altitude — see
   above) and check it against the **patterns** (`docs/patterns/`) and their
   binding **invariants**. A below-altitude fix (lint, typo, dependency bump)
   skips this with a word; a design change owes the reconcile. This is a guide,
   not a gate.
1. **Isolate.** This is a worktree-class, **PR-required** repo — never edit the
   anchor checkout and never push directly to `dev` **or** `main`. Create a
   worktree with `copilot-extensions create`, edit and commit there, then land
   through the repo's `pr-self-merge` flow. **`dev` is the contribution
   branch** — every worktree forks from it and every PR targets it (the
   in-repo `default_branch` config). `main` remains the repo's formal GitHub
   default and release branch: `dev` is periodically promoted to `main`
   through the release pipeline, never by an individual contributor's PR. The
   **only** sanctioned exception to landing on `dev` is a direct `main` change
   to unstick a broken release pipeline itself — a deliberate, explicitly
   named operator action, never a routine contribution. The pre-commit hook
   guard blocks direct in-worktree commits to **both** `main` and `dev`
   (`protected_branches` in the in-repo config), so this is enforced the same
   way for every agent regardless of which branch it assumes is "the" default.
2. **Edit in the repo, never the deployed copy.** The repo is the source of
   truth. Do **not** edit `~/.copilot/installed-plugins/...` (overwritten on
   update) or a runtime dir (`~/.agent-*/lib`, service venvs).
3. **Test.** Run `pytest` from the changed runtime plugin's dir
   (`plugins/<plugin>/`). Lint touched Python with `ruff check --select F,E9`.
   Respect the repo's `TESTING.md` for how to run the suites and the opt-in
   e2e smoke tests.

   **Fix every failing test you encounter — never label it "pre-existing" and
   move on.** `dev` feeds a fully automated release pipeline
   (`.github/workflows/validate-and-promote.yml`, the
   dev-branch-release-pipeline effort, ThomasMichon/copilot-extensions#3336):
   promotion only proceeds when the FULL per-plugin suite is genuinely green
   against `dev`'s current tip, and a red build blocks **every** pending
   contributor's already-merged work from ever reaching `main`, not just
   yours. There is no separate human gatekeeper who re-triages "was this
   pre-existing" before it jams the pipeline — you are the only checkpoint
   that can prevent that. If a test fails while you're working — even one you
   didn't write, in a plugin you didn't intend to touch, that only surfaces
   in the full/slow suite rather than the fast smoke lane — you own fixing it
   before you merge, exactly as if your own change had caused it. Do not
   defer it, comment past it, or note it as "pre-existing, not my concern" in
   a PR description and move on regardless — diagnose it and land a real fix
   (or an honest revert of whatever actually regressed it) in the same PR or
   an immediate follow-up before finalizing. The only exception is a failure
   already tracked as a known, accepted flake/gap with its own open issue —
   link that issue in your PR description rather than silently skipping past
   an undocumented one.

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
5. **Add a changefile — mandatory, same commit. Never hand-edit a version
   number.** This is the mistake that silently swallows changes: nothing
   ships until a real version bump lands, and versions are no longer
   hand-picked here — `main` is a wholesale-regenerated release snapshot the
   CI promotion pipeline (dev-branch-release-pipeline effort,
   ThomasMichon/copilot-extensions#3336) bumps and stamps automatically from
   pending changefiles. For every plugin you touched, once per PR:

   ```bash
   python tools/changefile.py add --plugin <name> --type patch --comment "<summary>"
   python tools/changefile.py list   # see what's pending
   ```

   Default to **`patch`** (or `dev` for an iterative fixup within the same
   change); never request `minor`/`major` unless the maintainer asks. Do
   **not** touch `plugin.json`'s `version`, `pyproject.toml`'s
   `[project].version`, or `.github/plugin/marketplace.json` by hand — the
   next promotion consumes your changefile and writes all three in lockstep.
   The exact changefile schema and edge cases (renamed/split plugins, shared
   libs) are in `CONTRIBUTING.md` § Release & Versioning — follow it; entries
   drift, so trust the repo over this summary.
6. **Open/update the PR.** Use `copilot-extensions create-pr` to squash the
   worktree, push `pr/<slug>`, and open the GitHub PR (the repo config has
   `auto_open: true`). If review feedback requires more commits in the same
   worktree, use `copilot-extensions push-changes` to update the PR head — never
   push a worktree branch or `dev`/`main` by hand. Opening the PR is a progress
   milestone, not a handoff or completion condition.
7. **Steward the PR through self-merge and finalization.** This repo's effective profile is
   **`pr-self-merge`**: the GitHub ruleset blocks direct pushes and requests a
   non-blocking Copilot review, but the submitter is authorized to merge.
   The submitting agent remains responsible until the PR is merged and its
   worktree is finalized:
   - Assess Copilot comments as advisory findings; address valid ones and
     explain or dismiss invalid ones. Never wait for Copilot to approve --
     its review is always a non-blocking comment, never a required approval,
     no matter how many rounds you go through or how it's worded (a "changes
     recommended" banner is not a gate). Use `pr-watch wait <owner>/<repo> <PR>
     --since r0 --until any --timeout 600` (a bounded ~10-minute window, not
     `--timeout 0`) rather than waiting indefinitely, then act on whatever
     review guidance has appeared by the time it returns -- address anything
     genuinely worth addressing, or proceed straight to merge if nothing has
     landed yet. Do not loop indefinitely re-requesting a review chasing a
     zero-finding pass that may never come.
   - Keep the branch current and mergeable. If `dev` moves or conflicts appear,
     reconcile with the supported worktree PR verbs, re-run the required gates,
     and update the PR with `push-changes`.
   - When provider checks are slow, use `pr-watch` or the agent-dispatch
     hibernation waiter. Sleeping the worker is allowed; dropping ownership is
     not.
   - Run `copilot-extensions pr-merge <PR> --now`, verify the provider reports
     the PR merged, then run `copilot-extensions finalize`.

   **Hard completion gate:** an open PR, a posted review, green checks, or a
   conflict-free branch is not completion. Stop only after merged + finalized,
   or after recording a concrete terminal blocker/abandonment in the owning
   task. This repository has no human-review handoff step.
8. **Deploy with `<repo> update` — one unified command.** Merging only *primes*
   the change; deploy it on each target machine (over SSH for remotes) with the
   repo's update binstub: **`<repo> update`** (e.g. `agent-worktrees update`, <!-- marketplace-isolation: allow deployment-management -->
   or
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

If `reviewing-customizations` (the `customizing-copilot` plugin) flags a
trigger collision or other finding on a plugin from **this** suite, it's
outside that consumer repo's control — this skill is the fix path review
points at. Confirm the `[copilot-extensions/<plugin>]` origin, reproduce
against the repo source (never the installed payload), and fix it through the
normal flow above (worktree, edit, bump, gates, PR). Full steps, including the
file-an-issue fallback:
[`references/fix-path-bridge.md`](../../references/fix-path-bridge.md).

## What NOT to do

- **Don't open/update a PR without a changefile for every touched plugin.**
  (See step 5. This is the one.)
- **Don't leave a test failure you encountered as "pre-existing."** (See
  step 3.) That reasoning is exactly what jams the release pipeline for
  everyone else.
- **Don't hand-edit a plugin's version anywhere** (`plugin.json`,
  `pyproject.toml`, `marketplace.json`) — add a changefile; the promotion
  pipeline stamps every version surface for you.
- **Don't edit installed/deployed copies** to "fix fast" — fix the repo source,
  add a changefile, PR/self-merge, deploy.
- **Don't hand-run `copilot plugin update` or a per-plugin `scripts/install.*` /
  `scripts/init.*`** — always deploy with the unified **`<repo> update`**
  (`agent-worktrees update`). <!-- marketplace-isolation: allow deployment-management -->
  One flow updates every plugin's payload AND
  rebuilds every runtime; the per-plugin installers are internals / local-test /
  recovery only. Running them by hand is easy to get wrong (wrong plugin, missed
  runtime, a version-unbumped no-op that looks like success).
- **Don't copy source into a runtime dir** — it bypasses versioning and leaves
  other machines stale.

## Coordinating concurrent drivers (public repo)

`copilot-extensions` is **public** and may be driven from **more than one
private control repo at once** (for example a personal control repo and a work
control repo). Everyone lands through the same PR-required `dev`. Two
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

#### Use a repository-scoped identity, never a global account switch

A coordinating session may be running under an identity that cannot access this
public repository (for example, an Enterprise Managed User). Resolve and inject
the repository's configured account for each command instead of changing the
machine-global active `gh` account:

```bash
<agent-worktrees catalog argv[0]> repos account-for ThomasMichon/copilot-extensions
<agent-worktrees catalog argv[0]> repos gh ThomasMichon/copilot-extensions -- api user --jq .login
<agent-worktrees catalog argv[0]> repos gh ThomasMichon/copilot-extensions -- issue list \
  --repo ThomasMichon/copilot-extensions --state open --search "<generic subject>"
<agent-worktrees catalog argv[0]> repos gh ThomasMichon/copilot-extensions -- issue create \
  --repo ThomasMichon/copilot-extensions --title "<generic title>" --body "<public-safe body>"
```

The first command names the expected login; the scoped `api user` result must
match it before any mutation. If `repos gh` warns that it could not mint that
account's token, exits non-zero, or reports another login, **do not let its
ambient-auth fallback create/comment/close anything**. Repair the account map
or token when possible; never use `gh auth switch` as a routine workaround on a
shared machine.

#### No authorized public identity: preserve coordination and keep moving

An unavailable public identity must not block local implementation. Until an
authorized repository-scoped identity is available:

1. Use the originating downstream issue's active `agent-issue-claim:v1` marker
   plus its deduplicated `agent-dispatch` task as the temporary coordination
   token. The claim must identify the owning worktree and target this upstream
   repository so other workers in that control plane can detect the reservation.
2. Keep the downstream issue, task ID, private URL, machine/worktree identity,
   and proprietary motivation **only downstream**. Never mention or link them in
   this public repository's issue, commit, PR, tests, or docs.
3. Proceed in an isolated upstream worktree and record private progress beats.
   Re-run the scoped public search before publication. If access becomes
   available, create or claim the generic public issue and link to it from the
   downstream tracker — never link back from public to private.
4. If publication itself still lacks an authorized identity, leave the local
   work and downstream claim resumable rather than switching global auth. This
   fallback coordinates implementation; it does not bypass repository
   authorization or the required PR gate.

If a public issue or conflicting implementation appears when access is restored,
reconcile or yield before publishing. The temporary downstream claim prevents a
blocked identity from duplicating work within its control plane; the required
pre-publication search closes the cross-control-repo gap.

### Serial, single-writer merges

Treat `dev` as a single-writer lane:

- Land one coherent change, then the next — avoid parallel in-flight PR merges from
  different worktrees or drivers.
- **Changefiles make concurrent merges safe by design** — unlike a hand-picked
  version, a changefile just accumulates; the promotion pipeline computes the
  real version once, at promotion time, from whatever's pending. You do not
  need to detect or react to another merge having "consumed" a version.
- If you pull and find another driver touched the same plugin's *content*,
  reconcile the actual conflict before updating/merging your PR rather than
  force-landing — that's a real collision the changefile mechanism doesn't
  paper over.

### Sanitization — keep private context off the public face

Everything that lands here is **world-readable**: commits, issues, code
comments, docs, `AGENTS.md`. Never put downstream-private material in them.

- **No** employer/multi-machine system names, internal service or host names, topology
  details, persona/role-play machinery, private URLs, or the specific downstream
  reason a change is wanted.
- **Do** describe changes in self-contained, general-purpose terms — as if for a
  stranger who has only this repo ("add a `--json` flag to `list`", *not* "so the
  internal dashboard can parse it").
- **Abstract every attached artifact** — examples, traces, repros, and
  references are the easy leaks; anything pasted to illustrate a change tends
  to smuggle consumer-side detail (internal paths/hostnames in error output,
  private setup in repros, real internal values in sample data, non-public
  anchors in references). Worked examples per category:
  [`references/sanitization-examples.md`](../../references/sanitization-examples.md).
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
