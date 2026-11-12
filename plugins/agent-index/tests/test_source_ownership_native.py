"""Opt-in real-engine process exclusion; private stores, no models or network."""

from __future__ import annotations

import queue
import os
import subprocess
import sys
import threading
import time

import numpy as np
import pytest
from agent_procutil import no_window_kwargs
from test_source_aware_store import chunk, cold as cold

from agent_index.indexing.gc import run_source_gc
from agent_index.indexing.state import IndexState

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
]


def test_selected_gc_compaction_leaves_retained_content_searchable(cold):
    config, store = cold
    retained = chunk("git:live", "needle retained")
    store.upsert(
        "small",
        [chunk("monorepo", "obsolete content"), retained],
        np.array([[1, 0], [0, 1]], dtype=np.float32),
    )
    assert store.ensure_fts_index()

    summary = run_source_gc(config, store, compact=True)

    assert summary["purged"] == {"monorepo": 1}
    assert summary["chunks_deleted"] == 1
    results = store.content_store.fts_search("needle")
    assert [chunk_id for chunk_id, _score in results] == [retained.chunk_id]
    receipt = store._source_domain.receipt()
    assert receipt["fts_coverage"]["chunks"] == receipt["versions"]["chunks"]


_CHILD = """
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import agent_index.sources
from agent_index.index_config import IndexConfig
from agent_index.indexing.engine import SourceSpec, run_reindex
from agent_index.sources.delta import SourceDelta

data_dir, alias, role = sys.argv[1:]
config = IndexConfig(data_dir=Path(data_dir), model_profiles={}, cluster_enabled=False)

def discover(last_commit, **kwargs):
    print("EVENT ACQUIRED", flush=True)
    if role in {"owner", "worker-owner", "crashed-owner"}:
        command = sys.stdin.readline().strip()
        if role == "crashed-owner" and command == "crash":
            os._exit(23)
        assert command == "release"
    return SourceDelta(commit="cursor-" + alias)

connector = SimpleNamespace(
    source_name="fixture:canonical", recovery_identity=("private-target",),
    discover_delta=discover,
)
agent_index.sources.get_connector = lambda *a, **k: connector

def run():
    result = run_reindex(
        config=config, source=alias, source_specs=[SourceSpec(name=alias, type="fixture")],
    )
    assert not result.get("sources_failed"), result

if role in {"worker-owner", "worker-contender"}:
    from functools import partial
    from agent_index.indexing import source_ownership
    from agent_index.indexing.task_store import TaskStore
    from agent_index.indexing.worker import run_worker
    tasks = TaskStore(config.data_dir / "tasks.db")
    task = tasks.enqueue(source=alias, full=False)
    assert tasks.dequeue_next().id == task.id
    indexer = partial(
        run_reindex, config=config, source_specs=[SourceSpec(name=alias, type="fixture")],
    )
    if role == "worker-contender":
        original_lock = source_ownership._try_lock
        waiting = False
        def lock(fd):
            global waiting
            acquired = original_lock(fd)
            if not acquired and not waiting:
                waiting = True
                assert tasks.get_task(task.id).status == "processing"
                print("EVENT WAITING", flush=True)
            return acquired
        source_ownership._try_lock = lock
    assert run_worker(task.id, config=config, indexer=indexer) == 0
    assert tasks.get_task(task.id).status == "complete"
elif role in {"owner", "crashed-owner"}:
    run()
else:
    try:
        run()
    except Exception as error:
        if type(error).__name__ != "SourcePipelineBusy":
            raise
        from agent_index.indexing.gc import run_source_gc
        from agent_index.indexing.state import IndexState
        load = IndexState.load
        def forbidden(*args):
            raise AssertionError("GC loaded state before admission")
        IndexState.load = forbidden
        try:
            run_source_gc(config, SimpleNamespace())
        except Exception as error:
            assert type(error).__name__ == "SourcePipelineBusy", error
        else:
            raise AssertionError("GC entered an owned data directory")
        finally:
            IndexState.load = load
        print("EVENT BUSY", flush=True)
    else:
        print("EVENT UNFENCED", flush=True)
    assert sys.stdin.readline().strip() == "retry"
    run()
print("EVENT COMPLETE", flush=True)
"""


class Child:
    def __init__(self, data_dir, alias, role, *, code=_CHILD, pass_fds=()):
        self.process = subprocess.Popen(  # noqa: S603 -- fixed contained private fixture
            [sys.executable, "-I", "-u", "-c", code, str(data_dir), alias, role],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, pass_fds=pass_fds, **no_window_kwargs(),
        )
        self.lines = []
        self.events = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.process.stdout:
            self.lines.append(line)
            if line.startswith("EVENT "):
                self.events.put(line.strip().removeprefix("EVENT "))
        self.events.put("EOF")

    def expect(self, event):
        try:
            actual = self.events.get(timeout=40)
        except queue.Empty:
            pytest.fail(f"Timed out waiting for {event}: {''.join(self.lines)}")
        assert actual == event, "".join(self.lines)

    def send(self, command):
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()


