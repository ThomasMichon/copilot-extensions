# Vendored Installer Engine

- **Slug:** `vendored-installer-engine`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase PRs (see Coordination)
- **Created:** 2026-09-12
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Vision:** extends `visions/plugin-services` §Features/`self-contained-runtime`
  + §Features/`immutable-versioned-runtime` (see Context)
- **Umbrella issue:** _TBD — file once this effort's plan clears review_
- **Sub-issues:** _TBD_

## Guiding Intent

Every `agent-*` runtime plugin hand-maintains its own canonical installer
entrypoint — `scripts/install.ps1`/`scripts/install.sh` where present, else
`scripts/init.ps1`/`scripts/init.sh` (the repo's fallback canonical entrypoint
when `install.*` is absent, e.g. `agent-containers`, `agent-mcp`,
`agent-machines`) — full lifecycle managers (venv build, package install,
binstub generation, versioned-slot management, scheduled-task/service wiring,
deploy manifests, ZDD cutover, draining) that are supposed to all follow the
same install contract (`docs/install-contract.md`) but are, in practice, ~12
independently-authored copies. When a bug is found in the shared *mechanics*
(not the per-service specifics), it must be manually ported to every plugin by
hand — exactly the failure mode that just recurred with a venv-corruption fix
(a shared uv-managed-interpreter race producing a stale/broken venv slot,
tracked in a downstream consumer repo and fixed here via
copilot-extensions#2482): the same `New-SignedVenv`/`uv venv` defect existed
in agent-bridge, agent-logger, agent-vault, agent-index, agent-codespaces,
agent-dispatch, agent-ssh, agent-containers, agent-mcp, and agent-machines,
and only the first two got fixed before the human operator had to say "we
shouldn't have to keep fixing per-app installers."

This effort's goal: collapse the **shared engine** (the parts of the installer
that don't vary by service — uv acquisition, venv build + health/retry,
package install, binstub generation, versioned-slot lifecycle, deploy
manifest, scheduled-task management, ZDD cutover, draining) into ONE canonical
vendored source, fanned out byte-identically to every plugin (the same pattern
already proven for `versioned_runtime.py`), with each plugin's own installer
entrypoint shrinking to a thin per-service **config** (package dir(s), launch
command, sibling installs, and a small capability flag set: supports
scheduled tasks, supports ZDD cutover, supports draining, etc.) plus a single
call into the shared engine's entry point. A bug fixed once in the canonical
engine is fixed everywhere on the next sync + version bump — no more
hand-porting. This does not change what `self-contained-runtime` and
`immutable-versioned-runtime` promise (every plugin still owns a complete,
standalone runtime that its own installer deploys, with nothing borrowed at
*runtime* from a sibling or a git checkout); it changes how the installer's
own *authoring-time* mechanics stay in sync without N independently-drifting
copies.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Effort owner (rotates per phase) | Drives the active phase; see the effort's Journal for current owner and phase | This repo's normal worktree/PR flow — no fixed venue |

_Later phases (per-plugin rollout) may be split across further worktrees/sessions
as independent per-plugin PRs; see Coordination._

## Coordination

- **Topology:** independent per-phase/per-plugin PRs. Each phase below is a
  self-contained, mergeable PR — mirroring how `uniform-runtime-resolution`
  (the `versioned_runtime.py` collapse, effort #581/#765) was landed as a
  sequence of phased PRs rather than one giant change.
- **Host (owns PRs):** whichever session/worktree is actively executing the
  current phase; only one phase should be in flight at a time to avoid
  cross-phase merge conflicts in the same installer files.
- **Delegates:** none yet — this is currently solo work. If parallelized,
  each delegate should own one *plugin's* rollout PR (Phase 2+), never a
  cross-cutting slice of the canonical engine itself.
- **Handoff:** completion criteria differ by phase type. **Phase 0** (audit,
  no code) is "done" once its PR merges with reviewed audit findings recorded
  in `## Proposal` — no deploy or sync-tool check applies, since neither a
  plugin change nor the sync tool exists yet at that point. **Phase 1+**
  (any phase that changes a plugin's installer) is "done" only when its PR is
  merged, its plugin(s) redeployed and verified healthy, and
  `tools/sync-installer-engine.py --check` (built in Phase 1) is green in CI.
  Use the standard context-handoff flow to hand off between phases; the
  Journal below is the resumption point.

## Context

- **`visions/plugin-services` §Features/`self-contained-runtime`**: *"Every
  runtime plugin owns a complete, standalone runtime (venv + binstub +
  service) that its own installer deploys and updates. Nothing a service
  needs to run is borrowed from a sibling plugin or from a git checkout of
  this repo."* This is the governing behavior this effort extends (not
  changes): it directly rules out fetching the shared engine from a git
  checkout at install/runtime, which is why byte-vendoring (not a git-fetch
  bootstrap) is this effort's chosen mechanism (see the design-decision note
  before Phase 1).
