# Install Contract

Every plugin in this repo installs its runtime the **same way**. Because the
Copilot CLI marketplace pulls each plugin's payload **independently**, each
plugin's install flow must be **completely self-contained** — there is no
shared install module that gets vendored. Instead, this document is the
reference, and `tools/check-install-contract.py` enforces conformance (run it
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

So the full deploy of a runtime plugin is always two steps: `copilot plugin
update <name>` (refresh the source cache) **then** run the installer from that
source via its install skill. Never hand-copy source into the deployed runtime
dir — that bypasses the venv sync, binstub/SAC handling, `_build_info.py`
stamping, manifest, and service restart (see "What NOT to Do" in
`CONTRIBUTING.md`).

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

### Hard rules

1. **No file-copy of the package** into `~/.<runtime>/lib`. Install via
   `uv pip install <plugin_dir>` (non-editable). Retire any legacy `lib/`.
2. **No `PYTHONPATH` to a `lib/` dir.** A binstub that points `PYTHONPATH` at a
   loose `…/lib` dir and runs `python -m <pkg>` is forbidden — the package must
   be `uv pip install`ed into the venv's site-packages (rule 1), not imported
   off a sidecar path. How the binstub launches differs by OS:
   - **Linux/WSL:** `exec` the venv's console script (`…/.venv/bin/<name>`) — a
     shebang script, no Smart App Control concern.
   - **Windows:** launch `…\.venv\Scripts\python.exe -m <pkg>`, **never** the
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
`agent-codespaces`, and `agent-containers`). `tools/check-install-contract.py`
flags any `install.ps1` that launches the `…\Scripts\<name>.exe` trampoline.

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
`Deploy-Binstub` in `agent-codespaces`, and `Deploy-Binstub` /
`Deploy-GlobalBinstub` (+ static `bin/agent-worktrees.ps1`) in
`agent-worktrees`.

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

The source-kind resolver is the one block tagged for byte-identical replication
across plugins:

```
# === install-contract:v3 source-kind … ===
… Get-SourceKind / _source_kind …
# === end install-contract:v3 source-kind ===
```

## Within-plugin consolidation

A plugin's own `scripts/*` and `src/<pkg>/installer.py` ship together, so they
may share freely. Secondary entry points (e.g. `init.ps1`/`init.sh`) should
delegate to the canonical `install.*` rather than duplicate the deploy logic.

## Enforcement

`tools/check-install-contract.py` verifies, per plugin:
- `uv pip install` is used (no package file-copy),
- no binstub sets `PYTHONPATH=…/lib`,
- no `install.ps1` launches the `…\Scripts\<name>.exe` console-script trampoline
  ([SAC-safe launchers](#sac-safe-launchers-windows)),
- a `schema_version` 3 manifest with a `source` block is written,
- the source-kind resolver is identical across plugins (per language).

Wire it as a `pre-push` hook (see `tools/hooks/pre-push`).
