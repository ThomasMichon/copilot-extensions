"""Guard + behavioral coverage for install.ps1/install.sh's version-activation
`gc` call: it must include a recency floor (`--min-age-days`) AND touch the
just-superseded slot's mtime at the moment of supersession, not just
`--protect-pids` + `--keep <prev>`.

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

**Why `--min-age-days` alone is not enough (review finding on PR #4451):**
`versioned_runtime.py`'s `_slot_age_days` measures a slot's age from its
directory mtime, which is ~= INSTALL time, not time-since-superseded. Most
real versions live for days/weeks between releases, so by the time a slot is
superseded it is almost always already older than any reasonable
`--min-age-days` floor -- the floor alone protects nothing in the realistic
case, only an artificially-fresh install. The actual fix touches the outgoing
`$prev` slot's mtime at the moment of supersession (right before `gc` runs),
so the floor measures what it needs to: time-since-superseded. The
behavioral test below proves both halves: the floor alone is a no-op for an
old slot, and touching the mtime makes it work -- across a realistic
V1 -> V2 -> V3 sequence within one boot.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
_VR_SCRIPT = SCRIPTS / "versioned_runtime.py"

pytestmark = pytest.mark.guard


def _load_versioned_runtime():
    spec = importlib.util.spec_from_file_location("versioned_runtime", _VR_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


vr = _load_versioned_runtime()


def _install(root: Path, version: str, *, age_days: float = 30.0) -> Path:
    """Create versions/<version> as a stand-in slot, backdated by age_days."""
    d = vr.version_dir(root, version)
    d.mkdir(parents=True, exist_ok=True)
    (d / "marker.txt").write_text(version, encoding="utf-8")
    if age_days:
        past = time.time() - age_days * 86400.0
        os.utime(d, (past, past))
    return d


def _touch_now(d: Path) -> None:
    """What install.ps1/.sh's activation now does to the outgoing $prev slot."""
    os.utime(d, None)


# ---------------------------------------------------------------------------
# Structural drift guards: both scripts must carry --min-age-days and the
# mtime-touch fix.
# ---------------------------------------------------------------------------

def test_install_ps1_gc_call_has_min_age_days_floor():
    text = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    assert "'gc', '--protect-pids', '--min-age-days', '0.05'" in text
    assert "LastWriteTime = Get-Date" in text


def test_install_sh_gc_call_has_min_age_days_floor():
    text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    assert "gc --protect-pids --keep \"$prev\" --min-age-days 0.05" in text
    assert "gc --protect-pids --min-age-days 0.05" in text
    assert 'touch "$INSTALL_DIR/versions/$prev"' in text


# ---------------------------------------------------------------------------
# Behavioral regression: --min-age-days alone is a no-op for a realistically
# old slot; touching its mtime at supersession is what actually protects it,
# across a V1 -> V2 -> V3 sequence within one boot.
# ---------------------------------------------------------------------------

def test_min_age_days_alone_does_not_protect_a_long_installed_slot(tmp_path):
    """Proves the review's HIGH-severity finding: the floor measures
    install-time mtime, so an old slot gets zero protection from it the
    moment it's superseded -- exactly the realistic case (most versions live
    for days/weeks before being superseded)."""
    _install(tmp_path, "1.0.0", age_days=30.0)
    _install(tmp_path, "2.0.0", age_days=0.0)
    vr.activate(tmp_path, "2.0.0", link_name=".venv", link_free=True)

    removed = vr.gc(tmp_path, keep=["2.0.0"], protect_pids=False, min_age_days=0.05)
    assert "1.0.0" in removed


def test_touching_superseded_slot_survives_v1_v2_v3_sequence(tmp_path):
    """The actual fix: touching $prev's mtime at each activation lets a plan
    resolved against an old V1 survive TWO chained activations (V1->V2->V3)
    within one boot -- the exact scenario the review asked to cover."""
    # V1 was installed a long time ago (realistic case).
    _install(tmp_path, "1.0.0", age_days=30.0)
    _install(tmp_path, "2.0.0", age_days=0.0)
    vr.activate(tmp_path, "2.0.0", link_name=".venv", link_free=True)

    # install.ps1/.sh touches $prev ("1.0.0") right before this gc, simulating
    # a plan resolved against V1 moments earlier that hasn't launched yet.
    _touch_now(vr.version_dir(tmp_path, "1.0.0"))
    removed = vr.gc(tmp_path, keep=["2.0.0"], protect_pids=False, min_age_days=0.05)
    assert "1.0.0" not in removed

    # Second activation in the same boot: V2 -> V3. V1 is not touched again
    # here (only V2, the new $prev, is) -- prove V1 still survives because
    # its OWN touch above is still well within the 0.05-day floor.
    _install(tmp_path, "3.0.0", age_days=0.0)
    vr.activate(tmp_path, "3.0.0", link_name=".venv", link_free=True)
    _touch_now(vr.version_dir(tmp_path, "2.0.0"))
    removed = vr.gc(tmp_path, keep=["3.0.0"], protect_pids=False, min_age_days=0.05)
    assert "1.0.0" not in removed
    assert "2.0.0" not in removed
