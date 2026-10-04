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

Before opening any PR, complete the documentation-impact review in
`CONTRIBUTING.md` against the final diff, regardless of change classification.
Repeat it after material scope or implementation changes. This requirement also
applies to below-altitude work.

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

### Coding-Agent / Reviewer Alignment

Both roles work from the **same rubric**, not adversarial, independently-derived
standards — read [`REVIEW.md`](REVIEW.md) and self-check your own diff against
it *before* opening a PR, rather than treating the first review round as your
first exposure to what it checks for. When a finding does arrive, fix the bug
*class* it names everywhere that class recurs in your diff, not only the one
flagged instance — a per-instance fix just spends the next round rediscovering
a sibling.

Both roles also share one **stopping rule for Copilot's own verdict** —
spelled out in full in `CONTRIBUTING.md` § "Waiting for a verdict": `Approve`,
or a `Comment` verdict with zero Medium/High-severity findings open, satisfies
that gate (on a Contributor PR, only after one full review loop — a
first-round clean `Comment` still needs another attempt). A still-open
Low-severity finding at that point is either genuinely valuable to fix, or
already considered and dismissed — spinning a further review round solely to
make the comment thread read zero is chasing a bar that neither this repo's
contribution flow nor the reviewer's own directives require. Required status
checks are a separate merge gate, independent of this verdict condition.
**Satisfying Copilot's verdict is never merge authorization by itself** — a
Contributor PR still needs a separate Maintainer-approval review before
merging (see "Review" in `CONTRIBUTING.md`); only the repo owner's own
bypassed PRs skip that second gate. If you are driving a PR toward merge and
unsure whether you've reached the applicable point, re-read that section
before assuming a further round is needed.

A shared rubric is not the same as shared context, though, and the two roles
are asymmetric there: you carry whatever subject-matter context you built up
authoring the change; Copilot's review approaches every PR fresh, with no
access to the conversation that produced it. See `CONTRIBUTING.md` § "Give
the reviewer your context, not just your diff" — state a deliberate design
choice's rationale in the PR description rather than leaving the reviewer to
guess whether an unexplained one was considered or missed. This transfers
context; it does not (and should not) soften the scrutiny applied to it —
REVIEW.md explicitly directs the reviewer to keep applying full independent
judgment regardless of a stated rationale, especially for vision-conformance
and security.

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
isolated worktree, publish with `copilot-extensions create-pr`, then follow
the wait-for-a-verdict loop below before merging with
`copilot-extensions pr-merge <PR> --now` and finalizing. Direct pushes to
`dev` are blocked by tooling and repository policy; `main` accepts pushes
only from the CI promotion pipeline (or explicit admin escalation) -- see
Release & Versioning in CONTRIBUTING.md.

**Wait for a real verdict before merging -- contributor and maintainer PRs
alike.** Copilot code review can only ever render `Approve` or `Comment`
(there is no "Request changes" capability in the product at all -- see
CONTRIBUTING.md § "Waiting for a verdict" for the current GitHub-docs
citation); Approvals
are enabled in this repo, so a genuinely ready **contributor** PR should
come back `Approve`, not merely `Comment` (see `REVIEW.md`). **This repo's
own owner-authored PRs are a documented, empirically confirmed exception**
(`plugins/agent-worktrees/src/agent_worktrees/pr_contract.py`'s
`NONBLOCKING_VERDICT_STATES`; every merged owner-authored PR in this repo's
history has been `Comment`-only, never `Approve`) -- there, a clean
`Comment` (zero Medium/High findings open) *is* the passing verdict, not an
unfinished one. Either way, a `Comment` review with a Medium/High finding
still open is never a pass:
1. Open/update the PR, wait ~5 minutes for a review. No review landed
   (a timeout, not a review event): skip straight to step 4 -- there's
   nothing to address or push yet.
