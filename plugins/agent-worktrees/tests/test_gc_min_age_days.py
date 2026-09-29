"""Guard + behavioral coverage for install.ps1/install.sh's version-activation
`gc` call: it must include a recency floor (`--min-age-days`), determine the
just-superseded slot by calling the CANONICAL resolver (resolve-runtime.ps1/
.sh) directly, and do so BEFORE `activate()` runs.

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
directory mtime, which is ~= INSTALL time, not time-since-superseded. Fixed
by touching the outgoing slot's mtime at the moment of supersession.

**Why the touch must run BEFORE `activate()` (review round 2):** installs run
concurrently by design, so a delayed touch leaves a window where a
concurrent installer's own gc -- which protects only its OWN `$prev` -- can
reap this `$prev` first.

**Why reading `current`/`last-known-good` as raw strings is not enough
(review rounds 2-5):** an ad hoc reimplementation of the marker -> last-
known-good -> newest-slot tiered fallback (checking only for emptiness, not
resolver-validity) kept missing cases: a marker-absent fallback to last-
known-good; both candidates unreadable falling back to a newest complete
slot among other pre-existing slots; and a NONEMPTY but INVALID marker/last-
known-good value (the resolver rejects it and falls through, but an
emptiness-only check would have stopped there and protected the wrong slot).
Each fix narrowed the gap without closing it, because the tiered validity
logic already lives correctly in `resolve-runtime.ps1`/`.sh` and re-deriving
it here was guaranteed to eventually diverge. The actual fix (round 5): call
`resolve-runtime.ps1`/`.sh` directly and derive `$prev` from whatever
interpreter path it resolves -- authoritative by construction, since it IS
the exact code path a real `resolve()`/launch would use.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
_VR_SCRIPT = SCRIPTS / "versioned_runtime.py"
_RESOLVER_PS1 = SCRIPTS / "resolve-runtime.ps1"
_RESOLVER_SH = SCRIPTS / "resolve-runtime.sh"

_PWSH = shutil.which("pwsh") or shutil.which("powershell")
_BASH = (
    shutil.which("bash", path=r"C:\Program Files\Git\usr\bin")
    or shutil.which("bash", path=r"C:\Program Files\Git\bin")
    or shutil.which("bash")
)


def _load_versioned_runtime():
    spec = importlib.util.spec_from_file_location("versioned_runtime", _VR_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


vr = _load_versioned_runtime()


def _install(root: Path, version: str, *, age_days: float = 30.0, complete: bool = True) -> Path:
    """Create versions/<version> as a stand-in slot, backdated by age_days.

    ``complete=False`` omits the completion marker, simulating an invalid/
    incomplete slot the resolver must reject.
    """
    d = vr.version_dir(root, version)
    (d / "Scripts").mkdir(parents=True, exist_ok=True)
    (d / "Scripts" / "python.exe").write_text("fake", encoding="utf-8")
    (d / "bin").mkdir(parents=True, exist_ok=True)
    (d / "bin" / "python").write_text("fake", encoding="utf-8")
    try:
        os.chmod(d / "bin" / "python", 0o755)
    except OSError:
        pass
    if complete:
        (d / ".install-complete.json").write_text(
            '{"version": "%s", "completed_at": "2026-01-01T00:00:00Z", "pid": 1}' % version,
            encoding="utf-8",
        )
    if age_days:
        past = time.time() - age_days * 86400.0
        os.utime(d, (past, past))
    return d


def _touch_now(d: Path) -> None:
    """What install.ps1/.sh's activation now does to the resolved candidate."""
    os.utime(d, None)


