"""Opt-in real-process token transactions on synthetic, caller-owned stores."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading

import agent_procutil as pu
import pytest
from single_instance_lease import OperationLock, OperationLockedError

from agent_codespaces import relay_token


def _spawn(path, script):
    prelude = (
        "import sys; from pathlib import Path; "
        "from agent_codespaces import relay_token as r; "
        "r._TOKENS_FILE=Path(sys.argv[1]); "
    )
    return pu.spawn_sync_in_kill_on_close_job(
        [sys.executable, "-c", prelude + script, str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **pu.no_window_kwargs(),
    )


def _line(child):
    result = queue.Queue()
    reader = threading.Thread(target=lambda: result.put(child.stdout.readline()), daemon=True)
    reader.start()
    line = result.get(timeout=15)
    reader.join(timeout=1)
    return line.strip()


def _retire(child, job):
    try:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)
    finally:
        if job is not None:
            job.close()


@pytest.mark.parametrize("operation", ["mint", "revoke"])
def test_independent_mutations_cannot_publish_stale_snapshots(tmp_path, monkeypatch, operation):
    path = tmp_path / "relay-tokens.json"
    monkeypatch.setattr(relay_token, "_TOKENS_FILE", path)
    original = relay_token.token_for("existing")
    first, first_job = _spawn(path, """
write = r._write_tokens
def paused(data):
    print("snapshot-ready", flush=True)
    assert sys.stdin.readline().strip() == "publish"
    write(data)
r._write_tokens = paused
""" + ("r.token_for('first')" if operation == "mint" else "r.revoke('existing')"))
    second = second_job = None
    try:
        assert _line(first) == "snapshot-ready"
        with pytest.raises(OperationLockedError):
            with OperationLock(path.with_suffix(".json.lock")):
                pass
        second, second_job = _spawn(
            path, "print('mutation-ready', flush=True); r.token_for('second')",
        )
        assert _line(second) == "mutation-ready"
        with pytest.raises(subprocess.TimeoutExpired):
            second.wait(timeout=0.25)
        _, error = first.communicate("publish\n", timeout=15)
        assert first.returncode == 0, error
        _, error = second.communicate(timeout=15)
        assert second.returncode == 0, error
        tokens = json.loads(path.read_text(encoding="utf-8"))
        assert "second" in tokens
        if operation == "mint":
            assert "first" in tokens
            assert relay_token.token_for("existing") == original
        else:
            assert "existing" not in tokens
        assert not list(tmp_path.glob(".*.tmp"))
    finally:
        _retire(first, first_job)
        if second is not None:
            _retire(second, second_job)


def test_independent_first_touch_reuses_one_secret(tmp_path, monkeypatch):
    path = tmp_path / "new" / "relay-tokens.json"
    children = [
        _spawn(path, "import hashlib; print(hashlib.sha256(r.token_for('shared').encode()).hexdigest())")
        for _ in range(4)
    ]
    try:
        fingerprints = []
        for child, _ in children:
            output, error = child.communicate(timeout=15)
            assert child.returncode == 0, error
            fingerprints.append(output.strip())
        monkeypatch.setattr(relay_token, "_TOKENS_FILE", path)
        stored = json.loads(path.read_text(encoding="utf-8"))["shared"]["token"]
        assert relay_token.token_for("shared") == stored
        assert set(fingerprints) == {hashlib.sha256(stored.encode()).hexdigest()}
        assert not list(path.parent.glob(".*.tmp"))
    finally:
        for child, job in children:
            _retire(child, job)


@pytest.mark.skipif(os.name != "nt", reason="native Windows sharing semantics")
@pytest.mark.parametrize("release_reader", [False, True])
def test_windows_reader_without_delete_sharing(tmp_path, monkeypatch, release_reader):
    from ctypes import wintypes

    path = tmp_path / "relay-tokens.json"
    monkeypatch.setattr(relay_token, "_TOKENS_FILE", path)
    relay_token.token_for("existing")
    before = path.read_bytes()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # Share reads/writes, deliberately not deletion (atomic replacement).
    handle = kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x80, None)
    assert handle != wintypes.HANDLE(-1).value, ctypes.get_last_error()
    child = job = None
    try:
        child, job = _spawn(path, """
write = r.atomic_write_text
def announced(*args, **kwargs):
    try:
        return write(*args, **kwargs)
    except OSError:
        print("write-blocked", flush=True)
        raise
r.atomic_write_text = announced
try:
    r.token_for("new")
except OSError:
    print("blocked", flush=True)
    sys.exit(7)
print("published", flush=True)
""")
        assert _line(child) == "write-blocked"
        if release_reader:
            assert kernel.CloseHandle(handle)
            handle = None
        output, error = child.communicate(timeout=15)
        assert child.returncode == (0 if release_reader else 7), error
        if release_reader:
            assert "published" in output
            assert set(json.loads(path.read_text(encoding="utf-8"))) == {"existing", "new"}
        else:
            assert "blocked" in output
            assert path.read_bytes() == before
        assert not list(tmp_path.glob(".*.tmp"))
    finally:
        if handle is not None:
            assert kernel.CloseHandle(handle)
        if child is not None:
            _retire(child, job)
