# Install Contract

Every plugin in this repo installs its runtime the **same way**. Because the
Copilot CLI marketplace pulls each plugin's payload **independently**, each
plugin's install flow must be **completely self-contained** — there is no
shared install module resolved at install or runtime. Shared primitives such as
`versioned_runtime.py` are **vendored in byte-identically at authoring time**
(from a single canonical source, kept in sync by a repo tool) rather than
resolved from a common location, so each installed plugin still ships everything
it needs. This document is the reference, and
`tools/check-install-contract.py` enforces conformance (run it
manually or wire it as a git `pre-push` hook).

## Plugin update ≠ runtime install

`copilot plugin update <name>` only refreshes the plugin's **marketplace
payload** — the cached source plus any skills/hooks/agents — under
`~/.copilot/installed-plugins/copilot-extensions/<name>`. It does **not** run the
plugin's runtime installer: the venv (`~/.<runtime>/.venv`), the `~/.local/bin`
binstubs, and any long-running service stay on the **old** version. Its
"updated successfully (vX → vY)" message refers to the payload only — the Copilot
CLI emits it and we cannot change it, so a runtime plugin can read "updated"
while its actual runtime has not moved.

Consequence — a rule for every plugin in this repo:

- A plugin that ships **only** skills, hooks, and/or agents needs no installer:
  `copilot plugin update` fully deploys it.