- **`visions/plugin-services` §Features/`immutable-versioned-runtime`**: the
  vendored engine lives *inside* each plugin's existing immutable versioned
  install; this effort does not change that model, only what authoring-time
  duplication looks like beneath it.
- **`docs/patterns/runtime-agent-plugin.md`** — the governing pattern for "add
  an `agent-*` plugin," including the cross-platform install contract shape
  this effort's canonical engine must keep satisfying.
- **`docs/patterns/graceful-daemon-cutover.md`** and
  **`docs/patterns/durable-vs-versioned-runtime.md`** — the existing patterns
  for ZDD cutover and durable-vs-versioned-runtime split; the engine's
  `SupportsZddCutover`/draining config flags must compose with these, not
  reimplement them.
- **`docs/install-contract.md`** is the existing, extensively-detailed
  contract every plugin's installer must already follow. It explicitly states
  the architectural constraint this effort must respect: *"Because the
  Copilot CLI marketplace pulls each plugin's payload independently, each
  plugin's install flow must be completely self-contained — there is no
  shared install module resolved at install or runtime."* Shared primitives
  are **vendored in byte-identically at authoring time... kept in sync by a
  repo tool** rather than resolved from a common location at runtime. This
  effort does not challenge that constraint — it *extends* the same vendoring
  pattern to a much larger shared surface than today's single-file
  `versioned_runtime.py`.
- **Existing precedent to mirror exactly:**
  - `libs/versioned-runtime/versioned_runtime.py` — the canonical single
    source for the immutable-versioned-slot primitive.
  - `tools/sync-versioned-runtime.py` — fans the canonical file out
    byte-identically to every Python runtime plugin's `scripts/`, `--check`
    mode for CI/pre-push. Notably already supports **opt-in adoption**
    (a plugin adopts `resolve-runtime.*` by dropping a copy in; the sync then
    keeps it byte-identical) — the same opt-in model this effort should use
    for a phased plugin-by-plugin rollout.
  - `tools/check-vendored-libs-sync.py` — a *different*, more generic
    existing guard for vendored **Python packages** under
    `plugins/<plugin>/libs/<lib>` (byte-identical `src/` trees + matching
    versions). It does not apply directly here because `install.ps1`/`.sh` are
    not `pip`-installed packages — they're the scripts that *build* the venv
    in the first place — but its "byte-identical + version-locked" invariant
    is the same one this effort needs for the new engine files.
  - `efforts/active/uniform-runtime-resolution/` (Status: Done) — the closed
    effort that did this exact collapse for `versioned_runtime.py` in phased
    PRs. Read its Journal for the phasing discipline and pitfalls before
    planning Phase 1+ here.
- **Immediate trigger:** a venv-corruption defect (a shared uv-managed
  interpreter cache leaving a slot with the python binary present but
  `pyvenv.cfg` missing/incomplete after a concurrent `uv venv` race) was fixed
  in `agent-bridge` and mirrored by hand into `agent-logger`
  (copilot-extensions#2482). The same `New-SignedVenv`/`uv venv` pattern (and
  an earlier, related shared-interpreter-race retry fix it built on) is
  duplicated, unfixed, in at least: `agent-vault`, `agent-index`,
  `agent-codespaces`, `agent-dispatch`, `agent-ssh`, `agent-containers`,
  `agent-mcp`, `agent-machines`. This effort exists so that class of bug gets
  fixed once, not N times.
