"""Reader lifetime and first-publication behavior across Windows replacement."""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from zdd import routing, routing_persistence


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows file sharing")
def test_route_reader_does_not_veto_atomic_publication(tmp_path: Path) -> None:
    path = tmp_path / "active.json"
    old = {"active": {"bind": "127.0.0.1", "port": 9281, "pid": 111}}
    new = {"active": {"bind": "127.0.0.1", "port": 9290, "pid": 222}}
    path.write_text(json.dumps(old), encoding="utf-8")
    with routing_persistence.open_reader(path) as reader:
        routing._atomic_write(path, new)
        assert json.load(reader) == old
        table = routing.read_table(tmp_path)
        assert table is not None and table["active"] == new["active"]


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows replacement flush")
def test_replacement_retains_uncertain_flush_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "active.json"
    path.write_text('{"active":"old"}', encoding="utf-8")
    real_fsync = os.fsync
    flushes: list[int] = []

    def fsync(descriptor: int) -> None:
        flushes.append(descriptor)
        if len(flushes) == 2:
            raise OSError("replacement flush unavailable")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fsync)
    with routing_persistence.open_reader(path) as reader:
        with pytest.raises(routing_persistence.PublicationCommitUncertain):
            routing_persistence.write(path, {"active": "new"})
        assert json.load(reader) == {"active": "old"}
    assert json.loads(path.read_text(encoding="utf-8")) == {"active": "new"}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native error codes")
@pytest.mark.parametrize("winerror", [1175, 1176, 1177])
def test_partial_replacement_is_not_reported_as_unchanged(
    tmp_path: Path, winerror: int,
) -> None:
    class Fail:
        def __init__(self, error: int) -> None:
            self.error = error

        def __call__(self, *_: object) -> int:
            getattr(ctypes, "set_last_error")(self.error)
            return 0

    kernel = SimpleNamespace(MoveFileExW=Fail(5), ReplaceFileW=Fail(winerror))
    with pytest.raises(routing_persistence.PublicationCommitUncertain):
        routing_persistence.windows_replace(tmp_path / "source", tmp_path / "target", kernel)


@pytest.mark.parametrize("missing_error", [2, 3])
def test_first_publication_keeps_original_sharing_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_error: int,
) -> None:
    path = tmp_path / "active.json"
    error_code = [0]
    moves: list[int] = []
    sleeps: list[float] = []

    class Move:
        def __call__(self, source: str, target: str, flags: int) -> int:
            moves.append(flags)
            if len(moves) == 1:
                error_code[0] = 5
                return 0
            os.replace(source, target)
            return 1

    class Replace:
        def __call__(self, *_: object) -> int:
            assert not path.exists()
            error_code[0] = missing_error
            return 0

    def failure(code: int) -> OSError:
        error = PermissionError("fixture native replacement error")
        setattr(error, "winerror", code)
        return error

    original = routing_persistence.windows_replace
    kernel = SimpleNamespace(MoveFileExW=Move(), ReplaceFileW=Replace())

    def replace(source: Path, target: Path) -> None:
        original(source, target, kernel)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(routing_persistence, "_last_error", lambda: error_code[0])
    monkeypatch.setattr(routing_persistence, "_windows_failure", failure)
    monkeypatch.setattr(routing_persistence, "windows_replace", replace)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    routing_persistence.write(path, {"active": "first"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"active": "first"}
    assert moves == [0x9, 0x9] and sleeps == [0.05]
