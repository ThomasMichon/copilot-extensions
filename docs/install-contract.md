# Install Contract

Every plugin in this repo installs its runtime the **same way**. Because the
Copilot CLI marketplace pulls each plugin's payload **independently**, each
plugin's install flow must be **completely self-contained** — there is no
shared install module that gets vendored. Instead, this document is the
reference, and `tools/check-install-contract.py` enforces conformance (run it
manually or wire it as a git `pre-push` hook).

## The flow (all plugins)

```
uv venv  ~/.<runtime>/.venv
uv pip install [--reinstall-package <pkg>] "<plugin_dir>"   # NON-editable
            └─ resolves deps from pyproject.toml (pyyaml, ssh-manager, …)
stamp _build_info.py  →  INTO the installed site-packages copy (after install)
binstub  ~/.local/bin/<name>(.cmd)  →  the venv console script
write deploy-manifest.json  (schema_version 3, source block, atomic temp+move)
```

### Hard rules

1. **No file-copy of the package** into `~/.<runtime>/lib`. Install via
   `uv pip install <plugin_dir>` (non-editable). Retire any legacy `lib/`.
2. **No `PYTHONPATH`.** Binstubs invoke the venv's generated **console script**
   (`…/.venv/Scripts/<name>.exe` / `…/.venv/bin/<name>`). A binstub that sets
   `PYTHONPATH=…/lib` and runs `python -m <pkg>` is forbidden.
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
- a `schema_version` 3 manifest with a `source` block is written,
- the source-kind resolver is identical across plugins (per language).

Wire it as a `pre-push` hook (see `tools/hooks/pre-push`).
