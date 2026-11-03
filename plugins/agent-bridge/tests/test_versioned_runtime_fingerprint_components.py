"""Fingerprint framing stays read-only and component loading stays serialized."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType, SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs


def _fixture_runtime(tmp_path):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    for name in ("versioned_runtime.py", "versioned_runtime_source.py", "versioned_runtime_leases.py"):
        shutil.copyfile(scripts / name, tmp_path / name)
    spec = importlib.util.spec_from_file_location(
        "fingerprint_components_fixture", tmp_path / "versioned_runtime.py",
    )
    runtime = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runtime
    spec.loader.exec_module(runtime)
    return runtime


def test_fingerprint_component_load_does_not_write_into_its_declared_input(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    runtime = _fixture_runtime(tmp_path)
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    first = runtime.fingerprint_source([tmp_path])
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before
    assert runtime.fingerprint_source([tmp_path]) == first


def test_first_concurrent_fingerprints_initialize_the_component_once(tmp_path, monkeypatch):
    runtime = _fixture_runtime(tmp_path)
    payload = tmp_path / "payload"
    payload.write_bytes(b"payload")
    gate = ModuleType("fingerprint_component_gate")
    gate.entered, gate.release, gate.calls = threading.Event(), threading.Event(), []
    monkeypatch.setitem(sys.modules, gate.__name__, gate)
    source = tmp_path / "versioned_runtime_source.py"
    source.write_text(source.read_text(encoding="utf-8").replace(
        "from __future__ import annotations\n",
        "from __future__ import annotations\n"
        "import fingerprint_component_gate as gate\n"
        "gate.calls.append(1)\ngate.entered.set()\n"
        "if not gate.release.wait(5):\n    raise RuntimeError('component gate timed out')\n",
        1,
    ), encoding="utf-8")
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(runtime.fingerprint_source, [payload])
        try:
            assert gate.entered.wait(3)
            acquired = runtime._runtime_components_lock.acquire(blocking=False)
            if acquired:
                runtime._runtime_components_lock.release()
            assert not acquired, "component initialization was not serialized"
            second = executor.submit(runtime.fingerprint_source, [payload])
        finally:
            gate.release.set()
        assert first.result(timeout=3) == second.result(timeout=3)
    assert gate.calls == [1]


def test_windows_read_only_fingerprint_batches_preserve_exact_framing(tmp_path, monkeypatch):
    import hashlib

    runtime = _fixture_runtime(tmp_path)
    source = runtime._runtime_component("versioned_runtime_source")
    monkeypatch.setattr(source, "os", SimpleNamespace(name="nt"))
    entries, expected, hashes = {}, hashlib.sha256(), {}
    for index in range(8):
        path = tmp_path / f"input-{index}"
        data = f"content-{index}".encode()
        path.write_bytes(data)
        entries[path] = "file"
        hashes[path] = hashlib.sha256(data).hexdigest()
        for field in (path.name.encode(), b"file", data):
            expected.update(len(field).to_bytes(8, "big") + field)
    caller = threading.get_ident()
    barrier, lock = threading.Barrier(4), threading.Lock()
    active = peak = 0
    read_bytes = Path.read_bytes

    def read(path):
        nonlocal active, peak
        assert threading.get_ident() != caller
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(timeout=3)
            return read_bytes(path)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(Path, "read_bytes", read)
    observed = {}
    result = source.fingerprint_entries(
        entries, label=lambda path: path.name, root_label=str, roots=[tmp_path],
        normalize_link=lambda value: value, path_type=Path,
        file_hashes=observed, on_file=None,
    )
    assert result == expected.hexdigest() and observed == hashes
    assert active == 0 and peak == 4


@pytest.mark.parametrize("error", [PermissionError("denied"), FileNotFoundError("missing")])
def test_windows_fingerprint_worker_read_failures_remain_explicit(tmp_path, monkeypatch, error):
    runtime = _fixture_runtime(tmp_path)
    source = runtime._runtime_component("versioned_runtime_source")
    monkeypatch.setattr(source, "os", SimpleNamespace(name="nt"))
    entries = {tmp_path / f"input-{index}": "file" for index in range(8)}
    for path in entries:
        path.write_bytes(b"content")
    read_bytes = Path.read_bytes

    def read(path):
        if path.name == "input-2":
            raise error
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(OSError, match="fingerprint_source: could not read") as raised:
        source.fingerprint_entries(
            entries, label=lambda path: path.name, root_label=str, roots=[tmp_path],
            normalize_link=lambda value: value, path_type=Path,
            file_hashes=None, on_file=None,
        )
    assert raised.value.__cause__ is error


def test_windows_fingerprint_callbacks_keep_sequential_reads_on_the_caller(tmp_path, monkeypatch):
    runtime = _fixture_runtime(tmp_path)
    source = runtime._runtime_component("versioned_runtime_source")
    monkeypatch.setattr(source, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        source, "ThreadPoolExecutor",
        lambda **_kwargs: pytest.fail("callback-bearing input admission cannot prefetch"),
    )
    entries = {tmp_path / f"input-{index}": "file" for index in range(8)}
    for path in entries:
        path.write_bytes(b"original")
    last = next(reversed(entries))
    observed = []
    caller = threading.get_ident()

    def retain(path, data):
        assert threading.get_ident() == caller
        observed.append((path, data))
        last.write_bytes(b"changed")

    source.fingerprint_entries(
        entries, label=lambda path: path.name, root_label=str, roots=[tmp_path],
        normalize_link=lambda value: value, path_type=Path,
        file_hashes=None, on_file=retain,
    )
    assert [path for path, _ in observed] == list(entries)
    assert observed[-1] == (last, b"changed")


@pytest.mark.skipif(os.name != "posix", reason="native fork while component initialization is active")
def test_fork_reinitializes_the_component_lock_before_child_fingerprinting(tmp_path):
    _fixture_runtime(tmp_path)
    code = """
import importlib.util,os,signal,sys,threading,types
from pathlib import Path
root=Path(sys.argv[1]);payload=root/"payload";payload.write_bytes(b"payload")
spec=importlib.util.spec_from_file_location("fork_fingerprint_runtime",root/"versioned_runtime.py")
runtime=importlib.util.module_from_spec(spec);sys.modules[spec.name]=runtime;spec.loader.exec_module(runtime)
gate=types.ModuleType("fork_fingerprint_gate");gate.entered=threading.Event();gate.release=threading.Event()
sys.modules[gate.__name__]=gate
source=root/"versioned_runtime_source.py"
source.write_text(source.read_text().replace("from __future__ import annotations\\n",
    "from __future__ import annotations\\nimport fork_fingerprint_gate as gate\\n"
    "gate.entered.set()\\nassert gate.release.wait(5)\\n",1))
worker=threading.Thread(target=runtime.fingerprint_source,args=([payload],))
worker.start()
try:
    assert gate.entered.wait(3)
    pid=os.fork()
    if pid==0:
        signal.signal(signal.SIGALRM,signal.SIG_DFL);signal.alarm(3)
        gate.release.set()
        runtime.fingerprint_source([payload])
        os._exit(0)
    _,status=os.waitpid(pid,0)
    assert os.waitstatus_to_exitcode(status)==0,status
finally:
    gate.release.set();worker.join(timeout=3)
assert not worker.is_alive()
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