- **Scale (current per-plugin canonical installer entrypoint line counts,
  PowerShell side only — `install.ps1` where it exists, else `init.ps1`):**
  agent-worktrees 3638, agent-dispatch 2940, agent-bridge 2806, agent-machines
  2141 (`init.ps1`), agent-index 2228, agent-codespaces 1360, agent-logger
  1339, agent-vault 1178, agent-containers 897 (`init.ps1`), budget-guidance
  886, agent-mcp 826 (`init.ps1`), agent-ssh 789. The `.sh` counterparts are
  similar in size. This is ~20,000+ lines of independently-authored installer
  logic across the fleet, most of it mechanically identical.
  - **`budget-guidance` is in scope for the audit** (it is a `pyproject.toml`
    runtime plugin per `tools/check-docs-consistency.py`'s definition, with an
    `install.ps1` that builds a venv via `uv venv` like the others) but is
    **excluded from this effort's rollout scope** because it is not an
    `agent-*` persistent-service plugin — it's a skill-delivery plugin with an
    incidental Python component, not a long-running daemon needing scheduled
    tasks/ZDD cutover/draining. Re-evaluate only if its installer independently
    picks up the same shared-engine bugs this effort is fixing.
- **`agent-bridge`'s installer is repeatedly cited elsewhere in this repo as
  "the reference implementation"** (e.g. a downstream consumer's tracked issue
  requiring `agent-logger` to mirror it) — it should likely be the source the
  canonical engine is extracted *from*, and the first plugin re-pointed *at*
  the canonical engine (dogfooding before asking any other plugin to adopt
  it).

## Request

> We need to ensure that all our agent-* services have the *exact same*
> installer engine; the installer should be vendored and enforced in-sync,
> and simply have a per-service config for the launch command and a couple
> of other key differentiators, like supporting scheduled tasks, zdd
> cutover, draining, etc. We shouldn't have to keep fixing per-app
> installers, they should all use the same flow.

## Plan

### Phase 0 — Audit the real shared surface (no code changes)
- [ ] Diff `New-SignedVenv` / `Ensure-Uv` / `Invoke-UvVenvResilient` /
      `Invoke-UvPipInstallResilient` / `Invoke-NativeCapture` /
      `Test-IsSreModuleMismatch` / `Test-IsVenvCorruption` / binstub-writing /
      deploy-manifest-writing / scheduled-task functions across all ~12
      runtime plugins' canonical installer entrypoint — `install.ps1` where it
      exists, else `init.ps1` (`agent-containers`, `agent-mcp`,
      `agent-machines`) — PowerShell first; `.sh` mirrors (`install.sh`/
      `init.sh`) after the shape is settled.
- [ ] Classify every duplicated function as: **(a) byte-identical or
      near-identical already** (pure engine — safe to collapse verbatim),
      **(b) same shape, different constants** (needs a config parameter, e.g.
      package name, Python version pin), or **(c) genuinely per-service**
      (agent-bridge's sibling-plugin install, agent-dispatch's supervisor
      service, agent-index's separate torch-stack engine venv, agent-codespaces'
      ssh-manager+cred-relay+config-migrate installs — these stay in the
      per-plugin config/wrapper, not the engine).
- [ ] Write the findings into `## Proposal` below before starting Phase 1 —
      this determines the engine's real function surface and config schema.

### Design decision — vendoring over git-fetch (resolved 2026-09-12)

Before Phase 1 started, the operator raised a real alternative: instead of
vendoring (byte-copying) the engine into every plugin, ship each plugin with a
tiny bootstrap stub that fetches the canonical engine directly via
`uv`'s git-VCS support (`uv pip install`/`uvx --from
"git+https://github.com/<org>/<this-repo>@<pinned-sha>#subdirectory=libs/installer-engine"
...`), pinned to an exact commit. This is technically feasible — `uv` is
pip-compatible for `git+URL@rev#subdirectory=path` sources and has its own git
support — and, if the engine were also rewritten as a single cross-platform
Python package, it would eliminate the PowerShell/bash **fork** duplication
too (not just the per-plugin duplication byte-vendoring solves).

