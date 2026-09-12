# Vendored Installer Engine

- **Slug:** `vendored-installer-engine`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase PRs (see Coordination)
- **Created:** 2026-09-12
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Vision:** extends the install-contract's existing vendoring precedent (see Context)
- **Umbrella issue:** _TBD — file once this effort's plan clears review_
- **Sub-issues:** _TBD_

## Guiding Intent

Every `agent-*` runtime plugin hand-maintains its own `scripts/install.ps1` /
`scripts/install.sh` — full lifecycle managers (venv build, package install,
binstub generation, versioned-slot management, scheduled-task/service wiring,
deploy manifests, ZDD cutover, draining) that are supposed to all follow the
same install contract (`docs/install-contract.md`) but are, in practice, ~12
independently-authored copies. When a bug is found in the shared *mechanics*
(not the per-service specifics), it must be manually ported to every plugin by
hand — exactly the failure mode that just recurred with the pyvenv.cfg
corruption fix (aperture-labs#6852): the same `New-SignedVenv`/`uv venv` defect
existed in agent-bridge, agent-logger, agent-vault, agent-index,
agent-codespaces, agent-dispatch, agent-ssh, agent-containers, agent-mcp, and
agent-machines, and only the first two got fixed before the human operator
had to say "we shouldn't have to keep fixing per-app installers."

This effort's goal: collapse the **shared engine** (the parts of the installer
that don't vary by service — uv acquisition, venv build + health/retry,
package install, binstub generation, versioned-slot lifecycle, deploy
manifest, scheduled-task management, ZDD cutover, draining) into ONE canonical
vendored source, fanned out byte-identically to every plugin (the same pattern
already proven for `versioned_runtime.py`), with each plugin's own
`install.ps1`/`install.sh` shrinking to a thin per-service **config** (package
dir(s), launch command, sibling installs, and a small capability flag set:
supports scheduled tasks, supports ZDD cutover, supports draining, etc.) plus a
single call into the shared engine's entry point. A bug fixed once in the
canonical engine is fixed everywhere on the next sync + version bump — no more
hand-porting.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| lambda-core (this session's lineage) | Drives Phase 0 (audit) and Phase 1 (canonical engine + reference plugin) | `copilot-extensions.worktrees/lambda-core-win-20260912-150643-b17b` |

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
- **Handoff:** a phase is "done" only when its PR is merged, its plugin(s)
  redeployed and verified healthy, and `tools/check-vendored-libs-sync.py` /
  the new sync tool (Phase 1) is green in CI. Use the standard context-handoff
  flow to hand off between phases; the Journal below is the resumption point.

## Context

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
- **Immediate trigger:** aperture-labs#6852 (pyvenv.cfg/uv-exit-106 venv
  corruption) was fixed in `agent-bridge` and mirrored by hand into
  `agent-logger` (PR copilot-extensions#2482). The same `New-SignedVenv`
  pattern (and the earlier #6785 `SRE module mismatch` retry fix it built on)
  is duplicated, unfixed, in at least: `agent-vault`, `agent-index`,
  `agent-codespaces`, `agent-dispatch`, `agent-ssh`, `agent-containers`,
  `agent-mcp`, `agent-machines`. This effort exists so that class of bug gets
  fixed once, not N times.
- **Scale (current per-plugin installer line counts, PowerShell side only):**
  agent-worktrees 3638, agent-dispatch 2940, agent-bridge 2806, agent-machines
  2141 (`init.ps1`), agent-index 2228, agent-codespaces 1360, agent-logger
  1339, agent-vault 1178, agent-containers 897 (`init.ps1`), budget-guidance
  886 (not a runtime plugin — no venv/uv logic, excluded from scope),
  agent-mcp 826 (`init.ps1`), agent-ssh 789. The `.sh` counterparts are
  similar in size. This is ~20,000+ lines of independently-authored installer
  logic across the fleet, most of it mechanically identical.
- **`agent-bridge`'s installer is repeatedly cited elsewhere in this repo as
  "the reference implementation"** (e.g. aperture-labs issue #930) — it should
  likely be the source the canonical engine is extracted *from*, and the
  first plugin re-pointed *at* the canonical engine (dogfooding before asking
  any other plugin to adopt it).

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
      runtime plugins' `install.ps1` (PowerShell first; `.sh` mirrors after
      the shape is settled).
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
- [ ] `agent-logger` (already has the pyvenv.cfg fix hand-applied today —
      good second-mover to prove the engine covers a second plugin's needs).
- [ ] `agent-vault`, `agent-ssh` (smaller installers, low risk).
- [ ] `agent-codespaces`, `agent-index` (each carries genuine per-service
      logic beyond the engine — sibling package installs / separate engine
      venv — prove the config schema handles these before doing the rest).
- [ ] `agent-dispatch`, `agent-containers`, `agent-mcp`, `agent-machines`.
- [ ] `agent-worktrees` is the control-plane plugin and largest/most bespoke
      installer (3638 lines) — evaluate last whether it should adopt the
      engine at all, or remain intentionally bespoke (it already opts out of
      the shared `resolve-runtime.*` fan-out for the same reason — see
      `tools/sync-versioned-runtime.py`'s `RESOLVER_BESPOKE` set).
- [ ] Retire the opt-in gate once every intended plugin has adopted the engine
      (mirroring how a fully-adopted primitive eventually becomes mandatory
      in `check-install-contract.py`).

## Validation Plan

- [ ] Phase 0's audit findings are recorded and reviewed (via the effort PR)
      before any engine code is written — wrong assumptions here would ripple
      into every later phase.
- [ ] `tools/sync-installer-engine.py --check` passes in CI for every plugin
      that has adopted the engine, and fails (with a clear diagnostic) when a
      vendored copy is hand-edited or drifts from canonical.
- [ ] Each converted plugin's full existing test suite still passes
      (`test-supervisor`-wrapped `pytest`), plus any install-contract guard
      (`tools/check-install-contract.py`).
- [ ] Each converted plugin is deployed to at least one real machine
      (lambda-core) and its daemon/service verified healthy post-conversion —
      a behavior-preserving refactor that silently breaks a live service is
      the one failure mode this effort must not introduce.
- [ ] After Phase 1, deliberately reproduce a class of bug already fixed once
      in the canonical engine (e.g. the #6852 pyvenv.cfg signature) against a
      **not-yet-migrated** plugin, confirm it's still present there, migrate
      that plugin, and confirm the same reproduction now passes — proving the
      "fix once, fixed everywhere on adopt" property this effort exists to
      deliver.
- [ ] `docs/install-contract.md` is updated to describe the new engine +
      config-schema pattern once Phase 1 lands, so it stays the accurate
      reference (not just this effort's private plan).

## Proposal

_Pending — Phase 0's audit findings land here before Phase 1 starts._

## Journal

### 2026-09-12 — Kickoff
- Effort created directly off the operator's request, immediately following
  aperture-labs#6852 (pyvenv.cfg venv-corruption fix, PR copilot-extensions#2482)
  — the session that surfaced this exact pain point (fixed agent-bridge, then
  had to hand-port the identical fix into agent-logger).
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
