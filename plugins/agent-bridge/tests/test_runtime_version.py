"""Tests for the running-version boot marker (dotfiles #533)."""

from __future__ import annotations

import json
import os

from agent_bridge import __version__
from agent_bridge.runtime_version import RUNNING_VERSION_FILE, write_running_version


def test_write_running_version_content(tmp_path):
    write_running_version(tmp_path)
    data = json.loads((tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert data["version"] == __version__
    assert data["pid"] == os.getpid()
    assert data["started_at"]  # ISO-8601 boot timestamp


def test_write_running_version_creates_dir(tmp_path):
    d = tmp_path / "nested" / ".agent-bridge"
    write_running_version(d)
    assert (d / RUNNING_VERSION_FILE).is_file()


def test_write_running_version_explicit_pid_and_version(tmp_path):
    # The cutover reconciler records the *new* daemon's pid + version, not the
    # deploy process's (dotfiles #533 caveat #1).
    write_running_version(tmp_path, pid=98765, version="9.9.9")
    data = json.loads((tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert data["pid"] == 98765
    assert data["version"] == "9.9.9"
    assert data["pid"] != os.getpid()


def test_write_running_version_never_raises(tmp_path):
    # A directory path that cannot be created (a file sits where a parent dir is
    # expected) must be swallowed -- the marker is best-effort, never fatal.
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    write_running_version(afile / "sub")  # must not raise
    assert not (afile / "sub" / RUNNING_VERSION_FILE).exists()
