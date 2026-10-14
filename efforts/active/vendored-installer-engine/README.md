# Vendored Installer Engine

- **Slug:** `vendored-installer-engine`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase PRs (see Coordination)
- **Created:** 2026-09-12
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Vision:** extends `visions/plugin-services` §Features/`self-contained-runtime`
  + §Features/`immutable-versioned-runtime` (see Context)
- **Umbrella issue:** _TBD — file once this effort's plan clears review_
- **Sub-issues:** #6389 (build/serving convergence); #6390 (Index installer result boundary); existing adopter and cutover owners remain in the linked audit.

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
- **Scale (per-plugin canonical installer entrypoint line counts as of
  2026-09-12, PowerShell side only — `install.ps1` where it exists, else
  `init.ps1`; re-check before relying on these for the Validation Plan's
  shrinkage measurement, as they drift with routine bugfixes):**
  agent-worktrees 3638, agent-dispatch 2940, agent-bridge 2855, agent-machines
  2141 (`init.ps1`), agent-index 2228, agent-codespaces 1360, agent-logger
  1392, agent-vault 1178, agent-containers 897 (`init.ps1`), budget-guidance
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

2026-10-12 follow-up, verbatim:

> Great. Ensure we update or file upstream issues, update any relevant efforts,
> and otherwise durably capture these concerns. I think we have an effort to
> standardize installers, so is works with that. Use context management tools
> to inline-handoff and keep driving here

The referenced concerns are the [serving-convergence audit](serving-convergence-audit.md).
This follow-up requests durable tracking and effort integration, not immediate
implementation of every recommendation or takeover of another participant's work.

## Plan

### Phase 0 — Audit the real shared surface (no code changes)
- [x] Diff `New-SignedVenv` / `Ensure-Uv` / `Invoke-UvVenvResilient` /
      `Invoke-UvPipInstallResilient` / `Invoke-NativeCapture` /
      `Test-IsSreModuleMismatch` / `Test-IsVenvCorruption` / binstub-writing /
      deploy-manifest-writing / scheduled-task functions across all ~13
      runtime plugins' canonical installer entrypoint — `install.ps1` where it
      exists, else `init.ps1` (`agent-containers`, `agent-mcp`,
      `agent-machines`) — PowerShell first; `.sh` mirrors (`install.sh`/
      `init.sh`) after the shape is settled.
- [x] Classify every duplicated function as: **(a) byte-identical or
      near-identical already** (pure engine — safe to collapse verbatim),
      **(b) same shape, different constants** (needs a config parameter, e.g.
      package name, Python version pin), or **(c) genuinely per-service**
      (agent-bridge's sibling-plugin install, agent-dispatch's supervisor
      service, agent-index's separate torch-stack engine venv, agent-codespaces'
      ssh-manager+cred-relay+config-migrate installs — these stay in the
      per-plugin config/wrapper, not the engine).
- [x] Write the findings into `## Proposal` below before starting Phase 1 —
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

### Design decision — `agent-pull-requests` as the Phase 1 pilot instead of `agent-bridge` (resolved 2026-09-22)

The Plan below originally named `agent-bridge` (a live production daemon,
deployed on real operator machines) as the Phase 1 reference conversion. A
new, unrelated effort (`agent-pull-requests`, a brand-new small on-demand CLI
plugin with no daemon, no scheduled task, and no live production traffic)
needed a real installer at the same time this effort was still Draft with
zero engine code written. The operator explicitly approved reordering: build
the real engine now and make `agent-pull-requests` the first pilot
conversion, ahead of `agent-bridge`.