**Decided: byte-vendoring, not git-fetch**, because git-fetch trades the
current problem for a worse instance of the *same* problem this effort exists
to prevent:

- **New mandatory network dependency at install/update time.** Today, once
  the marketplace payload is copied, install runs offline except for
  precedented, narrow network touches (uv self-acquire, PyPI/governed-feed
  installs). Git-fetch makes every install/update depend on GitHub
  reachability — including the exact moment someone needs to repair a broken
  install during an incident.
- **A new shared cache reintroduces the same hazard class already fixed
  once.** The fetched engine has to land in a local cache that concurrent
  installs share — structurally the same "shared cache, concurrent writers,
  partial-write corruption" shape as uv's managed-interpreter cache that
  caused the shared-interpreter-race bugs fixed via copilot-extensions#2482
  (and its earlier SRE-mismatch precursor). Vendoring keeps each plugin's
  copy inside its own already-isolated payload — no new shared-cache surface
  at all.
- **It would reverse a deliberate, documented constraint**
  (`docs/install-contract.md`: *"there is no shared install module resolved
  at install or runtime... each plugin's install flow must be completely
  self-contained"*) rather than extend it. Reversing that is a bigger,
  separate decision than this effort's actual trigger (stop hand-porting
  installer bugs) requires.
- **The real duplication-elimination win (ps1/sh fork) needs a full rewrite
  either way** — byte-vendoring doesn't get it, but neither does git-fetch
  unless the engine becomes pure Python, which is a much larger, separable
  rewrite (SAC-safe launchers, Task Scheduler vs. systemd wiring, etc., all
  currently native shell) that can be evaluated on its own merits later,
  independent of *how* the engine reaches each plugin.

