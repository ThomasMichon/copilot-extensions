"""Guard + behavioral coverage for install.ps1/install.sh's version-activation
`gc` call: it must include a recency floor (`--min-age-days`), touch BOTH
tier-1 (`current`) and tier-2 (`last-known-good`) superseded-slot candidates
at the moment of supersession, and do so BEFORE `activate()` runs.

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

**Why `--min-age-days` alone is not enough (review round 1 on PR #4451):**
`versioned_runtime.py`'s `_slot_age_days` measures a slot's age from its
directory mtime, which is ~= INSTALL time, not time-since-superseded. Most
real versions live for days/weeks between releases, so by the time a slot is
superseded it is almost always already older than any reasonable
`--min-age-days` floor. Fixed by touching the outgoing slot's mtime at the
moment of supersession, so the floor measures what it needs to.

**Why touching only `current`'s value is not enough (review round 2):**
`current` reports only the current-version MARKER -- tier 1 of
`resolve-runtime.ps1`/`.sh`'s three-tier resolution. If the marker is
absent/stale at the moment a launch plan actually resolves, that resolver
falls back to `last-known-good` (tier 2) instead, so the slot a pending plan
really pins can differ from what `current` reports here. Fixed by also
reading and touching/keeping `last-known-good` (read BEFORE this activation
overwrites it). Tier 3 (newest slot on a true first-run with neither marker
nor last-known-good present) has no prior slot to protect -- that state means
no version was ever activated before.

**Why the touch must run BEFORE `activate()` (review round 2):** installs run
concurrently by design, so a delayed touch (after activate + status-monitor-
restart + last-known-good write) leaves a window where a concurrent
installer's own gc -- which protects only its OWN `$prev` -- can reap this
`$prev` first.
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
    """What install.ps1/.sh's activation now does to each superseded candidate."""
    os.utime(d, None)


# ---------------------------------------------------------------------------
# Structural drift guards: both scripts must carry --min-age-days, touch both
# tier-1 and tier-2 candidates, and do so before activate().
# ---------------------------------------------------------------------------

def test_install_ps1_gc_call_has_min_age_days_floor():
    text = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    assert "'gc', '--protect-pids', '--min-age-days', '0.05'" in text
    assert "LastWriteTime = Get-Date" in text
    # Tier 2: last-known-good is read (before this activation overwrites it)
    # and included among the touch/keep candidates.
    assert "$prevLkg" in text
    assert "Join-Path $InstallDir 'last-known-good'" in text
    assert "if ($prevLkg -and $prevLkg -ne $prev) { $gcArgs += @('--keep', $prevLkg) }" in text
    # Ordering guard: the touch loop must happen BEFORE activate() runs, not
    # after -- installs run concurrently by design, so a delayed touch leaves
    # a window where a concurrent installer's own gc (protecting only ITS
    # $prev) can reap this $prev/prevLkg first.
    touch_idx = text.index("LastWriteTime = Get-Date")
    activate_idx = text.index("--link-name '.venv' activate $SrcVersion")
    assert touch_idx < activate_idx


def test_install_sh_gc_call_has_min_age_days_floor():
    text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    assert "gc --protect-pids \"${gc_keep_args[@]}\" --min-age-days 0.05" in text
    assert 'touch "$INSTALL_DIR/versions/$prev"' in text
    # Tier 2: last-known-good is read (before this activation overwrites it)
    # and included among the touch/keep candidates.
    assert "prev_lkg=" in text
    assert 'cat "$INSTALL_DIR/last-known-good"' in text
    assert 'touch "$INSTALL_DIR/versions/$prev_lkg"' in text
    assert 'gc_keep_args+=(--keep "$prev_lkg")' in text
    # Ordering guard: same reasoning as the ps1 test.
    touch_idx = text.index('touch "$INSTALL_DIR/versions/$prev"')
    activate_idx = text.index('".venv" activate "$SRC_VERSION" --no-link')
    assert touch_idx < activate_idx


# ---------------------------------------------------------------------------
# Behavioral regression: --min-age-days alone is a no-op for a realistically
# old slot; touching its mtime at supersession is what actually protects it,
# across a V1 -> V2 -> V3 sequence within one boot, AND across a marker-
# absent/stale resolution that falls back to last-known-good.
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


def test_marker_absent_resolution_falls_back_to_last_known_good_and_survives(tmp_path):
    """Proves the review round-2 HIGH-severity finding: when the
    current-version marker is absent/stale at the moment a plan resolves,
    resolve-runtime.ps1/.sh's tier-2 fallback (last-known-good) is what the
    plan actually pins -- so protecting only `current`'s value is not enough.
    Simulates: V1 was last-known-good (marker since went stale/absent, e.g.
    torn during a crash), a plan resolves against V1 via the tier-2 fallback,
    then V2 is installed and activated -- V1 must still survive gc because
    install.ps1/.sh reads and touches/keeps last-known-good too, not just
    `current`."""
    _install(tmp_path, "1.0.0", age_days=30.0)
    _install(tmp_path, "2.0.0", age_days=0.0)

    # V1 is last-known-good, but the marker is absent (simulating the
    # resolver's tier-2 fallback path a real resolve() would have taken).
    (tmp_path / "last-known-good").write_text("1.0.0\n", encoding="utf-8")
    marker = tmp_path / "current-version"
    if marker.exists():
        marker.unlink()

    # install.ps1/.sh's activation reads current (empty, marker absent) AND
    # last-known-good ("1.0.0") as touch/keep candidates, before activating
    # 2.0.0 -- simulate that here.
    _touch_now(vr.version_dir(tmp_path, "1.0.0"))
    vr.activate(tmp_path, "2.0.0", link_name=".venv", link_free=True)

    removed = vr.gc(tmp_path, keep=["2.0.0", "1.0.0"], protect_pids=False, min_age_days=0.05)
    assert "1.0.0" not in removed