- A plugin that ships a **runtime** — anything beyond skills/hooks/agents: a
  venv, `~/.local/bin` binstubs, or a long-running service — **must** ship both:
  1. `scripts/install.{ps1,sh}` implementing this contract, and
  2. an **install skill** an agent can trigger to deploy/refresh that runtime
     **from the source folder** after a payload update. The skill's job is to run
     the plugin's `scripts/install.* update` from the source dir (the marketplace
     plugin dir, or a local checkout — see
     [Source = where the installer runs from](#source--where-the-installer-runs-from-no-flag)).
     Existing examples: `copilot-extensions-setup` (agent-worktrees +
     agent-bridge), `codespaces-setup` (agent-codespaces), `containers-fleet`
     (agent-containers).

So the full deploy of a runtime plugin is two steps — a payload refresh **then**
its runtime installer — but **you do not run them per plugin by hand.** The
unified **`<repo> update`** (`agent-worktrees update`) performs BOTH for **every**
registered plugin at once: it refreshes each payload (the `copilot plugin
update` step, invoked for you) and then runs each runtime's installer + cutover,
and fast-forwards the anchor checkouts. This contract exists precisely so
`update` can orchestrate that self-contained per-plugin flow uniformly; the
per-plugin install skills below are its internals (and a local-testing /
recovery path). Never hand-copy source into the deployed runtime dir — that
bypasses the venv sync, binstub/SAC handling, `_build_info.py` stamping,
manifest, and service restart (see "What NOT to Do" and "Deploying: one command
— `<repo> update`" in `CONTRIBUTING.md`).

> **Runtime reconcile is version-keyed; `--force` overrides it.** `update` runs a
> plugin's runtime installer only when its deployed runtime version differs from
> the freshly-refreshed payload version (an equal version is assumed current and
> skipped for speed). `--force` re-runs every enabled plugin's runtime installer
> regardless — the escape hatch for a same-version content drift (a dev checkout,
> or a marketplace artifact whose version stamp lagged the code). This covers
> **every** enabled runtime plugin, not just agent-worktrees and the
> `modules.json` services, so a runtime like agent-codespaces can no longer have
> its payload refreshed while its venv silently keeps serving stale code
> (dotfiles #1025). The per-PR **version-bump guard** (`check-version-bump.py`)
> makes the same-version-drift case rare in the first place, so `--force` stays a
> last resort.

### What the marketplace vendors (copied vs loaded)

`copilot plugin update` copies the **entire git-tracked plugin folder** — the
`source:` path in `.github/plugin/marketplace.json` — into
`~/.copilot/installed-plugins/copilot-extensions/<name>/`, **not** just the
skill/agent subfolders. Everything committed under the plugin dir travels:
`skills/`, `src/`, `scripts/`, `docs/`, `tests/`, `bin/`, `extensions/`,
`plugin.json`, `pyproject.toml`, `README.md`, … The **only** exclusions are the
gitignored build/cache artifacts (`.venv/`, `build/`, `uv.lock`,
`.pytest_cache/`, `.ruff_cache/`). (Verify on any machine: compare a plugin's
repo folder to its installed dir — a runtime plugin's `docs/` and `tests/` are
present in the install even though neither is needed to run it.)

Two consequences worth separating:

- **Copied ≠ loaded.** The whole tree lands on disk, but `plugin.json` governs
  what the CLI **loads into a session**: the declared `skills` paths, `hooks`,
  and any auto-discovered `extensions/`. A plugin's `docs/` (and `tests/`) ride
  along in the payload but are **reference material** — read by an agent or user
  who navigates to them (in a checkout or at the install path), not injected into
  session context. The runtime-operative content an agent actually loads is the
  plugin's `skills/`.
- **A plugin carries its own copy; version-bump by where the file lives.** Each
  payload is vendored **independently and self-contained** — a plugin must not
  reference another plugin's files or a repo-root path at runtime; put anything it
  needs inside its own folder. It follows that:
  - a file **inside a plugin folder** (including that plugin's `docs/`) **ships in
    its payload**, so changing it **requires that plugin's version bump** (the
    marketplace detects updates by version — an unbumped change is silently
    skipped);
  - a **repo-root `docs/`** file (this contract, `harness-runbook.md`,
    `architecture.md`, `plans/`) is **not** part of any plugin payload — it is
    fetched by URL or read in a checkout — so changing it needs **no** version
    bump.

### Automatic reconciliation at launch (`runtimeScope`)

`agent-worktrees` closes this gap automatically for **repo-adopted** plugins.
When a session launches in a repo whose `.github/copilot/settings.json`
`enabledPlugins` enables `<name>@copilot-extensions`, the launcher runs
`agent-worktrees reconcile-plugins`, which:

1. ensures each enabled plugin's **payload** is installed (and refreshes it on a
   throttled cadence), and
2. ensures its **runtime** matches the installed payload version — comparing the
   payload `plugin.json` `version` against the runtime
   `~/.<name>/deploy-manifest.json` `source.version`, and running the plugin's
   own `scripts/install.* update` (or `init.*`) only on drift.

A plugin declares whether — and where — its runtime should be reconciled via a
**`runtimeScope`** field in its `plugin.json`:

| `runtimeScope` | Meaning |
|----------------|---------|
| `none` | The reconciler never touches the runtime. Use for skills/agents/hooks-only plugins, **plugin-contributed extensions** whose payload *is* the runtime (e.g. `context-handoff`), **and** plugins whose runtime is managed out-of-band (per-machine, by hand). |
| `universal` | Reconcile the runtime on **every** machine (a non-Python runtime that every machine needs and that deploys outside the plugin payload). |
| `machine-gated` | Reconcile the runtime only on machines in the plugin's allowed set (e.g. `agent-bridge`, `agent-codespaces`, `agent-containers`, `agent-mcp`). |

The machine set for `machine-gated` plugins is **not** hard-coded in the plugin:
the reconciler reads it from a **control-harness gate manifest** — by default a
file named `external-repos.yaml` (`repos.*.services[].{name, deploy_machines}`),
resolved from the current repo first and then, if a gate anchor repo is
configured, from that repo via the repos registry. Both knobs are **pluggable**
via environment variables — `WORKTREE_GATE_MANIFEST` (the filename) and
`WORKTREE_GATE_ANCHOR` (the anchor repo name) — so any control harness can point
the gate at its own manifest; the defaults (`external-repos.yaml`, anchor
`aperture-labs`) match this repo's reference facility. With no gate info
available, a `machine-gated` runtime is **skipped** (safe default — never
auto-install a machine-specific runtime where the policy is unknown).
Reconciliation is local and version-keyed, so a re-launch with no version change
does ~no work; the network payload refresh is throttled via a small cache under
`~/.agent-worktrees/`. Opt out per session with `WORKTREE_NO_RECONCILE=1`.

> **Headless caveat.** This runs only on **interactive** launches.
> `copilot -p --autopilot` (an autopilot/headless harness) does not merge repo
> `enabledPlugins`, so harness machines still need required runtimes installed
> globally, out-of-band.

> **Windows caveat — prefer a local checkout.** When a plugin is loaded in the
> running Copilot session, `copilot plugin update <name>` can fail outright on
> Windows: the live CLI holds handles inside
> `~/.copilot/installed-plugins/copilot-extensions/<name>`, so the update's
> rmdir hits `EBUSY` and not even the payload refreshes. The reliable path is to
> run the plugin's `scripts/install.* update` from a **local checkout** of this
> repo (which flips `source.kind` to `local`); the install skill should drive
> that. A future wired-in install hook would have to tolerate this loaded-plugin
> lock (e.g. an out-of-process staged swap).

## The flow (all plugins)

```
uv venv  ~/.<runtime>/.venv
uv pip install [--reinstall-package <pkg>] "<plugin_dir>"   # NON-editable
            └─ resolves deps from pyproject.toml (pyyaml, ssh-manager, …)
stamp _build_info.py  →  INTO the installed site-packages copy (after install)
binstub  ~/.local/bin/<name>.ps1 (+ .cmd fallback)  →  signed venv python -m
write deploy-manifest.json  (schema_version 3, source block, atomic temp+move)
```

> **Immutable-versioned layout (dotfiles #581 — the default; enforced).** The
> `~/.<runtime>/.venv` above is really an **immutable, versioned** layout: each
> version is built into `~/.<runtime>/versions/<version>/`, and the active one is
> published by a `~/.<runtime>/current-version` **plain-text marker file**. The
> installer re-points its **version-pinned binstubs** (+ scheduled task / deploy
> manifest) straight at `versions/<version>/…`, so `update` builds a new version
> dir and **republishes the marker** instead of mutating a live venv (binding
> invariant *Runtime installs are immutable and versioned* — see
> [`patterns/README.md`](patterns/README.md)). **On Windows there is no junction at
> all** — a reparse point was blocked by RedirectionGuard (WinError 448) on managed
> devices, so the marker + pinned binstubs replace it; **on POSIX** the marker is
> authoritative and a `venv`/`.venv` **symlink** (not a reparse point) still
> publishes the active slot as the stable runtime-facing path. This is the
> **only** layout for every Python runtime plugin, on both OSes: the installers
> are **always versioned** and the `AGENT_<NAME>_VERSIONED` /
> `COPILOT_EXT_NO_VERSIONED` opt-out (and the legacy in-place-venv fork it
> selected) are **retired**.
> `tools/check-install-contract.py` (run in
> CI) **enforces** it: every runtime ships a `versioned_runtime.py` primitive
> **byte-identical to the canonical source**
> (`libs/versioned-runtime/versioned_runtime.py`, vendored in by
> `tools/sync-versioned-runtime.py`) and wires the `install-contract:v3
> versioned-venv` block. The venv is still `uv pip install`ed exactly as below;
> only *where* it lives and *how* it is activated change. The per-plugin installer
> points its venv/binstub/manifest at the slot the primitive returns.

### Hard rules

1. **No file-copy of the package** into `~/.<runtime>/lib`. Install via
   `uv pip install <plugin_dir>` (non-editable). Retire any legacy `lib/`.
2. **No `PYTHONPATH` to a `lib/` dir.** A binstub that points `PYTHONPATH` at a
   loose `…/lib` dir and runs `python -m <pkg>` is forbidden — the package must
   be `uv pip install`ed into the venv's site-packages (rule 1), not imported
   off a sidecar path. How the binstub launches differs by OS (both resolve the
   active slot from the `current-version` marker — Windows directly, POSIX through
   the `venv`/`.venv` symlink that publishes it):
   - **Linux/WSL:** `exec` the active slot's console script
     (`…/versions/<v>/bin/<name>`, reachable via the `.venv` symlink) — a shebang
     script, no Smart App Control concern.
   - **Windows:** launch `…\versions\<v>\Scripts\python.exe -m <pkg>` (resolved via
     the `current-version` marker — there is no `.venv` junction), **never** the
     generated `…\Scripts\<name>.exe` console-script trampoline. That trampoline
     is an unsigned, zero-reputation PE that Smart App Control blocks
     (CodeIntegrity 3077). See [SAC-safe launchers (Windows)](#sac-safe-launchers-windows).
     The binstub itself is a `.ps1` (primary) plus a `.cmd` (fallback) — see
     [Binstub format (Windows)](#binstub-format-windows).
3. **Deps come from `pyproject.toml`**, not ad-hoc `uv pip install pyyaml`.
   Sibling libs not on PyPI (e.g. `ssh-manager`) are `uv pip install`ed from
   their vendored dir **before** the plugin.
4. **`readme` in `pyproject.toml` must be a path inside the plugin dir**
   (`README.md`), never `../../README.md` — the latter breaks `uv pip install`
   in the marketplace-vendored layout.
5. **`_build_info.py` is stamped into the installed site-packages copy** after
   install. Resolve the dir with `PYTHONPATH` cleared so a stale `…/lib` can't
   shadow it; retire `lib/` **before** the probe.
6. **Create the venv before installing the package** (the install targets it).
7. **The installer process is never elevated, and never self-elevates as a
   whole.** An installer must run to a useful result as the ordinary user. It may
   *skip* steps that genuinely require admin (warning with remediation), but it
   must not gate the whole run behind, or silently escalate to, Administrator.
   Re-running the entire script under UAC (the legacy `Invoke-SelfElevated`
   pattern) is retired.
8. **Scheduled-task registration is user-mode, idempotent, and update-in-place;
   only a dedicated task-scheduling action may (opt-in) elevate that one step.**
   - Default is a **per-user auto-run** task (`AtLogon`, `LogonType Interactive`,
     `RunLevel Limited`) — no elevation. Flows that *require* elevation (e.g. an
     `AtStartup` "run whether logged on or not" task under SYSTEM/stored creds)
     are **opt-in only**.
   - Be **idempotent**: if the task already matches the desired shape, do nothing.
   - Prefer **`Set-ScheduledTask`** to update an existing task in place — it
     modifies a task the user already owns *without admin*, unlike
     `Register-ScheduledTask -Force` (which some locked-down machines refuse to
     non-admins even for a per-user task).
   - If a **missing** task cannot be created without admin, **do not elevate the
     installer** — warn with remediation and continue; any existing task keeps
     running.
   - The **only** place elevation may happen is a dedicated task-only action
     (agent-index: `install.ps1 register-tasks -AllowTaskElevation`) that
     self-elevates **only that step**, never install/update.
9. **The default service lifecycle is user-mode; scheduled tasks are an opt-in
   advanced tier and are never in the start/stop path.** Start/stop/keep-alive of
   the daemon must not depend on any component that can require elevation.
   - **Default (no elevation, no task):** the daemon runs as a plain user process,
     started and kept alive by a user-mode *ensure* — health-gate the live routing
     endpoint; if unhealthy, start it via the user-mode CLI (agent-index:
     `agent-index deploy` on Windows; `systemctl --user` / nohup on POSIX). The
     installer's `install` / `update` / `start`, **and a `sessionStart` `ensure`
     hook**, all funnel through this same idempotent ensure. A Copilot session
     therefore guarantees the daemon, so it survives reboots **without** an
     AtLogon scheduled task.
   - **Windows:** the default path must NOT call `Install-Service` /
     `Register-ScheduledTask` / `Start-ScheduledTask`. Those live only behind the
     opt-in `register-tasks` action (rule 8). POSIX `systemd --user` units are
     already user-mode and remain the POSIX default.
   - **Never** gate *starting* the service on a step that may require elevation
     (e.g. don't `if (Register-Task) { Start }` — a locked-down box that can't
     create the task would then never start the daemon). Registration and
     start are independent; start is always user-mode.

## Update-flow robustness — self-stage, watchdog, completion markers (#935)

The runtime is (re)installed by **four** cooperating mechanisms, and the danger is
that they collide:

1. **`<repo> update`** updates every enabled plugin's payload (`copilot plugin
   update`) and re-runs each runtime installer.
2. **Worktree launch** runs `<repo> update` as a pre-flight.
3. **Copilot auto-update** refreshes the user/repo `enabledPlugins` payloads on its
   own cadence — it must *replace* `installed-plugins/<mkt>/<plugin>` on disk.
4. **Session-start hooks** (`bootstrap-check`) kick each plugin's installer to
   reconcile a drifted runtime.

(3)+(4) are meant to make (1)+(2) *unnecessary* day-to-day; running (1)/(2)
*guarantees* the payloads and runtimes don't drift apart. Two failure classes made
this fragile — **file locks** (in 3) and **stall-outs** (in 4). Three mechanisms fix
them; every Python runtime installer carries them (byte-identically where noted),
enforced by `tools/check-install-contract.py`.

### Self-stage — the installer never holds the singleton payload (fixes file locks)

An installer reads its own payload (`src/`, `libs/`, `pyproject.toml`) to build the
venv, so **while it runs it holds the singleton `installed-plugins/<mkt>/<plugin>`
dir open** (CWD/handles). On Windows a concurrent Copilot auto-update (3) then fails
to replace that dir with **os error 32** ("used by another process") — the payload
freezes at the old version and reconcile keeps reverting the runtime toward it (the
dev214↔dev230 drift saga).

Fix — the **`install-contract:v4 self-stage`** prologue (byte-identical, at each
installer entry): when running from the marketplace payload, copy the **whole**
payload into a **unique per-invocation** dir `~/.<name>/.install-stage/<ts>-<pid>/`
and **re-exec from there**. The singleton payload is then touched only for the fast
copy, never the whole (possibly-wedged) install. Guards:
- `COPILOT_PLUGIN_INSTALL_STAGED=1` prevents a re-exec loop; the stage path (not under
  `installed-plugins`) is a second guard.
- `COPILOT_PLUGIN_STAGED_FROM=<real payload path>` preserves marketplace detection
  (see [Source](#source--where-the-installer-runs-from-no-flag)).
- **Reap is pid-guarded:** a sibling stage dir is removed only if its owner pid (the
  `<ts>-<pid>` suffix) is **dead** — a concurrent or stalled installer's dir is never
  touched. *A stalled install must never block another copy.*

### Watchdog — a stalled install self-terminates (fixes stall-outs)

The session-start hook (4) launches the installer **detached with no deadline**;
before this, a wedged `uv pip install` (no network timeout) leaked forever — orphans
piled up one-per-session and (pre-self-stage) locked the payload.

Fix — the **self-stage parent doubles as a watchdog**: already outside the payload and
wrapping the child's whole lifetime, it enforces a deadline and, on expiry, kills the
**whole tree** via `taskkill /T` (Windows' subprocess-kill leaves grandchildren),
logs `WATCHDOG-KILL` to `~/.<name>/reconcile.err.log`, and exits `124`. Deadline:
`<NAME>_INSTALL_DEADLINE_SEC` → `COPILOT_PLUGIN_INSTALL_DEADLINE_SEC` → **480s**
default; `<=0` disables. Secondary: `UV_HTTP_TIMEOUT` bounds each uv request so a hung
download degrades to "failed + retryable" rather than wedging. Backstop:
`bootstrap-check`'s single-flight + stale-reap.

### Completion marker — no corpse reuse, clean retry

Completion was inferred only from the runtime-root `deploy-manifest.json` /
`running-version.json` + the active-version marker. A killed/crashed build left a
**half-built `versions/<v>` slot** on disk that the next `uv venv --allow-existing`
could silently reuse.

Fix — a per-slot **`.install-complete.json`** marker, written **atomically right after
the slot passes its isolated health gate** (so "marker present" == "healthy, complete
build"), owned by the shared `versioned_runtime.py`:
- `mark-complete <v>` / `is-complete <v>` (+ optional `--payload-hash` to force a
  rebuild when a dev-checkout changed the payload without bumping the version).
- `slot <v> --clean-incomplete` tosses an **incomplete** slot before building (never
  the current/active slot); `toss-incomplete` / `gc --toss-incomplete` reap markerless
  non-current slots.

Because **activate (marker publish + binstub re-point) runs only after the health
gate + marker**, a
watchdog-killed build never becomes the live version — the old daemon keeps serving,
and the markerless corpse is tossed + rebuilt on the next run (automatic retry).

> **Test it:** `tools/test-install-flow.ps1 -Plugin <name>` (Windows) and
> `bash tools/test-install-flow.sh --plugin <name>` (Linux/WSL) are the turn-key
> mini end-to-ends that assert all of the above in an isolated sandbox (STAGED,
> NOT-IN-PAYLOAD, PAYLOAD-FREE-during-install, MARKETPLACE-preserved, NO-COLLISION,
> WATCHDOG whole-tree kill, MARKER/TOSS, NO-ORPHANS, BOUNDED) — via a
> `COPILOT_PLUGIN_INSTALL_SMOKE` seam, without a heavy venv build.

### POSIX parity (`.sh`)

The `.sh` installers carry the **same** `install-contract:v4` blocks as `.ps1`,
byte-identical per language and enforced by `tools/check-install-contract.py`
(self-stage prologue, smoke seam, and the `_source_kind` env-fallback that honors
`COPILOT_PLUGIN_STAGED_FROM`). Two POSIX-specific choices mirror the Windows
behavior:

- **Watchdog kill uses the process group, not `taskkill /T`.** The staging parent
  launches the staged child under bash **job control** (`set -m`), giving it its
  own process group; on deadline it kills the **whole group** with
  `kill -- -<pgid>` (the POSIX twin of `taskkill /T`), so grandchildren die too.
- **Exit-code propagation via `wait`, not `setsid -w`.** Job control's `wait`
  returns the staged child's real exit code, so a genuinely failed install
  surfaces non-zero. `setsid -w` is deliberately avoided — on some util-linux
  builds it swallows the child's exit code (returns 0), which would mask a
  failed install.

The completion-marker primitive (`versioned_runtime.py`) is already
cross-platform, and **all 11 `.sh` installers are fully wired** (self-stage +
watchdog + smoke + `_source_kind` + toss-before-build + mark-after-health-gate).
The marker/toss body-wiring is per-plugin, not mechanical: the link-name is
derived as `basename "$LINK_DIR"` (`venv` vs `.venv`), and mark-complete is
placed relative to each plugin's health gate — on its own line before
`_versioned_activate` for the external-gate (daemon) plugins, or inside
`_versioned_activate` (after the gate) for the CLI plugins that fold the gate in.
`agent-bridge/scripts/install.sh` is the reference.

## SAC-safe launchers (Windows)

Smart App Control (SAC), enforcing on Windows 11, hard-blocks two unsigned,
zero-reputation binaries that a default `uv` install produces:

1. the uv-managed venv `python.exe`, and
2. the per-entry-point console-script trampoline `…\Scripts\<name>.exe`.

Both fail with `CodeIntegrity` event **3077** ("did not meet the Enterprise
signing level requirements"). Because these plugins ship publicly on GitHub, the
fix must **not** require downloaders to disable SAC.

### Rules (Windows `install.ps1` / `init.ps1`)

1. **Build the venv from a PSF-signed base Python via `--copies`.** Resolve a
   signed interpreter (`py -3.x` whose `Get-AuthenticodeSignature` reports
   `Valid`) and run `& $signedBase -m venv --copies $VenvDir`. `--copies`
   embeds a real copy of the signed `python.exe` in the venv (Authenticode
   survives the copy), which SAC trusts. Rebuild an existing **unsigned** venv
   the same way. Fall back to `uv venv` (unsigned) only when no signed Python
   exists, with a loud warning — those hosts stay SAC-blocked until a signed
   Python (python.org / Store) is installed.
2. **Launch via the signed venv python, never the trampoline.** Every launch
   path — the `~/.local/bin/<name>.cmd` binstub, the service start script, the
   scheduled-task action, and any in-installer `version` / status probe — must
   invoke `"<venv>\Scripts\python.exe" -m <package>`. Never invoke
   `…\Scripts\<name>.exe`.
3. **The legacy `<name>.exe` may still be *matched* for migration** (e.g. a
   `Get-RunningProcess` PID/path lookup that also recognizes the old trampoline
   process), but it must never be *launched*.
4. **Reputable unsigned wheel `.pyd`s** (pydantic_core, etc.) pass SAC via ISG
   reputation — only the locally generated, zero-reputation trampoline and the
   uv-managed python are blocked, so dependencies need no signing.
5. **Strip the trampolines after install.** `uv pip install` regenerates the
   `…\Scripts\<name>.exe` console scripts every time, so each installer removes
   them (every `agent-*.exe`, incl. sibling provider trampolines pulled into a
   shared venv) right after the package install via the shared
   `Remove-ConsoleTrampolines` helper (`# install-contract:v3 strip-trampolines`
   block, byte-identical across plugins). Nothing launches them — binstubs,
   services, and probes all use `python.exe -m <pkg>` — so removal is safe and
   keeps the venv free of SAC-blocked PEs. POSIX console scripts are the
   sanctioned launch path and are **not** stripped.

Reference implementation: `Get-SignedBasePython` + `New-SignedVenv` and the
`"$VenvPython" -m <pkg>` launchers in
`plugins/agent-bridge/scripts/install.ps1` (mirrored in `agent-worktrees`,
`agent-codespaces`, and — in their `init.ps1` — `agent-containers` and
`agent-mcp`). `tools/check-install-contract.py`
flags any `install.ps1` that launches the `…\Scripts\<name>.exe` trampoline.

> **Enforcement scope:** `check-install-contract.py` enforces each plugin's
> *canonical* runtime entrypoint — `install.ps1`/`install.sh` when present,
> otherwise `init.ps1`/`init.sh`. `agent-containers` and `agent-mcp` ship only
> `init.*`, so they are checked there; plugins with both (`agent-codespaces`,
> `agent-worktrees`) have `init.*` delegate to `install.*`, so only `install.*`
> is enforced. The SAC trampoline rule applies to whichever `.ps1` is the
> canonical entrypoint.

## Binstub format (Windows)

The SAC rule above fixes *what the binstub launches* (`python.exe -m <pkg>`).
This rule fixes *what the binstub is*. Each Windows entry point in
`~/.local/bin` is deployed as **two files**:

- **`<name>.ps1` — the primary.** PowerShell's command resolution ranks an
  ExternalScript (`.ps1`) above an Application (`.cmd`/`.exe`) **within the same
  directory**, so a bare `<name>` typed (or spawned) in pwsh resolves to the
  `.ps1` — no `PATHEXT` change required. The body forwards the argument array
  verbatim with `@args`:

  ```powershell
  $env:PYTHONUTF8 = '1'
  & "<venv>\Scripts\python.exe" -m <pkg> @args
  exit $LASTEXITCODE
  ```

- **`<name>.cmd` — the fallback.** Kept for non-PowerShell callers (cmd.exe, a
  bare `CreateProcess`/`PATHEXT` spawn, `cmd /c` Windows Terminal profiles, ssh
  launchers) that cannot resolve a `.ps1`. Forwards with `%*`.

### Why `.ps1` is primary, not `.cmd`

A `.cmd` forwarding `%*` **re-tokenizes** the command line through cmd.exe's
parser, which mangles — and can *inject* — shell metacharacters. For a payload
like `agent-bridge send peer 'echo "x" && ls | grep $HOME'`, cmd strips the
quotes, splits the argument, and executes `ls`/`grep` as separate commands
(operator injection). `setlocal enabledelayedexpansion` + `!args!` does **not**
fix this (embedded `"` still breaks it, and `!` is corrupted as the expansion
sigil). PowerShell hands the script an already-parsed argv array and `@args`
splats it to the child with correct Windows quoting — one parse, no injection.
Validated against quotes, `&&`, `|`, `;`, `!`, `$`, and globs. This matters
most for `agent-bridge send … '<cmd>'` and `agent-codespaces ssh … --remote-cmd
'<cmd>'`, whose payloads are themselves shell commands.

### The earlier-PATH-shadow gotcha

PowerShell prefers `.ps1` over `.cmd` **only within one directory**. Resolution
is still PATH-order first: a same-named stub in an *earlier* PATH directory
wins regardless of extension. A stray `pip install`'d `<name>.exe` in a system
`Python3xx\Scripts` that precedes `~/.local/bin` will shadow the binstub (both
`.ps1` and `.cmd`) and silently re-introduce SAC blocks and arg mangling. When
diagnosing, check `Get-Command <name> -All` resolves to `~/.local/bin` first;
if not, uninstall the shadowing package from the offending Python.

### Rules

1. Deploy **both** `<name>.ps1` and `<name>.cmd`; the `.ps1` body uses `@args`,
   the `.cmd` body uses `%*`. Both launch `python.exe -m <pkg>` (SAC rule).
2. Write the `.ps1` **after** (or alongside) the `.cmd` in the same dir so it is
   the preferred resolution; never deploy a `.cmd` without its `.ps1` sibling.
3. `uninstall` removes **both** files; `status` reports the `.ps1` as primary
   and warns if only the `.cmd` is present.

Reference: `Write-Binstubs` in `plugins/agent-bridge/scripts/install.ps1`,
`Deploy-Binstub` in `agent-codespaces`, `Deploy-Binstub` /
`Deploy-GlobalBinstub` (+ static `bin/agent-worktrees.ps1`) in
`agent-worktrees`, and the `init.ps1` binstub deployers in `agent-containers`
and `agent-mcp`.

## Deploy manifest (schema_version 3)

Written atomically (temp file → move). One shape for all plugins:

```jsonc
{
  "schema_version": 3,
  "service": "<plugin>",
  "deployed_at": "…Z",
  "deployed_by": "<machine>-<platform>",
  "source": {
    "kind": "local" | "marketplace",
    "path": "<plugin dir>",
    "repo": "copilot-extensions",
    "plugin": "<plugin>",
    "version": "<pyproject version>",
    "commit": "<short>|null",   // local only
    "branch": "<branch>|null",  // local only
    "dirty": false              // local only
  },
  "venv": "<venv dir>",
  "runtime": "python"
}
```

## Source = where the installer runs from (no flag)

The footprint's source is **inferred from the installer's own location**, never
a flag:

- plugin dir under `~/.copilot/installed-plugins/copilot-extensions/…`
  → `source.kind = marketplace`
- anything else (a git checkout) → `source.kind = local`

Run the installer from the marketplace plugin dir → marketplace takes over;
`update` keeps pulling from marketplace. Run it from a local checkout → local
takes over. Switching is an explicit act: invoke the installer from the other
location. `status` always reports the current `source.kind`.

> **Self-stage caveat (#935).** When the `install-contract:v4 self-stage` prologue
> re-execs out of the marketplace payload, the installer's *live* path is a
> throwaway `~/.<name>/.install-stage/…` dir — which would read as `local`. The
> resolver therefore honors **`COPILOT_PLUGIN_STAGED_FROM`** (the real payload path
> the prologue recorded) so a staged marketplace install still resolves to
> `marketplace`. Keep this env-fallback in the byte-identical resolver.

The source-kind resolver is the one block tagged for byte-identical replication
across plugins:

```
# === install-contract:v3 source-kind … ===
… Get-SourceKind / _source_kind …
# === end install-contract:v3 source-kind ===
```

## Non-Python plugins (extensions and payload runtimes)

Most plugins here ship a **Python** runtime (a venv + package + binstubs). Some
ship no Python at all (**no `pyproject.toml`**). The Python-specific rules above
— `uv pip install`, the venv build, SAC-safe venv launchers, `_build_info.py`
stamping, `~/.local/bin` binstubs — **do not apply** to these. There are two
shapes:

### Plugin-contributed extension (preferred — no install scripts)

A Copilot CLI session extension can be shipped **inside the plugin** and
discovered directly by the CLI, with **no install step**. Place each extension
at `extensions/<name>/extension.{mjs,cjs,js}` in the plugin; the CLI scans an
**enabled** plugin's `extensions/` dir at session startup and loads it as a
`plugin`-source extension. The canonical example is **context-handoff**
(`plugins/context-handoff/extensions/context-handoff/extension.mjs`).

Such a plugin ships **no `scripts/install.*`**, no deploy manifest, and copies
nothing to `~/.copilot/extensions/`. `copilot plugin update <name>` (or repo
`enabledPlugins` auto-install) is the entire deploy. Two conditions gate
loading, both handled outside the plugin:

- the plugin must be in `enabledPlugins` (a marketplace plugin's `extensions/`
  dir is only scanned when enabled);
- `experimental: true` must be set in `~/.copilot/settings.json` (the CLI gates
  *all* extension loading on it) — ensured by the **agent-worktrees** installer
  (`Ensure-CopilotExperimental`), not by the extension plugin.

Because it ships no install or init scripts, `check-install-contract.py` does not
include it (the checker only scans plugins that have `scripts/install.*` or
`scripts/init.*`).

### Payload runtime with installer (legacy)

The older shape deploys a non-Python artifact **outside** what the CLI can
discover from the plugin dir — so it needs an installer to place the payload and
record a footprint. It is identified structurally by having `scripts/install.*`
but no `pyproject.toml`. Prefer the plugin-contributed-extension shape above for
new extensions; reach for an installer only when the artifact genuinely must
land somewhere the CLI will not scan from the plugin. When an installer is used,
the Python rules still do not apply, but what does:

1. It is still a **runtime** (it deploys beyond what `copilot plugin update`
   does), so it **must** ship `scripts/install.{ps1,sh}` plus an **install
   skill** that runs `install.* update` from the source dir. The two-step deploy
   (payload update → run installer) is unchanged.
2. It **must** write a `schema_version` 3 deploy manifest with a `source` block,
   written atomically (temp+move). `venv` is `null`; `runtime` names the payload
   kind (e.g. `"extension"`); add an `extension_path` (or equivalent) pointing at
   the deployed artifact.
3. It **must** carry the byte-identical `# === install-contract:v3 source-kind`
   resolver block, exactly as the Python plugins do — `update` still re-installs
   from whatever footprint (marketplace vs local) the installer was run from.
4. Output stays ASCII unless the script establishes a UTF-8 context (the
   installers here use `[OK]` / `[WARN]` markers).

`check-install-contract.py` scans plugins that ship a runtime entrypoint —
`scripts/install.*` or, failing that, `scripts/init.*`. Plugin-contributed-extension
plugins (no install/init scripts) are not included at all. For a
payload-runtime-with-installer plugin it detects the absent `pyproject.toml` and
skips only the `uv pip install` check; the manifest and resolver checks are
still enforced.

## Within-plugin consolidation

A plugin's own `scripts/*` and `src/<pkg>/installer.py` ship together, so they
may share freely. Secondary entry points (e.g. `init.ps1`/`init.sh`) should
delegate to the canonical `install.*` rather than duplicate the deploy logic.

## Runtime self-reconcile (session-start hook)

A Python runtime plugin installs a `~/.local/bin/<name>` binstub, but a
`copilot plugin update` refreshes only the cached payload — it does **not**
redeploy the binstub/venv. Without a nudge, the runtime silently lags the payload
until someone re-runs the installer by hand.

So every Python runtime plugin **self-reconciles at session start**: it declares
a `sessionStart` hook that re-runs its own installer **only when the deployed
version drifts** from the payload.

- `plugin.json` sets `"hooks": "hooks.json"`.
- `hooks.json` `hooks.sessionStart` runs the plugin's `scripts/bootstrap-check.{ps1,sh}`
  — either the copy the installer deploys to `~/.<plugin>/bin/`, or (the
  self-locating variant) the one shipped in the plugin payload's `scripts/` dir.
- `scripts/bootstrap-check.{ps1,sh}` is a **version-gated reconcile**: it compares
  the deployed version (`~/.<plugin>/deploy-manifest.json` → `source.version`) to
  the payload (`pyproject.toml`) and, on drift (or a missing venv), re-runs the
  plugin's canonical installer (`init.*`, or `install.* install`) **in the
  background** — the atomic versioned-venv swap keeps concurrent use safe, and
  backgrounding keeps session start non-blocking.

This reconciles the **tool**, never machine state or config. First install remains
the one-time setup step; the hook only keeps an installed runtime current.

## Enforcement

`tools/check-install-contract.py` verifies, per plugin, against its canonical
runtime entrypoint (`install.*` if present, else `init.*`):
- `uv pip install` is used (no package file-copy) — **skipped for
  payload-runtime plugins** (no `pyproject.toml`; see
  [Non-Python plugins](#non-python-plugins-extensions-and-payload-runtimes)),
- no binstub sets `PYTHONPATH=…/lib`,
- no canonical `.ps1` entrypoint launches the `…\Scripts\<name>.exe`
  console-script trampoline ([SAC-safe launchers](#sac-safe-launchers-windows)),
- a `schema_version` 3 manifest with a `source` block is written,
- the source-kind resolver is identical across plugins (per language),
- each Python runtime's `scripts/versioned_runtime.py` is byte-identical to the
  canonical `libs/versioned-runtime/versioned_runtime.py` (edit the canonical and
  run `python tools/sync-versioned-runtime.py`; `--check` verifies in CI/pre-push),
- each Python runtime wires the **session-start reconcile hook** above
  (`plugin.json` `hooks` → a `sessionStart` `bootstrap-check`). Plugins predating
  the invariant are listed in `EXEMPT_SESSION_HOOK` (tracked in dotfiles#779) —
  new runtime plugins must comply, not be added to that set.

Wire it as a `pre-push` hook (see `tools/hooks/pre-push`, which also runs
`tools/check-no-internal-identifiers.py` — a repo-wide guard that fails the push
if any privately-configured internal identifier leaks into the tree; it no-ops
unless a denylist is configured, see the agent-codespaces README "Local
identifier guard").
