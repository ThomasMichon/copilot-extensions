"""Tests for scripts/versioned_runtime.py -- immutable per-version layout (#581).

The module is a stdlib-only helper that lives in ``scripts/`` (deliberately NOT
packaged into the venv), so it is loaded here by file path via importlib.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "versioned_runtime.py"


def _load():
    spec = importlib.util.spec_from_file_location("versioned_runtime", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vr = _load()


def _install(root: Path, version: str, *, age_days: float = 30.0) -> Path:
    """Create versions/<version> as a stand-in venv dir with a marker file.

    Backdates the slot mtime by ``age_days`` (default 30) so it sits past gc's
    recency floor -- most gc tests assert reap behavior and predate the floor.
    Pass ``age_days=0`` to create a fresh (young, floor-protected) slot.
    """
    d = vr.version_dir(root, version)
    d.mkdir(parents=True, exist_ok=True)
    (d / "marker.txt").write_text(version, encoding="utf-8")
    if age_days:
        past = time.time() - age_days * 86400.0
        os.utime(d, (past, past))
    return d


# ---------------------------------------------------------------------------
# slot / activate / current / resolve
# ---------------------------------------------------------------------------

def test_slot_creates_version_dir(tmp_path):
    d = vr.slot(tmp_path, "1.0.0")
    assert d == vr.version_dir(tmp_path, "1.0.0")
    assert d.is_dir()


def test_slot_clean_is_vacuous_when_target_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(
        vr,
        "_versions_with_live_process",
        lambda root: (_ for _ in ()).throw(AssertionError("must not scan processes")),
    )

    assert vr.slot(tmp_path, "1.0.0", clean_incomplete=True).is_dir()


def test_slot_cleans_incomplete_current_and_detaches_markers(tmp_path, monkeypatch):
    current = _install(tmp_path, "1.0.0")
    (tmp_path / vr.CURRENT_VERSION_FILE).write_text("1.0.0\n", encoding="utf-8")
    (tmp_path / vr.LAST_KNOWN_GOOD_FILE).write_text("1.0.0\n", encoding="utf-8")
    monkeypatch.setattr(vr, "_versions_with_live_process", lambda root: set())

    rebuilt = vr.slot(tmp_path, "1.0.0", clean_incomplete=True)

    assert rebuilt.is_dir()
    assert not (rebuilt / "marker.txt").exists()
    assert not (tmp_path / vr.CURRENT_VERSION_FILE).exists()
    assert not (tmp_path / vr.LAST_KNOWN_GOOD_FILE).exists()
    assert not list(tmp_path.glob(".*.stale-*"))
    assert current == rebuilt


def test_slot_preserves_live_incomplete_current_and_fails(tmp_path, monkeypatch):
    current = _install(tmp_path, "1.0.0")
    completion = current / vr.COMPLETE_MARKER
    completion.write_text("{malformed", encoding="utf-8")
    (tmp_path / vr.CURRENT_VERSION_FILE).write_text("1.0.0\n", encoding="utf-8")
    (tmp_path / vr.LAST_KNOWN_GOOD_FILE).write_text("1.0.0\n", encoding="utf-8")

    def live_after_withdrawal(root):
        assert not completion.exists()
        assert not (root / vr.CURRENT_VERSION_FILE).exists()
        assert not (root / vr.LAST_KNOWN_GOOD_FILE).exists()
        return {"1.0.0"}

    monkeypatch.setattr(vr, "_versions_with_live_process", live_after_withdrawal)

    with pytest.raises(RuntimeError, match="still in use"):
        vr.slot(tmp_path, "1.0.0", clean_incomplete=True)

    assert (current / "marker.txt").is_file()
    assert completion.read_text(encoding="utf-8") == "{malformed"
    assert (tmp_path / vr.CURRENT_VERSION_FILE).read_text().strip() == "1.0.0"
    assert (tmp_path / vr.LAST_KNOWN_GOOD_FILE).read_text().strip() == "1.0.0"


def test_slot_cleanup_preserves_concurrently_replaced_marker(tmp_path, monkeypatch):
    _install(tmp_path, "1.0.0")
    _install(tmp_path, "2.0.0")
    current = tmp_path / vr.CURRENT_VERSION_FILE
    current.write_text("1.0.0\n", encoding="utf-8")
    monkeypatch.setattr(vr, "_versions_with_live_process", lambda root: set())
    real_replace = vr.os.replace
    raced = False

    def replace_with_race(src, dst):
        nonlocal raced
        if Path(src) == current and not raced:
            raced = True
            current.write_text("2.0.0\n", encoding="utf-8")
        return real_replace(src, dst)

    monkeypatch.setattr(vr.os, "replace", replace_with_race)

    vr.slot(tmp_path, "1.0.0", clean_incomplete=True)

    assert raced
    assert current.read_text(encoding="utf-8").strip() == "2.0.0"


def _write_slot_python(root: Path, version: str) -> Path:
    subpath = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    python = vr.version_dir(root, version) / subpath
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    if os.name != "nt":
        python.chmod(0o755)
    return python


def test_resolve_python_falls_through_incomplete_marker_slots(tmp_path):
    current_python = _write_slot_python(tmp_path, "2.0.0")
    lkg_python = _write_slot_python(tmp_path, "1.0.0")
    (tmp_path / vr.CURRENT_VERSION_FILE).write_text("2.0.0\n", encoding="utf-8")
    (tmp_path / vr.LAST_KNOWN_GOOD_FILE).write_text("1.0.0\n", encoding="utf-8")
    vr.mark_complete(tmp_path, "1.0.0")

    assert vr.resolve_python(tmp_path) == lkg_python
    assert vr.resolve_python(tmp_path) != current_python


def test_resolve_python_rejects_every_incomplete_slot(tmp_path):
    _write_slot_python(tmp_path, "1.0.0")
    (tmp_path / vr.CURRENT_VERSION_FILE).write_text("1.0.0\n", encoding="utf-8")
    (tmp_path / vr.LAST_KNOWN_GOOD_FILE).write_text("1.0.0\n", encoding="utf-8")

    assert vr.resolve_python(tmp_path) is None


def test_activate_points_current_at_version(tmp_path):
    _install(tmp_path, "1.0.0")
    vr.activate(tmp_path, "1.0.0")
    assert vr.current_version(tmp_path) == "1.0.0"
    # current resolves (via the marker) to the concrete versioned slot
    resolved = vr.version_dir(tmp_path, "1.0.0") / "marker.txt"
    assert resolved.read_text(encoding="utf-8") == "1.0.0"


def test_activate_switch_is_repeatable(tmp_path):
    _install(tmp_path, "1.0.0")
    _install(tmp_path, "2.0.0")
    vr.activate(tmp_path, "1.0.0")
    assert vr.current_version(tmp_path) == "1.0.0"
    vr.activate(tmp_path, "2.0.0")
    assert vr.current_version(tmp_path) == "2.0.0"
    # switching back (rollback) is just another swap -- no rebuild
    vr.activate(tmp_path, "1.0.0")
    assert vr.current_version(tmp_path) == "1.0.0"
    assert (vr.version_dir(tmp_path, "1.0.0") / "marker.txt").read_text() == "1.0.0"


def test_activate_missing_version_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        vr.activate(tmp_path, "9.9.9")


def test_activate_preserves_old_version_dir(tmp_path):
    """Switching away from a version must never delete its immutable dir."""
    old = _install(tmp_path, "1.0.0")
    _install(tmp_path, "2.0.0")
    vr.activate(tmp_path, "1.0.0")
    vr.activate(tmp_path, "2.0.0")
    assert old.is_dir()
    assert (old / "marker.txt").read_text() == "1.0.0"


def test_activate_and_current_never_traverse_the_link(tmp_path, monkeypatch):
    """Regression (#637): activate() and current must operate on an existing link
    via lstat / os.readlink and never call exists()/resolve() on the link path.

    On Windows an os.stat that *traverses* a directory junction is blocked by
    RedirectionGuard (PROCESS_MITIGATION_REDIRECTION_TRUST_POLICY) with WinError
    448 ("untrusted mount point") over a non-interactive network logon -- i.e.
    when the installer runs over SSH (the mesh-rollout path). Simulate it by
    making Path.exists()/Path.resolve() on the link path raise OSError(448); the
    junction swap and the active-version lookup must still succeed.
    """
    _install(tmp_path, "1.0.0")
    _install(tmp_path, "2.0.0")
    vr.activate(tmp_path, "1.0.0")  # publish the marker (junction-free)
    link = vr.current_link(tmp_path)
    link_key = os.path.normcase(os.path.abspath(str(link)))

    real_exists = Path.exists
    real_resolve = Path.resolve

    def _guard(self):
        if os.path.normcase(os.path.abspath(str(self))) == link_key:
            raise OSError(448, "untrusted mount point (simulated RedirectionGuard)")

    def guarded_exists(self, *a, **k):
        _guard(self)
        return real_exists(self, *a, **k)

    def guarded_resolve(self, *a, **k):
        _guard(self)
        return real_resolve(self, *a, **k)

    monkeypatch.setattr(Path, "exists", guarded_exists)
    monkeypatch.setattr(Path, "resolve", guarded_resolve)

    # Swap over the existing link: must not evaluate exists()/resolve() on it.
    vr.activate(tmp_path, "2.0.0")
    # Reading the active version must not traverse the link either.
    assert vr.current_version(tmp_path) == "2.0.0"


def test_current_none_when_unset(tmp_path):
    assert vr.current_version(tmp_path) is None


def test_current_none_when_target_removed(tmp_path):
    _install(tmp_path, "1.0.0")
    vr.activate(tmp_path, "1.0.0")
    import shutil
    shutil.rmtree(vr.version_dir(tmp_path, "1.0.0"))
    # a dangling link resolves to a non-existent version -> None
    assert vr.current_version(tmp_path) is None


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def test_list_versions_sorted(tmp_path):
    _install(tmp_path, "0.4.0-dev9")
    _install(tmp_path, "0.4.0-dev10")
    _install(tmp_path, "0.4.0-dev2")
    got = vr.list_versions(tmp_path)
    # PEP 440-aware ordering: dev2 < dev9 < dev10
    assert got == ["0.4.0-dev2", "0.4.0-dev9", "0.4.0-dev10"]


# ---------------------------------------------------------------------------
# gc
# ---------------------------------------------------------------------------

def test_gc_keeps_current_and_kept(tmp_path):
    for v in ("1.0.0", "2.0.0", "3.0.0"):
        _install(tmp_path, v)
    vr.activate(tmp_path, "3.0.0")
    removed = vr.gc(tmp_path, keep=["2.0.0"])
    assert removed == ["1.0.0"]                       # only the unprotected one
    assert vr.version_dir(tmp_path, "3.0.0").is_dir()  # current kept
    assert vr.version_dir(tmp_path, "2.0.0").is_dir()  # explicitly kept
    assert not vr.version_dir(tmp_path, "1.0.0").exists()


def test_gc_nothing_to_remove(tmp_path):
    _install(tmp_path, "1.0.0")
    vr.activate(tmp_path, "1.0.0")
    assert vr.gc(tmp_path) == []


def test_gc_protect_pids_keeps_newest_non_current(tmp_path):
    import json
    for v in ("1.0.0", "2.0.0", "3.0.0"):
        _install(tmp_path, v)
    vr.activate(tmp_path, "3.0.0")
    # A live pid is recorded (this test process) -> protect the newest non-current
    # version (2.0.0) as its likely mid-cutover home; 1.0.0 is still collectable.
    (tmp_path / vr.RUNNING_VERSION_FILE).write_text(
        json.dumps({"version": "2.0.0", "pid": os.getpid()}), encoding="utf-8"
    )
    removed = vr.gc(tmp_path, protect_pids=True)
    assert removed == ["1.0.0"]
    assert vr.version_dir(tmp_path, "2.0.0").is_dir()


def test_gc_dead_pid_not_protected(tmp_path):
    import json
    for v in ("1.0.0", "2.0.0"):
        _install(tmp_path, v)
    vr.activate(tmp_path, "2.0.0")
    (tmp_path / vr.RUNNING_VERSION_FILE).write_text(
        json.dumps({"version": "1.0.0", "pid": 999999999}), encoding="utf-8"
    )
    removed = vr.gc(tmp_path, protect_pids=True)
    assert removed == ["1.0.0"]     # dead pid -> no protection


def test_gc_min_age_floor_is_optional_backstop(tmp_path):
    # The recency floor is now an OPT-IN backstop (default off): active-process
    # usage is the primary gate. A young, just-superseded slot is reaped by
    # default, but a caller may pass min_age_days to hold it -- e.g. for a stored
    # (not-running) path-pinned launch reference to age out (the dev14 concern).
    _install(tmp_path, "1.0.0", age_days=0)   # young, just superseded
    _install(tmp_path, "2.0.0")               # current (old)
    vr.activate(tmp_path, "2.0.0")
    # An explicit floor protects the young non-current slot...
    assert vr.gc(tmp_path, min_age_days=7) == []
    assert vr.version_dir(tmp_path, "1.0.0").is_dir()
    # ...but the default (floor off) reaps it.
    removed = vr.gc(tmp_path)
    assert removed == ["1.0.0"]
    assert not vr.version_dir(tmp_path, "1.0.0").exists()


def test_versions_with_live_process_maps_exe_to_slot(tmp_path, monkeypatch):
    # A live process whose executable resolves under versions/<v>/ marks that
    # version in-use -- the precise "no active process" gate. The dir is read
    # straight off the image path, so no version-string normalization is needed.
    for v in ("1.0.0", "2.0.0"):
        _install(tmp_path, v)
    live_exe = str(vr.version_dir(tmp_path, "2.0.0") / "Scripts" / "python.exe")
    monkeypatch.setattr(vr, "_iter_all_pids", lambda: [4321])
    monkeypatch.setattr(vr, "_pid_image_path",
                        lambda pid: live_exe if pid == 4321 else None)
    monkeypatch.setattr(vr, "_running_pids", lambda root: set())
    assert vr._versions_with_live_process(tmp_path) == {"2.0.0"}


def test_versions_with_live_process_via_recorded_version(tmp_path, monkeypatch):
    # Symlink-proof signal: running-version.json records {version, pid}. The
    # recorded PEP 440 string (1.0.0.dev5) matches the dir name (1.0.0-dev5) via
    # separator normalization -- no reliance on the interpreter's image path.
    import json
    _install(tmp_path, "1.0.0-dev5")
    _install(tmp_path, "2.0.0-dev5")
    vr.activate(tmp_path, "2.0.0-dev5")
    (tmp_path / vr.RUNNING_VERSION_FILE).write_text(
        json.dumps({"version": "1.0.0.dev5", "pid": os.getpid()}), encoding="utf-8")
    monkeypatch.setattr(vr, "_iter_all_pids", lambda: [])
    monkeypatch.setattr(vr, "_pid_image_path", lambda pid: None)
    monkeypatch.setattr(vr, "_pid_cmdline_argv0", lambda pid: None)
    assert vr._versions_with_live_process(tmp_path) == {"1.0.0-dev5"}


def test_versions_with_live_process_via_argv0_when_exe_symlinked(tmp_path, monkeypatch):
    # A symlinked venv interpreter: /proc/<pid>/exe resolves to the base
    # interpreter OUTSIDE the slot, but argv[0] preserves the in-slot launch path
    # versions/<v>/bin/python. argv[0] must still attribute the slot as in-use.
    for v in ("1.0.0", "2.0.0"):
        _install(tmp_path, v)
    argv0 = str(vr.version_dir(tmp_path, "2.0.0") / "bin" / "python")
    monkeypatch.setattr(vr, "_iter_all_pids", lambda: [555])
    monkeypatch.setattr(vr, "_running_pids", lambda root: set())
    monkeypatch.setattr(vr, "_pid_image_path",
                        lambda pid: "/usr/bin/python3.12")   # resolves outside
    monkeypatch.setattr(vr, "_pid_cmdline_argv0",
                        lambda pid: argv0 if pid == 555 else None)
    assert vr._versions_with_live_process(tmp_path) == {"2.0.0"}


def test_gc_reaps_iff_no_live_process(tmp_path, monkeypatch):
    # The invariant: reap a non-current version iff no active process runs from
    # it -- irrespective of slot age (no time floor by default).
    for v in ("1.0.0", "2.0.0", "3.0.0"):
        _install(tmp_path, v, age_days=0)          # all fresh/young
    vr.activate(tmp_path, "3.0.0")                 # current
    monkeypatch.setattr(vr, "_versions_with_live_process",
                        lambda root: {"2.0.0"})
    removed = vr.gc(tmp_path, protect_pids=True)
    assert removed == ["1.0.0"]                    # young but unused -> reaped
    assert vr.version_dir(tmp_path, "2.0.0").is_dir()   # in use -> kept
    assert vr.version_dir(tmp_path, "3.0.0").is_dir()   # current -> kept


def test_gc_protect_pids_fallback_when_enumeration_blocked(tmp_path, monkeypatch):
    # If precise scanning yields nothing but a live pid IS recorded (a platform
    # where enumeration/image-path lookup is blocked), fall back to the older
    # conservative rule -- keep the newest non-current slot -- so GC is never
    # LESS safe than before.
    import json
    for v in ("1.0.0", "2.0.0", "3.0.0"):
        _install(tmp_path, v)
    vr.activate(tmp_path, "3.0.0")
    monkeypatch.setattr(vr, "_versions_with_live_process", lambda root: set())
    (tmp_path / vr.RUNNING_VERSION_FILE).write_text(
        json.dumps({"version": "2.0.0", "pid": os.getpid()}), encoding="utf-8")
    removed = vr.gc(tmp_path, protect_pids=True)
    assert removed == ["1.0.0"]
    assert vr.version_dir(tmp_path, "2.0.0").is_dir()


# ---------------------------------------------------------------------------
# AV-tolerant reclaim (dotfiles #911): a transient Defender lock (WinError 5)
# on an old slot's python.exe must be retried + quietly deferred, never a noisy
# hard failure -- and must NOT wrongly report the slot as removed.
# ---------------------------------------------------------------------------

def _win_err(winerror: int) -> PermissionError:
    e = PermissionError("Access is denied")
    e.winerror = winerror  # emulate a Windows OSError
    return e


def test_is_transient_lock_classification():
    assert vr._is_transient_lock(_win_err(5)) is True     # access denied
    assert vr._is_transient_lock(_win_err(32)) is True    # sharing violation
    assert vr._is_transient_lock(PermissionError()) is True
    # A non-transient failure (e.g. dir not empty for another reason) is NOT it.
    assert vr._is_transient_lock(OSError(9, "bad fd")) is False


def test_gc_defers_slot_under_transient_lock(tmp_path, monkeypatch, capsys):
    """A slot Defender is scanning is retried, then deferred -- not removed,
    not reported as a hard 'could not remove' error."""
    for v in ("1.0.0", "2.0.0"):
        _install(tmp_path, v)
    vr.activate(tmp_path, "2.0.0")
    monkeypatch.setattr(vr.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(
        vr.shutil, "rmtree",
        lambda *_a, **_k: (_ for _ in ()).throw(_win_err(5)),
    )
    removed = vr.gc(tmp_path)
    assert removed == []                                   # not reported removed
    assert vr.version_dir(tmp_path, "1.0.0").is_dir()      # left for next sweep
    err = capsys.readouterr().err
    assert "deferring" in err                              # calm note...
    assert "could not remove" not in err                  # ...not an alarm


def test_gc_removes_after_transient_then_success(tmp_path, monkeypatch):
    """If the lock releases within the retry window, the slot is reclaimed."""
    for v in ("1.0.0", "2.0.0"):
        _install(tmp_path, v)
    vr.activate(tmp_path, "2.0.0")
    monkeypatch.setattr(vr.time, "sleep", lambda *_a, **_k: None)
    real_rmtree = vr.shutil.rmtree
    calls = {"n": 0}

    def flaky(path, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _win_err(5)     # first attempt: Defender still holding it
        return real_rmtree(path, *a, **k)

    monkeypatch.setattr(vr.shutil, "rmtree", flaky)
    removed = vr.gc(tmp_path)
    assert removed == ["1.0.0"]
    assert not vr.version_dir(tmp_path, "1.0.0").exists()


def test_gc_non_transient_error_still_reported(tmp_path, monkeypatch, capsys):
    """A genuinely non-transient OSError is still surfaced as an error."""
    for v in ("1.0.0", "2.0.0"):
        _install(tmp_path, v)
    vr.activate(tmp_path, "2.0.0")
    monkeypatch.setattr(
        vr.shutil, "rmtree",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError(9, "bad fd")),
    )
    removed = vr.gc(tmp_path)
    assert removed == []
    assert "could not remove" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------

def test_cli_activate_and_current(tmp_path, capsys):
    _install(tmp_path, "1.0.0")
    assert vr.main(["--root", str(tmp_path), "activate", "1.0.0"]) == 0
    capsys.readouterr()
    assert vr.main(["--root", str(tmp_path), "current"]) == 0
    assert capsys.readouterr().out.strip() == "1.0.0"


def test_cli_current_absent_returns_1(tmp_path):
    assert vr.main(["--root", str(tmp_path), "current"]) == 1


def test_cli_resolve_subpath(tmp_path, capsys):
    _install(tmp_path, "1.0.0")
    vr.main(["--root", str(tmp_path), "activate", "1.0.0"])
    capsys.readouterr()
    subpath = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    assert vr.main(["--root", str(tmp_path), "resolve", "--subpath", subpath]) == 0
    out = capsys.readouterr().out.strip()
    assert out.endswith(subpath.replace("/", os.sep))
    # resolve now returns the concrete slot path (versions/<current>/...),
    # not a `current` link path.
    assert vr.VERSIONS_DIR in out
    assert "1.0.0" in out


def test_cli_gc_json(tmp_path, capsys):
    import json
    for v in ("1.0.0", "2.0.0"):
        _install(tmp_path, v)
    vr.main(["--root", str(tmp_path), "activate", "2.0.0"])
    capsys.readouterr()
    assert vr.main(["--root", str(tmp_path), "--json", "gc"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"removed": ["1.0.0"]}


def test_cli_list_json(tmp_path, capsys):
    import json
    _install(tmp_path, "1.0.0")
    _install(tmp_path, "2.0.0")
    vr.main(["--root", str(tmp_path), "activate", "2.0.0"])
    capsys.readouterr()
    assert vr.main(["--root", str(tmp_path), "--json", "list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"versions": ["1.0.0", "2.0.0"], "current": "2.0.0"}


# ---------------------------------------------------------------------------
# pid liveness
# ---------------------------------------------------------------------------

def test_pid_alive_self_and_invalid():
    assert vr._pid_alive(os.getpid()) is True
    assert vr._pid_alive(0) is False
    assert vr._pid_alive(-1) is False


@pytest.mark.skipif(sys.platform != "win32", reason="junction behavior is Windows-only")
def test_windows_activate_creates_no_junction(tmp_path):
    """Junction-free: activate writes only the marker; no reparse point is laid."""
    _install(tmp_path, "1.0.0")
    vr.activate(tmp_path, "1.0.0")
    link = vr.current_link(tmp_path)
    assert not link.exists()
    assert not vr._is_link(link)
    # the marker is the sole source of truth
    assert vr.current_version(tmp_path) == "1.0.0"


@pytest.mark.skipif(sys.platform != "win32", reason="junction is Windows-only")
def test_windows_activate_removes_stale_legacy_junction(tmp_path):
    """A stale legacy `venv` junction from a pre-marker install is removed so it
    can't shadow or dangle over the marker."""
    _install(tmp_path, "1.0.0")
    link = tmp_path / "venv"
    # Lay a junction the old way (directly), then activate junction-free.
    import _winapi
    _winapi.CreateJunction(str(vr.version_dir(tmp_path, "1.0.0")), str(link))
    assert vr._is_link(link)
    vr.activate(tmp_path, "1.0.0", link_name="venv")
    assert not vr._is_link(link)
    assert vr.current_version(tmp_path, "venv") == "1.0.0"


# ---------------------------------------------------------------------------
# Slice 2 (#581): configurable link name + legacy real-dir migration.
# ---------------------------------------------------------------------------

def test_link_name_venv(tmp_path):
    """agent-bridge uses `venv` as the link name so its task/binstubs are
    unchanged; the active version is published by the link-name-agnostic
    `current-version` marker. Windows is junction-free (no link laid); POSIX lays
    a `venv` symlink the binstub/systemd/manifest resolve through."""
    _install(tmp_path, "1.0.0")
    vr.activate(tmp_path, "1.0.0", link_name="venv")
    assert vr.current_version(tmp_path, "venv") == "1.0.0"
    assert vr.current_version(tmp_path) == "1.0.0"
    link = vr.current_link(tmp_path, "venv")
    if os.name == "nt":
        # Junction-free on Windows: no `venv` link is laid.
        assert not vr._is_link(link)
    else:
        # POSIX: a `venv` symlink into the active slot.
        assert vr._is_link(link)
        assert (link / "marker.txt").read_text() == "1.0.0"


def test_activate_leaves_real_dir_without_flag(tmp_path):
    """A legacy real venv dir at the link path is not clobbered without the flag:
    the marker is written (the source of truth) and the real dir is left as-is on
    every OS (Windows is junction-free; POSIX returns early without replacing)."""
    _install(tmp_path, "1.0.0")
    legacy = tmp_path / "venv"
    legacy.mkdir()
    (legacy / "python.marker").write_text("legacy", encoding="utf-8")
    vr.activate(tmp_path, "1.0.0", link_name="venv")
    # marker published; legacy dir untouched
    assert vr.current_version(tmp_path, "venv") == "1.0.0"
    assert (legacy / "python.marker").read_text() == "legacy"


def test_activate_replace_nonlink_moves_legacy_aside(tmp_path):
    """--replace-nonlink migrates a legacy real venv. On Windows (junction-free)
    the marker is authoritative and the real dir is simply left; on POSIX the real
    dir is moved aside and a `venv` symlink is laid into the active slot."""
    _install(tmp_path, "1.0.0")
    legacy = tmp_path / "venv"
    legacy.mkdir()
    (legacy / "python.marker").write_text("legacy", encoding="utf-8")
    vr.activate(tmp_path, "1.0.0", link_name="venv", replace_nonlink=True)
    assert vr.current_version(tmp_path, "venv") == "1.0.0"
    aside = list(tmp_path.glob("venv.legacy-*"))
    if os.name == "nt":
        # Junction-free: nothing is moved; the real dir stays put.
        assert (legacy / "python.marker").read_text() == "legacy"
        assert aside == []
    else:
        # POSIX: real dir moved aside (preserved), symlink now resolves to the slot.
        assert vr._is_link(vr.current_link(tmp_path, "venv"))
        assert (vr.current_link(tmp_path, "venv") / "marker.txt").read_text() == "1.0.0"
        assert len(aside) == 1
        assert (aside[0] / "python.marker").read_text() == "legacy"


def test_is_link_distinguishes_real_dir_from_link(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    assert vr._is_link(real) is False
    # a genuine reparse point / symlink reports as a link
    target = _install(tmp_path, "1.0.0")
    link = tmp_path / "linky"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)
    assert vr._is_link(link) is True


def test_cli_link_name_threaded(tmp_path, capsys):
    _install(tmp_path, "1.0.0")
    assert vr.main(["--root", str(tmp_path), "--link-name", "venv",
                    "activate", "1.0.0"]) == 0
    capsys.readouterr()
    assert vr.main(["--root", str(tmp_path), "--link-name", "venv", "current"]) == 0
    assert capsys.readouterr().out.strip() == "1.0.0"


def test_cli_activate_replace_nonlink(tmp_path, capsys):
    _install(tmp_path, "1.0.0")
    (tmp_path / "venv").mkdir()
    rc = vr.main(["--root", str(tmp_path), "--link-name", "venv",
                  "activate", "1.0.0", "--replace-nonlink"])
    assert rc == 0
    capsys.readouterr()
    assert vr.main(["--root", str(tmp_path), "--link-name", "venv", "current"]) == 0
    assert capsys.readouterr().out.strip() == "1.0.0"


# ---------------------------------------------------------------------------
# gc: legacy Windows junction slots (#846)
# ---------------------------------------------------------------------------

def _make_junction(target: Path, junction: Path) -> None:
    import _winapi
    junction.parent.mkdir(parents=True, exist_ok=True)
    _winapi.CreateJunction(str(target), str(junction))


def test_junction_slot_names_empty_on_posix_and_realdirs(tmp_path):
    """No junction slots -> the GC candidate set equals list_versions (both OS)."""
    _install(tmp_path, "1.0.0")
    _install(tmp_path, "2.0.0")
    assert vr._junction_slot_names(tmp_path) == []
    assert vr._gc_candidate_versions(tmp_path) == vr.list_versions(tmp_path)


def test_gc_ordinary_dirs_unchanged_by_junction_path(tmp_path):
    """Junction-safe GC must not regress the normal real-directory behavior."""
    stale = _install(tmp_path, "1.0.0")
    kept = _install(tmp_path, "2.0.0")
    current = _install(tmp_path, "3.0.0")
    vr.activate(tmp_path, "3.0.0")
    assert vr.gc(tmp_path, keep=["2.0.0"]) == ["1.0.0"]
    assert not stale.exists()
    assert kept.is_dir()
    assert current.is_dir()


@pytest.mark.skipif(sys.platform != "win32", reason="junction behavior is Windows-only")
def test_windows_gc_removes_live_target_junction_slot_without_deleting_target(tmp_path):
    """A live-target junction slot is reclaimed as its reparse-point entry; the
    junction's target dir and contents are never traversed or deleted (#846).

    ``list_versions`` *does* include a live-target junction (its ``is_dir()``
    traverses the reparse point to a real dir) -- so pre-fix GC would
    ``shutil.rmtree`` it and delete the target's contents. The fix routes every
    junction slot through :func:`_remove_slot` (``os.rmdir``), never a traversal.
    """
    target = tmp_path / "legacy-target"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    junction = vr.version_dir(tmp_path, "1.0.0")
    _make_junction(target, junction)
    assert vr._is_link(junction)
    _install(tmp_path, "2.0.0")
    vr.activate(tmp_path, "2.0.0")

    assert "1.0.0" in vr._gc_candidate_versions(tmp_path)
    assert vr.gc(tmp_path) == ["1.0.0"]
    assert not os.path.lexists(junction)          # junction entry unlinked
    assert target.is_dir()                        # target dir NOT traversed/deleted
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(sys.platform != "win32", reason="junction behavior is Windows-only")
def test_windows_gc_removes_broken_target_junction_slot(tmp_path):
    """A broken-target junction slot (which list_versions drops because is_dir()
    traverses to a missing target) is still enumerated and reclaimed (#846)."""
    target = tmp_path / "legacy-target"
    target.mkdir()
    junction = vr.version_dir(tmp_path, "1.0.0")
    _make_junction(target, junction)
    shutil.rmtree(target)
    assert os.path.lexists(junction)
    assert not junction.exists()          # broken target
    assert "1.0.0" not in vr.list_versions(tmp_path)

    _install(tmp_path, "2.0.0")
    vr.activate(tmp_path, "2.0.0")

    assert vr.gc(tmp_path) == ["1.0.0"]
    assert not os.path.lexists(junction)


@pytest.mark.skipif(sys.platform != "win32", reason="junction behavior is Windows-only")
def test_windows_gc_keeps_current_junction_free_slots_when_junction_present(tmp_path):
    """The junction path must not disturb protection of current/kept real slots."""
    target = tmp_path / "legacy-target"
    target.mkdir()
    junction = vr.version_dir(tmp_path, "1.0.0")
    _make_junction(target, junction)
    _install(tmp_path, "2.0.0")           # kept
    current = _install(tmp_path, "3.0.0")
    vr.activate(tmp_path, "3.0.0")

    assert vr.gc(tmp_path, keep=["2.0.0"]) == ["1.0.0"]
    assert not os.path.lexists(junction)
    assert vr.version_dir(tmp_path, "2.0.0").is_dir()
    assert current.is_dir()


@pytest.mark.skipif(sys.platform != "win32", reason="junction behavior is Windows-only")
def test_windows_gc_reclaims_junction_slot_under_nonzero_min_age(tmp_path):
    """A junction slot must stay reclaimable even under a positive min_age_days
    floor: its ``stat()`` traverses to a possibly-young target, which would
    otherwise shield the legacy junction indefinitely (#846)."""
    target = tmp_path / "legacy-target"
    target.mkdir()                        # fresh target -> young mtime
    junction = vr.version_dir(tmp_path, "1.0.0")
    _make_junction(target, junction)
    _install(tmp_path, "2.0.0")
    vr.activate(tmp_path, "2.0.0")

    # A large floor would shield a young *real* slot -- but never a junction slot.
    assert vr.gc(tmp_path, min_age_days=3650) == ["1.0.0"]
    assert not os.path.lexists(junction)
    assert target.is_dir()