@pytest.mark.timeout(120)
@pytest.mark.parametrize(
    "entrypoint", ["owner", "worker-owner", "worker-contender", "crashed-owner"],
    ids=["direct", "queued-owner", "queued-contender", "crash-release"],
)
def test_actual_engine_owns_acquisition_and_reloads_state_before_retry(
    tmp_path, monkeypatch, entrypoint,
):
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    data_dir = tmp_path / "private-store"
    children = []
    try:
        owner = Child(data_dir, "alias-A", "owner" if entrypoint == "worker-contender" else entrypoint)
        children.append(owner)
        owner.expect("ACQUIRED")
        waiting = entrypoint == "worker-contender"
        contender = Child(data_dir, "alias-B", "worker-contender" if waiting else "contender")
        children.append(contender)
        contender.expect("WAITING" if waiting else "BUSY")
        crashed = entrypoint == "crashed-owner"
        owner.send("crash" if crashed else "release")
        owner.expect("EOF" if crashed else "COMPLETE")
        assert owner.process.wait(timeout=15) == (23 if crashed else 0), "".join(owner.lines)
        if not waiting:
            contender.send("retry")
        contender.expect("ACQUIRED")
        contender.expect("COMPLETE")
        assert contender.process.wait(timeout=15) == 0, "".join(contender.lines)
        state = IndexState.load(data_dir / "state.json")
        expected = {"alias-B": "cursor-alias-B"}
        if not crashed:
            expected["alias-A"] = "cursor-alias-A"
        assert {alias: source.last_commit for alias, source in state.sources.items()} == expected
    finally:
        # Release every fixture before waiting for any; a failed assertion may
        # leave either child waiting for its own next command.
        for child in children:
            child.process.stdin.close()
        deadline = time.monotonic() + 15
        for child in children:
            try:
                child.process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.process.kill()
                child.process.wait(timeout=5)
            child.reader.join(5)
            assert not child.reader.is_alive()
            child.process.stdout.close()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX descriptor inheritance")
@pytest.mark.timeout(45)
def test_forked_context_exit_cannot_unlock_the_parent(tmp_path):
    code = """
import os,sys
from agent_index.indexing.source_ownership import SourcePipelineBusy,source_writer
with source_writer(sys.argv[1]):
    pid = os.fork()
    if pid:
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0, status
if not pid:
    try:
        with source_writer(sys.argv[1]):
            pass
    except SourcePipelineBusy:
        os._exit(0)
    os._exit(12)
"""
    result = subprocess.run(  # noqa: S603 -- fixed contained private fixture
        [sys.executable, "-I", "-B", "-c", code, str(tmp_path / "forked")],
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX descriptor inheritance")
@pytest.mark.timeout(60)
def test_fork_during_descriptor_registration_cannot_retain_crashed_owner(tmp_path):
    code = """
import os,select,sys,threading
from agent_index.indexing import source_ownership as ownership
opened,attempted,returned,release,acquired = [threading.Event() for _ in range(5)]
native_open = os.open
def delayed_open(*args, **kwargs):
    fd = native_open(*args, **kwargs)
    opened.set()
    assert release.wait(10)
    return fd
ownership.os.open = delayed_open
def own():
    with ownership.source_writer(sys.argv[1]):
        os.write(1, b"EVENT ACQUIRED\\n")
        acquired.set()
        threading.Event().wait(20)
threading.Thread(target=own, daemon=True).start()
assert opened.wait(10)
def allow_registration():
    assert attempted.wait(10)
    # Old code returns from fork before registration. The fixed before-hook
    # blocks there, so release registration once that bounded wait expires.
    returned.wait(0.5)
    release.set()
threading.Thread(target=allow_registration, daemon=True).start()
os.register_at_fork(before=attempted.set, after_in_parent=returned.set)
pid = os.fork()
if not pid:
    os.write(1, b"EVENT CHILD\\n")
    control = int(sys.argv[2])
    if select.select([control], [], [], 20)[0]:
        os.read(control, 1)
        os.write(1, b"EVENT RELEASED\\n")
    else:
        os.write(1, b"EVENT EXPIRED\\n")
    os._exit(0)
assert acquired.wait(10)
os._exit(23)
"""
    read_fd, release_fd = os.pipe()
    owner = None
    try:
        owner = Child(
            tmp_path, str(read_fd), "fork-race", code=code, pass_fds=(read_fd,),
        )
        os.close(read_fd)
        read_fd = None
        try:
            observed = {owner.events.get(timeout=20), owner.events.get(timeout=20)}
        except queue.Empty:
            pytest.fail(f"Timed out waiting for fork fixture: {''.join(owner.lines)}")
        assert observed == {"ACQUIRED", "CHILD"}, "".join(owner.lines)
        assert owner.process.wait(timeout=10) == 23, "".join(owner.lines)
        result = subprocess.run(  # noqa: S603 -- fixed contained private fixture
            [sys.executable, "-I", "-c",
             "import sys; from agent_index.indexing.source_ownership import source_writer;"
             "\nwith source_writer(sys.argv[1]): pass", str(tmp_path)],
            capture_output=True, text=True, timeout=10, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        os.close(release_fd)
        if read_fd is not None:
            os.close(read_fd)
        if owner is not None:
            owner.process.stdin.close()
            owner.process.wait(timeout=25)
            owner.reader.join(25)
            assert not owner.reader.is_alive()
            owner.process.stdout.close()
            assert "EVENT RELEASED\n" in owner.lines, "".join(owner.lines)
