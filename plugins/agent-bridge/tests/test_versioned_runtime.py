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
