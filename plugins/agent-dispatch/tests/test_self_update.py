"""Tests for the coordinator's live version-staleness detection.

Exercises ``agent_dispatch.self_update.stale_target`` -- the fail-safe
predicate a coordinator's background self-update loop polls to decide whether
a different, fully-installed version is now published and should be handed
off to. Mirrors ``test_self_retire.py``'s style: exhaustive on the "stay put"
(``None``) side.
"""

from __future__ import annotations

from pathlib import Path

from agent_dispatch.self_update import (
    read_current_version,
    slot_python,
    stale_target,
)

ROOT = Path("/does/not/matter")


def test_stale_when_marker_names_a_different_installed_version():
    target = Path("/root/versions/0.1.2-dev40/bin/python")
    result = stale_target(
        ROOT, "0.1.2-dev37",
        read_marker=lambda _r: "0.1.2-dev40",
        resolve_slot_python=lambda _r, v: target if v == "0.1.2-dev40" else None,
    )
    assert result == target


def test_not_stale_when_no_marker():
    result = stale_target(
        ROOT, "0.1.2-dev37",
        read_marker=lambda _r: None,
        resolve_slot_python=lambda _r, v: Path("/should/not/be/used"),
    )
    assert result is None


def test_not_stale_when_marker_matches_running_version():
    result = stale_target(
        ROOT, "0.1.2-dev37",
        read_marker=lambda _r: "0.1.2-dev37",
        resolve_slot_python=lambda _r, v: Path("/should/not/be/used"),
    )
    assert result is None


def test_not_stale_when_marker_names_a_version_with_no_installed_interpreter():
    # Marker differs, but the slot is missing/incomplete -- fail safe.
    result = stale_target(
        ROOT, "0.1.2-dev37",
        read_marker=lambda _r: "0.1.2-dev40",
        resolve_slot_python=lambda _r, v: None,
    )
    assert result is None


def test_not_stale_when_marker_is_empty_string():
    result = stale_target(
        ROOT, "0.1.2-dev37",
        read_marker=lambda _r: "",
        resolve_slot_python=lambda _r, v: Path("/should/not/be/used"),
    )
    assert result is None


def test_read_current_version_missing_file_returns_none(tmp_path):
    assert read_current_version(tmp_path) is None


def test_read_current_version_reads_and_strips(tmp_path):
    (tmp_path / "current-version").write_text("0.1.2-dev40\n", encoding="utf-8")
    assert read_current_version(tmp_path) == "0.1.2-dev40"


def test_read_current_version_empty_file_returns_none(tmp_path):
    (tmp_path / "current-version").write_text("   \n", encoding="utf-8")
    assert read_current_version(tmp_path) is None


def test_slot_python_missing_slot_returns_none(tmp_path):
    assert slot_python(tmp_path, "0.1.2-dev40") is None


def test_slot_python_finds_posix_layout(tmp_path):
    binp = tmp_path / "versions" / "0.1.2-dev40" / "bin"
    binp.mkdir(parents=True)
    py = binp / "python"
    py.write_text("", encoding="utf-8")
    assert slot_python(tmp_path, "0.1.2-dev40") == py


def test_slot_python_finds_windows_layout(tmp_path):
    scripts = tmp_path / "versions" / "0.1.2-dev40" / "Scripts"
    scripts.mkdir(parents=True)
    py = scripts / "python.exe"
    py.write_text("", encoding="utf-8")
    assert slot_python(tmp_path, "0.1.2-dev40") == py