**Kept as a clean off-ramp, not closed off:** `libs/installer-engine/` gets a
real `pyproject.toml` from Phase 1 on, so it is *also* a valid
pip/uv git-fetch target later, if the cache-concurrency and
offline-failure questions get real answers in a future effort. This phase
does not have to bet on both problems (duplication *and* how it's delivered)
at once.

**Explicit non-goal this decision implies for Phase 1:** don't let the
vendored corpus balloon back into what it's replacing. The whole point is
fewer lines to fix N times, not the same line count moved one directory over.
Concretely:
- The canonical engine only carries the (a)/(b) functions from the Phase 0
  audit — genuinely per-service logic (agent-bridge's sibling-plugin installs,
  agent-dispatch's supervisor service, etc.) stays in each plugin's thin
  config/wrapper, never gets pulled into the engine "for convenience."
- Prefer **fewer, more configurable functions** over near-duplicate variants
  parameterized by a flag — a config-driven `Invoke-AgentServiceInstall`
  entry point, not a menu of twelve slightly-different `Invoke-XInstall`
  functions living in the same file.
- Track the vendored engine's own line count in the Validation Plan (below) —
  growth there is exactly the failure mode to watch for.

### Phase 1 — Canonical engine + reference plugin + CI guard
- [ ] Create `libs/installer-engine/installer-engine.ps1` and
      `installer-engine.sh` — the canonical (a) and (b) functions from Phase 0,
      parameterized by a small config object/associative-array (service name,
      package dir(s), launch command, `SupportsScheduledTask`,
      `SupportsZddCutover`, `SupportsDraining`, Python version pin, sibling
      installs list, etc.).
- [ ] Write `tools/sync-installer-engine.py` mirroring
      `sync-versioned-runtime.py` exactly: canonical source ->
      byte-identical fan-out to `plugins/<plugin>/scripts/installer-engine.*`,
      `--check` mode for CI/pre-push, **opt-in adoption** (only plugins that
      already carry a `scripts/installer-engine.*` copy get synced — enables
      the phased rollout in Phase 2+ without forcing every plugin to migrate
      in one PR).
    - Consider whether this should be a mode of `sync-versioned-runtime.py`
      (both vendor from `libs/` into `scripts/`) or a fully separate tool —
      decide during Phase 1, record the decision here.
- [ ] Wire the new sync tool's `--check` into `tools/check-install-contract.py`
      and/or CI (`guards + lint`), matching how `versioned_runtime.py`
      byte-identity is already enforced.
- [ ] Convert `agent-bridge`'s `install.ps1`/`install.sh` to source the vendored
      engine + a per-service config block, as the reference conversion.
      Validate with `agent-bridge`'s existing test suite
      (`test_install_sre_retry.py`, `test_install_venv_corruption_retry.py`,
      `test_installer_powershell51.py`, etc.) — these will need updating to
      extract functions from the vendored engine file instead of `install.ps1`
      directly (or the engine file needs its own equivalent test suite that
      plugin tests then just reference/import).
- [ ] PR this phase; get it through the automated review gate; merge; deploy
      + verify agent-bridge before starting Phase 2.

### Phase 2+ — Roll remaining plugins onto the engine, one small batch per PR

**Non-exempt adopter set — every plugin this effort requires to actually
adopt the engine (10 plugins):** `agent-bridge`, `agent-logger`,
`agent-vault`, `agent-ssh`, `agent-codespaces`, `agent-index`,
`agent-dispatch`, `agent-containers`, `agent-mcp`, `agent-machines`.
`agent-worktrees` is **not** part of this set — it carries a separately
recorded, permanent, non-adopting exception (below), so "every plugin in the
non-exempt adopter set has adopted" can be fully satisfied without it ever
adopting. `budget-guidance` is also not part of this set (see Context: not
an `agent-*` persistent-service plugin) and is never expected to adopt the
engine under this effort.

- [ ] `agent-logger` (already has the pyvenv.cfg fix hand-applied today —
      good second-mover to prove the engine covers a second plugin's needs).
- [ ] `agent-vault`, `agent-ssh` (smaller installers, low risk).
- [ ] `agent-codespaces`, `agent-index` (each carries genuine per-service
      logic beyond the engine — sibling package installs / separate engine
      venv — prove the config schema handles these before doing the rest).
- [ ] `agent-dispatch`, `agent-containers`, `agent-mcp`, `agent-machines`.
- [ ] **`agent-worktrees` is a decided permanent exception, not a deferred
      evaluation, and not part of the non-exempt adopter set above.** It is
      the control-plane plugin and by far the largest, most bespoke installer
      (3638 lines), and it already opts out of the related
      `resolve-runtime.*` fan-out for the same specialization reason (see
      `tools/sync-versioned-runtime.py`'s `RESOLVER_BESPOKE` set). It does
      **not** adopt the shared engine in this effort's scope, now or later —
      its exclusion is intentional and permanent, not a TODO. Record this
      exception explicitly in `tools/sync-installer-engine.py` (an exclusion
      list, analogous to `RESOLVER_BESPOKE`) and in any completion-criteria/
      guard scope alongside it, so a future "is this effort done" check does
      not treat agent-worktrees' non-adoption as unfinished work.
- [ ] Retire the opt-in gate once every plugin in the **non-exempt adopter
      set** named above has adopted the engine, with `agent-worktrees`'
      permanent-exception record still present and accurate — mirroring how a
      fully-adopted primitive eventually becomes mandatory in
      `check-install-contract.py`.
      "Done" for this effort means *that* set is fully adopted, not "every
      plugin in the repo, no exceptions."

## Validation Plan

- [ ] Phase 0's audit findings are recorded and reviewed (via the effort PR)
      before any engine code is written — wrong assumptions here would ripple
      into every later phase.
- [ ] `tools/sync-installer-engine.py --check` passes in CI for every plugin
      that has adopted the engine, and fails (with a clear diagnostic) when a
      vendored copy is hand-edited or drifts from canonical.
- [ ] Each converted plugin's full existing test suite still passes
      (`pytest`, run through this repo's normal bounded test runner), plus
      any install-contract guard (`tools/check-install-contract.py`).
- [ ] Each converted plugin is deployed to at least one real machine and its
      daemon/service verified healthy post-conversion — a behavior-preserving
      refactor that silently breaks a live service is the one failure mode
      this effort must not introduce.
- [ ] After Phase 1, deliberately reproduce a class of bug already fixed once
      in the canonical engine (e.g. the pyvenv.cfg/uv-exit-106 venv-corruption
      signature fixed via copilot-extensions#2482) against a
      **not-yet-migrated** plugin, confirm it's still present there, migrate
      that plugin, and confirm the same reproduction now passes — proving the
      "fix once, fixed everywhere on adopt" property this effort exists to
      deliver.
- [ ] `docs/install-contract.md` is updated to describe the new engine +
      config-schema pattern once Phase 1 lands, so it stays the accurate
      reference (not just this effort's private plan).
- [ ] **Net corpus size actually shrinks.** After each phase, total installer
      line count across converted plugins (their `install.ps1`/`.sh` +
      whatever share of the vendored `installer-engine.*` they carry) must be
      materially smaller than the pre-conversion baseline for those same
      plugins — not just relocated. Record before/after line counts per
      converted plugin in the Journal. A phase that leaves the corpus flat or
      larger is a signal the engine is accreting per-service special-casing
      and needs re-scoping, not a pass.

## Proposal

_Pending — Phase 0's audit findings land here before Phase 1 starts._

## Journal

### 2026-09-12 — Kickoff
- Effort created directly off the operator's request, immediately following
  a venv-corruption fix (pyvenv.cfg missing after a shared uv-managed
  interpreter race, PR copilot-extensions#2482) — the session that surfaced
  this exact pain point (fixed agent-bridge, then had to hand-port the
  identical fix into agent-logger).
- Grounded in existing precedent: `docs/install-contract.md`'s explicit
  vendoring-not-runtime-sharing constraint, `versioned_runtime.py` +
  `sync-versioned-runtime.py` (the closed `uniform-runtime-resolution` effort)
  as the pattern to extend, and `check-vendored-libs-sync.py` as a related but
  distinct existing guard (Python packages, not installer scripts).
- Confirmed scope via line-count audit: 12 `pyproject.toml`-bearing runtime
  plugins per `tools/check-docs-consistency.py`'s definition (one,
  `budget-guidance`, has no venv/uv engine logic and is out of this effort's
  actual scope -- see Context), ~20,000+ lines of
  installer PowerShell alone, most of it mechanically duplicated engine logic.
- Plan phased explicitly to mirror `uniform-runtime-resolution`'s landed
  discipline: audit first (Phase 0), canonical engine + one reference plugin
  (Phase 1), then small per-plugin rollout batches (Phase 2+) — never one
  giant cross-plugin PR.
- Not yet started: Phase 0 audit. This effort's own plan has not yet cleared
  the automated review gate.

### 2026-09-12 — Design decision: vendoring over git-fetch
- Before Phase 1 started, evaluated an operator-proposed alternative: ship a
  tiny per-plugin bootstrap stub that fetches the canonical engine directly
  via `uv`'s pinned git+subdirectory VCS support instead of byte-vendoring it.
  Confirmed technically feasible (`uv` is pip-compatible for
  `git+URL@rev#subdirectory=path`, has its own git support).
- Decided **against** git-fetch for now, in favor of byte-vendoring: git-fetch
  adds a mandatory network dependency to every install/update (today's flow is
  offline after the payload copy, save precedented uv/PyPI touches), and its
  fetched-engine cache would reintroduce the exact "shared cache, concurrent
  writers, partial-write corruption" hazard class already fixed once via
  copilot-extensions#2482 — just relocated, not eliminated. It would also reverse
  `docs/install-contract.md`'s documented self-containment constraint, which
  is a bigger, separate decision than this effort's actual trigger requires.
  Full reasoning recorded inline above Phase 1 (`### Design decision —
  vendoring over git-fetch`).
- Kept a clean off-ramp: `libs/installer-engine/` will carry a real
  `pyproject.toml` from Phase 1 on, so it remains a valid git-fetch target
  later if the cache/offline questions get solved in a future effort.
- Added an explicit non-goal + Validation Plan item: the vendored corpus must
  actually shrink per converted plugin, not just relocate the same line count
  — tracking before/after line counts per plugin going forward.
