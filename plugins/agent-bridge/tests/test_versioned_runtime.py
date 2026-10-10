"""Tests for scripts/versioned_runtime.py -- immutable per-version layout (#581).

The module is a stdlib-only helper that lives in ``scripts/`` (deliberately NOT
packaged into the venv), so it is loaded here by file path via importlib.
"""

from __future__ import annotations

import importlib.util
import json
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


def test_current_incomplete_slot_is_retained_without_process_enumeration(
    tmp_path, monkeypatch
):
    current = _install(tmp_path, "1.0.0")
    (tmp_path / vr.CURRENT_VERSION_FILE).write_text("1.0.0\n", encoding="utf-8")
    monkeypatch.setattr(vr, "_reliable_process_enumeration", lambda: False)
    monkeypatch.setattr(vr, "_versions_with_live_process", lambda root: set())

    with pytest.raises(RuntimeError, match="without reliable process enumeration"):
        vr.slot(tmp_path, "1.0.0", clean_incomplete=True)

    assert (current / "marker.txt").is_file()
    assert (tmp_path / vr.CURRENT_VERSION_FILE).read_text().strip() == "1.0.0"


def test_stale_recorded_owner_allows_cleanup_without_process_enumeration(
    tmp_path, monkeypatch
):
    current = _install(tmp_path, "1.0.0")
    (tmp_path / vr.CURRENT_VERSION_FILE).write_text("1.0.0\n", encoding="utf-8")
    (tmp_path / vr.RUNNING_VERSION_FILE).write_text(
        json.dumps({"version": "1.0.0", "pid": 424242}),
        encoding="utf-8",
    )
    monkeypatch.setattr(vr, "_reliable_process_enumeration", lambda: False)
    monkeypatch.setattr(vr, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(vr, "_versions_with_live_process", lambda root: set())

    rebuilt = vr.slot(tmp_path, "1.0.0", clean_incomplete=True)

    assert rebuilt.is_dir()
    assert not (rebuilt / "marker.txt").exists()
    assert not (tmp_path / vr.CURRENT_VERSION_FILE).exists()
    assert current == rebuilt


def test_unknown_recorded_owner_retains_current_without_process_enumeration(
    tmp_path, monkeypatch
):
    current = _install(tmp_path, "1.0.0")
    (tmp_path / vr.CURRENT_VERSION_FILE).write_text("1.0.0\n", encoding="utf-8")
    (tmp_path / vr.RUNNING_VERSION_FILE).write_text(
        json.dumps([
            {"version": "1.0.0", "pid": 424242},
            {"version": "1.0.0"},
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(vr, "_reliable_process_enumeration", lambda: False)
    monkeypatch.setattr(vr, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(vr, "_versions_with_live_process", lambda root: set())

    with pytest.raises(RuntimeError, match="without reliable process enumeration"):
        vr.slot(tmp_path, "1.0.0", clean_incomplete=True)

    assert (current / "marker.txt").is_file()
    assert (tmp_path / vr.CURRENT_VERSION_FILE).read_text().strip() == "1.0.0"


def test_duplicate_marker_cleanup_restores_state_on_conservative_failure(
    tmp_path, monkeypatch
):
    current = _install(tmp_path, "1.0.0")
    marker = vr.marker_path(tmp_path, "1.0.0")
    duplicate = '{"version": "1.0.0", "version": "1.0.0"}'
    marker.write_text(duplicate, encoding="utf-8")
    (tmp_path / vr.CURRENT_VERSION_FILE).write_text("1.0.0\n", encoding="utf-8")
    monkeypatch.setattr(vr, "_reliable_process_enumeration", lambda: False)

    with pytest.raises(RuntimeError, match="without reliable process enumeration"):
        vr.slot(tmp_path, "1.0.0", clean_incomplete=True)

    assert current.is_dir()
    assert marker.read_text(encoding="utf-8") == duplicate
    assert (tmp_path / vr.CURRENT_VERSION_FILE).read_text().strip() == "1.0.0"


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


@pytest.mark.parametrize(
    "marker",
    [
        "{not-json",
        '{"version": "wrong"}',
        '{"version": "1.0.0", "version": "1.0.0"}',
        '{"version": "1.0.0"}',
        '{"version": "1.0.0", "completed_at": "x"}',
        '{"version": "1.0.0", "completed_at": 1, "pid": 1}',
        '{"version": "1.0.0", "completed_at": "x", "pid": "1"}',
        '{"version": "1.0.0", "completed_at": "x", "pid": true}',
        '{"version": "1.0.0", "completed_at": "x", "pid": 1, "extra": 1}',
        '{"version": "1.0.0", "completed_at": "x", "pid": 1, "payload_hash": 1}',
    ],
)
def test_is_complete_rejects_invalid_or_ambiguous_version_marker(tmp_path, marker):
    _write_slot_python(tmp_path, "1.0.0")
    vr.marker_path(tmp_path, "1.0.0").write_text(marker, encoding="utf-8")

    assert not vr.is_complete(tmp_path, "1.0.0")
    assert vr.resolve_python(tmp_path) is None


@pytest.mark.parametrize(
    "marker",
    [
        {
            "version": "1.0.0",
            "completed_at": "2026-08-27T00:00:00Z",
            "pid": 1,
        },
        {
            "pid": 1,
            "version": "1.0.0",
            "completed_at": "2026-08-27T00:00:00Z",
            "payload_hash": "abc",
        },
    ],
)
def test_is_complete_accepts_canonical_schema_regardless_of_field_order(
    tmp_path, marker
):
    python = _write_slot_python(tmp_path, "1.0.0")
    vr.marker_path(tmp_path, "1.0.0").write_text(
        json.dumps(marker), encoding="utf-8"
    )

    assert vr.is_complete(tmp_path, "1.0.0")
    assert vr.resolve_python(tmp_path) == python


def test_mark_complete_writes_canonical_marker(tmp_path):
    _write_slot_python(tmp_path, "1.0.0")

    path = vr.mark_complete(
        tmp_path, "1.0.0", payload_hash="abc", pid=123
    )

    assert vr.read_marker(tmp_path, "1.0.0") == {
        "version": "1.0.0",
        "completed_at": json.loads(path.read_text())["completed_at"],
        "pid": 123,
        "payload_hash": "abc",
    }


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
    # Supported-version numeric ordering: dev2 < dev9 < dev10.
    assert got == ["0.4.0-dev2", "0.4.0-dev9", "0.4.0-dev10"]


def test_version_key_is_stdlib_and_orders_supported_dev_versions(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def without_packaging(name, *args, **kwargs):
        if name == "packaging" or name.startswith("packaging."):
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_packaging)

    assert sorted(
        ["0.4.0-dev10", "0.4.0-dev2", "0.4.0-dev9"],
        key=vr._version_key,
    ) == ["0.4.0-dev2", "0.4.0-dev9", "0.4.0-dev10"]
    assert sorted(
        ["0.4.0", "0.4.0-dev10", "0.4.0-dev9"],
        key=vr._version_key,
    ) == ["0.4.0-dev9", "0.4.0-dev10", "0.4.0"]


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


# ---------------------------------------------------------------------------
# Mutable dev slot: claim / release / GC interaction
# ---------------------------------------------------------------------------

def test_dev_status_absent_by_default(tmp_path):
    assert vr.read_dev_claim(tmp_path) is None


def test_dev_claim_writes_a_readable_record(tmp_path):
    record = vr.claim_dev(tmp_path, "worktree:abc123", previous_version="1.0.0")
    assert record["owner"] == "worktree:abc123"
    assert record["previous_version"] == "1.0.0"
    assert record["schema"] == vr.DEV_CLAIM_SCHEMA
    assert vr.read_dev_claim(tmp_path) == record


def test_dev_claim_by_same_owner_is_idempotent_and_keeps_previous_version(tmp_path):
    first = vr.claim_dev(tmp_path, "worktree:abc123", previous_version="1.0.0")
    second = vr.claim_dev(tmp_path, "worktree:abc123", previous_version="2.0.0")
    # Re-claiming as the SAME owner must not clobber the originally recorded
    # previous_version -- that's what release() restores current-version to.
    assert second["previous_version"] == "1.0.0"
    assert second["claimed_at"] >= first["claimed_at"]


def test_dev_claim_refuses_a_different_owner(tmp_path):
    vr.claim_dev(tmp_path, "worktree:abc123")
    with pytest.raises(vr.DevClaimConflict):
        vr.claim_dev(tmp_path, "worktree:xyz789")


def test_dev_claim_force_overrides_a_different_owner(tmp_path):
    vr.claim_dev(tmp_path, "worktree:abc123", previous_version="1.0.0")
    record = vr.claim_dev(tmp_path, "worktree:xyz789", force=True,
                          previous_version="9.9.9")
    assert record["owner"] == "worktree:xyz789"
    assert record["previous_version"] == "9.9.9"


def test_dev_release_by_owner_returns_the_claim_and_clears_it(tmp_path):
    vr.claim_dev(tmp_path, "worktree:abc123", previous_version="1.0.0")
    released = vr.release_dev(tmp_path, "worktree:abc123")
    assert released["previous_version"] == "1.0.0"
    assert vr.read_dev_claim(tmp_path) is None


def test_dev_release_with_no_claim_is_a_no_op(tmp_path):
    assert vr.release_dev(tmp_path, "worktree:abc123") is None


def test_dev_release_refuses_a_different_owner(tmp_path):
    vr.claim_dev(tmp_path, "worktree:abc123")
    with pytest.raises(vr.DevClaimConflict):
        vr.release_dev(tmp_path, "worktree:xyz789")


def test_dev_release_force_releases_a_different_owners_claim(tmp_path):
    vr.claim_dev(tmp_path, "worktree:abc123", previous_version="1.0.0")
    released = vr.release_dev(tmp_path, "worktree:xyz789", force=True)
    assert released["owner"] == "worktree:abc123"
    assert vr.read_dev_claim(tmp_path) is None


def test_gc_protects_dev_slot_while_claimed(tmp_path):
    _install(tmp_path, "1.0.0")
    _install(tmp_path, vr.DEV_VERSION)
    vr.activate(tmp_path, "1.0.0")
    vr.claim_dev(tmp_path, "worktree:abc123")
    # dev is not current, but a live claim protects it from GC.
    assert vr.gc(tmp_path) == []
    assert vr.version_dir(tmp_path, vr.DEV_VERSION).is_dir()


def test_gc_reclaims_dev_slot_once_released(tmp_path):
    _install(tmp_path, "1.0.0")
    _install(tmp_path, vr.DEV_VERSION)
    vr.activate(tmp_path, "1.0.0")
    vr.claim_dev(tmp_path, "worktree:abc123")
    vr.release_dev(tmp_path, "worktree:abc123")
    assert vr.gc(tmp_path) == [vr.DEV_VERSION]


def test_dev_status_malformed_file_reads_as_absent(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    vr.dev_claim_path(tmp_path).write_text("not json", encoding="utf-8")
    assert vr.read_dev_claim(tmp_path) is None


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


# ---------------------------------------------------------------------------
# fingerprint_source / check_admission (phase-3-runtime-admission, #5472/#5788)
# ---------------------------------------------------------------------------

def test_fingerprint_source_is_stable_for_identical_content(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("print('hi')", encoding="utf-8")
    (src / "b.py").write_text("x = 1", encoding="utf-8")
    first = vr.fingerprint_source([src])
    second = vr.fingerprint_source([src])
    assert first == second
    assert len(first) == 64  # sha256 hex digest


def test_fingerprint_source_changes_with_content(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("print('hi')", encoding="utf-8")
    before = vr.fingerprint_source([src])
    (src / "a.py").write_text("print('bye')", encoding="utf-8")
    after = vr.fingerprint_source([src])
    assert before != after


def test_fingerprint_source_changes_when_a_file_is_added(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("print('hi')", encoding="utf-8")
    before = vr.fingerprint_source([src])
    (src / "b.py").write_text("x = 1", encoding="utf-8")
    after = vr.fingerprint_source([src])
    assert before != after


def test_fingerprint_source_covers_every_declared_root_not_just_one_manifest(tmp_path):
    """A prior real-world gap (#5472): fingerprinting only `pyproject.toml`
    missed a changed `src/` tree entirely. Passing multiple roots must make
    BOTH matter."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("x = 1", encoding="utf-8")
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text("[project]\nname='x'\n", encoding="utf-8")

    before = vr.fingerprint_source([manifest, src])
    (src / "mod.py").write_text("x = 2", encoding="utf-8")
    after = vr.fingerprint_source([manifest, src])
    assert before != after


def test_fingerprint_source_is_order_independent(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("a", encoding="utf-8")
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text("m", encoding="utf-8")
    assert vr.fingerprint_source([src, manifest]) == vr.fingerprint_source([manifest, src])


def test_fingerprint_source_raises_on_a_missing_declared_root(tmp_path):
    """A missing declared root must never silently collapse to a
    best-effort digest over whatever remained -- that could let a marker's
    recorded hash match a later, genuinely different input set whose
    declared root also happened to vanish the same way."""
    missing = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError):
        vr.fingerprint_source([missing])


def test_fingerprint_source_raises_on_an_unreadable_file(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x", encoding="utf-8")

    real_read_bytes = Path.read_bytes

    def _boom(self):
        if self.name == "a.py":
            raise OSError("simulated unreadable file")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _boom)
    with pytest.raises(OSError):
        vr.fingerprint_source([src])


def test_fingerprint_source_raises_on_an_unscannable_directory(tmp_path, monkeypatch):
    """A directory that cannot be scanned/stat'd during the walk (e.g.
    permission-denied) must raise, never be silently omitted from the
    fingerprint -- ``Path.rglob``/``is_file`` swallow scandir/stat
    ``PermissionError`` on supported Python versions, so the walk must use
    a mechanism (``os.walk`` with ``onerror``) that does not."""
    src = tmp_path / "src"
    (src / "locked").mkdir(parents=True)
    (src / "locked" / "a.py").write_text("x", encoding="utf-8")

    import os as _os

    real_walk = _os.walk

    def _boom(top, *args, **kwargs):
        onerror = kwargs.get("onerror")
        for dirpath, dirnames, filenames in real_walk(top, *args, **kwargs):
            if dirpath.endswith("locked") and onerror is not None:
                onerror(PermissionError(13, "simulated permission denied", dirpath))
                continue
            yield dirpath, dirnames, filenames

    monkeypatch.setattr(_os, "walk", _boom)
    with pytest.raises(OSError):
        vr.fingerprint_source([src])


def test_fingerprint_source_raises_when_an_entry_cannot_be_lstatted(tmp_path, monkeypatch):
    """A single file entry whose metadata lookup is denied mid-walk must
    raise, never be silently omitted as if it simply wasn't a file/symlink
    -- ``Path.is_symlink()``/``Path.is_file()`` catch ``OSError``
    (including ``PermissionError``) internally and return ``False``,
    which would otherwise let this entry vanish from the fingerprint
    entirely and authorize a later `reuse` decision over a partial
    digest."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x", encoding="utf-8")

    import os as _os

    real_lstat = _os.lstat

    def _boom(path, *args, **kwargs):
        if Path(path).name == "a.py":
            raise PermissionError(13, "simulated permission denied", str(path))
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(_os, "lstat", _boom)
    with pytest.raises(OSError):
        vr.fingerprint_source([src])


def test_fingerprint_source_is_independent_of_overlapping_root_order(tmp_path):
    """With overlapping roots (a parent and its own child directory), the
    SAME two roots passed in either order must agree: each file is labeled
    relative to whichever declared root most specifically contains it
    (independent of argument order), and is hashed exactly once even though
    it is reachable through both roots."""
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    (child / "a.py").write_text("x", encoding="utf-8")
    (parent / "b.py").write_text("y", encoding="utf-8")

    forward = vr.fingerprint_source([parent, child])
    backward = vr.fingerprint_source([child, parent])
    assert forward == backward


def test_fingerprint_source_labels_are_unique_across_same_named_roots(tmp_path):
    """Two different declared roots that happen to share a directory name
    (e.g. "src" under two different parents) must never collide: a file's
    label is derived from its root's own full resolved path, never just
    the root's basename."""
    a_src = tmp_path / "a" / "src"
    a_src.mkdir(parents=True)
    (a_src / "x.py").write_text("one", encoding="utf-8")

    b_src = tmp_path / "b" / "src"
    b_src.mkdir(parents=True)
    (b_src / "x.py").write_text("two", encoding="utf-8")

    assert vr.fingerprint_source([a_src]) != vr.fingerprint_source([b_src])
    # And the combined fingerprint must be stable/repeatable despite the
    # label collision risk (same basename "src" for both roots).
    combined_first = vr.fingerprint_source([a_src, b_src])
    combined_second = vr.fingerprint_source([a_src, b_src])
    assert combined_first == combined_second


def test_fingerprint_source_frames_labels_and_content_unambiguously(tmp_path):
    """Labels and content must be framed unambiguously: a bare separator
    (even a NUL) does not unambiguously delimit arbitrary file bytes -- one
    file whose content embeds another file's label+separator could
    otherwise serialize identically to two genuinely different files.
    Construct that exact adversarial example (same root name on both
    sides, so the labels line up byte-for-byte under a naive separator
    scheme) and confirm the two distinct input sets fingerprint
    differently."""
    one_file_root = tmp_path / "g1" / "r"
    one_file_root.mkdir(parents=True)
    (one_file_root / "a").write_bytes(b"x\0r/b\0y")

    two_file_root = tmp_path / "g2" / "r"
    two_file_root.mkdir(parents=True)
    (two_file_root / "a").write_bytes(b"x")
    (two_file_root / "b").write_bytes(b"y")

    assert vr.fingerprint_source([one_file_root]) != vr.fingerprint_source([two_file_root])


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX-only")
def test_fingerprint_source_rejects_an_unsupported_filesystem_object_nested(tmp_path):
    """A FIFO/device/socket/etc. discovered mid-walk must never be
    silently omitted from the fingerprint (letting a later admission
    decision treat a changed install input as unchanged) -- only a
    regular file, directory, or symlink is a supported attributable
    input."""
    src = tmp_path / "src"
    src.mkdir()
    os.mkfifo(src / "a_fifo")
    with pytest.raises(ValueError):
        vr.fingerprint_source([src])


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX-only")
def test_fingerprint_source_rejects_an_unsupported_filesystem_object_as_root(tmp_path):
    fifo = tmp_path / "a_fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError):
        vr.fingerprint_source([fifo])


def test_fingerprint_source_is_independent_of_absolute_location(tmp_path_factory):
    """Relocating an entire checkout to a different absolute path (a fresh
    clone, a different OS, a renamed parent directory) must not change the
    fingerprint of logically-identical content: labels must never embed a
    declared root's own absolute path text, only its position relative to
    the OTHER declared roots."""
    first_base = tmp_path_factory.mktemp("first-location")
    second_base = tmp_path_factory.mktemp("a-very-differently-named-second-spot")

    def _populate(base):
        src = base / "proj" / "src"
        src.mkdir(parents=True)
        (src / "a.py").write_text("x = 1", encoding="utf-8")
        manifest = base / "proj" / "pyproject.toml"
        manifest.write_text("[project]\nname='x'\n", encoding="utf-8")
        return manifest, src

    first_manifest, first_src = _populate(first_base)
    second_manifest, second_src = _populate(second_base)

    first = vr.fingerprint_source([first_manifest, first_src])
    second = vr.fingerprint_source([second_manifest, second_src])
    assert first == second

    # And genuinely different content under the relocated tree still
    # changes the fingerprint -- relocation-independence must not collapse
    # into "always reuses", only "location alone doesn't matter".
    (second_src / "a.py").write_text("x = 2", encoding="utf-8")
    third = vr.fingerprint_source([second_manifest, second_src])
    assert third != second


def test_fingerprint_source_preserves_file_symlink_identity(tmp_path):
    """A symlink must never be silently resolved away and conflated with
    its target: an `alias.py -> real.py` symlink sitting alongside the
    real file it targets must be hashed as its OWN entry, so adding,
    removing, or re-pointing it changes the fingerprint even though its
    resolved path is identical to `real.py`'s."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "real.py").write_text("x = 1", encoding="utf-8")
    alias = src / "alias.py"
    try:
        alias.symlink_to(src / "real.py")
    except OSError:
        pytest.skip("symlinks are unavailable")

    with_alias = vr.fingerprint_source([src])
    alias.unlink()
    without_alias = vr.fingerprint_source([src])
    assert with_alias != without_alias


def test_fingerprint_source_changes_when_a_symlink_is_repointed(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "real_a.py").write_text("x = 1", encoding="utf-8")
    (src / "real_b.py").write_text("x = 2", encoding="utf-8")
    link = src / "alias.py"
    try:
        link.symlink_to(src / "real_a.py")
    except OSError:
        pytest.skip("symlinks are unavailable")

    before = vr.fingerprint_source([src])
    link.unlink()
    link.symlink_to(src / "real_b.py")
    after = vr.fingerprint_source([src])
    assert before != after


def test_fingerprint_source_rejects_a_nested_symlinked_directory_to_an_undeclared_target(tmp_path):
    """A nested symlinked directory pointing OUTSIDE every declared root
    is rejected outright, never silently reduced to pointer-identity
    text: its content is not otherwise covered by this fingerprint, so a
    change made through it (an installer that follows the link) could
    change the real install input with NO effect on the digest."""
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    (real_dir / "nested.py").write_text("x = 1", encoding="utf-8")

    src = tmp_path / "src"
    src.mkdir()
    link = src / "linked_dir"
    try:
        link.symlink_to(real_dir, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(ValueError):
        vr.fingerprint_source([src])


def test_fingerprint_source_allows_a_nested_symlinked_directory_to_a_declared_target(tmp_path):
    """A nested symlinked directory whose target lies INSIDE one of the
    declared roots is allowed (never walked into, but its target's
    content is already covered by that root's own separate walk, so
    identity-only hashing is safe here): changing the target's content
    IS visible in the digest, through the target's own direct entry."""
    src = tmp_path / "src"
    src.mkdir()
    real_dir = src / "real_dir"
    real_dir.mkdir()
    (real_dir / "nested.py").write_text("x = 1", encoding="utf-8")
    link = src / "linked_dir"
    try:
        link.symlink_to(real_dir, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    before = vr.fingerprint_source([src])
    (real_dir / "nested.py").write_text("x = 2", encoding="utf-8")
    after = vr.fingerprint_source([src])
    assert before != after

    # Re-pointing the link itself (to a different, also-declared target)
    # must also change the digest.
    other_dir = src / "other_dir"
    other_dir.mkdir()
    (other_dir / "nested.py").write_text("x = 1", encoding="utf-8")
    link.unlink()
    link.symlink_to(other_dir, target_is_directory=True)
    after_repoint = vr.fingerprint_source([src])
    assert after_repoint != after


def test_fingerprint_source_rejects_a_nested_file_symlink_to_an_undeclared_target(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    external = tmp_path / "external.py"
    external.write_text("x = 1", encoding="utf-8")
    alias = src / "alias.py"
    try:
        alias.symlink_to(external)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(ValueError):
        vr.fingerprint_source([src])


def test_fingerprint_source_allows_a_nested_symlink_to_a_dangling_target(tmp_path):
    """A dangling symlink has no actual content to hide, so it is allowed
    through as identity-only (unlike an undeclared-but-EXISTING external
    target, which is rejected)."""
    src = tmp_path / "src"
    src.mkdir()
    alias = src / "alias.py"
    try:
        alias.symlink_to(src / "does-not-exist.py")
    except OSError:
        pytest.skip("symlinks are unavailable")
    # Must not raise.
    vr.fingerprint_source([src])


def test_fingerprint_source_raises_when_symlink_target_lookup_is_denied(tmp_path, monkeypatch):
    """A PermissionError (or any OSError other than FileNotFoundError)
    while resolving a symlink's target must never be treated the same as
    "dangling": the target may genuinely exist with unverified content,
    so silently allowing identity-only hashing would violate this
    function's fail-closed contract. Only an actual FileNotFoundError
    (a truly dangling target) is allowed through."""
    src = tmp_path / "src"
    src.mkdir()
    alias = src / "alias.py"
    try:
        alias.symlink_to(src / "does-not-exist.py")
    except OSError:
        pytest.skip("symlinks are unavailable")

    real_resolve = Path.resolve

    def _boom(self, *args, **kwargs):
        if self.name == "alias.py":
            raise PermissionError(13, "simulated permission denied", str(self))
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", _boom)
    with pytest.raises(OSError):
        vr.fingerprint_source([src])


def test_normalize_windows_extended_path_converts_unc_form():
    """The \\\\?\\UNC\\server\\share\\... extended-length form must become
    an ordinary \\\\server\\share\\... UNC path, never a naive 4-character
    strip (which would leave `UNC\\server\\share\\...`, a path Windows
    treats as RELATIVE, silently breaking downstream isabs()/resolve()
    comparisons)."""
    assert (
        vr._normalize_windows_extended_path(r"\\?\UNC\myserver\myshare\file.txt")
        == r"\\myserver\myshare\file.txt"
    )


def test_normalize_windows_extended_path_strips_generic_prefix():
    assert vr._normalize_windows_extended_path(r"\\?\C:\a\b.txt") == r"C:\a\b.txt"


def test_normalize_windows_extended_path_leaves_ordinary_paths_unchanged():
    assert vr._normalize_windows_extended_path(r"C:\a\b.txt") == r"C:\a\b.txt"
    assert vr._normalize_windows_extended_path("relative/path.py") == "relative/path.py"


def test_fingerprint_source_rejects_a_symlink_to_an_external_target_regardless_of_location(tmp_path_factory):
    """A nested symlink to an undeclared EXTERNAL target is rejected
    outright (see the dedicated rejection tests above); this must hold
    consistently regardless of where the declared source tree itself
    lives, since the rejection is based on root-containment, never on
    absolute-location comparison."""
    external = tmp_path_factory.mktemp("external-fixed-location")
    (external / "shared.py").write_text("x = 1", encoding="utf-8")

    for base_name in ("first-tree", "a-very-differently-named-second-tree"):
        base = tmp_path_factory.mktemp(base_name)
        src = base / "src"
        src.mkdir()
        try:
            (src / "alias.py").symlink_to(external / "shared.py")
        except OSError:
            pytest.skip("symlinks are unavailable")
        with pytest.raises(ValueError):
            vr.fingerprint_source([src])


def test_fingerprint_source_symlink_to_internal_target_is_relocation_stable(tmp_path_factory):
    """An absolute symlink target that lies INSIDE the declared source set
    is made relocation-invariant the same way a file label is: relocating
    the WHOLE tree moves the target the same way it moves everything
    else, so the relative-to-common-ancestor form stays stable."""
    first_base = tmp_path_factory.mktemp("first-tree")
    second_base = tmp_path_factory.mktemp("a-very-differently-named-second-tree")
    for base in (first_base, second_base):
        src = base / "src"
        src.mkdir()
        (src / "real.py").write_text("x = 1", encoding="utf-8")
        try:
            (src / "alias.py").symlink_to(src / "real.py")
        except OSError:
            pytest.skip("symlinks are unavailable")

    first = vr.fingerprint_source([first_base / "src"])
    second = vr.fingerprint_source([second_base / "src"])
    assert first == second


def test_fingerprint_source_rejects_a_target_under_common_ancestor_but_outside_declared_roots(tmp_path_factory):
    """Containment must be checked against the ACTUAL declared roots, not
    merely their common ancestor: a sibling path under that ancestor
    (e.g. `project/shared.py` alongside declared roots `project/src` and
    `project/pyproject.toml`) was never itself declared. It must be
    rejected as an undeclared external target, consistently, regardless
    of whether the declared roots happen to live alongside it (so a naive
    common-ancestor containment check would wrongly treat it as
    "inside") or at a wholly different location."""
    project = tmp_path_factory.mktemp("project")
    (project / "shared.py").write_text("shared", encoding="utf-8")

    def _declare_roots(base):
        src = base / "src"
        src.mkdir()
        try:
            (src / "alias.py").symlink_to(project / "shared.py")
        except OSError:
            pytest.skip("symlinks are unavailable")
        manifest = base / "pyproject.toml"
        manifest.write_text("[project]\nname='x'\n", encoding="utf-8")
        return manifest, src

    # Roots physically alongside the external `project/shared.py` sibling.
    first_manifest, first_src = _declare_roots(project)
    with pytest.raises(ValueError):
        vr.fingerprint_source([first_manifest, first_src])

    # The SAME two roots at a wholly different location; `shared.py`
    # itself never moves.
    other_base = tmp_path_factory.mktemp("elsewhere")
    second_manifest, second_src = _declare_roots(other_base)
    with pytest.raises(ValueError):
        vr.fingerprint_source([second_manifest, second_src])


def test_fingerprint_source_rejects_a_declared_root_that_is_itself_a_symlink(tmp_path):
    """A declared ROOT -- the caller's own attributable content
    declaration -- that is itself a symlink must be REJECTED, not silently
    reduced to the link's own target-identity text: doing so would hash
    only "where this points", never the content actually read through
    that path, recreating the exact content-drift blind spot this
    function exists to close. This applies to both a symlinked directory
    root and a symlinked file root."""
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    (real_dir / "nested.py").write_text("x = 1", encoding="utf-8")
    dir_root_link = tmp_path / "dir_root_link"
    try:
        dir_root_link.symlink_to(real_dir, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    with pytest.raises(ValueError):
        vr.fingerprint_source([dir_root_link])

    real_file = tmp_path / "real_file.py"
    real_file.write_text("x = 1", encoding="utf-8")
    file_root_link = tmp_path / "file_root_link.py"
    file_root_link.symlink_to(real_file)
    with pytest.raises(ValueError):
        vr.fingerprint_source([file_root_link])

    # Declaring the REAL path (not the symlink) still works normally and
    # does cover its actual content.
    before = vr.fingerprint_source([real_dir])
    (real_dir / "nested.py").write_text("x = 2", encoding="utf-8")
    after = vr.fingerprint_source([real_dir])
    assert before != after


def test_fingerprint_source_distinguishes_a_file_from_a_symlink_with_matching_bytes(tmp_path):
    """The digest must encode each entry's TYPE, not just its label and
    payload bytes: a regular file whose raw content happens to equal the
    byte-string a symlink's own target would encode to must never hash
    identically to that symlink at the same label."""
    file_root = tmp_path / "g1" / "r"
    file_root.mkdir(parents=True)
    # The literal relative target text a symlink "r/x.py" -> "y.py" would
    # encode to (kept in sync with fingerprint_source's own target-framing
    # format: the posix-relative target string, UTF-8 encoded).
    (file_root / "x.py").write_bytes(Path("y.py").as_posix().encode("utf-8"))

    symlink_root = tmp_path / "g2" / "r"
    symlink_root.mkdir(parents=True)
    (symlink_root / "y.py").write_text("irrelevant", encoding="utf-8")
    try:
        (symlink_root / "x.py").symlink_to(symlink_root / "y.py")
    except OSError:
        pytest.skip("symlinks are unavailable")

    assert vr.fingerprint_source([file_root]) != vr.fingerprint_source([symlink_root])


def test_check_admission_construct_when_slot_absent(tmp_path):
    assert vr.check_admission(tmp_path, "1.0.0", payload_hash="abc") == vr.ADMIT_CONSTRUCT


def test_check_admission_construct_when_slot_incomplete(tmp_path):
    vr.version_dir(tmp_path, "1.0.0").mkdir(parents=True)
    assert vr.check_admission(tmp_path, "1.0.0", payload_hash="abc") == vr.ADMIT_CONSTRUCT


def test_check_admission_health_repair_required_when_slot_path_is_a_file(tmp_path):
    """`versions/<version>` existing but NOT being a directory (a stray
    file, or a broken symlink sitting where the slot should be) is an
    invalid slot shape -- ambiguous evidence, never "never built"."""
    vdir = vr.version_dir(tmp_path, "1.0.0")
    vdir.parent.mkdir(parents=True)
    vdir.write_text("not a directory", encoding="utf-8")
    assert (
        vr.check_admission(tmp_path, "1.0.0", payload_hash="abc")
        == vr.ADMIT_HEALTH_REPAIR_REQUIRED
    )


def test_check_admission_health_repair_required_when_slot_path_is_a_dangling_symlink(tmp_path):
    """`Path.exists()` reports a dangling symlink as absent (it follows
    the link to a target that is not there). A dangling symlink sitting at
    the slot path is still ambiguous evidence -- SOMETHING is declared
    there -- and must never be silently treated as "never built"."""
    vdir = vr.version_dir(tmp_path, "1.0.0")
    vdir.parent.mkdir(parents=True)
    try:
        vdir.symlink_to(vdir.parent / "does-not-exist", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    assert (
        vr.check_admission(tmp_path, "1.0.0", payload_hash="abc")
        == vr.ADMIT_HEALTH_REPAIR_REQUIRED
    )


def test_check_admission_health_repair_required_when_slot_path_is_a_valid_symlink(tmp_path):
    """A slot symlink that resolves to a PERFECTLY VALID, complete,
    matching-hash directory must still be rejected, never silently
    followed through to `reuse`: a published slot's own path must be an
    immutable real directory, never an indirection, because an
    indirection can be RETARGETED later without this contract's
    create-once guarantee ever noticing."""
    real_dir = tmp_path / "real_target_dir"
    real_dir.mkdir()
    marker = json.dumps({
        "version": "1.0.0", "completed_at": "2020-01-01T00:00:00Z",
        "pid": 1, "payload_hash": "abc",
    })
    (real_dir / vr.COMPLETE_MARKER).write_text(marker, encoding="utf-8")

    vdir = vr.version_dir(tmp_path, "1.0.0")
    vdir.parent.mkdir(parents=True)
    try:
        vdir.symlink_to(real_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    assert (
        vr.check_admission(tmp_path, "1.0.0", payload_hash="abc")
        == vr.ADMIT_HEALTH_REPAIR_REQUIRED
    )


def test_check_admission_health_repair_required_when_marker_path_is_a_symlink(tmp_path):
    """Same rationale as the slot-path check above, applied to the marker
    path itself: even a marker symlink that resolves to a perfectly valid
    marker file must be rejected, never silently read through."""
    vr.version_dir(tmp_path, "1.0.0").mkdir(parents=True)
    real_marker = tmp_path / "real-marker.json"
    real_marker.write_text(
        json.dumps({"version": "1.0.0", "completed_at": "x", "pid": 1,
                    "payload_hash": "abc"}),
        encoding="utf-8",
    )
    marker_file = vr.marker_path(tmp_path, "1.0.0")
    try:
        marker_file.symlink_to(real_marker)
    except OSError:
        pytest.skip("symlinks are unavailable")
    assert (
        vr.check_admission(tmp_path, "1.0.0", payload_hash="abc")
        == vr.ADMIT_HEALTH_REPAIR_REQUIRED
    )


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW is POSIX-only")
def test_check_admission_catches_a_marker_swapped_to_a_symlink_mid_probe(tmp_path, monkeypatch):
    """Simulates the exact TOCTOU race the O_NOFOLLOW/dir_fd-anchored read
    exists to close: something swaps the marker path for a symlink to a
    DIFFERENT, otherwise-perfectly-valid-and-matching marker in between
    this function's own checks and its actual open/read of the marker. A
    pathname re-resolved a second time would silently follow the swapped-
    in symlink and return `reuse`; the open-time O_NOFOLLOW guard must
    instead catch it (ELOOP) and report `health-repair-required`."""
    vr.mark_complete(tmp_path, "1.0.0", payload_hash="abc")
    marker_file = vr.marker_path(tmp_path, "1.0.0")

    decoy_dir = tmp_path / "decoy"
    decoy_dir.mkdir()
    decoy_marker = decoy_dir / vr.COMPLETE_MARKER
    decoy_marker.write_text(marker_file.read_text(encoding="utf-8"), encoding="utf-8")

    real_open = os.open
    state = {"swapped": False}

    def _swap_then_open(path, flags, *args, **kwargs):
        p = path if isinstance(path, Path) else Path(path)
        if not state["swapped"] and p.name == vr.COMPLETE_MARKER:
            state["swapped"] = True
            marker_file.unlink()
            marker_file.symlink_to(decoy_marker)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", _swap_then_open)
    assert (
        vr.check_admission(tmp_path, "1.0.0", payload_hash="abc")
        == vr.ADMIT_HEALTH_REPAIR_REQUIRED
    )



def test_check_admission_reuse_when_marker_matches(tmp_path):
    vr.mark_complete(tmp_path, "1.0.0", payload_hash="abc")
    assert vr.check_admission(tmp_path, "1.0.0", payload_hash="abc") == vr.ADMIT_REUSE


def test_check_admission_content_conflict_when_marker_differs(tmp_path):
    vr.mark_complete(tmp_path, "1.0.0", payload_hash="abc")
    assert (
        vr.check_admission(tmp_path, "1.0.0", payload_hash="different")
        == vr.ADMIT_CONTENT_CONFLICT
    )


def test_check_admission_never_mutates_the_slot(tmp_path):
    """A stateless probe: calling it repeatedly, in either admission state,
    must never write anything -- an ordinary caller is expected to call this
    cheaply and often, with no construction lease held."""
    vr.mark_complete(tmp_path, "1.0.0", payload_hash="abc")
    marker_before = vr.marker_path(tmp_path, "1.0.0").read_bytes()
    for _ in range(5):
        vr.check_admission(tmp_path, "1.0.0", payload_hash="abc")
        vr.check_admission(tmp_path, "1.0.0", payload_hash="other")
    assert vr.marker_path(tmp_path, "1.0.0").read_bytes() == marker_before


def test_check_admission_health_repair_required_when_marker_is_malformed(tmp_path):
    """A marker FILE that exists but fails validation (corrupt JSON, wrong
    schema) is ambiguous evidence, NOT the same as "never built": a caller
    following plain ``ADMIT_CONSTRUCT`` would acquire the lease and write
    into what could be an already-published, possibly-live slot whose
    marker was merely corrupted on disk after the fact."""
    marker = vr.marker_path(tmp_path, "1.0.0")
    marker.parent.mkdir(parents=True)
    marker.write_text("{not valid json", encoding="utf-8")
    assert (
        vr.check_admission(tmp_path, "1.0.0", payload_hash="abc")
        == vr.ADMIT_HEALTH_REPAIR_REQUIRED
    )


def test_check_admission_health_repair_required_when_marker_is_invalid_utf8(tmp_path):
    """A marker read through a TEXT-mode wrapper would raise
    ``UnicodeDecodeError`` on invalid UTF-8 bytes BEFORE the JSON-parse
    handler ever runs, escaping this function's documented
    health-repair-required contract entirely. Invalid UTF-8 is exactly as
    malformed as invalid JSON and must be classified the same way."""
    marker = vr.marker_path(tmp_path, "1.0.0")
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"\x80\x81\x82\x83 not valid utf-8 or json")
    assert (
        vr.check_admission(tmp_path, "1.0.0", payload_hash="abc")
        == vr.ADMIT_HEALTH_REPAIR_REQUIRED
    )


def test_check_admission_health_repair_required_never_mutates_the_slot(tmp_path):
    marker = vr.marker_path(tmp_path, "1.0.0")
    marker.parent.mkdir(parents=True)
    marker.write_text("{not valid json", encoding="utf-8")
    before = marker.read_bytes()
    vr.check_admission(tmp_path, "1.0.0", payload_hash="abc")
    assert marker.read_bytes() == before


def test_cli_fingerprint_json(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x", encoding="utf-8")
    rc = vr.main(["--root", str(tmp_path), "--json", "fingerprint", str(src)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"fingerprint"}
    assert len(out["fingerprint"]) == 64


def test_cli_check_admission_construct(tmp_path, capsys):
    rc = vr.main([
        "--root", str(tmp_path), "--json", "check-admission", "1.0.0",
        "--payload-hash", "abc",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"version": "1.0.0", "admission": vr.ADMIT_CONSTRUCT}


def test_cli_check_admission_reuse(tmp_path, capsys):
    vr.mark_complete(tmp_path, "1.0.0", payload_hash="abc")
    rc = vr.main([
        "--root", str(tmp_path), "--json", "check-admission", "1.0.0",
        "--payload-hash", "abc",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"version": "1.0.0", "admission": vr.ADMIT_REUSE}


def test_cli_check_admission_health_repair_required(tmp_path, capsys):
    marker = vr.marker_path(tmp_path, "1.0.0")
    marker.parent.mkdir(parents=True)
    marker.write_text("{not valid json", encoding="utf-8")
    rc = vr.main([
        "--root", str(tmp_path), "--json", "check-admission", "1.0.0",
        "--payload-hash", "abc",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"version": "1.0.0", "admission": vr.ADMIT_HEALTH_REPAIR_REQUIRED}

