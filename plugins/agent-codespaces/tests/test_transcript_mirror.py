"""Tests for the Connection Owner's transcript mirror (``transcript_mirror``)."""

from __future__ import annotations

import asyncio
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


def test_the_script_carries_limits_for_new_sessions_too():
    script = tm.remote_script({}, limits={SID: 128, "../etc": 256}, chunk=64)
    assert f"{SID}:0:128" in script
    assert "../etc" not in script


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


def test_a_new_torn_first_line_does_not_create_a_session_dir_or_workspace(tmp_path):
    mirror = tm.TranscriptMirror(root=tmp_path, chunk=8)
    text = (
        _chunk(SID, 0, b'{"partial', size=99)
        + f"{tm._WORKSPACE}{SID}\n{base64.b64encode(b'cwd: /w').decode()}\n"
        + tm._DONE + "\n"
    )
    assert mirror.apply("cs-1", text) == {}
    assert not (tmp_path / "cs-1" / "session-state" / SID).exists()
    assert mirror.limits("cs-1") == {SID: 16}


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
def test_a_new_first_line_longer_than_the_read_is_mirrored_by_reading_more(tmp_path):
    home = tmp_path / "home"
    (home / ".copilot" / "session-state" / SID).mkdir(parents=True)
    big = b'{"blob":"' + b"x" * 300 + b'"}\n'
    (home / ".copilot" / "session-state" / SID / "events.jsonl").write_bytes(big)
    env = {**os.environ, "HOME": str(home)}
    mirror = tm.TranscriptMirror(root=tmp_path / "mirror", chunk=64)

    def run() -> str:
        script = tm.remote_script(mirror.offsets("cs-1"), limits=mirror.limits("cs-1"), chunk=64)
        return subprocess.run([_git_bash(), "-c", script], capture_output=True, text=True,
                              env=env, check=True).stdout

    assert mirror.apply("cs-1", run()) == {}
    assert not (mirror._root / "cs-1" / "session-state" / SID).exists()
    passes = 1
    while not (mirror._root / "cs-1" / "session-state" / SID / "events.jsonl").is_file() and passes < 10:
        mirror.apply("cs-1", run())
        passes += 1
    assert _events(mirror) == big
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


async def test_a_pass_with_no_whole_new_line_does_not_push(tmp_path, direct_exec):
    pushes = []
    manager = _Manager(_chunk(SID, 0, b'{"partial', size=99) + tm._DONE + "\n")
    mirror = _mirror_with(tmp_path, manager, pushes)
    assert (await mirror("cs-1"))["changed"] == 0
    assert pushes == []
    assert not (tmp_path / "cs-1.dirty").exists()


async def test_a_failed_read_or_a_bad_name_pushes_nothing(tmp_path, direct_exec):
    pushes = []
    mirror = _mirror_with(tmp_path, _Manager("", exit_code=255), pushes)
    assert not (await mirror("cs-1"))["ok"]
    assert not (await mirror("../x"))["ok"]
    assert pushes == []


async def test_a_failed_push_is_retried_until_it_lands_even_after_a_restart(tmp_path, direct_exec):
    results = [(False, "hub unreachable"), (False, "still down"), (False, "down"), (True, "pushed")]
    pushes = []

    def push(source, label):
        pushes.append(label)
        return results[len(pushes) - 1]

    manager = _Manager(_chunk(SID, 0, b'{"a":1}\n') + tm._DONE + "\n")

    async def opener(codespace):
        return manager

    mirror = tm.TranscriptMirror(open_manager=opener, push=push, root=tmp_path)
    assert (await mirror("cs-1"))["ok"] is False
    manager.stdout = tm._DONE + "\n"  # nothing new on the box: the push is still owed
    assert (await mirror("cs-1"))["ok"] is False
    manager.exit_code = 255  # the box can't be read either: the owed push still goes ahead
    assert (await mirror("cs-1"))["ok"] is False
    manager.exit_code = 0
    restarted = tm.TranscriptMirror(open_manager=opener, push=push, root=tmp_path)
    assert (await restarted("cs-1"))["ok"] is True
    assert len(pushes) == 4 and not (tmp_path / "cs-1.dirty").exists()
    assert (await restarted("cs-1")) == {"ok": True, "changed": 0}  # settled: no more pushes
    assert len(pushes) == 4

    async def unreachable(codespace):
        raise OSError("tunnel down")

    settled = tm.TranscriptMirror(open_manager=unreachable, push=push, root=tmp_path)
    assert (await settled("cs-1"))["ok"] is False and len(pushes) == 4  # nothing owed: no push


