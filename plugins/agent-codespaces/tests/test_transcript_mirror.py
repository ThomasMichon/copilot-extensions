"""Tests for the Connection Owner's transcript mirror (``transcript_mirror``)."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from agent_codespaces import connection_owner as owner
from agent_codespaces import session_forwards as sf
from agent_codespaces import transcript_mirror as tm

SID = "0123abcd-4567-89ef-0123-456789abcdef"
SID2 = "fedcba98-7654-3210-fedc-ba9876543210"


def _chunk(sid, off, data, *, size=None, reset=False):
    size = off + len(data) if size is None else size
    return f"{tm._HEAD}{sid} {off} {size} {1 if reset else 0}\n{base64.b64encode(data).decode()}\n"


def _events(mirror, cs=None, sid=SID):
    return (mirror._root / (cs or "cs-1") / "session-state" / sid / "events.jsonl").read_bytes()


def test_the_script_carries_known_offsets_and_drops_invalid_ids():
    script = tm.remote_script({SID: 120, "../etc": 5, "x": 1}, limits={SID: 8})
    assert f"{SID}:120:8" in script
    assert "../etc" not in script and " x:1" not in script
    assert tm._DONE in script


def test_parse_reads_chunks_and_workspaces_and_skips_garbage():
    text = (
        _chunk(SID, 0, b'{"a":1}\n')
        + f"{tm._WORKSPACE}{SID}\n{base64.b64encode(b'cwd: /w').decode()}\n"
        + _chunk("../bad-session-id", 0, b"x\n")
        + f"{tm._HEAD}{SID2} 0 5 0\nnot base64!!\n"
        + tm._DONE + "\n"
    )
    chunks, workspaces, complete = tm.parse_output(text)
    assert chunks == [(SID, 0, 8, False, b'{"a":1}\n')]
    assert workspaces == {SID: b"cwd: /w"}
    assert complete


def test_only_whole_lines_are_mirrored_and_the_next_pass_resumes(tmp_path):
    mirror = tm.TranscriptMirror(root=tmp_path)
    assert mirror.apply("cs-1", _chunk(SID, 0, b'{"a":1}\n{"b":')) == {SID: ["events.jsonl"]}
    assert _events(mirror) == b'{"a":1}\n'
    assert mirror.offsets("cs-1") == {SID: 8}  # the torn line is pulled again
    assert mirror.apply("cs-1", _chunk(SID, 8, b'{"b":2}\n')) == {SID: ["events.jsonl"]}
    assert _events(mirror) == b'{"a":1}\n{"b":2}\n'


def test_a_chunk_without_a_whole_line_or_at_a_stale_offset_changes_nothing(tmp_path):
    mirror = tm.TranscriptMirror(root=tmp_path)
    mirror.apply("cs-1", _chunk(SID, 0, b'{"a":1}\n'))
    assert mirror.apply("cs-1", _chunk(SID, 8, b'{"partial')) == {}
    assert mirror.apply("cs-1", _chunk(SID, 3, b'{"c":3}\n')) == {}
    assert _events(mirror) == b'{"a":1}\n'


def test_a_replaced_transcript_is_mirrored_again_from_its_start(tmp_path):
    mirror = tm.TranscriptMirror(root=tmp_path)
    mirror.apply("cs-1", _chunk(SID, 0, b'{"old":1}\n{"old":2}\n'))
    assert mirror.apply("cs-1", _chunk(SID, 0, b'{"new":1}\n', reset=True)) == {SID: ["events.jsonl"]}
    assert _events(mirror) == b'{"new":1}\n'


def _git_bash() -> str | None:
    if sys.platform != "win32":
        return shutil.which("bash")
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramW6432", "")):
        candidate = Path(base) / "Git" / "bin" / "bash.exe"
        if base and candidate.is_file():
            return str(candidate)
    return None


@pytest.mark.skipif(_git_bash() is None, reason="needs a GNU bash")
def test_the_script_round_trips_a_real_session_state_tree(tmp_path):
    home = tmp_path / "home"
    state = home / ".copilot" / "session-state"
    (state / SID).mkdir(parents=True)
    (state / SID / "events.jsonl").write_bytes(b'{"n":1}\n{"n":2}\n')
    (state / SID / "workspace.yaml").write_bytes(b"cwd: /workspaces/x\n")
    (state / "not-a-session").mkdir()
    env = {**os.environ, "HOME": str(home)}
    mirror = tm.TranscriptMirror(root=tmp_path / "mirror")

    def run() -> str:
        script = tm.remote_script(mirror.offsets("cs-1"))
        return subprocess.run([_git_bash(), "-c", script], capture_output=True, text=True,
                              env=env, check=True).stdout

    assert mirror.apply("cs-1", run()) == {SID: ["events.jsonl", "workspace.yaml"]}
    assert _events(mirror) == b'{"n":1}\n{"n":2}\n'
    ws = mirror._root / "cs-1" / "session-state" / SID / "workspace.yaml"
    assert ws.read_bytes() == b"cwd: /workspaces/x\n"
    assert tm.parse_output(run())[0] == []  # nothing new: nothing pulled
    with open(state / SID / "events.jsonl", "ab") as fh:
        fh.write(b'{"n":3}\n')
    old = (state / SID / "events.jsonl")
    os.utime(old, (1, 1))  # written long ago: a known transcript still catches up
    assert mirror.apply("cs-1", run()) == {SID: ["events.jsonl"]}
    assert _events(mirror) == b'{"n":1}\n{"n":2}\n{"n":3}\n'


@pytest.mark.skipif(_git_bash() is None, reason="needs a GNU bash")
def test_a_line_longer_than_the_read_is_mirrored_by_reading_more(tmp_path):
    home = tmp_path / "home"
    (home / ".copilot" / "session-state" / SID).mkdir(parents=True)
    big = b'{"blob":"' + b"x" * 300 + b'"}\n'
    (home / ".copilot" / "session-state" / SID / "events.jsonl").write_bytes(b'{"n":1}\n' + big)
    env = {**os.environ, "HOME": str(home)}
    mirror = tm.TranscriptMirror(root=tmp_path / "mirror", chunk=64)

    def run() -> str:
        script = tm.remote_script(mirror.offsets("cs-1"), limits=mirror.limits("cs-1"), chunk=64)
        return subprocess.run([_git_bash(), "-c", script], capture_output=True, text=True,
                              env=env, check=True).stdout

    mirror.apply("cs-1", run())
    passes = 0
    while _events(mirror) != b'{"n":1}\n' + big and passes < 10:
        mirror.apply("cs-1", run())
        passes += 1
    assert _events(mirror) == b'{"n":1}\n' + big
    assert mirror.limits("cs-1") == {}  # back to the normal read once it moved


class _Manager:
    def __init__(self, stdout: str, exit_code: int = 0) -> None:
        self.stdout, self.exit_code, self.commands, self.disconnected = stdout, exit_code, [], 0

    async def exec(self, codespace, command, **_kw):
        self.commands.append(command)
        return types.SimpleNamespace(exit_code=self.exit_code, stdout=self.stdout, stderr="")

    async def disconnect(self, codespace):
        self.disconnected += 1


def _mirror_with(tmp_path, manager, pushes):
    async def opener(codespace):
        return manager

    def push(source, label):
        files = {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()}
        pushes.append((files, label))
        return True, "pushed"

    return tm.TranscriptMirror(open_manager=opener, push=push, root=tmp_path)


@pytest.fixture
def direct_exec(monkeypatch):
    async def run(manager, codespace, command, **kw):
        return await manager.exec(codespace, command, **kw)

    monkeypatch.setattr(tm, "exec_with_retry", run)


async def test_a_pass_pushes_its_own_namespace_only_when_something_changed(tmp_path, direct_exec):
    pushes = []
    manager = _Manager(_chunk(SID, 0, b'{"a":1}\n') + _chunk(SID2, 0, b'{"b":1}\n') + tm._DONE + "\n")
    mirror = _mirror_with(tmp_path, manager, pushes)
    result = await mirror("cs-1")
    assert result["ok"] and result["changed"] == 2
    # Its own label: the close-out capture's ``.codespaces/<name>`` is never touched.
    assert pushes[-1][1] == ".codespaces-live/cs-1"
    assert pushes[-1][0] == {f"session-state/{SID}/events.jsonl", f"session-state/{SID2}/events.jsonl"}
    assert manager.disconnected == 1
    # A snapshot of a directory that only grows: nothing it pushed before goes missing.
    manager.stdout = _chunk(SID2, 8, b'{"b":2}\n') + tm._DONE + "\n"
    assert (await mirror("cs-1"))["changed"] == 1
    assert pushes[-1][0] == {f"session-state/{SID}/events.jsonl", f"session-state/{SID2}/events.jsonl"}
    manager.stdout = tm._DONE + "\n"
    assert (await mirror("cs-1"))["changed"] == 0
    assert len(pushes) == 2


async def test_a_failed_read_or_a_bad_name_pushes_nothing(tmp_path, direct_exec):
    pushes = []
    mirror = _mirror_with(tmp_path, _Manager("", exit_code=255), pushes)
    assert not (await mirror("cs-1"))["ok"]
    assert not (await mirror("../x"))["ok"]
    assert pushes == []


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(owner, "OWNER_FILE", tmp_path / "connection-owner.json")
    monkeypatch.setattr(owner, "_LOCK_FILE", tmp_path / "connection-owner.lock")
    monkeypatch.setattr(owner, "LIVE_FILE", tmp_path / "connection-owner.live.json")
    monkeypatch.setattr(owner, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    return tmp_path


class _Channel:
    is_alive = True

    async def start(self):
        pass

    async def stop(self):
        pass


async def _probe_once(verdict, mirrored):
    async def session_probe(codespace, muxes):
        return {m: verdict for m in muxes}

    async def mirror(codespace):
        mirrored.append(codespace)

    forwards = sf.SessionForwards(
        lambda cs, port: _Channel(), session_probe, transcript_mirror=mirror,
    )
    owner.hold("cs-1", "cli:a", daemon_port=41234, mux_session="wt-a", confirmed=True)
    await forwards.probe(owner.list_holds())
    for task in list(forwards._mirroring.values()):
        await task
    await forwards.shutdown()


async def test_the_owner_mirrors_only_while_a_session_provably_runs(store):
    mirrored = []
    await _probe_once(True, mirrored)
    assert mirrored == ["cs-1"]
    for verdict in (False, None):  # stopped or unknown: never connect (it would wake the box)
        mirrored.clear()
        owner.release("cs-1", "cli:a")
        await _probe_once(verdict, mirrored)
        assert mirrored == []


async def test_a_failing_mirror_never_breaks_the_probe(store):
    async def session_probe(codespace, muxes):
        return {m: True for m in muxes}

    async def mirror(codespace):
        raise RuntimeError("boom")

    forwards = sf.SessionForwards(
        lambda cs, port: _Channel(), session_probe, transcript_mirror=mirror,
    )
    owner.hold("cs-1", "cli:a", daemon_port=41234, mux_session="wt-a", confirmed=True)
    await forwards.probe(owner.list_holds())
    await forwards._mirroring["cs-1"]
    assert owner.list_holds()[0].sessions  # tenant still renewed
    await forwards.shutdown()


def test_the_owner_can_turn_the_mirror_off(monkeypatch):
    from agent_codespaces import owner_cli

    monkeypatch.setenv("AGENT_CODESPACES_TRANSCRIPT_MIRROR", "0")
    assert owner_cli._transcript_mirror() is None
    monkeypatch.delenv("AGENT_CODESPACES_TRANSCRIPT_MIRROR")
    assert isinstance(owner_cli._transcript_mirror(), tm.TranscriptMirror)
