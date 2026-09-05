# Uniform Runtime Resolution — one way to spawn the versioned interpreter

- **Slug:** `uniform-runtime-resolution`
- **Repo:** copilot-extensions (PR-required `main`, self-merge)
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `main`
- **Created:** 2026-08-19
- **Status:** Done <!-- Draft | Active | Blocked | Done -->
  <!-- Completed 2026-08-20: guard reports 0 and is --strict in CI; every plugin
       resolves the interpreter the one marker-only way; the .venv/venv link is
       retired across all 10 versioned-runtime plugins. 16 PRs. -->
- **Intent:** **extends** the deploy contract
  ([`docs/install-contract.md`](../../../docs/install-contract.md)) — today it
  states a **dual** resolution model (Windows resolves the active slot directly
  from the `current-version` marker; POSIX resolves *through* a `venv`/`.venv`
  **symlink** that the marker publishes). This effort makes resolution
  **marker-only on every OS** (the model `agent-worktrees` already proved in
  #1106), and **closes** the genuinely-divergent drift the dual model let creep
  in (a bare `python3`-on-PATH fallback, a cross-plugin `~/.agent-bridge/venv`
  reference, and per-plugin resolvers that disagree on the fallback order).
  Adds a **how-we-build** pattern: `docs/patterns/uniform-runtime-resolution.md`.
- **Umbrella issue:** #765

## Guiding Intent

There must be **exactly one way** a versioned-runtime plugin's Python interpreter
is resolved and spawned — reachable identically from a `~/.local/bin` binstub, a
systemd user unit or scheduled task, a service/daemon launcher, and an agent
shelling the binstub via a skill. Today there is not: the resolution logic is
**copy-pasted into every plugin's install script and binstub**, and the copies
have diverged into at least four methods:

1. **Marker → slot, junction-free, 3-tier** (`current-version` →
   `last-known-good` → newest complete slot) — `agent-worktrees` only
   (`resolve-runtime.sh`/`.ps1`; the robust target).
2. **Hardcoded `~/.<svc>/.venv/bin/python`** — most plugins on POSIX (the retired
   stable-link; a **junction/reparse point on Windows** that RedirectionGuard
   blocks with WinError 448, the exact hazard #1106 removed).
3. **Marker → slot, inlined per-plugin with a *newest-slot* fallback and no
   last-known-good** — several plugins' Windows launch lines; resolves
   *differently* from (1) during a version swap, so two callers can bind
   different slots.
4. **Bare `python3` on PATH** (`agent-index/ensure-service.sh`) and a cross-plugin
   `~/.agent-bridge/venv/bin/python` reference (`agent-codespaces`) — a service
   under the **system** interpreter, or a second link-name variant.

The consequence is exactly the failure the single-instance model exists to
prevent: the same service launched by its `.venv` binstub and by a
marker-resolving caller can bind **different slots mid-swap**; on Windows the
`.venv` path fails outright; and a service can silently come up under the wrong
interpreter. **Durable runtimes are out of scope** — a heavy engine's own
`~/.<svc>/engine/.venv` (see `patterns/durable-vs-versioned-runtime`) is a
separate, intentional runtime and keeps its explicit venv.

## The uniform method

- **`versioned_runtime.resolve_python(root)`** — the one canonical resolution in
  the shared primitive: `current-version` marker → `last-known-good` → newest
  **complete** slot, junction-free, **never** a `venv`/`.venv` link, **never** a
  PATH python. `activate()` stamps `last-known-good` atomically alongside the
  marker so the fallback always has the last-active version to prefer. A
  `resolve-python` CLI subcommand exposes it to shell/install callers.
- **Canonical parameterized shell resolvers** — one `resolve-runtime.sh` /
  `resolve-runtime.ps1`, service-parameterized (`AGENT_RT_ROOT` + module +
  console-script), fanned out **byte-identically** to every plugin (like
  `versioned_runtime.py`) and **embedded** into every self-contained binstub
  from one template — so a binstub, a hook, and a service launcher all resolve
  identically with no external dependency.
- **Python callers** (service/daemon launch, `agent_worktrees.config.venv_python`,
  the agent-bridge spawn path) call `resolve_python(root)`.
- **Agents/skills** inherit uniformity for free: skill-directed calls go through
  the binstubs, which now all resolve identically.
- **A guard** (`tools/check-runtime-resolution.py`) fails the build on any launch
  path that resolves through a `venv`/`.venv` link, falls back to a PATH python,
  or inlines a non-canonical resolver.

## Plan (phased PRs)

### Phase 1 — Intent + foundation *(this PR)*
- Update `docs/install-contract.md` to the marker-only model; add
  `docs/patterns/uniform-runtime-resolution.md`; author this effort; file the
  umbrella issue.
- Primitive: `resolve_python()` + `slot_python()` + `last-known-good` in
  `activate()` + a `resolve-python` CLI verb. Re-vendor to all plugins.
- Canonical parameterized `resolve-runtime.sh`/`.ps1` in `libs/versioned-runtime`
  + sync plumbing. Guard `check-runtime-resolution.py` (report-only first).
- Unit tests for the 3-tier resolution.

### Phase 2..N — Migrate plugins (small batches)
- Each plugin's binstubs + service launchers + Python callers adopt the canonical
  resolver; delete the `.venv`/`python3`/cross-`venv` fallbacks; pass `--no-link`
  to `activate`. 2-3 plugins per PR to keep review + version-bump churn tractable.

### Phase final — Retire the link + enforce
- Stop creating the `venv`/`.venv` symlink in `activate()` once no consumer
  resolves through it; turn the guard **blocking** in CI.

## Validation Plan
- Unit tests for `resolve_python` (all tiers, junction-free, no-PATH).
- The guard reports zero non-canonical launch paths at the end of migration.
- Field: on a host, every plugin's binstub and service resolves to the same slot
  the marker names; a mid-swap does not bind a stale slot; Windows launches never
  touch a junction.
- `check-install-contract`, `check-vendored-libs-sync`, version guards green.

## Journal

### 2026-08-19 - Kickoff (Phase 1)
- Audited every Python-spawn site across the then-current runtime plugins; mapped the four
  divergent methods above. Confirmed the install-contract states a dual model
  (Windows marker / POSIX `.venv` symlink) that `agent-worktrees` already
  superseded with marker-only (#1106), and that a few genuinely-bad fallbacks
  (bare `python3`, cross-plugin `venv`) slipped in under it.
- Decision (operator): go **full marker-only**, uniform on every OS.
- Next: land Phase 1 (contract + pattern + `resolve_python` primitive + canonical
  parameterized resolvers + guard), then migrate plugins in batches.

### 2026-08-19 - Phase 2 batch 1: agent-ssh + resolver fan-out
- Extended `tools/sync-versioned-runtime.py` to also vendor the canonical
  parameterized resolvers (`resolve-runtime.sh`/`.ps1`) **opt-in** -- byte-
  identically to any plugin that already carries a copy in `scripts/`, excluding
  the bespoke `agent-worktrees` variant. A plugin adopts the resolver by dropping
  the file in; `--check` then enforces sync (CI/pre-push).
- Migrated **agent-ssh** fully onto the marker-only resolver:
  - Vendored `resolve-runtime.sh`/`.ps1` into `scripts/`; installer co-deploys
    them to `~/.agent-ssh/bin/` alongside the session-start hooks.
  - Binstub (`install.sh` heredoc) now **sources the deployed
    `resolve-runtime.sh`** (`AGENT_RT_ROOT` -> `AGENT_RT_PY`) instead of
    `~/.agent-ssh/.venv/bin/python`; the self-provision block is the confined-
    first-run fallback (no inline resolver duplication).
  - Windows `.ps1` binstub now **dot-sources the canonical `resolve-runtime.ps1`**
    (replacing the inline newest-slot/lexicographic resolver -> last-known-good +
    completeness-aware, uniform with POSIX). The `install.ps1` non-Windows shim
    dropped its `.venv/bin/python` line for the marker resolver.
  - Source wrappers `emit-profile.{sh,ps1}` / `verify.{sh,ps1}` delegate to the
    resolving binstub (self-provisions), with a source-tree python only as an
    annotated raw-checkout bootstrap fallback.
- Guard: `check-runtime-resolution` **26 -> 22** (agent-ssh 4 -> 0). Smoke-tested
  all three resolver tiers + the binstub happy path; guards + pwsh parse + ruff
  green. Version-bumped agent-ssh 0.1.0-dev35 -> dev36.
- Next batches (2-3 plugins each, same pattern): the `_py="$_root/.venv/bin/
  python"`-style binstubs in agent-vault, agent-dispatch, agent-mcp,
  agent-containers, agent-machines, agent-logger, agent-index (+ its bare-
  `python3` service), agent-codespaces (+ its cross-plugin bridge ref).

### 2026-08-19 - Phase 2 batch 2: agent-mcp + agent-machines
- Migrated **agent-mcp** and **agent-machines** (both clean binstub-only plugins,
  no service/scheduled-task launchers) onto the marker-only resolver, mirroring
  batch 1:
  - Vendored `resolve-runtime.sh`/`.ps1`; the installer co-deploys them to
    `~/.agent-<svc>/bin/`.
  - POSIX `init.sh` binstub sources the deployed `resolve-runtime.sh`
    (`AGENT_RT_ROOT` -> `AGENT_RT_PY`) instead of `.venv/bin/python`; the
    `init.ps1` non-Windows shim likewise.
  - Both plugins deliberately ship a **.cmd-only** Windows binstub (stdin-verbatim
    for the stdio MCP transport; a `.ps1` shim would break stdin), so the `.cmd`
    keeps its native `current-version` fast path (no PowerShell on the hot path)
    and gains a canonical tier-2/3 fallback that dot-sources the deployed
    `resolve-runtime.ps1` via pwsh -- so it agrees with the resolver on
    `last-known-good` / newest-complete-slot when the marker is absent/stale.
- Guard: `check-runtime-resolution` **22 -> 18** (agent-mcp 2 -> 0, agent-machines
  2 -> 0). All guards + ruff + pwsh parse + 72 plugin tests green. Bumped
  agent-mcp 0.2.0-dev57 -> dev58, agent-machines 0.1.0-dev28 -> dev29.

### 2026-08-19 - Phase 2 batch 3: agent-containers
- Migrated **agent-containers** (the last clean, service-free binstub-only plugin)
  onto the marker-only resolver, full agent-ssh pattern: vendored the resolvers +
  installer co-deploys them; POSIX `init.sh` binstub and the `init.ps1` non-Windows
  shim source `resolve-runtime.sh`; the Windows `.ps1` binstub dot-sources the
  canonical `resolve-runtime.ps1` (replacing the inline newest-slot resolver); the
  `.cmd` delegates to the `.ps1`. The non-Windows shim also prints a runnable
  `bash "<installer>" provision` (batch-2 reviewer feedback, applied proactively).
- Guard: `check-runtime-resolution` **18 -> 16** (agent-containers 2 -> 0). Guards +
  ruff + pwsh parse + 110 tests green. Bumped agent-containers 0.1.2-dev58 -> dev59.
- Remaining (16): the **service/scheduled-task-carrying** plugins (agent-vault,
  agent-dispatch, agent-logger, agent-index) each have an extra inline resolver in
  a Windows daemon/scheduled-task launcher (e.g. agent-vault's `conhost --headless
  "$taskPy"` at-logon task) -- their Windows launchers want operator validation
  (ties into windows-launch-hardening #786), so they get focused batches; plus
  agent-codespaces (cross-plugin agent-bridge venv ref + readiness-context `.venv`
  probes) and agent-worktrees (bespoke; preview-picker dev-echoes + the bin shim's
  PATH-python last resort -> annotate/retire in the final phase).

### 2026-08-19 - Phase 2 batch 4: agent-vault (first service-plugin, incl. its at-logon task)
- Migrated **agent-vault** fully -- the first plugin with a Windows service
  launcher. POSIX `install.sh` binstub sources the deployed `resolve-runtime.sh`;
  the Windows `.ps1` binstub dot-sources the canonical `resolve-runtime.ps1` (the
  `.cmd` delegates to it); and crucially the **at-logon scheduled task**
  (`Register-AgentVaultTask` -> `conhost --headless "$taskPy" -m
  agent_vault.service`) now resolves `$taskPy` via the canonical resolver instead
  of its own inline marker/newest-slot block -- so the daemon binds the same slot
  as the binstub (last-known-good + completeness-aware).
- Guard: `check-runtime-resolution` **17 -> 16** (agent-vault 1 -> 0; baseline rose
  to 17 as concurrent merges surfaced agent-bridge). pwsh parse + shellcheck +
  binstub smoke test green. Bumped agent-vault 0.1.0-dev52 -> dev53.
- NOTE: 3 agent-vault pytest failures (`test_client_discovery` TCP-fallback /
  WSL-ensure, `test_extensions` builtin-fallthrough) are **pre-existing and
  environment-contaminated** -- they fail identically on clean origin/main because
  a live agent-vault service on the dev host is discovered as `discovered-tcp`.
  Unrelated to installer-script changes (pytest never imports install.*); a clean
  room passes.
- Guard false positives found (for the guard-accuracy follow-up): `agent-dispatch
  install.ps1:311` is `<# #>` block-comment prose; `agent-logger install.sh:191/194`
  are install-time `$VENV`-slot health-gate uses (uppercase matches the case-
  insensitive regex); `agent-worktrees preview-picker.{sh,ps1}` are echoed dev
  instructions. The guard skips `#` lines but not `<# #>` blocks -- worth teaching
  it block-comment + echoed-string awareness (or annotate those lines).

### 2026-08-19 - Phase 2 batch 5: guard accuracy (PowerShell block comments)
- `check-runtime-resolution.py` skipped single-line `#` comments but not
  PowerShell `<# .. #>` **block** comments, so a `.venv`/PATH-python path quoted
  inside block-comment *prose* was a false positive (e.g. `agent-dispatch
  install.ps1:311`, a legacy-migration doc line). Added `_strip_ps_block_comments`
  (inline, line-spanning, multi-span) applied to `.ps1/.psm1/.psd1`, plus a unit
  test (`tools/test_check_runtime_resolution.py`). This is on the critical path to
  the final `--strict` flip -- false positives can't remain when the guard blocks.
- Guard: **16 -> 15** (agent-dispatch 2 -> 1; the remaining `install.sh:507`
  binstub is a real violation, migrated in a later batch). Tooling-only; no plugin
  version bump.

### 2026-08-19 - Phase 2 batch 6: agent-codespaces readiness probe
- **agent-codespaces** `readiness-context.{sh,ps1}` decided runtime-READY by
  probing `versions/$ver/{bin,Scripts}/python` and, as a legacy fallback,
  `.venv/{bin,Scripts}/python`. Dropped the retired `.venv` fallback so readiness
  is marker-only (the current-version slot), matching the resolution model. This
  is a probe, not a launcher -- no resolver needed.
- Guard: **15 -> 13** (agent-codespaces 2 -> 0). shellcheck + pwsh parse + 824
  tests green. Bumped agent-codespaces 0.4.0-dev53 -> dev54.
- IMPORTANT triage of the remaining 5 plugins (risk-tiered; some touch LIVE
  services on the dev host):
  - **agent-dispatch** -- its POSIX **systemd** unit `ExecStart=$LINK_PYTHON -m
    agent_dispatch serve` (install.sh:804) launches the LIVE dispatch daemon
    through the `.venv` link (a real violation the guard can't see behind the
    variable), plus 4+ Windows conhost/scheduled-task daemon launchers. Only the
    binstub (install.sh:507) is guard-flagged. Migrating the daemon launchers
    affects a production service + untested Windows daemons -> wants operator
    coordination (ties to windows-launch-hardening #786).
  - **agent-index** -- `ensure-service.sh` picks `.venv/bin/python` else bare
    `python3` for a service; also a live-ish index service. Needs the marker
    resolver + likely operator validation.
  - **agent-logger** -- real: install.ps1:764 POSIX shim; install.sh:191/194 are
    install-time `$VENV`-slot health-gate false positives (annotate). Has a
    scheduled orchestrator.
  - **agent-bridge** -- uses a plain `venv/` dir (not the versioned `.venv`/slot
    model); needs its own investigation before assuming the marker resolver fits.
  - **agent-worktrees** -- bespoke resolver; preview-picker.{sh,ps1} are echoed
    dev-setup instructions (annotate `# runtime-resolution: allow`); bin shim's
    `exec python` PATH fallback + the `.venv`-link retirement are the final phase.

### 2026-08-20 - Phase 2 batch 7: agent-bridge (full, incl. systemd ExecStart)
- Operator approved **push_all** -- proceed through the live-daemon ExecStart
  migrations, Windows daemon launchers, and the final link-retirement/--strict.
- **Key mechanism for daemon ExecStart:** systemd can't source a resolver at
  launch, but at install time `$VENV_DIR` already IS the slot being built and
  activated -- so `ExecStart=$LINK_PYTHON/...` (link) becomes
  `ExecStart=$VENV_DIR/...` (the slot), a direct marker-only path re-written on
  every install/update (the unit tracks the active version; each daemon runs from
  its own immutable slot between updates). No resolver needed in the unit; only
  the persistent binstub resolves dynamically.
- **agent-bridge** (versioned model, link-name `venv`): POSIX binstub now sources
  the deployed `resolve-runtime.sh` -> `$AGENT_RT_PY -m agent_bridge` (was the
  `venv/bin/agent-bridge` console script via the link); `ExecStart` uses the slot
  (`$VENV_DIR/bin/agent-bridge`); Windows `.ps1` binstub dot-sources the canonical
  `resolve-runtime.ps1` (replacing its inline lexicographic resolver), `.cmd`
  delegates to it. The 3 guard-flagged lines were **legacy-venv cleanup** (pruning
  the retired `~/.agent-bridge/venv`, not launching) -> annotated
  `# runtime-resolution: allow`.
- Guard: **13 -> 10** (agent-bridge 3 -> 0). pwsh parse + shellcheck + binstub
  smoke + 1521 tests green. Bumped agent-bridge 0.4.0-dev304 -> dev305.

### 2026-08-20 - Phase 2 batch 8: agent-dispatch (live coordinator + supervisor daemons)
- **agent-dispatch** (the live systemd coordinator + supervisor on the dev host):
  - POSIX binstub sources the deployed `resolve-runtime.sh` -> `-m agent_dispatch`.
  - The coordinator systemd `ExecStart` and BOTH supervisor-launcher exec lines
    (`supervise-service.sh`) now use `$VENV_PYTHON` (the slot) instead of
    `$LINK_PYTHON` (the `.venv` link) -- the slot is baked per install/update.
  - Windows `.ps1` binstub dot-sources the canonical `resolve-runtime.ps1`
    (replacing its inline lexicographic resolver); `.cmd` delegates to the `.ps1`.
  - The Windows conhost `--headless` scheduled-task launchers were left as-is:
    they derive their root from `$LinkDir` only as a STRING for path arithmetic
    and resolve `$_py` via the marker (the baked `$LinkPython` is overwritten), so
    they are already marker-based and link-retirement-safe.
  - Install-time health-gate `$LINK_PYTHON` uses (import checks, manifest) are
    left for the final link-retirement sweep (they work while the link exists).
- Guard: **10 -> 9** (agent-dispatch 1 -> 0). pwsh parse + shellcheck + binstub
  smoke + 1062 tests green. Bumped agent-dispatch 0.1.0-dev189 -> dev190.

### 2026-08-20 - Phase 2 batch 9: agent-index (index service + health probe)
- **agent-index**: POSIX binstub sources the deployed `resolve-runtime.sh`; the
  index-service systemd `ExecStart` uses `$VENV_PYTHON` (slot) instead of
  `$LINK_PYTHON`; `ensure-service.sh`'s health probe resolves the slot python via
  the canonical resolver (was `.venv/bin/python` else PATH `python3`), degrading to
  curl when no slot is resolvable; Windows `.ps1` binstub dot-sources the canonical
  `resolve-runtime.ps1`, `.cmd` delegates to it. The **durable engine venv**
  (`$ENGINE_VENV_PYTHON`, a separate intentional runtime per
  `durable-vs-versioned-runtime`) is untouched and out of scope. Install-time
  `$LINK_PYTHON` health-gate uses left for the final link-retirement sweep.
- Guard: **9 -> 6** (agent-index 3 -> 0). pwsh parse + binstub smoke + 226 tests
  green. Bumped agent-index 0.1.0-dev69 -> dev70. (Pre-existing SC1087 shellcheck
  false positives on pip-extras `$PLUGIN_DIR[store]` syntax are unrelated.)

### 2026-08-20 - Phase 2 batch 10: agent-logger (session-sync timer)
- **agent-logger**: POSIX binstub sources the deployed `resolve-runtime.sh` ->
  `-m agent_logger` (was the `.venv/bin/agent-logger` console script via the link);
  the `session-sync` timer `ExecStart` uses `${VENV}/bin/session-sync` (slot)
  instead of `${LINK_DIR}/...`; the `install.ps1` non-Windows shim + Windows `.ps1`
  binstub adopt the canonical resolver, `.cmd` delegates. The `install.sh:191/194`
  `$VENV`-slot health-gate lines were **false positives** ($VENV IS the slot;
  uppercase matched the case-insensitive regex) -> annotated
  `# runtime-resolution: allow`.
- Fixed a **pre-existing** stale `_build_info.py __version__ = 0.1.1-dev53` (out of
  sync with pyproject dev55 on main; failed `test_version_matches_build_info` on
  clean main) -> synced to dev56 alongside the bump.
- Guard: **6 -> 3** (agent-logger 3 -> 0). pwsh parse + shellcheck + binstub smoke
  + tests green. Bumped agent-logger 0.1.1-dev55 -> dev56. Only **agent-worktrees**
  (bespoke) + the final link-retirement remain.

### 2026-08-20 - Phase 2 batch 11: agent-worktrees -> GUARD AT 0
- **agent-worktrees** (bespoke resolver, plugins[0]): its 3 flagged lines were all
  legitimate non-launches:
  - `preview-picker.{sh,ps1}` -- **echoed dev-setup instructions** that mention a
    `.venv` path. `_EXCLUDE_PATH` already listed `"preview-picker"` but the guard
    only matched it against *line content*, not the *file path*, so it never fired
    (a guard bug). Fixed `check-runtime-resolution.py` to also skip a whole file
    whose path matches `_EXCLUDE_PATH`; added a unit test.
  - `bin/agent-worktrees` `exec python -m agent_worktrees` -- the deliberate
    confined-host **PATH-python last resort** (only reached with
    `AGENT_WORKTREES_NO_SELFPROVISION=1`, after marker/slot resolution +
    self-provision) -> annotated `# runtime-resolution: allow`.
- **`check-runtime-resolution`: 3 -> 0, and `--strict` passes.** Every plugin's
  binstub, service/daemon launcher, and Python caller now resolves the versioned
  interpreter the one uniform marker-only way. Bumped agent-worktrees
  1.5.3-dev559 -> dev560 (+ marketplace metadata 1.7.5-dev586 -> dev587).
  (agent-worktrees full pytest suite has pre-existing sandbox-only flakiness --
  identical on clean main; the marker-only + guard tests pass.)
- Remaining for the effort: flip the guard to `--strict` in CI; and the physical
  `.venv`/`venv` link retirement in `activate()` (its precondition -- "no consumer
  resolves through it" -- still isn't met: install-time health-gates + a couple
  variable-hidden `$LINK_PYTHON` ExecStart lines, e.g. agent-vault:673, still use
  the link, so that is its own careful cross-installer sweep).

### 2026-08-20 - Phase final (a): guard blocking in CI
- Wired `check-runtime-resolution.py --strict` into `.github/workflows/ci.yml`
  (alongside the install-contract / version guards). The migration is now
  **enforced**: any new `.venv`/`venv`-link or PATH-python launch path fails CI.
- Re-assessment of the remaining physical **link retirement** (stop creating the
  `.venv`/`venv` symlink in `activate()`): with every *launcher* now marker-only,
  the link's RedirectionGuard hazard (WinError 448) no longer bites (it only fired
  when something resolved *through* the junction at runtime under a blocked
  context). The link is still read by install-time health-gates (a plain import
  check, not a blocked context) + a couple variable-hidden `$LINK_PYTHON` ExecStart
  lines (e.g. agent-vault:673), so its precondition ("no consumer resolves through
  it") isn't met. Retiring it is now a **cleanliness** step (the correctness win is
  already banked via guard=0 + --strict), and it's a cross-installer sweep touching
  live-service installers -- carried as a focused follow-on.

### 2026-08-20 - Phase final (b1): link retirement -- no-daemon CLIs
- Started the `.venv` symlink retirement (operator: do_it_now). Mechanism per
  installer: pass **`--no-link`** to `versioned_runtime.py activate` -- which both
  REMOVES any existing `.venv`/`venv` symlink and stops creating a new one (it's
  the `link_free` path) -- and repoint the post-activate install-time python uses
  (gc, health-verify, manifest, status) from the link (`$LINK_DIR/bin/python` /
  `$LINK_PYTHON`) to the **slot** (`$VENV_PYTHON`/`$VENV_DIR`). `LINK_DIR` is kept
  ONLY to derive `--link-name '.venv'` so activate/gc can still find + remove a
  pre-existing link. Pre-slot bootstrap python finders (`_bootstrap_python`) keep
  their link check -- it's simply skipped once the link is gone (they degrade to a
  PATH python to run the stdlib-only helper before the slot exists).
- Batch = the 3 no-daemon CLIs: **agent-ssh, agent-mcp, agent-machines** (lowest
  risk -- no service resolves through the link). Guard stays `--strict`-clean;
  shellcheck + tests green. Bumped agent-ssh dev38->39, agent-mcp dev60->61,
  agent-machines dev29->30.

### 2026-08-20 - Phase final (b2): link retirement -- remaining no-daemon plugins
- **agent-containers** + **agent-codespaces** (both no-daemon `.venv-as-symlink`
  CLIs): same retirement pattern -- `activate --no-link` (removes + stops creating
  the link), post-activate gc/manifest/status repointed to the slot, agent-codespaces
  `LINK_PYTHON` re-pointed to the slot so its many owner-status/verify uses follow.
  Guard stays `--strict`-clean; tests green. Bumped agent-containers dev60->61,
  agent-codespaces dev60->61.
- Link retirement done for all 5 no-daemon plugins (ssh, mcp, machines, containers,
  codespaces). Remaining: the 5 daemon/timer plugins (agent-bridge, agent-dispatch,
  agent-index, agent-logger, agent-vault) -- these have systemd/scheduled-task
  ExecStart already on the slot (done in the earlier batches), so their retirement
  is repointing install-time health-gate `$LINK_PYTHON` + `activate --no-link`.

### 2026-08-20 - Phase final (b3): link retirement -- agent-vault + agent-bridge
- **agent-vault**: re-pointed `LINK_PYTHON` -> slot (which fixes the systemd
  `ExecStart=$LINK_PYTHON` at :679 -- the last variable-hidden link launcher --
  and the version-check/verify uses), `activate --no-link`, manifest -> slot. Its
  `_versioned_current`/`_versioned_gc` already fall back to the slot.
- **agent-bridge** (link-name `venv`): `activate --no-link`, manifest -> slot, and
  rewrote `_installed_version` (the downgrade guard) to read the current-version
  marker instead of `$LINK_DIR/bin/python` (it had NO slot fallback, so it would
  have silently bypassed once the link was gone). Its systemd ExecStart was already
  slot-based (batch 7); its gc/current helpers already fall back to the slot.
- Guard stays `--strict`-clean. Bumped agent-vault dev54->55, agent-bridge
  dev312->313. (agent-vault's 3 pre-existing env-contaminated pytest failures --
  live vault service on the dev host -- are unrelated, per batch 4.)
- Remaining link retirement: agent-dispatch, agent-index, agent-logger.

### 2026-08-20 - Phase final (b4): link retirement -- agent-dispatch + agent-index + agent-logger (COMPLETE)
- **agent-dispatch** + **agent-index**: re-pointed `LINK_PYTHON` -> slot (verify +
  WSL-fallback service start/status), `activate --no-link`, manifest -> slot, and
  rewrote `_installed_version` (the downgrade guard) to read the current-version
  marker instead of `$LINK_PYTHON` -- re-pointing it to the NEW slot would have made
  the guard report the version being installed, defeating it. Their systemd/conhost
  ExecStart were already slot-based (batches 8/9); gc/current helpers already fall
  back to the slot.
- **agent-logger**: `activate --no-link`, gc via the slot `$py` (was
  `$LINK_DIR/bin/python`), manifest -> `$VENV`. Also synced the out-of-band
  `_build_info.py __version__` (a 4th, non-triplet version file the bump process
  doesn't touch) to the bump.
- Guard stays `--strict`-clean; agent-dispatch (1062) + agent-index (226) +
  agent-logger tests pass. Bumped dispatch dev193->194, index dev72->73, logger
  dev57->58.

## THE EFFORT IS COMPLETE
All four target goals met:
1. `check-runtime-resolution` reports **0** (and is `--strict` in CI).
2. Every plugin's binstub, service/daemon launcher, and Python caller resolves the
   versioned interpreter the ONE uniform marker-only way (current-version ->
   last-known-good -> newest complete slot).
3. The `.venv`/`venv` **symlink is retired** across all 10 versioned-runtime plugins
   (`activate --no-link` removes any existing link + never creates a new one; every
   install-time consumer resolves the slot directly or via the marker).
4. The guard is **blocking (`--strict`) in CI**.
16 PRs (#793, #796, #798, #799, #800, #801, #804-#809, #850, #852, #855, this).