@pytest.mark.parametrize("output, landed", [
    ("session-sync: ok -> /hub/.codespaces-live/cs-1 (3 files)", True),
    ("session-sync: ok -> /hub/x (3 files)\nsession-sync: excluded 1 detritus file(s) in 1 root(s) (0.1 MiB)", True),
    ("session-sync: disabled via AGENT_LOGGER_SYNC_DISABLED", False),
    ("session-sync: ok -> /hub/x (skipped 1 locked file(s), will retry: a/events.jsonl) (3 files)", False),
    ("", False),
])
def test_only_a_whole_publication_counts_as_landed(output, landed):
    assert tm.landed_whole(output) is landed


async def test_a_zero_exit_push_that_did_not_land_whole_keeps_the_debt(tmp_path, direct_exec, monkeypatch):
    import agent_codespaces.sessions as sessions

    outputs = [
        "session-sync: disabled via AGENT_LOGGER_SYNC_DISABLED",
        "session-sync: ok -> /hub/x (skipped 1 locked file(s), will retry: e) (1 files)",
        "session-sync: ok -> /hub/x (1 files)",
    ]
    calls = []

    def fake(source, label, *, verbose):
        calls.append(label)
        return True, outputs[len(calls) - 1]  # session-sync exits 0 every time

    monkeypatch.setattr(sessions, "_push_via_session_sync", fake)
    manager = _Manager(_chunk(SID, 0, b'{"a":1}\n') + tm._DONE + "\n")

    async def opener(codespace):
        return manager

    mirror = tm.TranscriptMirror(open_manager=opener, root=tmp_path)
    assert (await mirror("cs-1"))["ok"] is False  # disabled: nothing was pushed
    assert (tmp_path / "cs-1.dirty").exists()
    manager.stdout = tm._DONE + "\n"
    assert (await mirror("cs-1"))["ok"] is False  # partial: a locked file was skipped
    assert (tmp_path / "cs-1.dirty").exists()
    assert (await mirror("cs-1"))["ok"] is True
    assert not (tmp_path / "cs-1.dirty").exists() and len(calls) == 3


async def test_a_pass_skips_a_codespace_another_owner_is_mirroring(tmp_path, direct_exec):
    from single_instance_lease import SingleInstance

    pushes = []
    mirror = _mirror_with(tmp_path, _Manager(_chunk(SID, 0, b'{"a":1}\n') + tm._DONE + "\n"), pushes)
    other = SingleInstance(tmp_path, service="transcript-mirror", lock_name="cs-1.lock")
    other.acquire()
    try:
        out = await mirror("cs-1")
        assert out["changed"] == 0 and "another pass" in out["detail"] and pushes == []
    finally:
        other.release()
    assert (await mirror("cs-1"))["changed"] == 1


def test_prune_if_clean_removes_only_settled_mirrors(tmp_path):
    mirror = tm.TranscriptMirror(root=tmp_path)
    (tmp_path / "cs-1" / "session-state" / SID).mkdir(parents=True)
    (tmp_path / "cs-1" / "session-state" / SID / "events.jsonl").write_text(
        "{}\n", encoding="utf-8",
    )
    assert mirror.prune_if_clean("cs-1")
    assert not (tmp_path / "cs-1").exists()
    assert (tmp_path / "cs-1.lock").exists()  # kept: removing it would split the lock

    (tmp_path / "cs-1" / "session-state" / SID).mkdir(parents=True)
    (tmp_path / "cs-1.dirty").touch()
    assert not mirror.prune_if_clean("cs-1")
    assert (tmp_path / "cs-1").exists()


