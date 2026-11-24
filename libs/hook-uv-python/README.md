# hook-uv-python

One small helper that session hooks use to run their Python writer on the
repository's pinned, uv-managed CPython (`.python-version`, today 3.11).

## Why

A `sessionStart` hook runs before any plugin runtime exists, so it cannot use
an installed venv. Earlier hooks searched for `python3`, `python` or `py` on
`PATH`. That chose whatever system Python was present (for example 3.9 in a
CodeSpace) and failed silently when none was present. This helper replaces
that search:

- It finds `uv` on `PATH`, then in `~/.local/bin` and `~/.cargo/bin`.
- It runs `uv run --isolated --no-project --no-config --no-env-file
  --no-python-downloads --managed-python --python <pin> python ...`.
- A session hook never acquires Python. Explicit setup prepares the pinned
  Python: the plugin installer and the bootstrap already run on it. When it is
  missing, uv reports the missing interpreter and the hook keeps its no-op
  result. Run the plugin installer, or `uv python install 3.11`, to prepare it.
- It ignores uv configuration files, `.env` files and an ambient `.venv`, and
  it clears inherited selectors (`UV_PYTHON`, `UV_CONFIG_FILE`, `VIRTUAL_ENV`,
  `PYTHONPATH` and similar) so that they cannot change the interpreter or its
  imports.
- It never falls back to a system Python. When uv is missing, it writes a
  clear message to stderr and returns exit code 127. The hook keeps its own
  no-op result (normally `{}`), so a session still starts.

## Shape

| File | Use |
|------|-----|
| `hook-uv-python.sh` | `. "$helper"`, then `hook_uv_python <python args...>` (bash). The function runs in a subshell, so its environment changes do not leak. |
| `hook-uv-python.ps1` | `. $helper`, then `Invoke-HookUvPython -ArgumentList @(...) [-InputText <stdin>]` (Windows PowerShell 5.1 compatible). It restores the environment after the call and sets `$LASTEXITCODE`. |

Plugins install independently, so each adopting plugin ships its own copy in
`scripts/`. Edit only the canonical files here, then run:

```text
tools/run-python tools/sync-hook-uv-python.py           # copy into adopters
tools/run-python tools/sync-hook-uv-python.py --check   # verify (CI)
```

`--check` fails when a copy is missing or differs, when an adopter's
`write-session-guidance.*` does not call the helper or still searches for a
system Python, or when the pinned version differs from `.python-version`.
To adopt the helper in another plugin, add it to `ADOPTERS` in
`tools/sync-hook-uv-python.py`.

Hooks that run an already-installed runtime venv, or that use the released
payload's `_bootstrap_python` selector, do not need this helper.