def _run_ps1_resolver(root: Path) -> tuple[str, str]:
    """Run resolve-runtime.ps1 against root, return (AwPy, derived_version)."""
    script = f"""
$env:AGENT_RT_ROOT = '{root}'
. '{_RESOLVER_PS1}'
if ($AwPy) {{
    $derived = Split-Path -Leaf (Split-Path -Parent (Split-Path -Parent $AwPy))
}} else {{
    $derived = ''
}}
Write-Output "AWPY=$AwPy"
Write-Output "DERIVED=$derived"
"""
    out = subprocess.run(
        [_PWSH, "-NoProfile", "-NoLogo", "-Command", script],
        capture_output=True, text=True, check=True,
    ).stdout
    awpy = ""
    derived = ""
    for line in out.splitlines():
        if line.startswith("AWPY="):
            awpy = line[len("AWPY="):].strip()
        elif line.startswith("DERIVED="):
            derived = line[len("DERIVED="):].strip()
    return awpy, derived


# ---------------------------------------------------------------------------
# Structural drift guards: the gc call must carry the floor, and $prev must
# be derived by calling the canonical resolver (never a reimplemented tier
# heuristic), before activate() runs.
# ---------------------------------------------------------------------------

@pytest.mark.guard
def test_install_ps1_gc_call_has_min_age_days_floor():
    text = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    assert "'gc', '--protect-pids', '--min-age-days', '0.05'" in text
    assert "LastWriteTime = Get-Date" in text
    # $prev must be derived from the canonical resolver, not a hand-rolled
    # tier heuristic (review rounds 2-5: emptiness checks kept missing
    # invalid-but-nonempty markers and further fallback tiers).
    assert "Join-Path $ScriptDir 'resolve-runtime.ps1'" in text
    assert ". $resolverSrc" in text
    assert "Split-Path -Leaf (Split-Path -Parent (Split-Path -Parent $AwPy))" in text
    # Ordering guard: the touch must happen BEFORE activate() runs, not after
    # -- installs run concurrently by design, so a delayed touch leaves a
    # window where a concurrent installer's own gc (protecting only ITS
    # $prev) can reap this $prev first.
    touch_idx = text.index("LastWriteTime = Get-Date")
    activate_idx = text.index("--link-name '.venv' activate $SrcVersion")
    assert touch_idx < activate_idx
    # Ordering guard (review round 6): the resolver call must run BEFORE
    # Invoke-VersionedMarkComplete -- marking $SrcVersion complete makes IT a
    # valid tier-3 candidate too, so resolving afterward could have the
    # newest-slot scan pick the brand-new slot itself instead of the real
    # previously-pinned older slot.
    resolve_idx = text.index(". $resolverSrc")
    mark_complete_idx = text.index("\n    Invoke-VersionedMarkComplete\n")
    assert resolve_idx < mark_complete_idx


@pytest.mark.guard
def test_install_sh_gc_call_has_min_age_days_floor():
    text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    assert "gc --protect-pids \"${gc_keep_args[@]}\" --min-age-days 0.05" in text
    assert 'touch "$INSTALL_DIR/versions/$prev"' in text
    # $prev must be derived from the canonical resolver.
    assert '_resolver_src="$SCRIPT_DIR/resolve-runtime.sh"' in text
    assert '. "$_resolver_src"' in text
    assert 'prev="$(basename "$(dirname "$(dirname "$AW_PY")")")"' in text
    # install.sh runs under `set -euo pipefail`; sourcing a script with bare
    # (non-&&/||-guarded) commands that can return non-zero must not abort
    # the whole install (review self-catch: resolve-runtime.sh has such
    # commands on a normal miss).
    assert "set +e" in text
    assert re_set_dash_e_after_source(text)
    # Ordering guard: same reasoning as the ps1 test.
    touch_idx = text.index('touch "$INSTALL_DIR/versions/$prev"')
    activate_idx = text.index('".venv" activate "$SRC_VERSION" --no-link')
    assert touch_idx < activate_idx
    # Ordering guard (review round 6): same reasoning as the ps1 test.
    resolve_idx = text.index('. "$_resolver_src"')
    mark_complete_idx = text.index("\n    _versioned_mark_complete\n")
    assert resolve_idx < mark_complete_idx