Rationale: proving the engine's shape against a brand-new, low-risk,
zero-existing-users plugin is strictly safer than a first cut against a live
daemon other sessions/machines already depend on. `agent-bridge`'s own
conversion (Phase 1's originally-planned reference) remains open follow-through
and should still happen — its test-suite migration
(`test_install_sre_retry.py`, `test_install_venv_corruption_retry.py`,
`test_installer_powershell51.py`) is real work this reordering does not
remove, it only postpones ahead of an even lower-risk proof. This means the
Validation Plan's "net corpus size actually shrinks" criterion does **not**
apply to this reorder step — `agent-pull-requests` is a brand-new plugin,
not a converted existing one, so there is no pre-conversion baseline to
shrink against yet. That criterion still applies, unchanged, once
`agent-bridge`/the Phase 2+ set actually convert.

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

- [x] **Critical: adapt `tools/check-install-contract.py` (lines ~280-368) to
      the composed engine/wrapper split *before* converting any plugin.**
      That guard currently text-scans each plugin's own `install.*`/`init.*`
      for specific literal markers — `uv pip install`, the
      `install-contract:v3 versioned-venv` block marker, `stamp`/`provision`,
      source-kind blocks, and the v4 markers. A thin wrapper that moves those
      blocks into `scripts/installer-engine.*` will fail this existing guard
      even though the contract is still honored, just relocated. Either make
      the guard also inspect the vendored engine file(s) a wrapper sources
      (composed view), or keep the required literal marker seams present in
      each plugin's thin wrapper itself (not just the engine). Add regression
      coverage for whichever approach is chosen — this must land in the same
      PR as the first engine/wrapper conversion, not after.
      **Landed:** kept the required literal marker seams (self-stage/smoke-seam
      v4 blocks, `Get-SourceKind`/source-kind, `install-contract:v3
      versioned-venv`) in each plugin's own thin wrapper; `check-install-contract.py`
      was given a narrow, non-loophole addition that accepts a wrapper's
      `uv pip install`/schema-v3-manifest requirements as satisfied only when
      the wrapper both (a) dot-sources the vendored engine AND (b) actually
      calls the specific engine function (`Invoke-UvPipInstallResilient`/
      `Write-DeployManifest`) — merely referencing the engine file is not
      sufficient to pass. It also now calls `tools/sync-installer-engine.py`'s
      own drift check as part of the same run.
- [x] Create `libs/installer-engine/installer-engine.ps1` and
      `installer-engine.sh` — the canonical (a) and (b) functions from Phase 0,
      parameterized by a small config object/associative-array (service name,
      package dir(s), launch command, `SupportsScheduledTask`,
      `SupportsZddCutover`, `SupportsDraining`, Python version pin, sibling
      installs list, etc.).
      **Landed** with the minimal-CLI-plugin function set the Phase 0 audit
      identified as necessary: `Invoke-NativeCapture`, `Test-IsSreModuleMismatch`,
      `Test-IsVenvCorruption`, `Invoke-UvPipInstallResilient`,
      `Invoke-UvVenvResilient`, `Ensure-Uv`, `New-SignedVenv`,
      `Write-DeployManifest`, and a newly-written `Write-SimpleBinstub`
      (no shared function for this existed anywhere yet). Scheduled-task
      functions were correctly left out of the engine per Phase 0's (c)
      classification — `agent-pull-requests` needs none, and the
      per-service semantics genuinely differ (agent-dispatch/agent-logger/
      agent-index/agent-vault all have irreducibly different task lifecycles).
- [x] Write `tools/sync-installer-engine.py` mirroring
      `sync-versioned-runtime.py` exactly: canonical source ->
      byte-identical fan-out to `plugins/<plugin>/scripts/installer-engine.*`,
      `--check` mode for CI/pre-push, **opt-in adoption** (only plugins that
      already carry a `scripts/installer-engine.*` copy get synced — enables
      the phased rollout in Phase 2+ without forcing every plugin to migrate
      in one PR).
    - Consider whether this should be a mode of `sync-versioned-runtime.py`
      (both vendor from `libs/` into `scripts/`) or a fully separate tool —
      decide during Phase 1, record the decision here.
      **Decided:** a separate `tools/sync-installer-engine.py` tool, structured
      like `sync-versioned-runtime.py` (single canonical pair of files ->
      explicit adopter fan-out, `--check` drift detection), rather than a mode
      flag on the existing tool — the two sync targets (`versioned_runtime.py`
      vs. `installer-engine.ps1`/`.sh`) have independent adoption timelines
      (a plugin can adopt versioned-runtime without the installer engine, and
      vice versa) and keeping them as separate tools keeps that independence
      obvious.
- [x] Wire the new sync tool's `--check` into `tools/check-install-contract.py`
      and/or CI (`guards + lint`), matching how `versioned_runtime.py`
      byte-identity is already enforced.
- [x] Convert `agent-bridge`'s `install.ps1`/`install.sh` to source the vendored
      engine + a per-service config block, as the reference conversion.
      Validate with `agent-bridge`'s existing test suite
      (`test_install_sre_retry.py`, `test_install_venv_corruption_retry.py`,
      `test_installer_powershell51.py`, etc.) — these will need updating to
      extract functions from the vendored engine file instead of `install.ps1`
      directly (or the engine file needs its own equivalent test suite that
      plugin tests then just reference/import).
      **Done 2026-10-05:** after the low-risk `agent-pull-requests` pilot and
      the `agent-logger`/`agent-vault`/`agent-ssh` second-mover legs proved
      the canonical-reference wrapper shape, `agent-bridge` itself now adopts
      that same shared-engine form. The PowerShell/Linux retry regressions now
      read the shared engine for the truly shared helpers, while the
      bridge-specific build-artifact scrub / vendored-lib staging /
      service-lifecycle logic remains in the wrapper.
- [x] PR this phase; get it through the automated review gate; merge; deploy
      + verify **`agent-pull-requests`** (the reordered pilot) before starting
      Phase 2 / `agent-bridge`'s conversion.

### Phase 2+ — Roll remaining plugins onto the engine, one small batch per PR

> **Forward note (2026-09-29):** `agent-pull-requests`, the current proof
> adopter, no longer keeps a byte-vendored `scripts/installer-engine.*` copy
> on `dev`. By explicit operator-approved cross-effort decision (recorded in
> the Journal below), it now sources canonical
> `libs/installer-engine/installer-engine.{sh,ps1}` directly during authoring
> and promotion materializes the local copy back into the shipped payload.
> Future adopters in this Phase 2+ list should therefore go straight to that
> canonical-reference form rather than first landing a new byte-vendored dev
> copy. The runtime/install-time constraint that shipped payloads remain fully
> self-contained is unchanged.

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

- [x] `agent-logger` (already has the pyvenv.cfg fix hand-applied today —
      good second-mover to prove the engine covers a second plugin's needs).
- [x] `agent-vault` (smaller installer, low risk).
- [x] `agent-ssh` (smaller installer, low risk).
- [x] `agent-codespaces` — PR #5804 merged after current-head approval and
      green CI; released payload and runtime verified through the supported
      update flow. See the 2026-10-08 journal entry and issue #5802.
- [ ] `agent-index` (separate engine venv — prove the config schema handles
      this before completing the remaining adopters). Implementation merged
      as PR #5885; release and live deployment verification remain paused
      at the operator's request.
- [x] Resolve the POSIX installer/stamp permission mismatch before the stricter
      startup admission ships. New installer-created artifacts must not inherit
      group/other write access from `umask 0002`. Include practical, bounded
      session-start repair per the 2026-10-12 operator clarification below;
      merged as PR #6116 with actual installer/stamp/activation and adversarial
      proofs. Release and live rollout remain tracked by the open Index item.
- [x] `agent-dispatch` — PR #5832 merged, release #5866 promoted the
      adopter, and the normal update verified payload/runtime `0.14.0-dev1`
      with a healthy routed coordinator and running supervisor.
- [ ] `agent-containers`, `agent-mcp`, `agent-machines`.
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

### Validation infrastructure — temporary Docker accountability (#6109)

- [x] Review the [temporary-container obligation proposal](temporary-container-obligations.md)
      before implementing its new producer transaction and identity contract —
      PR #6116 merged after current-head review and successful source CI.
- [ ] After that review, implement and validate automatic reservations, exact
      Docker identity binding and verified close-out across both clean-room
      wrappers and the devcontainer test runner. Existing Index live-proof
      requirements remain open; this infrastructure slice does not satisfy them.

### Bug sweep — linked open bugs (2026-09-24)

_Correlated via a facility-driven sweep of open `bug`-labeled issues against active efforts (VEI + direct review). Not yet triaged into a numbered phase — listed here as upcoming work for whoever picks this effort back up._

- [ ] **#777** deploy/installer: reinstall overwrites the LIVE versions/<v> slot in place (non-immutable) -- stomps a running daemon's venv -> resume/start FileNotFoundError [WinError 2]
  - Overwriting a live versioned slot directly violates this effort's `immutable-versioned-runtime` feature.
- [ ] **#776** reconcile/deploy: same-version redeploy (dev296->dev296) reinstalls the slot -- version identity isn't content-addressed (commit:null), so an already-installed build can't be skipped
  - Non-content-addressed version identity directly violates this effort's `self-contained-runtime` feature.
- [ ] **#438** agent-bridge: <repo> update leaves the running daemon on the old version; installer should own the zero-downtime cutover (restart == drain-safe deploy)
  - The installer owning zero-downtime cutover is the same immutable-runtime-deploy scope this effort covers.

### Serving convergence follow-up — 2026-10-12 audit

- [x] Record the source-pinned suite audit and deduplicated issue/owner map in
      [serving-convergence-audit.md](serving-convergence-audit.md). This completes
      the requested capture slice, not implementation or live acceptance.
- [ ] **#6389** _(agent-recommended)_: separate build/selection success from
      serving convergence, including updater current-version skips, bootstrap
      parity, explicit partial/deferred outcomes and retry from a completed
      immutable slot without rebuilding.
- [ ] **#6390** _(agent-recommended)_: preserve Index native deploy failures at
      the installer boundary and reconcile healthy-but-old non-systemd services.
- [ ] Coordinate adapter acceptance with existing #438, #3959, #5558, #2386,
      #6143 and #5769 owners; retain #6373's narrow monitor PID-adapter scope.
      #5956 separately owns the reviewed engine/full-entrypoint applicability
      decision. This amendment neither removes Worktrees' engine exception nor
      pulls all service-specific lifecycle logic into the shared build engine.

## Validation Plan

- [ ] _(agent-recommended)_: exercise the [serving-convergence cases](serving-convergence-audit.md#validation-cases)
      for each applicable adopter. Record build, selected generation, expected
      serving identity/readiness and exact predecessor retirement independently;
      structural guard success or HTTP 200 alone is not live acceptance.
- [ ] Temporary Docker producers satisfy the reservation, identity, actual
      finalize-gate and close-out checks in
      [their scoped validation plan](temporary-container-obligations.md#validation-plan).
      Mocked lifecycle evidence and native/live evidence are reported separately.
- [ ] Phase 0's audit findings are recorded and reviewed (via the effort PR)
      before any engine code is written — wrong assumptions here would ripple
      into every later phase.
- [ ] `tools/sync-installer-engine.py --check` passes in CI for every plugin
      that has adopted the engine, and fails (with a clear diagnostic) when a
      vendored copy is hand-edited or drifts from canonical.
- [ ] `tools/check-install-contract.py` passes for every converted plugin
      *without modification to lower its bar* — verifying it was adapted (per
      Phase 1's critical item) to recognize the contract seams from a
      composed engine/wrapper, not that the seams were quietly dropped from
      enforcement.
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
- [ ] **Net corpus size actually shrinks across the full rollout.** The final
      non-exempt adopter set's total installer line count (their
      `install.ps1`/`.sh` plus the shared `installer-engine.*`) must be
      materially smaller than the pre-conversion baseline — not just
      relocated. Record before/after line counts per converted plugin in the
      Journal. An individual adopter leg may temporarily grow when necessary
      shared safety/robustness fixes land in the engine or wrapper at the same
      time, but that growth must be called out explicitly in the Journal and
      then either (a) offset by later adopter conversions before this effort
      closes, or (b) carried by an explicit follow-up tightening item in the
      Plan/Validation Plan rather than silently treated as satisfied.

## Proposal

**Phase 0 audit findings (2026-09-22):**

| Function / concern | Classification | Notes |
|---|---|---|
| `Invoke-NativeCapture` | (a) | Byte-identical shape across `agent-bridge`/`agent-worktrees`/`agent-logger`: capture scriptblock output, return `{ExitCode; Output}`. |
| `Test-IsSreModuleMismatch` | (a) | One-arg predicate matching "SRE module mismatch". |
| `Test-IsVenvCorruption` | (a) | One-arg predicate matching "failed to locate pyvenv.cfg" / "exit code:\s*106". |
| `Invoke-UvPipInstallResilient` | (a), minor variation | Real source is `agent-bridge`/`agent-logger` (not the smaller pilots) — 3/6/10s retry on SRE mismatch. |
| `Invoke-UvVenvResilient` | (a)/(b) | Same source; retries SRE mismatch + missing pyvenv.cfg. Venv path + uv args are the real parameters. |
| `Ensure-Uv` | (b) | Policy varies per plugin (PATH check, tool dir, index resolution, repair) — needs config params (InstallRoot, ToolDirectory, AcquireIfMissing). |
| `New-SignedVenv` | (b) | Signed-base-Python preference + Authenticode validation + uv fallback is shared; Python pin and validation hooks vary — needs real parameters. |
| Binstub writing | (b) minimal path, (c) service-heavy | No clean shared function existed anywhere; `Write-SimpleBinstub` is genuinely new code, generalized from the simplest existing self-provisioning launcher pattern (`budget-guidance`), cross-checked against `agent-ssh`/`agent-vault`. |
| Deploy manifest writing | (b) | Schema-version-3 shape + `source` block is shared; service-specific fields vary — needs an `AdditionalFields` escape hatch. |
| Scheduled-task functions | (c) | Not shared — agent-dispatch/agent-logger/agent-index/agent-vault each have irreducibly different task lifecycles. Out of scope for the engine entirely (a minimal on-demand CLI plugin needs none). |
| Versioned-runtime slot handling | already a shared primitive | `libs/versioned-runtime/versioned_runtime.py`, synced via the pre-existing `tools/sync-versioned-runtime.py` (opt-in: any plugin with `pyproject.toml` + an installer is auto-discovered as an adopter — no registry edit needed to add a new plugin). The installer engine calls this primitive rather than reimplementing it. |

**`tools/check-install-contract.py`'s pre-existing enforced contract** (verified
by reading the file, not just this table): both install.ps1+install.sh (or
init.ps1+init.sh); literal `uv pip install`; no runtime `PYTHONPATH` into a
package `lib/` dir; schema-version-3 manifest with a `source` block; Windows
launchers must avoid unsigned console-script `.exe` trampolines and invoke
the venv Python with `-m`; byte-identical `scripts/versioned_runtime.py` +
literal `install-contract:v3 versioned-venv` marker; `stamp`/`provision`
actions; byte-identical `Get-SourceKind`/source-kind blocks and v4
self-stage/smoke-seam blocks per language.

**Existing sync-tool templates evaluated:** `tools/sync-versioned-runtime.py`
(closest match — single canonical file pair, explicit/auto-discovered
adopters, byte-identical fan-out, `--check` drift detection) and
`tools/sync-installation-context.py` (better for multiple files per adopter,
not needed here since the engine is exactly two files). Followed the
`sync-versioned-runtime.py` shape for `tools/sync-installer-engine.py`.

**Minimal function set a small new CLI plugin actually needs** (proven live
by `agent-pull-requests`): `Ensure-Uv` -> `New-SignedVenv` ->
`Invoke-UvPipInstallResilient` (package install) -> `Write-SimpleBinstub` ->
`Write-DeployManifest`, plus the pre-existing `versioned_runtime.py` for the
immutable-versioned-runtime slot contract. No scheduled task, no sibling
installs, no service-specific config needed for this class of plugin.

## Journal

### 2026-10-13 — Generic disposable ownership slice merged; Docker integration next

- PR #6391 merged to `dev` as `b78c23aa05c74079f9724cfcfff9c4de14e5f37a`
  at 02:43 Pacific. Its final head
  `1adf10048aebe52aa89ebfc8a412cec4017e4c53` received a fresh Balanced review
  with zero open findings; all 29 visible checks passed or intentionally
  skipped. The exact head and required checks were rechecked immediately
  before its exclusive serial merge slot was used.
- The operator accepted the clean automated assessment under the standard CI
  and serial-coordination gates. The reviewer's final-human-review
  recommendation was retained; no separate human review is claimed.
- The merged generic ledger now covers preallocation fencing, immutable
  identity, exact available-provider closeout, accepted recipient-first
  transfer and recovery, frozen-owner replay, malformed-metadata preservation,
  JSON-safe projections, byte-bounded ingress and isolated bundle exports.
  Source-level fence, accepted-retry and real-finalizer validation items are
  complete; none implies Docker producer integration or live resource proof.
- The final runtime snapshot `334217bbd85ed8f4f09c16a08adfb574059fae35`
  passed 283 contained focused tests, 530 structural guards with 13 intentional
  skips and 16 exact-snapshot native Windows token/legacy-save regressions.
  The final head changed only the capabilities reference and source-validation
  checklist; prior native selections remain scoped to their earlier snapshots.
- Post-merge reconciliation retained recovery refs and advanced the existing
  source worktree onto the verified squash merge. The operator authorized
  continuing the effort in that same workspace while preserving the original
  source and control context. This changes the working venue, not the parent
  objective, completion gate or live-operation authority.
- **Next source stretch:** implement provenance-qualified, non-provisioning
  Docker identity callbacks and the shared disposable producer integration
  under [the reviewed scoped plan](temporary-container-obligations.md).
  Issue #6109, actual Bash/PowerShell/devcontainer integration, Docker
  lifecycle proof and all remaining parent validation gates stay open.
  No resource allocation/removal, runtime rollout or service change occurred.
  The effort remains Active.

### 2026-10-12 — Suite-wide serving-convergence audit captured

- The read-only audit covers 14 public `agent-*` plugins: 12 runtime providers
  and two payload-only N/A entries. Its pinned matrix and seven causal chains
  are linked from [the audit supplement](serving-convergence-audit.md).
- Shared build machinery is substantially uniform; service convergence is not.
  Current manifests/completed slots can coexist with healthy older services.
  This is additional semantic acceptance, not a reversal of completed adopter
  conversions or the September Phase 0 mechanics audit.
- Filed #6389 and #6390 after searching existing coverage; cross-linked #5703,
  #438, #6143, #5769 and #5956. Existing conditional cutover, Dispatch/pool,
  Index role/Job-survival and monitor PID owners remain responsible for their
  slices. #6369/#6374's merged fingerprint correction is recorded separately.
- No installer code, service, runtime slot or live deployment was changed by
  this capture. Implementation and live convergence remain open. The new
  acceptance recommendations are explicitly agent-recommended.

### 2026-10-12 — Generic disposable producer ledger implemented and validated

- Direct regressions confirmed that stored closeout validation already rejected
  missing, false and non-boolean availability before the shared predicate changed.
  The shared exact-subject absence predicate now also requires literal
  `available: true`, retaining the existing caller and persisted-metadata guards.
  The Docker plan now states the technical source/integration boundary rather
  than a transient review status. The earlier JSON-projection snapshot
  `ff3e134c0a19d1403d5b22dad5a371e34a082c35` passed 191 contained focused tests,
  530 structural guards with 13 intentional skips and 11 exact-snapshot native
  Windows projection regressions with verified supervisor cleanup.
- A subsequent review found malformed raw YAML could break JSON claim/status
  projections. The source correction exposes an explicit invalid-metadata
  sentinel without changing the raw ledger or its unsettled ownership.
  The source-only fence and accepted-retry validation checkboxes now match the
  completed regressions; Docker integration and live validation remain open.
  The earlier close-replay snapshot
  `99875b31cfec2257acda3f59658b3f420fbdd902` passed 167 contained focused tests,
  530 structural guards with 13 intentional skips and 32 exact-snapshot native
  Windows replay regressions with verified supervisor cleanup.
- Further review identified pending-close allocation races, per-owner token
  ambiguity and accepted-retry dependence on a source that may already be
  reaped. The operator accepted the review-directed allocation-fence amendment
  after clarifying resource close/handoff scope. The operator asked, verbatim,
  "What's the subject here? Close/handoff of what?" The response distinguished
  resource cleanup responsibility from closing a Copilot session, conversation
  or service. The operator then approved the generic amendment, verbatim,
  "Sounds good. Proceed". This does not authorize physical cleanup or deployment.
  The durable rationale is in the
  [allocation-fence plan section](temporary-container-obligations.md#allocation-fence-rationale).
- The proposed source now requires begin before creation, validates private
  generation-specific fences for bind/completion, retains the epoch against
  stale absence through ABA, and requires separate authoritative quiescence
  evidence for lost-fence recovery. Transfer rejection preserves all records;
  accepted retry repairs the recipient without loading the source or
  resurrecting closed/transferred responsibility. These changes still await
  the normal review gate; Docker integration remains pending. The exact committed
  fence-ABI snapshot `c1550157eae5b3c61a7e258b0dd5020ca21fcfb7` passed 184 native
  Windows tests under Python 3.12.13 and Windows PowerShell 5.1. Its archive
  SHA-256 was `9ce88882b58009b98ad2a0c25a31e0b457bdfc6294a485d73a8decccf417c536`;
  the bounded supervisor verified successful cleanup. This supersedes the
  earlier 126-test run as fence-ABI evidence, not as live Docker proof.
- The first ownership implementation slice adds a provider-neutral
  `resource-producer` ABI to the existing ledger: durable pending reservation,
  immutable identity binding, exact-subject provider-verified close-out and
  local, explicitly accepted ownership transfer. Generic release, repair,
  stale saves and finalization cannot discharge these obligations, including
  malformed metadata and duplicate-reference corruption.
- Independent review found two preservation gaps in damaged ledger records;
  both were corrected with regression coverage before publication. The
  supported contained selections passed 611 tests; the full structural guard
  selection passed 528 tests with 13 intentional skips. Module-size,
  install-contract, lint and changefile checks passed without relaxed limits.
- PR #6391's first current-head review found two additional terminal-owner
  gaps: archived tombstones could receive reservations and accepted transfers.
  Both now use one shared frozen-owner status set, including finalizing and
  orphaned records. Regression cases prove rejected acceptance changes neither
  owner record nor bundle registry and retains the source obligation.
  The post-fix contained producer/handoff selection passed 113 tests.
- The next required CI run exposed a one-line module-ceiling overrun only in
  the synthetic merge: concurrent upstream guidance-worker liveness protection
  increased the finalizer relative to the local PR head. Consolidating the
  repeated producer checks into the existing obligation helper reduced the
  local finalizer to 2,033 lines without widening its 2,038-line ceiling or
  changing those gates. The combined contained producer, handoff and actual
  finalization selections passed 124 tests.
- An exact committed snapshot of
  `02d8773bf39647bf92386e30a1e6dd02781dedb0` passed 126 targeted native Windows
  tests under Python 3.12 and Windows PowerShell 5.1, including the producer,
  protected claim writes, transitive finalization and lazy dispatch selections.
  The proof archive's byte count and SHA-256 were verified before extraction;
  the bounded supervisor reported successful cleanup. An initial proof
  transport stalled before tests began; replacing its PowerShell Console
  ingestion with a byte-verified native Python reader resolved that harness
  issue without changing the implementation or installing dependencies.
- This is source validation, not released/live proof. Docker identity/provider
  callbacks, clean-room and devcontainer producer integration, actual resource
  allocation/removal and their scoped validation gates remain open under
  issue #6109. Fleet borrowing remains unchanged. The generic API does not
  allocate resources; integrated producers must not be enabled until every
  ledger-writing runtime understands their metadata.
- The effort remains Active. Publish this generic slice for review, then
  implement the exact Docker identity/provider and producer integrations.

### 2026-10-12 — Stamp/permission/provenance slice and ownership proposal merged

- PR #6116 merged to `dev` as `cbb7256268e6fb3943b88fffcb0b8fd12da2b5ba`.
  The final source head `bccca50e2aae793fbc546fed79e7da168b48804f` received a
  fresh Balanced review with zero open findings and all 30 visible source
  checks completed successfully or intentionally skipped. No review-gate
  bypass was applied.
- The practical bounded POSIX repair, safe installer stamp/publication paths,
  activation marker mask and committed-code candidate transformation are now
  reviewed source. Final contained Index evidence: 985 passed, 25 skips;
  materializer/provenance: 103 passed, 2 native junction skips; exact-head
  native Windows PowerShell 5.1/Python 3.12 combined proof: 93 passed,
  75 platform skips. Native proof does not substitute for POSIX mode tests
  or released/live behavior.
- Post-merge reconciliation required refreshing the canonical PR status
  before the merged-boundary proof could exclude squash-merged commits.
  Reconciliation then preserved the backup and moved this same worktree onto
  the merge, without a manual reset, new serial worktree or finalization.
- The temporary Docker resource proposal's plan-review item is closed.
  **Next execution slice:** implement its generic producer transaction,
  exact Docker identity/provider and integrated clean-room/devcontainer
  allocation/close-out guarantees. No automatic disposable claiming exists
  yet, no container was allocated here, and its entire validation plan remains
  open.
- The effort remains Active. Index release/live verification and remaining
  adopters, permanent exception enforcement, opt-in retirement, linked bug
  sweep and aggregate validation remain unresolved. This merge does not
  satisfy those gates or authorize deployment/deletion.

### 2026-10-12 — Include practical session-start permission repair

- Operator clarification, verbatim:
  > session-start should also do repair if practical
- The actual compatibility issue is distinct from the rejected claim that
  `mkdir(..., 0755)` gains write bits from a umask. Legacy installer shell
  redirections request `0666`, while `chmod +x` can preserve inherited shared
  write bits. These artifacts can therefore fail the stricter stamp admission
  under a common `0002` umask.
- The scope now includes preventing unsafe new installer artifact modes and
  practical repair during dependency-light session stamping. This does not
  authorize session-start provisioning, service/engine startup or broad
  filesystem permission normalization.
- **Agent-recommended design constraints, not implemented:** bind repair to
  verified creator ownership and exact no-follow identities; retain refusal
  for foreign owners, links, multiple-link files and unsafe surrounding
  namespaces. Do not infer trust in previously writable marker contents:
  canonical stamp/launcher bytes and supersession decisions need explicit
  treatment. Permissions changed after admission must still be rechecked.
  State any installer-created ancestor that cannot be repaired safely rather
  than silently broadening the repair boundary.
- The migration protocol, its review and real installer-to-hook/adversarial
  regressions remain pending. No live artifact permission was changed.

#### Candidate protocol and execution evidence

- The bounded candidate repairs only creator-owned, group-writable (not
  world-writable or special-mode) runtime roots and exact legacy launcher
  destination directories. It pins every ancestor and rechecks identities
  before reducing permissions. Arbitrary ancestors remain strict.
- Group-writable marker/launcher files are regenerated atomically only after
  their contents match trusted payload/version or canonical launcher bytes;
  a retained writer descriptor cannot modify the replacement. Unknown
  contents, writable deployed-version state, links, multiple links and
  foreign ownership still refuse. Explicit operator recovery is required
  where migration cannot safely infer intent. Namespaced cells do not inherit
  this legacy repair policy.
- Installer stamp publication uses the same guarded file writer. Its
  publication lock is prepared and validated before Bash adopts descriptor 9,
  then checked against that descriptor's identity; neither side replaces or
  truncates the lock inode. New lock creation is bounded by a private umask
  that is restored before callbacks.
- Actual-function installer-under-`0002`/`0077` to actual-hook tests cover Bash
  and Linux PowerShell, known legacy migration and idempotence, retained
  writable descriptors, ambiguous contents and supersession state, link
  refusal before installer lock truncation, and directory replacement before
  chmod. Relevant safety/publication/acquisition selection: 90 passed,
  14 native-Windows skips. Full contained Index suite: 981 passed, 25 skipped
  across three sub-suites (304/8, 321/2, 356/15). Coupled provenance and
  customization scanner: 170 passed, 2 native-Windows skips. Review and native
  validation remain pending. No production permission or service was changed.
- Follow-up candidate regressions exercise the actual activation writer, not
  only `do_stamp`: publication callbacks mask shared-write bits additively,
  preserving stricter caller masks and restoring the caller's environment.
  `current-version` and `last-known-good` remain strictly admitted after real
  marker-only activation under `0002` and `0077` in both hook shells.
- Candidate materialization now runs the archived commit's transformation
  implementation and helpers in an isolated subprocess with site loading
  disabled; host working-tree transformation edits cannot affect committed
  candidate bytes. A destination that appears during preparation is refused,
  not removed. Full contained Index suite after this follow-up: 985 passed,
  25 skipped (304/8, 321/2, 360/15). Full materializer and provenance suites:
  103 passed, 2 native-Windows junction skips. Native and review gates remain
  pending. All child processes remain bounded and headless on Windows.

### 2026-10-10 — Temporary Docker accountability proposal; implementation gated

- Operator request:
  > We clearly are missing "claims" for containers. For temporary containers, we need to make worktrees responsible for them before they may finalize.
- Coordination #6109 owns this validation-infrastructure slice. Source tracing
  confirmed that active container claims already block finalize, including its
  final locked ownership check, but clean-room and devcontainer allocations do
  not automatically create those claims. The existing `run` pending reservation
  is useful precedent, not a Docker identity or close-out implementation.
- Recorded the [scoped proposal](temporary-container-obligations.md) in this
  existing effort rather than creating a parallel plan or ownership store.
  The new generic producer transaction, temporary-resource settlement policy
  and raw-Docker identity contract require plan review before implementation.
  Proposed placements and design choices are explicitly agent-recommended,
  not recorded as approved decisions.
- No runtime, wrapper, provider, test runner, plugin version or pending
  changefile changed. No Docker resource, production system or live claim
  ledger was touched. Implementation and all lifecycle proofs remain pending.
  Parent integration owns plan publication/review and subsequent implementation;
  this effort remains Active and Index live proof remains unresolved.

### 2026-10-10 — Explicit session-start policy; Index-only implementation slice

- Operator decision (generic/public-safe verbatim):
  > sessionStart hooks only ever do quick payload-and-binstub stamps; they never deploy via install scripts or start services. Instead, we defer to first attempted binstub invocation by an agent, or if the installer is directly run via `<repo> update`.
- This refines the existing reviewed self-contained/lazy installer intent below
  a newly explicit activation boundary; it does not replace the approved
  byte-vendoring design or authorize a fleet-wide lifecycle rewrite. Index
  hooks may publish metadata/binstubs, but never install/ensure/provision,
  acquire uv/packages, create venvs, register autostart or start services/engines.
  Runtime provisioning remains behind repository opt-in, installation governance
  and explicit role/setup intent. No implicit host choice is allowed.
- #6082 retains the corrected clean-room contract; #5873 retains the real release
  and live-verification gates. This bounded candidate is not a shipped release.
  The Active effort and adopter checkbox remain open. Parent integration owns
  publication/review/merge/deployment; no next adopter starts here.
- Candidate hook execution passed for Bash and PowerShell 7 on Linux with
  temporary homes and inactive/corpus-only/client/host configurations.
  Focused hook/runtime/scenario selection: 97 passed, 1 skipped. Hardened,
  network-disconnected full Index suite: 760 passed, 107 skipped
  (284/19, 269/48, 207/40). Final parser/materialization selection: 96 passed.
  Shell/PowerShell parsers, F/E9 lint, install contract (13 plugins), and
  installer-engine byte-sync (8 adopters) passed. Native Windows, PS5.1 and
  macOS are not claimed.
- Built a public standalone candidate using normal release materialization and
  preview tools, with source payload version `0.1.0-dev210` (preview's
  hypothetical pending-changefile version `0.1.1-dev1`). This is explicitly
  **not** released Index `0.10.15-dev1` or a production update; version fields
  were not hand-bumped. Installed installer/manifest bytes and both vendored
  engine files matched candidate/canonical bytes in the fresh box.
- Fresh candidate witness reached 17 passes / 1 failure, phase 5.
  Inactive admission, metadata-only startup, explicit role refusal, policy/CAS
  activation, host/client role reads and genuine incomplete-slot recovery
  passed. First-use `setup --single --yes` reached the real runtime gate and
  cell installer, produced a genuine completed importable host venv, and
  reported its exact candidate CLI version directly from that venv.
- **Genuine product STOP:** payload-local `--version` in the active namespaced
  cell invokes `launch-validate --command --version`; argparse rejects it with
  `argument --command: expected one argument`, then the gate exits 126.
  No alternate launcher, hand-install, synthetic completion or temporary
  product fix was used. Later scenario health/MCP/no-role assertions remain
  unexecuted; the full clean-room gate is FAIL, not partial completion.
  A separate read-only diagnostic observed `/health` `ok`, promoted/non-passive
  at candidate `0.1.0-dev210+host`; this is not a scenario pass.
- Fixture-only fresh attempts before that stop recorded 1/1 (missing
  marketplace owner), 6/1 (status resolver telemetry), 13/1 (missing real
  snapshot provenance), 13/1 (incomplete slot is correctly broken, not absent),
  and 15/1 (setup logs contain both provisioning and final role JSON).
  Corrected each witness assumption in source without dropping its stage.
  Every failed box/report remains retained; borrowed OAuth cleanup requires
  approval. No host runtime/socket/credential/private-code mount, privilege,
  GPU/model, production service, scheduler or system-setting change occurred.
- Final combined bounded hook/runtime/parser/materialization selection,
  including executable tripwires forbidding uv/pip/service/task commands:
  **183 passed, 1 skipped**. Full-suite result above predates only those
  additional tripwires and final scenario-parser corrections, not product edits.
  Candidate product bytes were unchanged through the final fresh run. A final
  metadata-only hardening suppresses Python bytecode writes when importing the
  stdlib-only version comparator; its actual hook selection passed 17 cases.
  That final hardening was not rerun in a new clean room after the product STOP.

### 2026-10-10 — Released Index clean-room witness corrected; live gate blocked

- Coordination #6082 owns this bounded scenario correction; Index validation
  #5873 remains open. No installer, runtime authorization, service architecture,
  version or plugin payload changed. Clean-room tooling/tests/docs and this
  journal do not require a plugin changefile.
- Updated the existing `agent-index-solo` witness for canonical repository
  opt-in, explicit installation-mode/activation governance, payload-local
  commands, real owned incomplete venv recovery and strict role-profile
  boundaries. Documented each obsolete expectation's replacement rather than
  silently dropping it. Actual venv/import/version and installation-owned
  HTTP health/status are required; synthetic completion is never deployment.
- Both fresh attempts used released commit
  `cb9d2c4d11c1ca947464ac522ccd1c4af24a205e` (promotion #6075),
  Index `0.10.15-dev1`, not a mounted development payload. The first stopped
  with 1 pass / 1 fail because directory marketplace installation is live,
  not copied into `installed-plugins`. Corrected that scenario path assumption;
  retained the failed box and used another fresh named container.
- The corrected fresh run stopped at the first genuine contract obstacle:
  5 passes / 1 failure, `index-config`, phase 2. Installed release and both
  vendored engine files matched release/canonical bytes. Non-opted-in status
  was `inactive` and non-mutating. After the sole minimal session hook witness,
  opted-in status returned `setup_required` with runtime state `stamped`,
  rather than the documented non-mutating `absent`.
- Released `hooks.json` still invokes `install.sh ensure`. Its legacy binstub
  publication path writes `payload-dir` and resolver copies under the legacy
  root; the fresh box contained those files and a publication lock. This
  contradicts the documented non-stamping session contract. No expectation was
  weakened and no installer/runtime workaround or dependency hand-install
  followed the failure.
- No runtime venv or API service was installed; real CLI runtime version,
  health, cell activation, partial recovery and later role/MCP assertions
  remain unexecuted. Engine directory/listener, model cache and Index scheduler
  artifacts were absent. No production/canary, GPU/model, warm engine or host
  scheduler mutation occurred. Retained disposable boxes require explicit
  cleanup authorization; borrowed credentials were not printed or committed.
- Focused scenario/parser/runner-auth guards passed initially (12 cases);
  the final selection passed 13 cases, including a regression rejecting
  hook-created `stamped` readiness. Shell parsing, focused Python F/E9 lint and
  staged whitespace checks passed. All pytest runs use bounded admission
  120 / timeout 600 seconds.
  The corrected witness is prepared locally, not reviewed or merged, and its
  real installer/API proof is blocked. Publication, review, deployment and
  disposition of the contract mismatch remain parent-owned; no next adopter
  starts and neither the Index checkbox nor the Active effort is closed.

### 2026-10-09 — Index installer PR merged; rollout paused

- PR #5885 squash-merged to `dev` as
  `3b1cbe29706cd8494157b06a5873d8dbb25895b9` after current-head review had
  zero Medium/High findings and source CI plus required gates passed.
  The retained Low metrics finding was addressed in the live description,
  not bypassed as an unresolved implementation defect.
- The final acquisition head passed native Windows installer selection
  (74 passed, 42 skipped); final Index host/disconnected suites passed
  (848/11 and 756/103). Actual SAC, PowerShell 5.1, native macOS and live
  first-use provisioning remain disclosed limitations.
- Source CI exposed an independent wrapped-I/O contention regression from
  #5893. The narrow #5911 correction landed in this PR with its own
  agent-worktrees changefile; the exact guard lane passed 333 cases with
  10 skips, preserving the original real deadline lower-bound assertion.
  Native classification passed 13 cases. The permanent shared-engine
  exception is unchanged.
- Publication recovery retained backup refs and used only explicitly
  approved local operations: first a backed-up fast-forward recovery, then
  one private-branch merge to reconcile newer trunk edits when legacy
  branch-record replay proof refused. Final trunk integration was still
  squash-only; no force push or tracking-file edit was used. The tooling
  defect remains tracked as #5915.
- The managed worktree reconciled onto the merge and keeps its effort binding
  open. The operator requested pausing after this installer change, so no
  subsequent adopter starts and no new live update, canary activation or
  warm-engine restart is authorized. Release/deployment verification for
  Index remains open under #5873; the parent effort is Active, not Done.

### 2026-10-09 — Index installation-wide uv acquisition serialized

- Scoped follow-up to PR #5885's shared tool bootstrap race. CLI and durable
  engine use different build targets but the same installation tool directory;
  validation, partial executable removal and bootstrap must therefore share an
  acquisition guard, not their distinct build locks.
- Added a private Index adapter around the unchanged canonical helpers.
  Windows `Uv` mutex scope and POSIX advisory fd7 key to the installation root
  and use bounded 180-second admission. Acquisition releases before build or
  publication, and never owns those locks while waiting or invoking bootstrap.
  Cached-engine reuse stays cheap and does not acquire/bootstrap uv.
- Real concurrent fixture runtime/engine callers start from an absent tool.
  Exactly one bootstrap writer publishes a complete executable; both consumers
  validate and use it. Independent build/publication probes prove lock order
  and distinct fd7/8/9 ownership. Both POSIX flock and stdlib fcntl paths run.
  No real download, package/model initialization or service operation is used.
- Existing rejected-PATH/pip fallback, readiness barrier, snapshot authorization,
  per-version build locks, publication guard and Global-to-Local diagnostics are
  retained. Canonical bytes and existing changefiles are unchanged; no other
  plugin or recovery history was edited.
- Bounded validation: focused installer/acquisition selection 116 passed;
  disconnected full Index suite 756 passed / 103 skipped; full host suite
  848 passed / 11 skipped. The initial acquisition fixture omitted its optional
  server definition and failed explicitly; after completing that fixture,
  the full matrix passed. One admission deferred with 75 and was retried
  under the authorized wait policy, without bypass.
  Relevant engine-sync/materialization/install-contract tooling: 107 passed.
  Guards, version/changefile checks, F/E9 lint and both installer parsers passed.
- Measurements: shell 2132 -> 2142 (+10), PowerShell 2656 -> 2671 (+15),
  wrappers 4788 -> 4813, canonical pair remains 881, aggregate 5669 -> 5694.
  Native acquisition revalidation remains integration-owner work; preceding
  native results and the independently resolved upstream guard lane are not
  claimed as validation of this follow-up. Effort/adopter completion stays open.

### 2026-10-09 — Index cross-version publication and supported snapshot recovery

- Continued only PR #5885 from published head
  `b0e9125614c62c9096c0a66710158d067b16c041`, preserving its worker-readiness
  barrier and all prior admission, mutex fallback, interpreter, pip and engine
  policy changes. Canonical engine and existing changefile are unchanged.
- POSIX publication now owns installation-wide fd9 independently from
  version/target build fd8. Freshness, invalid-marker preparation, activation,
  stamp/payload/launcher writes and manifests share the publication guard.
  Nested calls reuse their scope, build remains outermost, and heavy package
  work does not hold publication. Both descriptors retain the same OS advisory
  locking, bounded admission and explicit-unlock semantics.
- Missing authoring origins remain source provenance. Runtime-gate and
  installer mandatory legacy probes select the validated owning snapshot when
  the original no longer supplies its declaration. Complete declaration and
  namespace/legacy governance validation are retained rather than bypassed.
- PowerShell package branches now use only the accepted UvCommand. A failed
  Ensure-Uv cannot rediscover rejected PATH uv through a literal helper fallback
  or Get-Command package branch; all three runtimes use Python/pip instead.
- Real process fixtures interleave an older stamp or invalid-marker repair
  with newer activation, prove publication blocks while another version's
  build remains independent, and reject stale direct writers. Supported
  runtime-gate setup/provision fixtures reach snapshot UV only after the real
  declaration probe; a denied governance decision blocks before provisioning.
  A still-discoverable broken executable is rejected by real Ensure-Uv, and
  all nine CLI/server/engine package calls use pip without invoking it again.
- Bounded validation: new contract selection 7 passed; combined installer/
  activation matrix 113 passed; disconnected full suite 745 passed / 102
  skipped; full host suite 836 passed / 11 skipped; relevant tooling 107
  passed. Guards, F/E9 lint and shell/PowerShell parsing passed.
- New measurements: shell 2132, PowerShell 2656, wrappers 4788, unchanged
  canonical pair 881, aggregate 5669 (+73 against the correctly published
  2064/2651/4715/5596 table). Runtime-gate grows 941 -> 942 lines separately.
  The effort's aggregate tightening gate remains open.
- Native 70 passed / 36 skipped applies to the prior published head after its
  readiness barrier; neither that result nor its earlier handshake retry is
  attributed to this follow-up. Native revalidation and publication remain
  integration-owner work. No live mutation or next adopter was performed.

### 2026-10-09 — Index POSIX advisory-lock ownership corrected

- Scoped follow-up to the new POSIX helper at local head
  `d84c33a7916ae60aaa47682ee470922c85a4e492`, before publication. A PID symlink
  compare/readlink/delete sequence cannot atomically reclaim a stale owner:
  another caller may replace it between the final comparison and deletion.
  Descriptor close alone also fails when descendants retain the same open-file
  description.
- Replaced PID reclamation with real OS advisory locking. No-flock/macOS
  admission uses bootstrap Python's stdlib fcntl on inherited fd8, preserving
  its open-file description in the shell. Cleanup explicitly unlocks before
  close in both normal flock and fcntl paths, preserves callback status, and
  surfaces acquisition/unlock failures. Admission remains bounded to 180
  seconds; no network or third-party prerequisites were added.
- Real process regressions keep a harmless descendant alive with fd8 after
  callback return, then prove another caller immediately acquires the same
  target. Both paths also time out against a real held flock, preserve stale
  PID evidence unchanged and retain one stable lock inode across reuse.
  Children finish through a fixture signal, without termination.
- Windows code, parent policy changes, canonical engine and existing
  changefile are untouched. Measurements: shell 2030 -> 2064 (+34),
  PowerShell remains 2651, wrappers 4715, canonical pair 881, aggregate 5596.
  Native macOS and remaining native revalidation are not claimed; integration,
  publication and the overall effort remain coordinator-owned/open.
- Bounded validation: four OS-backed edge regressions passed, combined
  installer/admission selection 106 passed, full host suite 829 passed /
  11 skipped, disconnected full suite 741 passed / 99 skipped. Guards,
  shell parsing and F/E9 lint passed. One admission attempt deferred with 75;
  the authorized bounded retry completed without bypassing containment.

### 2026-10-09 — Index direct-build admission and user-mode mutex fallback

- Continued only PR #5885 from published head
  `baf17d5f35eac433fde38aee0f4803a374776988`. Preserved its narrowed optional
  repair catch, unpinned interpreter selection, Windows Python/pip fallback,
  durable-engine uv policy and the existing patch changefile. Canonical engine
  bytes remain unchanged.
- Added target-venv/version-scoped build admission before cleanup and repair,
  held across package installation, health and activation/publication. The
  optional server sibling stays inside the CLI build guard; durable engine
  provisioning uses its independent target guard. Returns and failures release
  in `finally`, and different versions remain independently admissible.
  POSIX mirrors the build scope with bounded flock/PID admission and subshell
  cleanup. The existing Dispatch 180-second build-admission window is reused.
- Lock order remains build then publication. Stamp's snapshot lock is released
  before publication, and no publication holder acquires a build lock. Runtime
  freshness is rechecked after build admission and again before publication.
- Global Windows mutex creation catches only UnauthorizedAccessException and
  falls back to Local with an explicit cross-session-degradation warning.
  Other creation errors still propagate; normalized key/scope identity and
  reentrancy are preserved.
- Real two-process direct-install fixtures prove a second same-version caller
  cannot clean or mutate the first build, while another version remains
  admissible. Failure and supersession cases prove the peer proceeds before the
  first process exits, rather than relying on process death to release the
  guard. Timeout probes and POSIX failure/PID cleanup cover bounded admission.
  Simulated denied Global construction proves Local reentry without elevation;
  normal Global and unrelated-error paths are separate regressions.
- Published-head measurements 1975/2624/4599/5480 were correct and are not
  retroactively changed. This follow-up measures shell 2030 and PowerShell 2651,
  wrappers 4681, plus unchanged engine pair 881 = 5562 aggregate (+82).
  This remains disclosed temporary safety growth, not effort completion.
- Validation remains bounded to admission 120 seconds / timeout 600 seconds.
  Focused installer/admission matrix: 104 passed; full host suite:
  827 passed / 11 skipped; disconnected full suite: 739 passed / 99 skipped;
  engine-sync/materialization/install-contract tooling: 107 passed.
  Contract, sync, versions, module size, docs, changefile, F/E9 lint and
  shell/PowerShell parsing passed without a flake exception.
  Required native revalidation belongs to the integration owner; native
  64 passed / 32 skipped applies only to the preceding published head.
  No publication, live mutation, next adopter or child agent was authorized.

### 2026-10-09 — Index publication and health boundaries hardened

- Continued only the Index adopter from PR #5885's published head
  `84b5889932b3fdf8ed4f76fc8ffeec47afd9004d`. Preserved unpinned uv selection,
  Windows Python/pip fallback when acquisition fails, and the durable engine's
  explicit uv-first policy; CLI/server remain signed-first. The existing
  patch changefile is unchanged; canonical engine bytes are unchanged.
- Reused one installation-keyed publication mutex across stamp, runtime marker
  cleanup, activation, payload/launcher writes and manifests. Runtime builds
  release preparation locks before provisioning and recheck freshness before
  the complete publication transaction. Direct activation/launcher callers
  enter the same reentrant guard. Snapshot and publication locks remain
  sequential, with no publication-to-snapshot acquisition. Windows uses the
  global namespace; path aliases with trailing separators share one identity.
- A deterministic two-process fixture brings newer activation to the mutex
  while an older stamp is inside its freshness check. The activation must
  report blocked, then publish the newest current-version, payload, launcher
  and manifest after stamp releases. Separate cases prove current/stamped
  freshness prevents all older runtime writes and direct launcher repair.
- Optional server repair catches only its venv repair/probe boundary, reports
  the literal exception and restores ErrorActionPreference in `finally`.
  Actual repair-helper tests inject filesystem and probe exceptions and prove
  warning fallback plus primary continuation.
- POSIX CLI/server/engine builds now share an isolated interpreter-prefix
  health probe and local repair adapter around the unchanged engine helper.
  Real Python venv cases cover healthy reuse, corrupt executables, base
  interpreter wrappers, wrong prefixes and failed probes. Fake package-build
  interpreters emit truthful physical prefixes instead of bypassing health.
  No real models, services or scheduler operations are used.
- Counts relative to the published head: shell 1958 -> 1975 (+17),
  PowerShell 2579 -> 2624 (+45), wrappers 4537 -> 4599 (+62); canonical pair
  remains 881 lines, combined corpus 5418 -> 5480 (+62). This is disclosed
  temporary safety growth, not satisfaction of the effort's aggregate
  shrinkage gate; the rollout must offset it before that gate closes.
- Bounded validation (admission 120 seconds, timeout 600 seconds): focused
  installer/stamp/activation matrix 96 passed; disconnected full plugin suite
  737 passed / 93 skipped; full host suite 819 passed / 11 skipped.
  Relevant engine-sync/materialization/install-contract tooling: 107 passed.
  Contract, engine/library sync, version, module-size, docs, changefile,
  F/E9 lint and both platform parsers passed without a flake exception.
- Native Windows validation previously reported 56 passed / 26 skipped at
  the published head, not this follow-up. The new global mutex/repair paths
  need native revalidation; PS5.1, SAC and macOS remain unclaimed gaps.
  Publication, review, merge, release and live verification remain owned by
  the integration coordinator. The adopter and overall effort remain open.

### 2026-10-08 — Index adopter implementation prepared for integration

- Public coordination: #5873. This Phase 2+ slice closes the reviewed
  self-contained/immutable-runtime intent without changing service architecture.
  Both Index wrappers use canonical engine references on `dev`; adopter
  registration materializes byte-identical payload-local engines at release.
- Shared acquisition, capture, venv/package retries and manifests cover the
  versioned CLI, optional host-server sibling and independent durable engine.
  No canonical engine change or new shared parameter was necessary.
  Index retains CLI/server signed-Python preference, the durable engine's uv
  interpreter selection, package ordering,
  backend/CUDA/Torch feeds, engine.env and every activation/task/daemon lifecycle.
  Signed-result usability, not exit code alone, gates recovery; an invalid
  existing interpreter cannot bypass rebuilding.
- PowerShell stamps materialize both engines and all three local libraries,
  rewrite dependency references and reuse the existing ordinal payload hash.
  Unchanged content reuses an immutable snapshot; plugin, engine, library and
  case-distinct edits change its identity. Sequential snapshot/publication
  locks recheck the same-version candidate before atomic marker and launcher
  publication, addressing the stamp marker race tracked in #5271.
- Installer counts: shell 2004 -> 1958 (-46), PowerShell 2539 -> 2579 (+40);
  wrappers 4543 -> 4537 (-6). Canonical engine remains 403 shell + 478
  PowerShell lines; combined corpus 5424 -> 5418 (-6). PowerShell growth
  supplies self-contained immutable snapshots and shared signed-result health
  policy rather than a duplicated service-specific engine.
- Snapshot-origin provisioning preserves the durable snapshot pointer even
  after the authoring source disappears; source metadata independently retains
  the original payload provenance rather than advertising staging paths.
- Contained fixtures exercise both CLI and durable-engine builds without real
  packages/models or daemon contact. Available PowerShell coverage includes
  signed-result simulation, snapshot reuse/content identity, unavailable
  authoring sources and stale-candidate refusal. POSIX optional-server package
  failure now warns without escaping the documented fallback via errexit.
- The host full suite exposed repeated copied-interpreter fixture storage
  growth: each POSIX venv copied three approximately 29 MB interpreter leaves.
  Runtime-gate fixtures now use ordinary POSIX venv symlinks, retaining copies
  on Windows. The same 2 GiB sub-suite budget then passed; no limit was raised.
- Validation passed under admission 120 seconds / timeout 600 seconds:
  focused installer selection 80 passed; full disconnected devcontainer suite
  731 passed / 85 skipped; full host suite including available PowerShell
  coverage 805 passed / 11 skipped; engine-sync/materialization/install-contract
  tooling 107 passed. Install contract, engine sync, vendored-library sync,
  versions, module size, docs, changefile, F/E9 lint and both installer parsers
  passed. No accepted-flake exception was required.
- Coordinator compatibility check retained unpinned uv interpreter selection
  across all three venvs and the existing Windows Python/pip fallback when uv
  acquisition fails. The durable engine does not acquire the CLI/server's
  signed-rebuild policy. Final focused selection passed 82 cases; final full
  host suite passed 807 cases with 11 skips.
- The operator requested finishing this Index installer change, then pausing.
  No further adopter slice will start at this boundary; the parent effort
  remains Active with its remaining roster and validation gates intact.
- Review, merge, release and live verification remain coordinator-owned and
  outstanding. Native Windows/PowerShell 5.1, SAC and macOS behavior, and real
  package provisioning, are not claimed. The adopter checkbox remains unchecked
  and the overall effort remains Active.

### 2026-10-08 — Dispatch adopter merged and deployment verified

- PR #5832 merged as `3c153fae456d3ca70a18dc9c19f360d9b4fd3980` after
  source CI and required gates passed and the final owner-authored `Comment`
  review reported zero open findings. Four review rounds closed the snapshot
  leak, existing-invalid signed rebuild, case-distinct hash and same-version
  stale publication defects; no remaining finding was bypassed.
- Final native Windows PowerShell 7 installer/snapshot/stamp/activation
  selection passed (76 passed, 12 skipped). Actual SAC enforcement,
  PowerShell 5.1, native macOS and live first-use provisioning remain
  separate validation limitations, not claimed coverage.
- Post-merge reconciliation initially hit a squash-replay conflict and
  auto-aborted without changing the branch. Refreshing authoritative merged
  PR metadata with `pr-status` allowed `pr-complete` to reconcile past the
  verified squash merge while preserving its backup ref and active effort.
- Release-driving `dev` CI was superseded twice by newer contributions,
  rather than failing tests. Release promotion and normal deployed-runtime
  verification were then completed: promotion PR #5866 included the adopter,
  and the explicitly approved normal update installed `0.14.0-dev1`.
- Both payload-local engine files match canonical bytes and the release
  contains the local snapshot candidate guard. The active routed coordinator
  reports `status: ok`, the new version and `draining: false`; its companion
  supervisor is active/running. No forced update or unadopted runtime enablement
  was requested.
- The pre-update legacy unit was repeatedly rejecting a duplicate coordinator,
  while the routed coordinator was healthy. This already-tracked condition
  matches #5551; it was not treated as an outage or remedied with process kills
  or a forced restart.
- This adopter is complete. The effort remains Active: `agent-index`,
  `agent-containers`, `agent-mcp`, `agent-machines`, permanent-exception
  enforcement, opt-in retirement and aggregate validation remain open.

### 2026-10-08 — `agent-dispatch` adopter prepared for integration

- Public coordination: #5826. This is a vision-closing Phase 2+ slice of the
  reviewed installer effort, not a new staging or daemon architecture.
- Both wrappers source canonical installer-engine files on `dev`; the adopter
  registry makes release/preview tooling materialize standalone copies.
  Shared uv acquisition, native capture, transient venv/package retries and
  deploy-manifest writing replace local mechanics. No canonical engine change
  was needed.
- Dispatch retains artifact scrubbing, dependency order, optional MCP-extra
  fallback, signed-Python preference/recovery, launchers, supervisor/coordinator
  lifecycle, update markers, activation/publication locks and ZDD cutover.
  Captured package result codes survive post-install cleanup. Signed-Python
  results are accepted only after venv validation, including a usable result
  produced with a nonzero exit code.
- PowerShell snapshots materialize both engine files and all six declared
  libraries before publication. Local-checkout `stamp` uses content-addressed
  standalone snapshots so unchanged stamps reuse one immutable tree, while
  source, engine or library edits create a new identity. Direct local builds
  and the POSIX owning-payload stamp stay unchanged.
- Installer line counts: `install.sh` 2295 -> 2250 (-45), `install.ps1`
  3993 -> 4015 (+22), combined wrappers 6288 -> 6265 (-23).
  Engine remains 403 shell + 478 PowerShell lines; combined corpus
  7169 -> 7146 (-23). Required snapshot materialization and signed-result
  health validation account for the PowerShell growth. These were the initial
  implementation counts; review-fix measurements supersede them below.
- Validation:
  - `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run_tests_in_devcontainer.py agent-dispatch --admission-wait 120 -k 'install or installer'`:
    120 passed, 74 skipped, 4362 deselected.
  - `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run_tests_in_devcontainer.py agent-dispatch --admission-wait 120`:
    all seven sub-suites passed; 4470 passed, 86 skipped. Dependency preparation
    precedes the network-disconnected test pass.
  - `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-dispatch --admission-wait 120 -k 'install or installer'`:
    189 passed, 5 skipped, 4362 deselected, including available PowerShell
    execution. Initial fixture failures were corrected: the simulated Python
    launcher must set its exit code, and snapshot assertions inspect operative
    source lines rather than inert rewrite strings.
  - `test-supervisor --admission-timeout 120 --timeout 600 -- .test-venvs/linux/agent-dispatch/bin/python -m pytest -q tools/test_sync_installer_engine.py tools/test_materialize_main.py tools/test_check_install_contract.py tools/test_install_contract_guard.py tools/test_preview_release.py`:
    120 passed.
  - Shell syntax, PowerShell parsing, install-contract, engine-sync and touched
    Python F/E9 lint checks passed.
- The requested 1200-second outer test bound is unsupported by the installed
  supervisor (`timeout must be 60-600 seconds`); all runs used its supported,
  tighter 600-second bound.
- Native Windows SAC/signature enforcement and scheduled-task behavior, native
  macOS behavior, and a real first-use network/package install remain untested.
  Safe standalone proof uses fixture uv after the authoring tree becomes
  unavailable; it is not a deployed-service verification.
- Issues #5820, #5356, #5066 and #5529 remain separate tracked staging,
  live-slot, marker and lock work. Open PR #5567 touches adjacent POSIX service
  unit configuration, which this slice does not edit. Neither their fixes nor
  a staging-lifecycle redesign is included here.
- Implementation is local and prepared for coordinator inspection. Review,
  merge, release and deployed verification remain outstanding; the adopter
  checkbox and overall effort stay open.

### 2026-10-08 — Dispatch review fixes and native validation

- The first reviewed head passed a native Windows PowerShell 7 focused
  installer/snapshot/artifact-scrub selection: 42 passed, 10 skipped (eight
  POSIX cases and two opt-in mutex races). Actual SAC enforcement, scheduler
  registration and PowerShell 5.1 remain unproved; signatures are simulated.
- Fixed both Medium review findings: local stamps now derive their snapshot
  identity from the complete materialized file/directory tree, reusing
  unchanged content rather than leaking GUID copies; an existing invalid
  Python executable no longer bypasses an available signed interpreter.
- Regression coverage includes unchanged repeated stamps, independent plugin,
  engine and library edits, preservation of prior snapshots, and signed
  recovery from missing-config and wrong-prefix existing slots.
- Revised wrapper counts: shell 2295 -> 2250 (-45), PowerShell 3993 -> 4044
  (+51); combined wrappers 6288 -> 6294 (+6). Canonical engine remains
  881 lines, so combined corpus 7169 -> 7175 (+6). This leg's small growth
  buys content identity and repair correctness; the Validation Plan's
  materially-smaller aggregate rollout gate remains open and must offset
  this growth through subsequent adopters before closure.
- Review-fix validation: all seven network-disconnected plugin sub-suites
  passed (4470 passed, 94 skipped); the contained installer selection with
  available PowerShell execution passed (197 passed, 5 skipped).
  Install-contract, engine-sync, documentation and touched Python lint
  checks passed. The wrong-prefix fixture was corrected to keep rejecting
  its stale config after a simulated failed signed rebuild.
- The second review found PowerShell's default case-insensitive map could
  collapse case-distinct snapshot paths on POSIX. The snapshot digest now
  uses ordinal keys, with a real case-sensitive filesystem regression.
  A guard-blocking private identifier in existing install-contract prose
  was removed without changing the documented marker behavior.
- The third review identified a same-version local stamp publication race.
  Snapshot creation now records its latest content candidate under the
  version-scoped lock; publication rechecks that candidate under the global
  lock and rejects superseded markers and launchers. Existing sequential
  lock acquisition is preserved to avoid runtime-install AB-BA deadlock.
  The deterministic interleaving regression constructs and publishes newer
  content between an older stamp's snapshot creation and publication lock.
- This follow-up's installer/snapshot/stamp/activation selection passed:
  75 passed, 4 skipped. Final wrapper counts are shell 2250 and PowerShell
  4058, for 6308 total versus the 6288 baseline (+20); the canonical engine
  remains 881 lines. The aggregate rollout shrinkage gate remains open.

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

### 2026-09-22 — Phase 0 audit landed; Phase 1 engine built, `agent-pull-requests` as reordered pilot

- Ran the Phase 0 audit for real (see `## Proposal` above): read the actual
  `install.ps1`/`install.sh` bodies in `agent-bridge`, `agent-logger`,
  `agent-vault`, `agent-ssh`, `agent-worktrees`, and `budget-guidance` rather
  than assuming the effort's own guessed classification was correct going in.
  One correction found: the resilient uv retry helpers
  (`Invoke-UvPipInstallResilient`/`Invoke-UvVenvResilient`) actually live in
  `agent-bridge`/`agent-logger`, not the smaller `agent-vault`/`agent-ssh`
  pilots as initially assumed — used the real source for extraction.
- **Reordered Phase 1's pilot**: operator explicitly approved building the
  real engine now and converting `agent-pull-requests` (new, no daemon, no
  existing users) first, instead of `agent-bridge` as originally planned —
  see the Design decision recorded above Phase 1. `agent-bridge`'s own
  conversion remains open, unscoped-down follow-through.
- Landed `libs/installer-engine/installer-engine.ps1`/`.sh` (the (a)/(b)
  functions from the audit, plus a newly-written `Write-SimpleBinstub` that
  didn't exist anywhere yet), `tools/sync-installer-engine.py` (mirrors
  `sync-versioned-runtime.py`'s shape), and a narrow, non-loophole addition to
  `tools/check-install-contract.py` (accepts a wrapper's `uv pip
  install`/schema-v3-manifest requirements as satisfied only when it both
  vendors the engine AND actually calls the specific resilient-install/
  manifest-writer functions — not merely referencing the engine file).
- Converted `plugins/agent-pull-requests` into a real runtime plugin: kept
  every contract-required literal seam (v4 self-stage/smoke-seam,
  `Get-SourceKind`, `install-contract:v3 versioned-venv`, stamp/provision
  actions) in its own thin wrapper, dot-sourcing the vendored engine only for
  the genuinely shared helper bodies. Added it to `tools/sync-versioned-runtime.py`'s
  adopter set automatically (auto-discovered by `pyproject.toml` + installer
  presence — no registry edit needed).
- **Validated for real, not just asserted**: `tools/sync-installer-engine.py
  --check` correctly fails before syncing and passes after;
  `tools/sync-versioned-runtime.py --check` passes across all 13 plugins;
  `tools/check-install-contract.py` passes (13 plugins, unchanged pass for
  the other 12); `tools/check-version-consistency.py`/`check-docs-consistency.py`
  clean; the plugin's own 5-test pytest suite passes. Removed a stray manual
  `pip install`-ed copy of `agent-pull-requests` from this machine's global
  Python first, then ran the **real** `install.ps1` end-to-end: it vendored
  uv, built a signed venv, installed the package, wrote a schema-v3 deploy
  manifest, and deployed a real binstub at `<home>\.local\bin\agent-pull-requests.ps1`.
  Invoked that exact binstub directly: `agent-pull-requests status --repo
  <owner>/<repo> --number <n>` returned real live PR data — proving
  the whole chain end-to-end through the real installer, not a manual
  workaround.
- **Not done in this slice** (tracked, not forgotten): `agent-bridge`'s actual
  conversion (Phase 1's original reference target) and all of Phase 2+'s
  remaining adopters. The live end-to-end proof above was Windows-only; a
  POSIX (`install.sh`) live-install proof is still open, worth deciding
  whether each future adopter phase requires one rather than only repo-level
  guards.

### 2026-09-22 — PR #3287 merged: 10 real review findings fixed, no shortcuts

- Operator explicitly rejected the earlier session's ad-hoc `pip install`
  workaround ("use the proper vendored installer engine from the get-go, no
  shortcuts") and confirmed the Phase-0-then-Phase-1-with-a-reordered-pilot
  plan (Design decision above) before any code was written.
- Landed `libs/installer-engine/` + `tools/sync-installer-engine.py` +
  `agent-pull-requests`'s conversion as `ThomasMichon/copilot-extensions#3287`.
  Copilot's automated review found **10 real, non-cosmetic** issues across
  three review rounds (concurrency/immutability bugs, not style nits) — all
  fixed before merge, none dismissed or worked around:
  1. POSIX binstub lock had no `flock`-unavailable fallback and never
     released fd 9 before `exec`, so a successful lock blocked every other
     invocation for the dispatched process's entire lifetime.
  2. Cleanup/mark-complete helper failures were silently swallowed in both
     `.ps1`/`.sh`, letting install continue into an incomplete/protected slot.
  3. `-Force`/`--force` could reach the venv/install step even for an
     already-complete, currently-active immutable slot, and the
     interpreter-presence-only build gate missed a killed-build-left-no-marker
     case entirely — the exact hazard the versioned-runtime contract exists
     to prevent.
  4. The install-contract's new engine exemption was a naive substring check
     (a comment mentioning the function name alone would bypass the
     requirement) — tightened to require a real dot-source/include plus a
     real call shape, with regression fixtures for both the legitimate pass
     and a deliberate bypass attempt.
  5. The POSIX uv bootstrap piped the mutable `astral.sh/uv/install.sh`
     script with no version pin or integrity check, unlike the Windows path's
     pinned-version + SHA-256-verified download — brought to parity.
  6. `ensure_uv`'s diagnostic lines leaked onto stdout ahead of its returned
     path, so `UV_CMD=$(ensure_uv ...)`-style capture could try to execute
     log text as a command on a fresh host with no uv on PATH.
  7. A custom `-InstallDir`/`--install-dir` wasn't forwarded to the
     self-provisioning binstub's `provision` invocation, so first-use
     provisioning silently installed to the wrong (default) root.
  8. POSIX interpreter resolution for `mark-complete` checked the legacy
     link/ambient Python before the just-built versioned slot's own
     interpreter, so a host with no ambient Python (uv-provisioned Python
     only) never got a completion marker despite a working install.
  9. No single lock covered the whole install transaction (slot
     create/install/activate/manifest), only the deployed binstub's own
     first-use lock — a manual `install.sh` run and a binstub-triggered
     provision could race each other on the same slot.
  10. The installed Windows binstub/`.cmd` resolved `pwsh`/PowerShell via
      ambient `Get-Command`/bare names, unlike the payload shim's
      restricted-PATH-safe absolute `%SystemRoot%\System32\where.exe` +
      absolute-path fallback — brought to parity.
- Also caught and fixed two things the review flagged that weren't concurrency
  bugs: a genuinely missing `tools/check-bootstrap-sync.py` `FAMILIES`
  registration (the plugin's `bootstrap-check.ps1`/`.sh` weren't byte-identical
  to the shared `versioned-venv/psscriptroot` family template — fixed by
  copying the real template verbatim and registering the plugin), and a
  required PR-description "Documentation impact" statement.
- One operational hazard hit and recovered from mid-session: an accidental
  `git stash pop` in the shared repo checkout popped a stale, unrelated stash
  entry from a completely different historical session (git stash is
  per-repository, not per-worktree) and produced a large merge-conflict mess
  touching unrelated plugins. Recovered cleanly with `git reset --hard HEAD`
  before anything was pushed — the stash entry itself was untouched/preserved
  throughout. Lesson: never `git stash`/`git stash pop` in a shared
  multi-worktree repo checkout without first confirming the stash stack is
  actually empty or owned by this session.
- Final state: all 10 findings fixed and validated for real (every repo
  guard + the plugin's own test suite + the install-contract's own new
  regression fixtures), plus a full clean-reinstall + idempotent-reinstall
  proof and a real `agent-pull-requests status` call through the deployed
  binstub, immediately before merge. Squash-merged; worktree finalized.
- **Genuinely still open** (not silently dropped): `agent-bridge`'s actual
  Phase 1 conversion, all Phase 2+ adopters, and a POSIX live-install proof
  lane (this session's end-to-end proof was Windows-only, matching the
  machine it ran on).

### 2026-09-22 — Two unrelated bugs found landing #3287, fixed separately

- `#3289`: a pre-existing `agent-containers` `__version__` fallback stale by
  one dev increment, failing `check-version-consistency` for every open PR
  on `main` at the time. Trivial fix, unrelated to the engine work.
- `#3304`: `agent-worktrees`' own `instructions/worktree-context-guide
  .instructions.md` template had grown to 4380 bytes, past
  `customizing-copilot`'s `MAX_TEMPLATE_BYTES = 4096` projection budget —
  the exact same bug class as the earlier `context-handoff` fix
  (private downstream repo #404 -> upstream #3250), this time hitting
  `agent-worktrees` and hard-blocking `push-changes`/finalize validation in
  *every* repo that resolves this projection (discovered when it blocked a
  routine knowledge-repo effort-doc push, unrelated to this effort). Trimmed
  to 3938 bytes, preserving every safety point in the trimmed sections.

### 2026-09-22 — Hardened enforcement: closed two real gaps in the new guards

Operator asked to make sure CI + pre-push actually enforce the vendored
installer-engine copies staying byte-identical across every consumption
site — not just assert that they do. Two real gaps found and closed
(`ThomasMichon/copilot-extensions#3326`):

1. `tools/sync-installer-engine.py`'s `verify()` only checked plugins
   already listed in its `ADOPTERS` tuple — a plugin that hand-copied
   `scripts/installer-engine.*` without being added to `ADOPTERS` would
   drift silently forever, invisible to both CI and pre-push. Added
   `unregistered_adopters()`, mirroring `tools/sync-installation-context
   .py`'s existing pattern for the identical class of gap. Verified live: put
   a copy of the canonical file in an unregistered plugin's `scripts/`,
   confirmed `--check` failed with a clear message, reverted, confirmed
   clean again.
2. Neither `tools/test_check_install_contract.py` (the substring-bypass
   regression fixtures added in #3287) nor a new
   `tools/test_sync_installer_engine.py` (covering the new detection above)
   were wired into any CI job — they only ran when invoked by hand. Both are
   now explicit `guards + lint` steps in `.github/workflows/ci.yml`.

Also independently re-verified the *existing* enforcement is real, not just
present: deliberately drifted a vendored copy and confirmed both
`check-install-contract.py` and the actual `tools/hooks/pre-push` script
(active via `core.hooksPath=tools/hooks` in this checkout) block on it
*before* the push leaves the machine, then reverted. One unrelated,
pre-existing CI failure (`worktree-manager`'s `test_doctor_human_render_
matches_exhaustive_json`, a missing `_render_dropin_registry_report`
attribute — the same class of unrelated `main` drift hit twice earlier this
session) did not block merge; `merge state: clean` confirmed it isn't a
required check.

### 2026-09-23 — `agent-pull-requests` verb parity: `create`/`merge`/`wait` implemented

Resumed via context handoff (again worked around a broken `consume_handoff`
MCP tool — `Extension disconnected before responding to tool call`, three
consecutive attempts — by reading the file-backed handoff JSON directly
from a private repo state-root `worktrees/<id>/handoff/handoff-<id>.json` path and
`bind-session`ing manually; this is now the *second* distinct
`consume_handoff` failure mode hit across two sessions and still not
reported upstream).

Picked the smaller of the two roster options (vs. `agent-bridge`'s Phase 1
installer-engine conversion, a live production daemon deferred as
appropriately larger/riskier for one sitting):

- Implemented all three previously-stubbed verbs in
  `plugins/agent-pull-requests/src/agent_pull_requests/__main__.py`:
  - `create`: `gh pr create --repo <owner/repo> --head <branch> [--base ...]
    --title ... [--body ...] [--draft]`, no local checkout required (proved
    the exact transport works with a live `--dry-run` round-trip through
    `agent-worktrees repos gh ... -- pr create --dry-run`, no PR created);
    parses the created PR's number from `gh`'s printed URL.
  - `merge`: `gh pr merge` with `--squash` (default) / `--merge` /
    `--rebase`, `--auto`, `--delete-branch`.
  - `wait`: polls `status` until `MERGED`/`CLOSED` or a timeout; distinct
    exit codes (0 merged, 1 closed-unmerged/status-error, 3 timed-out).
  - All three reuse the existing `agent-worktrees repos gh` transport and
    the established leading-diagnostic-tolerant stdout parsing (added a
    plain-text sibling, `_strip_leading_diagnostic`, alongside the existing
    JSON-tail parser).
- Added unit tests for each new verb (parser-flag tests + JSON-output smoke
  tests via monkeypatched `_github_create`/`_github_merge`/`_github_status`),
  bumped the plugin version (`0.1.0-dev2` -> `0.1.0-dev3`) across
  `plugin.json`/`pyproject.toml`/`__init__.py`, and corrected the plugin
  README/CLI-reference/marketplace description, which had gone stale
  describing a payload-only, status-only scaffold from before the real
  installer landed.
- Landed as `ThomasMichon/copilot-extensions#3363` (squash-merged): 15/15
  plugin tests passing, `check-version-consistency.py` /
  `check-docs-consistency.py` / `check-install-contract.py` all clean, a
  live `status` call against the private downstream repo's PR #491 still correct
  through the module (unchanged transport), full CI green (one prior
  unrelated `worktree-manager` drift failure not present this run).
- **Genuinely still open**: `agent-bridge`'s Phase 1 installer-engine
  conversion (the effort's original pilot target), Phase 2+'s remaining
  adopters, a POSIX live-install proof lane (all proofs to date are
  Windows-only), and the cross-repo documentation sweep pointing consumers
  at `agent-pull-requests` instead of `agent-worktrees`' worktree-bound PR
  verbs.

### 2026-09-23 — `docs/install-contract.md` updated for the engine + a real, unrelated CI-blocking bug fixed along the way

- Considered `agent-bridge`'s Phase 1 conversion next (per the roster), but
  its `install.ps1` is 2880 lines with many non-trivial call sites
  (scheduled-task/service lifecycle, the ZDD cutover, legacy
  project-service migration, several sibling-plugin installs) driving a
  live production daemon other machines/sessions depend on right now. A
  faithful "no shortcuts" conversion needs the same multi-round review
  rigor the much smaller `agent-pull-requests` pilot needed (10 real bugs
  across 3 rounds) plus full existing-test-suite adaptation and a
  real-machine post-conversion health check per this effort's own
  Validation Plan -- not something to start and leave half-finished in one
  sitting. Deferred it rather than rush it; still the natural next slice
  for a session with more room.
- Picked a smaller, explicitly-required, safely-completable item instead:
  the Validation Plan's own "`docs/install-contract.md` is updated to
  describe the new engine + config-schema pattern once Phase 1 lands" —
  overdue since the engine itself (and its first real adopter,
  `agent-pull-requests`) already landed in #3287/#3363, but the doc never
  mentioned `libs/installer-engine/` at all.
- Added a new "Shared installer-engine helpers" section (what the engine
  covers vs. deliberately leaves to each plugin, the vendoring-over-git-
  fetch rationale, the phased opt-in adoption model, `agent-worktrees`'
  permanent exception) plus a matching Enforcement bullet for
  `tools/sync-installer-engine.py --check`. Landed as
  `ThomasMichon/copilot-extensions#3417`.
- **Found and fixed one real, unrelated bug along the way**: PR #3417's own
  CI failed on the required `guards + lint` check with a
  `check-version-consistency.py` violation -- `worktree-manager`'s
  `pyproject.toml` (bumped to `0.1.0-dev74` by an unrelated, already-merged
  PR #3414) had drifted from `src/worktree_manager/__init__.py`'s
  `__version__` (still `0.1.0-dev73`). Confirmed via `git show
  origin/main:...` that this was already broken on `main` itself, not
  introduced by this PR, and would block every other PR's `guards + lint`
  check the same way. Bumped `__init__.py` to match and pushed it as a
  second commit on the same PR (matches this session's established pattern
  of fixing a blocking, unrelated bug found along the way -- see the
  `#3289`/`#3304` entries above). Verified `check-version-consistency.py`
  clean before pushing; full CI green afterward.
- **Genuinely still open**: unchanged from the prior entry --
  `agent-bridge`'s Phase 1 conversion, Phase 2+'s remaining adopters, a
  POSIX live-install proof lane, and the cross-repo documentation sweep are
  all still open follow-through.

### 2026-09-26 — Cross-reference: a pointer-based mechanism may supersede byte-vendoring here
- A new sibling effort, `efforts/active/vendor-pointer-generalization`,
  proposes generalizing the DRY vendor-pointer pattern (built by
  `vendored-doc-pointers` + `dev-branch-release-pipeline`, currently applied
  to docs and designed-but-unapplied for shared libs) to this effort's own
  installer-engine surface, as a Phase 2 evaluation. That effort's own Plan
  explicitly commits to coordinating with this effort's driver before
  changing this effort's chosen mechanism (`sync-installer-engine.py
  --check`, a drift detector) — not forking the design unilaterally. No
  mechanism change is proposed or made here; this is a forward pointer only,
  so this effort's own driver sees it on next resume.

### 2026-09-29 — Narrow operator-approved revisit: dev-time canonical reference adopted for the shared engine proof point

- The 2026-09-12 "vendoring over git-fetch" decision has now been **narrowly
  revisited with explicit operator approval**, but only for the *dev-time*
  authoring shape of the shared installer engine. The original decision's
  core reasoning still fully stands: a shipped plugin install/update must NOT
  fetch or resolve a shared module from a sibling plugin, git checkout, or
  network location at install/runtime; the payload a real user receives from
  `main` remains completely self-contained.
- What changed is only how `dev` keeps the shared engine in sync. The proof
  adopter (`agent-pull-requests`, still this effort's only current adopter)
  now sources canonical
  `libs/installer-engine/installer-engine.{sh,ps1}` directly during
  authoring, with **no** local `scripts/installer-engine.*` copy remaining on
  `dev`. Promotion/materialization then copies canonical back into
  `scripts/installer-engine.{sh,ps1}` and rewrites the wrapper's source line
  to the local form before anything ships to `main`, preserving the original
  self-contained install/runtime contract exactly.
- `vendor-pointer-generalization` Phase 2 landed the supporting changes in
  this same coordinated leg: `tools/sync-installer-engine.py --check` now
  recognizes this canonical-reference form as valid, `tools/check-install-
  contract.py` accepts the wrapper + real engine-function-call shape with the
  canonical relative-path source line, and both `materialize_main.py` and
  `preview_release.py` now perform the promotion-time/scratch-preview
  copy-then-rewrite.
- Consequence for this effort's future rollout only: the remaining Phase 2+
  adopters listed above should go straight to this canonical-reference form
  when their individual adoption PRs happen, rather than first creating a
  fresh byte-vendored dev copy. This does **not** redesign the effort into a
  runtime/install-time shared module, and it does **not** reverse the
  documented rejection of git-fetch / shared install-time resolution.

### 2026-10-05 — `agent-logger` adopted the shared installer engine as the second mover

- Converted `plugins/agent-logger/scripts/install.sh` and
  `plugins/agent-logger/scripts/install.ps1` to the same canonical-reference
  wrapper shape `agent-pull-requests` already uses: both now source
  `libs/installer-engine/installer-engine.{sh,ps1}` directly on `dev`, keep
  their own contract seams (`install-contract:v3 versioned-venv`,
  source-kind/self-stage/smoke-seam blocks) in the wrapper, and retain only
  the genuinely per-service logic that Phase 0 classified as non-engine
  (`Ensure-UvIndex`, agent-logger's extra trampoline cleanup, payload snapshot
  publication, config-repo discovery, systemd/Scheduled Task wiring,
  self-provisioning wrapper publication, and the install-lock/stale-payload
  guards). `tools/installer_engine_ref.py` now registers `agent-logger` as an
  adopter so `tools/sync-installer-engine.py --check` enforces the wrapper form.
- **Fix-once / fixed-everywhere proof:** before this conversion,
  `agent-logger` still carried its own hand-maintained copies of the
  pyvenv.cfg / uv-exit-106 retry logic from copilot-extensions#2482 in both
  `install.sh` and `install.ps1` (`_is_venv_corruption` /
  `_uv_venv_resilient`, `Test-IsVenvCorruption` / `Invoke-UvVenvResilient`).
  After conversion those duplicate functions are gone from the wrapper
  entirely; the wrapper now sources the canonical engine and the relevant
  regression tests (`test_install_sre_retry.py`,
  `test_install_venv_corruption_retry.py`,
  `test_install_signed_python_probe.py`) extract their helper bodies from
  `libs/installer-engine/installer-engine.ps1` / `.sh`, proving the bug fix is
  now inherited from one shared source instead of preserved by a second manual
  port.
- **Line-count / corpus result:** wrapper-only installer lines shrank from
  `install.sh` 1215 -> 1193 (-22) and `install.ps1` 1637 -> 1506 (-131), for
  a combined wrapper drop of 2852 -> 2699 (-153). The canonical engine grew by
  3 lines on the POSIX side (368 -> 371) to fail closed when `uv venv` returns
  success without leaving `pyvenv.cfg`; the PowerShell engine stayed flat at
  462 lines. Net result for this conversion leg: **150 lines removed** from the
  combined agent-logger + shared-engine corpus, not just relocated.
- Validation completed here:
  - `python3 tools/check-install-contract.py`
  - `python3 tools/sync-installer-engine.py --check`
  - `python3 tools/check-docs-consistency.py`
  - `python3 tools/check-version-consistency.py`
  - `python3 tools/check-module-size.py`
  - `python3 tools/check-changefile-presence.py --base origin/dev`
  - `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-logger --reinstall --admission-wait 540`
    -> PASS (`494 passed, 17 skipped`; wrapper runner summary `247 passed, 7 skipped`)
  - `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-pull-requests --reinstall --admission-wait 540`
    -> PASS (`21 passed`; wrapper runner summary `21 passed`) to prove the shared-engine bash fix did not regress the existing pilot adopter
  - Real POSIX install proof: ran `plugins/agent-logger/scripts/install.sh
    install --install-dir <scoped session scratch>` against the live checkout
    with the real user systemd instance, confirmed the scoped timer became
    active, `status` reported the installed runtime healthy, the versioned
    slot's Python imported `agent_logger`, `yaml`, and `plugin_activation`
    successfully, and the published snapshot carried payload-local
    `scripts/installer-engine.{sh,ps1}` copies with both installer source
    lines rewritten to the local form before the first-use installer path could
    consume them. Then removed the scoped timer again with `uninstall --install-dir ...`.
    Also re-confirmed the `dev` checkout keeps **no** local
    `plugins/agent-logger/scripts/installer-engine.*` copy afterward.
- Validation not possible in this Linux/WSL session:
  - No real Windows install lane / Task Scheduler exercise for
    `install.ps1`; instead validated that lane through the full plugin test
    suite plus a `pwsh` parse check of `plugins/agent-logger/scripts/install.ps1`.
  - No broader multi-machine production deployment beyond the scoped local
    timer proof above.

### 2026-10-05 — installer-test hygiene sweep for the next adopter lane

- Added `installer-test-hygiene.md` to record a bounded sweep across the 10
  installer-engine adoption targets, starting from the concrete `agent-logger`
  churn in PR #5228 and genericized publicly as issue #5245.
- Fixed the concrete hygiene gaps found:
  - `plugins/agent-logger/tests/conftest.py` +
    `plugins/agent-logger/tests/test_scaffold.py`: real temp-repo `git`
    fixtures now scrub ambient git-routing env vars and use a 20-second
    timeout, so they cannot silently follow a caller's `GIT_DIR` /
    `GIT_WORK_TREE` into some other checkout or hang forever.
  - `plugins/agent-logger/tests/test_install_binstub.py`: the real
    stamp/provision/first-use snapshot tests now build a temp-rooted env that
    mirrors `tools/plugin_test_containment.py`'s HOME/XDG/temp containment
    instead of overriding only a subset of roots; the PowerShell task-warning
    harness there is also now time-bounded.
  - `plugins/agent-logger/tests/test_install_signed_python_probe.py`,
    `test_install_sre_retry.py`, `test_install_venv_corruption_retry.py`, and
    `test_install_sync_repo_config.py`: added explicit 20-second timeouts to
    the previously unbounded local shell/PowerShell harness subprocesses.
  - `plugins/agent-bridge/tests/test_install_sre_retry.py` and
    `test_install_venv_corruption_retry.py`: applied the same timeout fix to
    the analogous PowerShell retry harnesses.
  - `plugins/agent-codespaces/tests/test_self_provisioning_binstub.py`: real
    bash binstub runs now execute under temp-rooted HOME/XDG/temp dirs and have
    an explicit 20-second timeout.
- Spot-check result for the other next-wave plugins (`agent-vault`,
  `agent-ssh`, `agent-index`, `agent-dispatch`, `agent-containers`,
  `agent-mcp`, `agent-machines`): their installer-adjacent harnesses already
  carried the containment this sweep was looking for (explicit subprocess
  bounds and/or temp-rooted homes), so no code changes were needed in this
  round.
- Validation completed here:
  - `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-logger --admission-wait 540`
    -> PASS (`496 passed, 17 skipped`; wrapper runner summary `247 passed, 7 skipped`)
  - `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-bridge --admission-wait 540`
    -> PASS on retry after one transient failure in
    `tests/test_codespace_spawner.py::test_relay_ping_probe_command_round_trips_against_real_listener`
    (`warning-only first run failure; subsequent full-suite pass`)
  - `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-codespaces --admission-wait 540`
    -> PASS (`688 passed, 1 skipped`; wrapper runner summary additional sub-suites `497 passed, 11 skipped`, `355 passed, 1 skipped`, `65 passed`)

### 2026-10-05 — `agent-vault` adopted the shared installer engine

- Converted `plugins/agent-vault/scripts/install.sh` and
  `plugins/agent-vault/scripts/install.ps1` to the canonical-reference wrapper
  form: both now source
  `libs/installer-engine/installer-engine.{sh,ps1}` directly on `dev`, and
  `tools/installer_engine_ref.py` now registers `agent-vault` as an adopter so
  `tools/sync-installer-engine.py --check` enforces that form.
- Removed the duplicated wrapper-local installer-engine bodies:
  POSIX now uses shared `ensure_uv`, `new_signed_venv`,
  `invoke_uv_pip_install_resilient`, `write_deploy_manifest`, and
  `write_simple_binstub`; PowerShell now uses shared `Ensure-Uv`,
  `New-SignedVenv`, `Invoke-UvPipInstallResilient`,
  `Write-DeployManifest`, and `Write-SimpleBinstub`. Genuinely per-service
  logic stayed local: agent-vault's versioned-runtime helpers, downgrade
  guard, the PowerShell-side pip→uv index bridge (`Ensure-UvIndex`),
  KeePassXC prerequisite check + askpass helper, systemd/Scheduled Task
  lifecycle, Windows drain-safe fixed-endpoint cutover, and the PowerShell
  snapshot materialization needed for first-use provisioning from a stamped
  payload.
- The Windows `stamp` path now materializes both the canonical installer-engine
  pair and the external `[tool.uv.sources]` libs (`zdd`, `agent-procutil`,
  `single-instance-lease`) into the published snapshot before the staged
  payload marker flips over, so first-use provisioning from a dev-time
  canonical-reference wrapper still ships a self-contained snapshot.
- **Line-count / corpus result:** wrapper-only installer lines shrank from
  `install.sh` 930 -> 831 (-99) and `install.ps1` 1253 -> 1246 (-7), for a
  combined wrapper drop of 2183 -> 2077 (**-106**). The canonical engine grew
  by 3 POSIX lines (`installer-engine.sh` 376 -> 379) to preserve the
  pre-existing nonzero failure contract when provisioning reports success but
  leaves no resolvable runtime; `installer-engine.ps1` stayed flat at 462
  lines. Net result for this conversion leg: **103 lines removed** from the
  combined agent-vault + shared-engine corpus instead of merely relocating
  them.
- Validation completed here:
  - `python3 tools/sync-vendored-libs.py --check`
  - `python3 tools/sync-installer-engine.py --check`
  - `python3 tools/check-vendored-libs-sync.py`
  - `python3 tools/check-install-contract.py`
  - `python3 tools/check-version-consistency.py`
  - `python3 tools/check-module-size.py`
  - `python3 tools/check-docs-consistency.py`
  - `python3 tools/check-changefile-presence.py --base origin/dev`
  - `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-vault --reinstall --admission-wait 540`
    -> PASS (`260 passed, 12 skipped`)

### 2026-10-08 — `agent-codespaces` conversion prepared for review

- Resumed the pending adopter and reconciled its launch-memory fixture
  correction with the current contribution branch, preserving private
  directory/file permissions and the upstream compatibility test.
- Both installer wrappers source the canonical engine on `dev`. Shared uv
  acquisition, transient pip/venv retries and manifest writing replace local
  copies. Connection Owner lifecycle, dependency order, editable development
  installs and Windows signed-Python health/recovery remain plugin-owned.
- PowerShell snapshot creation materializes the engine pair and all declared
  local libraries before publishing the snapshot. Regression coverage proves
  that provisioning does not need the authoring checkout afterward.
- Installer wrappers shrink by 69 lines; the canonical engine is unchanged.
- Validation: isolated Codespaces suite 1653 passed / 56 skipped; host suite
  including available PowerShell coverage 1696 passed / 13 skipped; shared
  materialization and install-contract tooling 145 passed. Sync, install
  contract, vendored-library, version, module-size, documentation and lint
  guards pass. Test fixtures no longer depend on ambient `gh` presence or an
  unmocked live stop operation.
- Review, merge and deployed verification are still required; this entry
  records implementation, not effort completion. Next requested adopter is
  `agent-dispatch`, after this slice lands. Public tracking: #5802.
- Review identified a signed-Python exit-zero/unusable-venv recovery gap.
  Health validation now gates accepting the signed result regardless of exit
  code; an unusable result is discarded before the shared uv fallback.
  The regression matrix covers usable/unusable results at both zero and
  nonzero exit codes.
- Native Windows validation exposed fixture uv lookup escaping to a real
  bootstrap. Fixtures now select their fake executable explicitly and forbid
  bootstrap/service/process effects. The corrected native installer/recovery
  and launch-memory selection passes: 52 passed / 15 skipped, including a
  native signed-venv health matrix. Deployment remains outstanding.
- PR #5804 subsequently merged, with current-head approval and green CI.
  Release promotion included the conversion. The supported unified update
  completed on a real host: the released Codespaces wrapper sources its
  payload-local engine, engine bytes match the canonical source, and
  `agent-codespaces version` reports `0.5.41-dev1`. Codespaces adoption is
  complete; next requested adopter is `agent-dispatch`. The overall effort
  remains Active with the other adopter and Validation Plan gates open.
  - `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-pull-requests --reinstall --admission-wait 540`
    -> PASS (`21 passed`) after the shared POSIX binstub helper picked up the
    same success-without-runtime exit fix this leg needed for agent-vault
  - Focused follow-up after the initial full-suite run surfaced only the two
    installer preinstall-loop guards; the review-fix pass then re-ran only the
    affected installer regressions (`preinstall_loop`, `uv_index`,
    `snapshot_only`) before the final all-green full suite:
    `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-vault --reinstall --admission-wait 540 -k "uv_index or snapshot_only or preinstall_loop"`
    -> PASS (`11 passed, 1 skipped, 260 deselected`). No other repeated full
    suite ran between the initial failing full run and the final all-green one.
  - Real POSIX install proof: with `HOME` redirected into the session-state
    files area and `--no-service --install-dir <scoped root>`, the converted
    `plugins/agent-vault/scripts/install.sh install` completed end-to-end,
    built the versioned runtime slot, imported `agent_vault` successfully,
    wrote a deploy manifest with the correct local source metadata, and left
    **no** `plugins/agent-vault/scripts/installer-engine.{sh,ps1}` copy in the
    `dev` checkout afterward.
- Validation not possible in this Linux/WSL session:
  - No real Windows `.ps1` install / Scheduled Task lane on a Windows host;
    that path was covered here by the full plugin suite plus a PowerShell parse
    check only.
- Test-hygiene note: no new containment bug class was found in agent-vault's
  installer-adjacent tests. The touched preinstall-loop guards already used
  temp-rooted work dirs and explicit 30-second subprocess timeouts; the new
  PowerShell snapshot/uv-index regressions were written to the same model
  (isolated HOME/XDG/temp roots, stripped inherited Python env, explicit
  subprocess timeouts), and the existing preinstall-loop guards only needed
  shape updates so they continued extracting/exercising the refactored wrapper
  logic.

### 2026-10-05 — `agent-ssh` adopted the shared installer engine

- Converted `plugins/agent-ssh/scripts/install.sh` and
  `plugins/agent-ssh/scripts/install.ps1` to the canonical-reference wrapper
  form: both now source
  `libs/installer-engine/installer-engine.{sh,ps1}` directly on `dev`, and
  `tools/installer_engine_ref.py` now registers `agent-ssh` as an adopter so
  `tools/sync-installer-engine.py --check` enforces that form.
- Removed the wrapper-local copies of the shared engine logic:
  POSIX now uses shared `ensure_uv`, `new_signed_venv`,
  `invoke_uv_pip_install_resilient`, `write_deploy_manifest`, and
  `write_simple_binstub`; PowerShell now uses shared `Ensure-Uv`,
  `New-SignedVenv`, `Invoke-UvPipInstallResilient`,
  `Write-DeployManifest`, and `Write-SimpleBinstub`. Genuinely per-service
  logic stayed local: the vendored-lib resolution helpers, the
  `agent-ssh`-specific package-install dependency list, versioned-runtime
  helpers, PATH persistence, the session-start hook deployment, and the
  snapshot materialization needed for first-use provisioning from a stamped
  payload.
- The stamped snapshot path now materializes both the canonical installer-engine
  pair and the canonical-on-`dev` `[tool.uv.sources]` libraries
  (`agent-procutil`, `ssh-manager`, `venue-copilot`, `zdd`,
  `remote-login-shell`) into the published snapshot and rewrites the wrapper
  source lines to the local `scripts/installer-engine.*` form before
  first-use provisioning, so the shipped snapshot remains self-contained even
  though the `dev` checkout keeps no local `scripts/installer-engine.*` copy.
- Shared-engine follow-up found and fixed in the same leg: PowerShell's
  `Write-DeployManifest` now falls back to the host name when
  `$env:COMPUTERNAME` is absent, so Linux/WSL `pwsh` validation can exercise
  the shared manifest writer without a null dereference.
- **Line-count / corpus result:** wrapper-only installer lines grew from
  `install.sh` 701 -> 817 (+116) and `install.ps1` 899 -> 990 (+91), for a
  combined wrapper change of 1600 -> 1807 (**+207**). The canonical engine grew by
  11 POSIX lines (`installer-engine.sh` 379 -> 390) for shell-literal quoting,
  snapshot-safe provenance/version overrides, and missing-payload tolerance, and
  by 11 PowerShell lines (`installer-engine.ps1` 462 -> 473) for the matching
  provenance/version override support plus the `$env:COMPUTERNAME` fallback
  above. Net result for this conversion leg: **229 lines added** to the combined
  agent-ssh + shared-engine corpus. Per the re-scoped Validation Plan below,
  that makes this a documented temporary growth leg, not a silent pass: the
  safety/provenance fixes earned their keep here, and the remaining adopter
  rollout must still offset this growth (or split out an explicit tightening
  follow-up) before the effort can close.
- Validation completed here:
  - `python3 tools/sync-vendored-libs.py --check`
  - `python3 tools/sync-installer-engine.py --check`
  - `python3 tools/check-vendored-libs-sync.py`
  - `python3 tools/check-install-contract.py`
  - `python3 tools/check-version-consistency.py`
  - `python3 tools/check-module-size.py`
  - `python3 tools/check-docs-consistency.py`
  - `python3 tools/check-changefile-presence.py --base origin/dev`
  - initial full suite once, before edits:
    `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-ssh --reinstall --admission-wait 540`
    -> PASS (`207 passed, 7 skipped`)
  - focused follow-up only on the changed installer regressions after the new
    snapshot tests exposed the missing self-contained `uv` source handling and
    the later review rounds identified quoting/snapshot-provenance regressions:
    `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-ssh --reinstall --admission-wait 540 -k "installer_fallback or install_manifest_source_path or install_snapshot"`
    -> PASS (`13 passed, 1 skipped, 207 deselected`), then after the final
    payload-copy / older-snapshot guards landed:
    `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-ssh --reinstall --admission-wait 540 -k "installer_fallback or install_snapshot"`
    -> PASS (`10 passed, 1 skipped, 210 deselected`)
  - focused PS/POSIX snapshot-only proof after the shared manifest-writer fix:
    `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-ssh --reinstall --admission-wait 540 -k install_snapshot`
    -> PASS (`13 passed, 214 deselected`)
  - final full suite after the review-fix pass:
    `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-ssh --reinstall --admission-wait 540`
    -> PASS (`220 passed, 7 skipped`)
  - real POSIX install proof: with `HOME` redirected into the session-state
    files area and `--install-dir <scoped root>`, the converted
    `plugins/agent-ssh/scripts/install.sh install` completed end-to-end,
    built the versioned runtime slot, imported `agent_ssh` successfully, wrote
    a deploy manifest with the correct local source metadata, and left **no**
    `plugins/agent-ssh/scripts/installer-engine.{sh,ps1}` copy in the `dev`
    checkout afterward.
- Validation not possible in this Linux/WSL session:
  - No real Windows `.ps1` install or native Windows PATH persistence lane on a
    Windows host. The PowerShell wrapper path was still exercised here through
    `pwsh`-driven snapshot/first-use tests plus a parse check, but that is not
    a substitute for a native Windows install.
- Test-hygiene note: no existing `agent-ssh` installer-adjacent test needed a
  containment rewrite this round, matching the 2026-10-05 hygiene sweep's
  spot-check. The new snapshot regressions were written to the same model as
  the sweep: isolated HOME/USERPROFILE/XDG/temp roots, stripped inherited
  Python env, and explicit subprocess timeouts. No fixture touched a real
  `~/.ssh` or real SSH key material; every provision/snapshot path ran entirely
  inside temp-rooted test homes.

### 2026-10-05 — `agent-bridge` adopted the shared installer engine

- Converted `plugins/agent-bridge/scripts/install.ps1` and
  `plugins/agent-bridge/scripts/install.sh` to the canonical-reference wrapper
  form: both now source
  `libs/installer-engine/installer-engine.{sh,ps1}` directly on `dev`, and
  `tools/installer_engine_ref.py` now registers `agent-bridge` as an adopter so
  `tools/sync-installer-engine.py --check` enforces that form.
- Removed the wrapper-local copies of the clearly shared helper bodies:
  PowerShell now uses shared `Invoke-NativeCapture`, `Test-IsSreModuleMismatch`,
  `Test-IsVenvCorruption`, `Invoke-UvVenvResilient`, `Ensure-Uv`,
  `New-SignedVenv`, and `Write-DeployManifest`; POSIX now uses shared
  `test_is_sre_module_mismatch`, `test_is_venv_corruption`,
  `invoke_uv_venv_resilient`, `ensure_uv`, and `write_deploy_manifest`.
  Genuinely bridge-specific logic stayed local: the build-artifact scrub
  wrapper around `uv pip install`, the bridge-specific uv bootstrap override
  path (`AGENT_BRIDGE_UV_BOOTSTRAP_*`), the large daemon lifecycle / draining /
  ZDD cutover flow, sibling-plugin installs, and the PowerShell stamped-snapshot
  materialization that now copies both the canonical installer-engine pair and
  the resolved vendored libs into the published snapshot before first-use
  provisioning can consume it.
- Installer-adjacent regression coverage now follows the same split as the
  other adopters: the generic retry/venv-corruption tests extract their bodies
  from `libs/installer-engine/installer-engine.{ps1,sh}`, while the
  bridge-specific build-artifact scrub tests keep exercising the local wrapper.
- **Line-count / corpus result:** wrapper-only installer lines shrank from
  `install.sh` 2392 -> 2204 (-188) and `install.ps1` 3211 -> 3139 (-72), for a
  combined wrapper drop of 5603 -> 5343 (**-260**). The canonical engine stayed
  flat at `installer-engine.sh` 403 lines and `installer-engine.ps1` 478 lines,
  so the combined agent-bridge + shared-engine corpus dropped from 6484 ->
  6224 (**-260**). This leg genuinely shrank the corpus; it did not merely move
  the same bytes elsewhere.
- Validation completed here:
  - `python3 tools/sync-vendored-libs.py --check`
  - `python3 tools/sync-installer-engine.py --check`
  - `python3 tools/check-vendored-libs-sync.py`
  - `python3 tools/check-install-contract.py`
  - `python3 tools/check-version-consistency.py`
  - `python3 tools/check-module-size.py`
  - `python3 tools/check-docs-consistency.py`
  - `python3 tools/check-changefile-presence.py --base origin/dev`
  - initial full suite once, before edits:
    `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-bridge --reinstall --admission-wait 540`
    -> baseline PASS for the main suite (`625 passed, 1 skipped`) plus the
    same long-standing transient warning-only failure later re-seen in the
    follow-up wrapper suite (`tests/test_codespace_spawner.py::test_relay_ping_probe_command_round_trips_against_real_listener`)
  - focused follow-up only on installer-adjacent regressions during the edit loop:
    `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-bridge --reinstall --admission-wait 540 -k "install_sre_retry or install_venv_corruption_retry or installer_powershell51 or install_ps1_build_artifact_scrub or install_ssh_manager_selectors"`
    -> PASS (`15 passed, 11 skipped, 3233 deselected`)
    `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-bridge --reinstall --admission-wait 540 -k "install or installer"`
    -> PASS (`140 passed, 12 skipped, 3107 deselected`)
  - final full suite after the conversion:
    `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-bridge --reinstall --admission-wait 540`
    -> PASS (all 8 sub-suites green after the review-fix pass; aggregate
    `3228 passed, 31 skipped`)
  - shared-engine regression suites:
    `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-pull-requests --reinstall --admission-wait 540`
    -> PASS (`26 passed`)
    `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-logger --reinstall --admission-wait 540`
    -> PASS (`496 passed, 17 skipped`; wrapper runner sub-suite summary `247 passed, 7 skipped`)
    `test-supervisor --admission-timeout 120 --timeout 600 -- python3 tools/run-plugin-tests.py agent-vault --reinstall --admission-wait 540`
    -> PASS (`260 passed, 12 skipped`)
