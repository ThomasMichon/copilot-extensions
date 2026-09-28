"""Guard: install.ps1/install.sh's version-activation `gc` call must include a
recency floor (`--min-age-days`), not just `--protect-pids` + `--keep <prev>`.

Root cause this protects against (#4432): `agent_worktrees resolve` bakes the
running interpreter's path into a resolved launch plan (a stored, not-running
path-pinned reference) BEFORE this same install flow's own runtime
self-update/activate/gc sequence can run. `--keep <prev>` only protects the
ONE version that was current immediately before THIS activation; when two
activations chain in a single boot (a plugin/marketplace reinstall
immediately followed by a separate runtime self-update -- an observed, real
sequence), a plan resolved from an EARLIER version falls outside both
`--keep <prev>` (no longer "prev" by the time gc runs) and `--protect-pids`
(the resolving process already exited) and gets reaped mid-flight, breaking
the pending launch with "failed to locate pyvenv.cfg".

`--min-age-days` is the library's own purpose-built backstop for exactly this
class of gap (see `libs/versioned-runtime/versioned_runtime.py`'s own
docstring: "hold a just-superseded slot long enough for a stored (not-
running) pinned reference to age out"), deliberately defaulted OFF
(`DEFAULT_GC_MIN_AGE_DAYS = 0.0`) as a global default for disk-footprint
reasons (#681) -- so this plugin's own `gc` call must opt in explicitly
rather than relying on (or silently losing) a global default. `agent-mcp`'s
`init.ps1`/`init.sh` already opt in at `0.05` days (~72min) for the
analogous "not-yet-live concurrent install" case; this mirrors that value
for consistency.
"""

from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"


def test_install_ps1_gc_call_has_min_age_days_floor():
    text = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    assert "'gc', '--protect-pids', '--min-age-days', '0.05'" in text


def test_install_sh_gc_call_has_min_age_days_floor():
    text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    assert "gc --protect-pids --keep \"$prev\" --min-age-days 0.05" in text
    assert "gc --protect-pids --min-age-days 0.05" in text
