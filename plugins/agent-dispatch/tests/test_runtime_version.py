"""Tests for the running-version boot marker (dotfiles #533)."""

from __future__ import annotations

import json
import os

from agent_dispatch import __version__
from agent_dispatch.runtime_version import (
    CURRENT_VERSION_FILE,
    RUNNING_VERSION_FILE,
    read_active_version,
    write_running_version,
)


def test_write_running_version_content(tmp_path):
    write_running_version(tmp_path)
    data = json.loads((tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert data["version"] == __version__
    assert data["pid"] == os.getpid()
    assert data["started_at"]  # ISO-8601 boot timestamp


def test_write_running_version_creates_dir(tmp_path):
    d = tmp_path / "nested" / ".agent-dispatch"
    write_running_version(d)
    assert (d / RUNNING_VERSION_FILE).is_file()


def test_write_running_version_never_raises(tmp_path):
    # A directory path that cannot be created (a file sits where a parent dir is
    # expected) must be swallowed -- the marker is best-effort, never fatal.
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    write_running_version(afile / "sub")  # must not raise
    assert not (afile / "sub" / RUNNING_VERSION_FILE).exists()


# -- read_active_version (live-update slot watch) ----------------------------


def test_read_active_version_reads_marker(tmp_path):
    (tmp_path / CURRENT_VERSION_FILE).write_text("0.1.0-dev42\n", encoding="utf-8")
    assert read_active_version(tmp_path) == "0.1.0-dev42"


def test_read_active_version_missing_is_none(tmp_path):
    assert read_active_version(tmp_path) is None


def test_read_active_version_blank_is_none(tmp_path):
    (tmp_path / CURRENT_VERSION_FILE).write_text("   \n", encoding="utf-8")
    assert read_active_version(tmp_path) is None