def re_set_dash_e_after_source(text: str) -> bool:
    source_idx = text.index('. "$_resolver_src"')
    following = text[source_idx:source_idx + 60]
    return "set -e" in following


# ---------------------------------------------------------------------------
# Behavioral: the underlying gc mechanism (touch + --min-age-days) actually
# protects a touched slot, and fails to protect an untouched one.
# ---------------------------------------------------------------------------

def test_min_age_days_alone_does_not_protect_a_long_installed_slot(tmp_path):
    """The floor measures install-time mtime, so an old slot gets zero
    protection from it the moment it's superseded -- exactly the realistic
    case (most versions live for days/weeks before being superseded)."""
    _install(tmp_path, "1.0.0", age_days=30.0)
    _install(tmp_path, "2.0.0", age_days=0.0)
    vr.activate(tmp_path, "2.0.0", link_name=".venv", link_free=True)

    removed = vr.gc(tmp_path, keep=["2.0.0"], protect_pids=False, min_age_days=0.05)
    assert "1.0.0" in removed


def test_touching_superseded_slot_survives_v1_v2_v3_sequence(tmp_path):
    """Touching $prev's mtime at each activation lets a plan resolved
    against an old V1 survive TWO chained activations (V1->V2->V3) within
    one boot."""
    _install(tmp_path, "1.0.0", age_days=30.0)
    _install(tmp_path, "2.0.0", age_days=0.0)
    vr.activate(tmp_path, "2.0.0", link_name=".venv", link_free=True)

    _touch_now(vr.version_dir(tmp_path, "1.0.0"))
    removed = vr.gc(tmp_path, keep=["2.0.0"], protect_pids=False, min_age_days=0.05)
    assert "1.0.0" not in removed

    _install(tmp_path, "3.0.0", age_days=0.0)
    vr.activate(tmp_path, "3.0.0", link_name=".venv", link_free=True)
    _touch_now(vr.version_dir(tmp_path, "2.0.0"))
    removed = vr.gc(tmp_path, keep=["3.0.0"], protect_pids=False, min_age_days=0.05)
    assert "1.0.0" not in removed
    assert "2.0.0" not in removed


# ---------------------------------------------------------------------------
# Integration: resolve-runtime.ps1 itself, across all three resolution tiers
# plus the "nonempty but invalid" case the ad hoc reimplementation missed.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_PWSH is None, reason="pwsh/powershell not available")
def test_resolver_tier1_marker_valid(tmp_path):
    _install(tmp_path, "1.0.0", age_days=5.0)
    (tmp_path / "current-version").write_text("1.0.0", encoding="utf-8")
    awpy, derived = _run_ps1_resolver(tmp_path)
    assert awpy
    assert derived == "1.0.0"


@pytest.mark.skipif(_PWSH is None, reason="pwsh/powershell not available")
def test_resolver_tier1_marker_nonempty_but_invalid_falls_back_to_tier2(tmp_path):
    """The review's key catch: a NONEMPTY marker naming an incomplete slot
    must be rejected, not treated as resolved -- the resolver falls through
    to last-known-good."""
    _install(tmp_path, "1.0.0", age_days=5.0, complete=False)  # marker names this, but it's incomplete
    _install(tmp_path, "2.0.0", age_days=3.0)
    (tmp_path / "current-version").write_text("1.0.0", encoding="utf-8")
    (tmp_path / "last-known-good").write_text("2.0.0", encoding="utf-8")
    awpy, derived = _run_ps1_resolver(tmp_path)
    assert awpy
    assert derived == "2.0.0"


@pytest.mark.skipif(_PWSH is None, reason="pwsh/powershell not available")
def test_resolver_tier3_both_invalid_falls_back_to_newest_complete(tmp_path):
    _install(tmp_path, "1.0.0", age_days=10.0)
    _install(tmp_path, "2.0.0", age_days=5.0)
    # No marker, no last-known-good.
    awpy, derived = _run_ps1_resolver(tmp_path)
    assert awpy
    assert derived == "2.0.0"
