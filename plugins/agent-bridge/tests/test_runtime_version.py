"""Tests for the running-version boot marker (dotfiles #533)."""

from __future__ import annotations

import json
import os

from agent_bridge import __version__
from agent_bridge.runtime_version import (
    RUNNING_VERSION_FILE,
    read_running_generation_id,
    set_running_generation_id,
    write_running_version,
)


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


def test_write_running_version_includes_generation_id_when_given(tmp_path):
    write_running_version(tmp_path, generation_id="0.4.1-1234-1700000000.000000")
    data = json.loads((tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert data["generation_id"] == "0.4.1-1234-1700000000.000000"


def test_write_running_version_omits_generation_id_by_default(tmp_path):
    # The boot-time caller (app.py's lifespan) runs BEFORE the SessionManager
    # that computes the real id exists -- must not fabricate one.
    write_running_version(tmp_path)
    data = json.loads((tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert "generation_id" not in data


def test_set_running_generation_id_merges_onto_existing_marker(tmp_path):
    # The follow-up call must preserve the earlier write's pid/version/
    # started_at verbatim, only adding generation_id.
    write_running_version(tmp_path, pid=4242, version="9.9.9")
    set_running_generation_id("9.9.9-4242-1700000000.500000", tmp_path)
    data = json.loads((tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert data["pid"] == 4242
    assert data["version"] == "9.9.9"
    assert data["generation_id"] == "9.9.9-4242-1700000000.500000"


def test_set_running_generation_id_starts_fresh_marker_if_absent(tmp_path):
    # Unusual ordering (or an earlier write failure) -- still best-effort,
    # never raises, and the generation_id is recorded regardless.
    d = tmp_path / ".agent-bridge"
    set_running_generation_id("1.0.0-1-1700000000.000000", d)
    data = json.loads((d / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert data["generation_id"] == "1.0.0-1-1700000000.000000"
    assert data["pid"] == os.getpid()


def test_set_running_generation_id_never_raises(tmp_path):
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    set_running_generation_id("whatever", afile / "sub")  # must not raise
    assert not (afile / "sub" / RUNNING_VERSION_FILE).exists()


def test_set_running_generation_id_recovers_from_non_dict_marker(tmp_path):
    # A malformed/legacy marker that parses as JSON but isn't an object (a
    # bare list, in this case) must not raise on payload["generation_id"] =
    # ... -- fall back to a fresh dict instead of assuming dict-shape.
    (tmp_path / RUNNING_VERSION_FILE).write_text("[1, 2, 3]", encoding="utf-8")
    set_running_generation_id("recovered-gen-id", tmp_path)
    data = json.loads((tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert data["generation_id"] == "recovered-gen-id"
    assert data["pid"] == os.getpid()


def test_read_running_generation_id_roundtrip(tmp_path):
    assert read_running_generation_id(tmp_path) is None  # no marker yet
    set_running_generation_id("roundtrip-gen-id", tmp_path)
    assert read_running_generation_id(tmp_path) == "roundtrip-gen-id"


def test_read_running_generation_id_none_when_marker_missing_field(tmp_path):
    write_running_version(tmp_path)  # no generation_id
    assert read_running_generation_id(tmp_path) is None


def test_read_running_generation_id_none_on_non_dict_marker(tmp_path):
    (tmp_path / RUNNING_VERSION_FILE).write_text("[1, 2, 3]", encoding="utf-8")
    assert read_running_generation_id(tmp_path) is None


def test_read_running_generation_id_none_on_missing_file(tmp_path):
    assert read_running_generation_id(tmp_path / "nonexistent") is None