async def test_a_prune_requested_while_a_push_is_owed_happens_once_it_lands(tmp_path):
    from single_instance_lease import SingleInstance

    results = [(False, "hub down"), (True, "pushed")]
    pushes = []

    def push(source, label):
        pushes.append(label)
        return results[len(pushes) - 1]

    mirror = tm.TranscriptMirror(push=push, root=tmp_path)
    (tmp_path / "cs-1" / "session-state" / SID).mkdir(parents=True)
    (tmp_path / "cs-1.dirty").touch()
    assert not mirror.request_prune("cs-1")  # deleted with a push still owed: kept, and remembered
    assert (tmp_path / "cs-1.prune").exists() and mirror.owed_codespaces() == ["cs-1"]
    restarted = tm.TranscriptMirror(push=push, root=tmp_path)
    await restarted.push_owed("cs-1")  # the push fails: still owed, still there
    assert (tmp_path / "cs-1").exists() and (tmp_path / "cs-1.prune").exists()
    await restarted.push_owed("cs-1")  # it lands: the requested prune follows
    assert not (tmp_path / "cs-1").exists() and not (tmp_path / "cs-1.prune").exists()
    assert restarted.owed_codespaces() == [] and len(pushes) == 2

    # Deleted mid-pass (its lock held, nothing owed): the next owed pass prunes it.
    (tmp_path / "cs-2" / "session-state" / SID).mkdir(parents=True)
    held = SingleInstance(tmp_path, service="transcript-mirror", lock_name="cs-2.lock")
    held.acquire()
    try:
        assert not mirror.request_prune("cs-2")
    finally:
        held.release()
    assert mirror.owed_codespaces() == ["cs-2"]
    assert (await mirror.push_owed("cs-2")) == {"ok": True, "changed": 0}
    assert not (tmp_path / "cs-2").exists() and mirror.owed_codespaces() == []


async def test_a_push_debt_with_no_mirror_left_is_dropped_not_retried_forever(tmp_path):
    pushes = []
    mirror = tm.TranscriptMirror(push=lambda source, label: pushes.append(label) or (False, "no source"),
                                 root=tmp_path)
    (tmp_path / "cs-1.dirty").touch()  # its directory never made it (or was removed by hand)
    assert (await mirror.push_owed("cs-1")) == {"ok": True, "changed": 0}
    assert pushes == [] and mirror.owed_codespaces() == []


async def test_a_cancelled_pass_keeps_the_codespace_until_its_push_finishes(tmp_path, direct_exec):
    import threading

    from single_instance_lease import AlreadyRunningError, SingleInstance

    entered, go = threading.Event(), threading.Event()

    def slow_push(source, label):
        entered.set()
        go.wait(10)
        return True, "pushed"

    async def opener(codespace):
        return _Manager(_chunk(SID, 0, b'{"a":1}\n') + tm._DONE + "\n")

    mirror = tm.TranscriptMirror(open_manager=opener, push=slow_push, root=tmp_path)
    task = asyncio.ensure_future(mirror("cs-1"))
    await asyncio.to_thread(entered.wait, 10)
    task.cancel()  # the Owner shutting down
    with pytest.raises(asyncio.CancelledError):
        await task
    probe = SingleInstance(tmp_path, service="transcript-mirror", lock_name="cs-1.lock")
    with pytest.raises(AlreadyRunningError):  # the push thread still runs, so the lock holds
        probe.acquire()
    go.set()
    for _ in range(100):
        try:
            probe.acquire()
            break
        except AlreadyRunningError:
            await asyncio.sleep(0.05)
    assert probe.held
    probe.release()


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
