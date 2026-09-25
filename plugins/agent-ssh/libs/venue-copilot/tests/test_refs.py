"""Tests for reference-file staging into a detached venue session."""

from __future__ import annotations

import base64
import io
import tarfile

import pytest
from venue_copilot import refs as venue_refs


def _members(payload: bytes) -> dict[str, bytes]:
    tf = tarfile.open(fileobj=io.BytesIO(base64.b64decode(payload)), mode="r:gz")
    return {m.name: tf.extractfile(m).read() for m in tf.getmembers() if m.isfile()}


def test_files_travel_over_stdin_into_a_batch_dir_outside_the_repo(tmp_path):
    (tmp_path / "a").mkdir()
    har = tmp_path / "a" / "trace.har"
    har.write_bytes(b"HAR")
    command, payload, files = venue_refs.build_refs_upload([str(har)], "scope-1")
    assert command.startswith('mkdir -p "$HOME/.agent-bridge/refs/scope-1"')
    assert "base64 -d | tar -xzf -" in command and command.endswith("pwd")
    assert len(command) < 200  # the payload is never on the command line
    assert _members(payload) == {"trace.har": b"HAR"}
    assert files == [("trace.har", 3)]


def test_same_named_files_do_not_collide(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    (tmp_path / "x" / "notes.md").write_text("one")
    (tmp_path / "y" / "notes.md").write_text("two")
    _, payload, files = venue_refs.build_refs_upload(
        [str(tmp_path / "x" / "notes.md"), str(tmp_path / "y" / "notes.md")], "b",
    )
    assert [n for n, _ in files] == ["notes.md", "notes-2.md"]
    assert _members(payload) == {"notes.md": b"one", "notes-2.md": b"two"}


def test_a_folder_is_sent_whole(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "one.log").write_text("1")
    (logs / "two.log").write_text("22")
    _, payload, files = venue_refs.build_refs_upload([str(logs)], "b")
    assert files == [("logs", 3)]
    assert set(_members(payload)) == {"logs/one.log", "logs/two.log"}


def test_missing_and_oversized_inputs_are_refused(tmp_path, monkeypatch):
    with pytest.raises(venue_refs.RefFileError, match="not found"):
        venue_refs.build_refs_upload([str(tmp_path / "nope.har")], "b")
    big = tmp_path / "big.bin"
    big.write_bytes(b"0" * 2048)
    monkeypatch.setattr(venue_refs, "MAX_REF_BYTES", 1024)
    with pytest.raises(venue_refs.RefFileError, match="limit"):
        venue_refs.build_refs_upload([str(big)], "b")


def test_note_names_exact_venue_paths_and_asks_not_to_commit():
    note = venue_refs.refs_note(
        "/home/codespace/.agent-bridge/refs/b", [("trace.har", 3 * 1024 * 1024)],
    )
    assert "- /home/codespace/.agent-bridge/refs/b/trace.har (3.0 MB)" in note
    assert "never" in note and "commit" in note


def test_deliver_note_sends_over_stdin():
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw.get("input")))
        return type("R", (), {"returncode": 0})()

    assert venue_refs.deliver_note("sid-1", "see /x/y.har", run=run)
    argv, stdin = calls[0]
    assert argv[1:] == ["send", "sid-1", "--prompt-file", "-", "--no-wait", "--steer"]
    assert stdin == "see /x/y.har"
