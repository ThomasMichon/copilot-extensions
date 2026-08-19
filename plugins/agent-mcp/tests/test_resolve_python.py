"""Tests for the canonical interpreter resolution -- `versioned_runtime.resolve_python`.

Exercises the shared primitive's single, uniform, junction-free resolution
(current-version marker -> last-known-good -> newest complete slot; never a
`.venv` link, never a PATH python) via the vendored copy this plugin ships.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load_vr():
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "vr_resolve_under_test", _SCRIPTS / "versioned_runtime.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vr = _load_vr()


def _make_slot(root: Path, version: str, *, complete: bool = True) -> None:
    d = root / "versions" / version / "bin"
    d.mkdir(parents=True, exist_ok=True)
    py = d / "python"
    py.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(py, 0o755)
    if complete:
        vr.mark_complete(root, version)


def test_tier1_resolves_current_version_marker(tmp_path):
    _make_slot(tmp_path, "0.1.0")
    _make_slot(tmp_path, "0.2.0")
    vr.activate(tmp_path, "0.1.0", link_free=True)
    assert vr.resolve_python(tmp_path).as_posix().endswith("versions/0.1.0/bin/python")


def test_activate_stamps_last_known_good(tmp_path):
    _make_slot(tmp_path, "0.1.0")
    vr.activate(tmp_path, "0.1.0", link_free=True)
    assert vr.read_last_known_good(tmp_path) == "0.1.0"


def test_tier2_prefers_last_known_good_over_newest(tmp_path):
    _make_slot(tmp_path, "0.1.0")
    _make_slot(tmp_path, "0.2.0")
    vr.activate(tmp_path, "0.1.0", link_free=True)
    # Marker gone; last-known-good (0.1.0) must win over the newest slot (0.2.0).
    (tmp_path / "current-version").unlink()
    assert vr.resolve_python(tmp_path).as_posix().endswith("versions/0.1.0/bin/python")


def test_tier3_newest_complete_slot_on_true_first_run(tmp_path):
    _make_slot(tmp_path, "0.1.0")
    _make_slot(tmp_path, "0.2.0")
    # No marker, no last-known-good -> newest complete slot.
    assert vr.resolve_python(tmp_path).as_posix().endswith("versions/0.2.0/bin/python")


def test_tier3_skips_incomplete_newest(tmp_path):
    _make_slot(tmp_path, "0.1.0", complete=True)
    _make_slot(tmp_path, "0.2.0", complete=False)  # partial/failed build
    # The newest slot is incomplete; resolution falls to the newest COMPLETE slot.
    assert vr.resolve_python(tmp_path).as_posix().endswith("versions/0.1.0/bin/python")


def test_no_runtime_returns_none_never_path_python(tmp_path):
    # No slots installed. Even with a python on PATH, resolution returns None so
    # the caller degrades deliberately instead of binding the system interpreter.
    assert vr.resolve_python(tmp_path) is None


def test_never_resolves_through_a_venv_link(tmp_path):
    # A stray `.venv` (the retired link) must not be resolved through.
    _make_slot(tmp_path, "0.1.0")
    vr.activate(tmp_path, "0.1.0", link_free=True)
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(venv / "python", 0o755)
    resolved = vr.resolve_python(tmp_path).as_posix()
    assert "/.venv/" not in resolved
    assert "/versions/0.1.0/" in resolved


def test_slot_python_missing_slot_is_none(tmp_path):
    assert vr.slot_python(tmp_path, "9.9.9") is None
    assert vr.slot_python(tmp_path, "") is None
