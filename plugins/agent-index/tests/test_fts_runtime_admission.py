"""FTS runtime lookup precedes handoff and never enters native-build recovery."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
import subprocess
import sys
import traceback
from types import SimpleNamespace
from typing import Any, NoReturn, cast

import pytest
from agent_procutil.owned_process import OutputLimitExceeded, OwnedCleanupError

from agent_index.store import content_fts_worker as worker
from agent_index.store.content_store import ContentStore


def forbidden(*args: object, **kwargs: object) -> NoReturn:
    pytest.fail("Refused admission must not hand off, build, retry cleanup, or sleep")


class FtsStore(worker._ContentFtsWorkerMixin):
    def __init__(self, path: Path, *, source: bool = False) -> None:
        self._db_path = str(path)
        self._table_name = "chunks"
        self._fts_available = True
        self._fts_dirty = True
        self._fts_consecutive_failures = 0
        self._fts_next_retry_at = 0.0
        self._source_domain = cast(Any, SimpleNamespace(fts_handoff=forbidden)) if source else None
        self.current = False

    def fts_current(self) -> bool:
        return self.current

    def _get_or_create_table(self) -> Any:
        return SimpleNamespace(count_rows=lambda: 1)

    def _durable_fts_index_exists(self, table: Any) -> bool:
        return True

    def _fts_file_lock(self) -> Any:
        return nullcontext(True)


@pytest.mark.parametrize("source", [False, True])
@pytest.mark.parametrize("full", [False, True])
def test_fts_admits_before_handoff_and_preserves_child_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: bool, full: bool,
) -> None:
    store = FtsStore(tmp_path, source=source)
    events: list[str] = []
    selected = str(tmp_path / "selected runtime")

    def admit(python: str, *, uv: str | None) -> str:
        assert python == sys.executable and uv == "bound UV"
        events.append("admit")
        return selected

    @contextmanager
    def handoff(*, table_name: str) -> Iterator[str]:
        assert table_name == "chunks" and events == ["admit"]
        events.append("handoff")
        yield "nonce"
        events.append("settled")

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        events.append("child")
        assert command[:4 if source else 3] == (
            [selected, "-I", "-B", "-c"] if source else [selected, "-B", "-c"]
        )
        if source:
            assert command[-4:] == [str(tmp_path), "chunks", "nonce", "1" if full else "0"]
        else:
            assert command[-2:] == [str(tmp_path), "chunks"]
        assert kwargs["capture_output"] and kwargs["text"] and kwargs["timeout"] == 19
        code = command[4 if source else 3]
        assert ("create_fts_index" in code) == (full or source)
        return subprocess.CompletedProcess(command, 0, "", "")

    if source:
        store._source_domain = cast(Any, SimpleNamespace(fts_handoff=handoff))
    monkeypatch.setenv("AGENT_INDEX_UV", "bound UV")
    monkeypatch.setattr(worker, "resolve_runtime_python", admit)
    monkeypatch.setattr(subprocess, "run", run)
    store._run_fts_build(full=full, timeout=19)
    assert events == (["admit", "handoff", "child", "settled"] if source else ["admit", "child"])


def test_current_fts_skips_runtime_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = FtsStore(tmp_path, source=True)
    store.current = True
    monkeypatch.setattr(worker, "resolve_runtime_python", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    store._run_fts_build(full=False)


@pytest.mark.parametrize("source", [False, True])
@pytest.mark.parametrize("failure", ["missing", "mismatch", "timeout", "output", "decode"])
def test_refused_fts_lookup_has_no_handoff_or_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: bool, failure: str,
) -> None:
    store = FtsStore(tmp_path, source=source)
    errors = {
        "missing": FileNotFoundError("missing UV"),
        "mismatch": RuntimeError("wrong runtime"),
        "timeout": subprocess.TimeoutExpired(["uv"], 10),
        "output": OutputLimitExceeded("stdout", 65536),
        "decode": UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
    }
    original = errors[failure]

    def refuse(*args: object, **kwargs: object) -> NoReturn:
        raise original

    monkeypatch.setattr(worker, "resolve_runtime_python", refuse)
    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(type(original)) as caught:
        store._run_fts_build(full=True)
    assert caught.value is original


@pytest.mark.parametrize("source", [False, True])
@pytest.mark.parametrize("escalating", [False, True])
def test_unconfirmed_fts_lookup_escapes_both_retry_handlers_and_blocks_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: bool, escalating: bool,
) -> None:
    store = FtsStore(tmp_path, source=source)
    original = OwnedCleanupError(
        SimpleNamespace(pid=123, returncode=None), object(),
        cleanup_resources=(("output", object()),), cleanup_retry=forbidden,
    )
    attempts: list[str] = []
    resources = original.cleanup_resources

    def refuse(*args: object, **kwargs: object) -> NoReturn:
        attempts.append("lookup")
        if escalating and len(attempts) == 1:
            raise RuntimeError("ordinary incremental build failure")
        raise original

    monkeypatch.setattr(worker, "resolve_runtime_python", refuse)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr("agent_index.store.content_store.time", SimpleNamespace(sleep=forbidden))
    with pytest.raises(OwnedCleanupError) as caught:
        ContentStore._rebuild_fts_locked(cast(ContentStore, store), max_retries=5)
    assert caught.value is original
    assert attempts == ["lookup"] * (2 if escalating else 1)
    assert store._fts_admission_cleanup is original and original.cleanup_resources is resources
    assert store._fts_dirty and store._fts_consecutive_failures == 0 and store._fts_next_retry_at == 0
    store.current = True
    depths = []
    for _ in range(4):
        with pytest.raises(OwnedCleanupError) as retained:
            store._run_fts_build(full=False)
        assert retained.value is original
        depths.append(len(traceback.extract_tb(original.__traceback__)))
    assert len(set(depths)) == 1
    assert attempts == ["lookup"] * (2 if escalating else 1)
