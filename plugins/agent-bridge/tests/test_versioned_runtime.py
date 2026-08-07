"""Tests for scripts/versioned_runtime.py -- immutable per-version layout (#581).

The module is a stdlib-only helper that lives in ``scripts/`` (deliberately NOT
packaged into the venv), so it is loaded here by file path via importlib.
"""

from __future__ import annotations

import importlib.util
import os
import sys
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


def _install(root: Path, version: str) -> Path:
    """Create versions/<version> as a stand-in venv dir with a marker file."""
    d = vr.version_dir(root, version)
    d.mkdir(parents=True, exist_ok=True)
    (d / "marker.txt").write_text(version, encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# slot / activate / current / resolve
# ---------------------------------------------------------------------------

def test_slot_creates_version_dir(tmp_path):
    d = vr.slot(tmp_path, "1.0.0")
    assert d == vr.version_dir(tmp_path, "1.0.0")
    assert d.is_dir()


def test_activate_points_current_at_version(tmp_path):
    _install(tmp_path, "1.0.0")
    vr.activate(tmp_path, "1.0.0")
    assert vr.current_version(tmp_path) == "1.0.0"
    # current resolves to the version's contents
    resolved = vr.current_link(tmp_path) / "marker.txt"
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
    assert (vr.current_link(tmp_path) / "marker.txt").read_text() == "1.0.0"


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
    vr.activate(tmp_path, "1.0.0")  # lay the link
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
    assert vr.CURRENT_LINK in out


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


@pytest.mark.skipif(sys.platform != "win32", reason="junction is Windows-only")
def test_windows_uses_junction_not_symlink(tmp_path):
    """On Windows the current link is a junction (no privilege needed)."""
    _install(tmp_path, "1.0.0")
    vr.activate(tmp_path, "1.0.0")
    link = vr.current_link(tmp_path)
    assert link.exists()
    # A junction is a reparse point that resolves to the version dir.
    assert link.resolve() == vr.version_dir(tmp_path, "1.0.0").resolve()


# ---------------------------------------------------------------------------
# Slice 2 (#581): configurable link name + legacy real-dir migration.
# ---------------------------------------------------------------------------

def test_link_name_venv(tmp_path):
    """agent-bridge uses `venv` as the link so its task/binstubs are unchanged."""
    _install(tmp_path, "1.0.0")
    vr.activate(tmp_path, "1.0.0", link_name="venv")
    assert vr.current_version(tmp_path, "venv") == "1.0.0"
    # default 'current' link is untouched
    assert vr.current_version(tmp_path) is None
    link = vr.current_link(tmp_path, "venv")
    assert link.name == "venv"
    assert (link / "marker.txt").read_text() == "1.0.0"


def test_activate_refuses_real_dir_without_flag(tmp_path):
    """A legacy real venv dir at the link path is not clobbered by default."""
    _install(tmp_path, "1.0.0")
    legacy = tmp_path / "venv"
    legacy.mkdir()
    (legacy / "python.marker").write_text("legacy", encoding="utf-8")
    with pytest.raises(FileExistsError):
        vr.activate(tmp_path, "1.0.0", link_name="venv")
    # legacy dir untouched
    assert (legacy / "python.marker").read_text() == "legacy"


def test_activate_replace_nonlink_moves_legacy_aside(tmp_path):
    """First migration: the real venv is moved aside and the junction laid."""
    _install(tmp_path, "1.0.0")
    legacy = tmp_path / "venv"
    legacy.mkdir()
    (legacy / "python.marker").write_text("legacy", encoding="utf-8")
    vr.activate(tmp_path, "1.0.0", link_name="venv", replace_nonlink=True)
    # link now resolves to the versioned dir
    assert vr.current_version(tmp_path, "venv") == "1.0.0"
    assert (vr.current_link(tmp_path, "venv") / "marker.txt").read_text() == "1.0.0"
    # the old real dir was preserved (moved aside), not deleted
    aside = list(tmp_path.glob("venv.legacy-*"))
    assert len(aside) == 1
    assert (aside[0] / "python.marker").read_text() == "legacy"


def test_is_link_distinguishes_real_dir_from_link(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    assert vr._is_link(real) is False
    _install(tmp_path, "1.0.0")
    vr.activate(tmp_path, "1.0.0", link_name="venv")
    assert vr._is_link(vr.current_link(tmp_path, "venv")) is True


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