2. Contributor PR, `Approve` landed: merge (subject to the separate
   Maintainer-approval gate on a Contributor's PR). Owner-authored PR,
   `Comment` landed with zero Medium/High findings open: merge -- that's
   the passing verdict here.
3. `Comment` landed: address genuinely valuable findings (explain/dismiss
   the rest). If that requires an actual change, push it and wait ~5
   minutes for the automatic post-push review; if every finding was
   dismissed/explained with no real change needed, skip the push (there's
   nothing new for a re-review to see) and go straight to step 4.
4. Still not passing (a `Comment` with a Medium/High finding still open on
   either PR type, or a contributor PR still short of `Approve`, including
   a post-push timeout, which counts the same as a `Comment`): explicitly
   re-request review via the API (see CONTRIBUTING.md § "Requesting a
   fresh review" for the exact call) and wait ~5 minutes again -- do not
   just keep pushing small commits hoping the next automatic pass flips on
   its own.
5. **Narrow verdict-shape exception, Contributor PRs only:** after at least
   one full loop, if the *current* `Comment` review's remaining findings
   are all Low severity, Copilot's own verdict requirement (this step) is
   satisfied without chasing a further `Approve`, stating what was
   dismissed and why. **This is strictly about Copilot's verdict and never
   substitutes for the separate, always-required Maintainer-approval gate**
   on a Contributor's PR -- satisfying this step alone never authorizes a
   merge by itself. Any Medium/High finding blocks proceeding past this
   step at all, regardless of who authored the PR.
Full mechanics, the re-request API call, and the Maintainer-approval gate
this doesn't override: CONTRIBUTING.md § "Waiting for a verdict". **This is
agent discipline, not yet tool-enforced** -- `pr-merge --now` does not itself
check Copilot's verdict before merging (`.agent-worktrees/config.yaml`'s
`review_blocking: false` makes it pass `--admin` unconditionally); follow
the loop deliberately rather than relying on the tooling to refuse a
premature merge.

**Never post an `@copilot review` (or any `@copilot` mention) comment to
request a fresh pass.** GitHub's automatic review already fires on every push
with no mention needed. An explicit `@copilot` mention instead risks routing
to the autonomous **Copilot coding agent**, which can push its OWN commit
directly onto your PR branch (observed: a `copilot-swe-agent[bot]`-authored
commit landing mid-session, requiring inspection before trusting it and
creating a real force-push race against the driving agent's own commits). If
a fresh review genuinely helps after a substantive fix, just push the update
(`push-changes`) and let the automatic review re-fire -- never `@`-mention the
bot to ask for one.

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
- **PR metadata is public too, including hidden HTML comments.** This repo's
  `pr.source_attribution` must stay in `codename` mode (the default; only the
  worktree's assigned codename, decodes to nothing on its own) or `false`
  (fully anonymous); never publish raw machine, worktree, or session
  identifiers in a PR title, body, commit message, label, or generated marker.
  Closed-circuit repos may opt in to full raw source attribution
  (`pr.source_attribution: true`) in their own config.

PR merges to `dev` are single-writer: update from `origin/dev` before
publication or merge and re-check the changefile in case a concurrent merge
already touched the same plugin.

<!-- visions:cross-repo-sequencing:start -->
When a vision revision in this review-gated repository also drives a directly
published change in a related repository with no PR review, land the
vision-update PR first. Only completion markers follow the related-repository
implementation; all intent belongs in the earlier reviewed PR.
<!-- visions:cross-repo-sequencing:end -->

### Changefile Required For Every Plugin Change

**PRs target `dev`, not `main`** (dev-branch-release-pipeline effort,
ThomasMichon/copilot-extensions#3336). `main` is regenerated by a CI
promotion pipeline (`.github/workflows/validate-and-promote.yml`); it is
not a place any PR lands directly, and branch protection enforces that.

**Every PR that changes a plugin must include a changefile** for each plugin
changed — never a hand-picked version number. The marketplace detects
updates by comparing versions; skip the changefile and the promotion
pipeline has nothing to bump, so machines report "already at latest" and
silently ignore the change.

```bash
python tools/changefile.py add --plugin <name> --type patch --comment "<summary>"
# one PR touching two plugins with one shared reason:
python tools/changefile.py add \
  --plugin agent-worktrees --type patch \
  --plugin agent-bridge --type dev \
  --comment "Shared fix for Y"
python tools/changefile.py list   # see what's pending
```

Default bump: **`patch`** (or `dev` for an iterative fixup within an
already-in-flight patch). Do not request `minor`/`major` unless the
maintainer explicitly says so. Never hand-edit `plugin.json` /
`pyproject.toml` / `marketplace.json` version fields yourself — the CI
promotion pipeline's `tools/accumulate_bumps.py` consumes every pending
changefile and writes the real version numbers when it promotes `dev` to
`main`. See `CONTRIBUTING.md` § Release & Versioning for the full scheme,
the wait between merging to `dev` and a promotion actually shipping to
`main`, and how to preview past it. (The `tools/check-docs-consistency.py`
guard keeps the plugin lists/counts in the docs honest; run it before
publishing doc changes.)

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

**After fixing a bug, prefer the devcontainer-isolated runner when available
(Linux, Docker + the devcontainers CLI present):**

```bash
python tools/run_tests_in_devcontainer.py <plugin>   # same suite, inside a hardened, ephemeral, network-disconnected container
```

It runs the exact suite above inside a hardened, network-disconnected,
ephemeral container — a real OS-level boundary on top of the turn-key
runner's own process-level containment, so a fix that *looks* contained but
still reaches outside its redirected roots (an absolute-path write, a raw
socket) can't leave evidence on, or depend on state from, this host. Fall
back to the bare runner above when Docker/the devcontainers CLI isn't
available. See `TESTING.md` § *Optional devcontainer-based isolation* for
the full mechanics.

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
  on the old version. Always commit, add a changefile, publish through a
  PR targeting `dev`, merge, then wait for promotion.
- **Do not publish a plugin change without a changefile.** With no changefile,
  the promotion pipeline has nothing to bump and machines will silently
  ignore the update.
- **Do not edit installed plugin copies** under
  `~/.copilot/installed-plugins/`. The marketplace overwrites them on
  update. Fix the source here instead.
- **Do not mix up deployment paths.** agent-worktrees deploys via the
  marketplace + its own installer. agent-bridge deploys via its own
  installer (or a project service framework that wraps it). They are
  different pipelines.
- **Do not open a cutover/drain/promotion/process-repair PR without
  self-checking it first.** `docs/patterns/graceful-daemon-cutover.md`'s
  "Common review findings" section lists the small, recurring set of
  concurrency-ordering, PID-identity-safety, and cross-platform gaps that
  cost this repo's own graceful-cutover rollout 6-14 review rounds per PR —
  check your diff against it before opening the PR, not after the first
  review round names them.

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
