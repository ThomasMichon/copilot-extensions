"""Private platform cache selection, without touching installed application state."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agent_procutil import python_cache as cache
from agent_procutil import python_cache_permissions as permissions
from agent_procutil import python_cache_store as store
from agent_procutil import no_window_kwargs


@pytest.fixture
def local_cache(tmp_path, monkeypatch):
    base = store.native(tmp_path / "user-cache")
    monkeypatch.setattr(store, "user_cache", lambda: base)
    monkeypatch.setattr(cache, "_store", lambda: store)
    return base


def _installed(root):
    target = root / "versions" / "1.2.3"
    record = target / "Lib" / "site-packages" / "fixture.dist-info" / "RECORD"
    record.parent.mkdir(parents=True)
    record.write_text("fixture.py,sha256=" + "A" * 43 + ",12\n", encoding="utf-8")
    (target / ".install-complete.json").write_text(json.dumps({
        "version": target.name, "completed_at": "fixture", "pid": 1,
        "payload_hash": "a" * 64,
    }), encoding="utf-8")
    return target


def test_completed_target_reuses_private_os_cache_and_records_real_owner(tmp_path, local_cache):
    root = tmp_path / "installation"
    target = _installed(root)
    store.prepare(root, target, cache._installed_identity(target), read_record=cache._read_regular)
    arguments = cache.policy_arguments(root, runtime=target)
    assert arguments[0] == "-X", arguments
    prefix = Path(arguments[1].split("=", 1)[1])
    assert prefix.is_relative_to(local_cache) and prefix.name == "bytecode"
    assert arguments == cache.policy_arguments(root, runtime=target)
    assert not (root / "cache").exists()
    owner = json.loads((prefix.parent / "owner.json").read_text(encoding="utf-8"))
    assert owner["runtime"] == store.normalized(target)
    assert owner["runtime_root"] == store.normalized(root)
    permissions.check(prefix)


def test_targets_and_installations_do_not_alias(tmp_path, local_cache):
    roots = [tmp_path / name for name in ("first", "second")]
    targets = [_installed(root) for root in roots]
    assert cache.policy_arguments(roots[0], runtime=targets[0]) != cache.policy_arguments(
        roots[1], runtime=targets[1],
    )
    assert len(list(local_cache.glob("copilot-extensions/python/v1/*/installed-*"))) == 2
    assert cache.policy_arguments(roots[1], runtime=targets[0]) == ["-B"]


def test_catalog_indexes_one_owner_and_corruption_disables_cache(tmp_path, local_cache):
    root = tmp_path / "installation"
    target = _installed(root)
    selected = cache.policy_arguments(root, runtime=target)
    assert selected[0] == "-X"
    prefix = Path(selected[1].split("=", 1)[1])
    catalog = prefix.parent.parent / "catalog.sqlite"
    assert selected == cache.policy_arguments(root, runtime=target)
    with sqlite3.connect(catalog) as connection:
        assert connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1
        connection.execute("PRAGMA user_version=9")
    assert cache.policy_arguments(root, runtime=target) == ["-B"]
    with sqlite3.connect(catalog) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 9


def test_unsealed_invocation_does_not_allocate_any_namespace(tmp_path, local_cache):
    root = tmp_path / "installation"
    for _ in range(50):
        assert cache.policy_arguments(root, runtime=tmp_path / "unsealed") == ["-B"]
    assert not local_cache.exists() and not root.exists()


def test_warm_selection_never_inventories_cache_contents(tmp_path, local_cache, monkeypatch):
    root = tmp_path / "installation"
    target = _installed(root)
    expected = cache.policy_arguments(root, runtime=target)
    real_scandir = os.scandir

    def bounded(path):
        if not isinstance(path, int) and Path(path).is_relative_to(local_cache):
            pytest.fail("cache allocation must not enumerate its cache tree")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", bounded)
    assert cache.policy_arguments(root, runtime=target) == expected


def test_relative_override_does_not_select_working_directory(tmp_path, monkeypatch):
    if os.name == "nt":
        monkeypatch.setenv("LOCALAPPDATA", "relative-cache")
        monkeypatch.setattr(store, "_local_appdata", lambda: tmp_path / "known-folder")
        assert store.normalized(store.user_cache()) == store.normalized(tmp_path / "known-folder")
    elif sys.platform != "darwin":
        monkeypatch.setenv("XDG_CACHE_HOME", "relative-cache")
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        assert store.user_cache() == tmp_path / "home" / ".cache"


def test_matching_record_does_not_override_native_permission_refusal(
    tmp_path, local_cache, monkeypatch,
):
    root = tmp_path / "installation"
    target = _installed(root)
    arguments = cache.policy_arguments(root, runtime=target)
    assert arguments[0] == "-X"
    real_check = permissions.check

    def reject(path, **kwargs):
        if path.name.startswith("installed-"):
            raise PermissionError("foreign owner")
        real_check(path, **kwargs)

    monkeypatch.setattr(permissions, "check", reject)
    monkeypatch.setattr(store, "component", lambda name: permissions if name == "python_cache_permissions"
                        else __import__("agent_procutil." + name, fromlist=["_"]))
    assert cache.policy_arguments(root, runtime=target) == ["-B"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX native ownership/mode boundary")
def test_shared_writable_base_or_ancestor_is_never_selected(tmp_path, local_cache):
    root = tmp_path / "installation"
    target = _installed(root)
    local_cache.mkdir(mode=0o777)
    local_cache.chmod(0o777)
    assert cache.policy_arguments(root, runtime=target) == ["-B"]
    local_cache.chmod(0o700)
    tmp_path.chmod(0o777)
    try:
        assert cache.policy_arguments(root, runtime=target) == ["-B"]
    finally:
        tmp_path.chmod(0o700)


def test_conflicting_or_oversized_owner_record_is_preserved(tmp_path, local_cache):
    root = tmp_path / "installation"
    target = _installed(root)
    selected = cache.policy_arguments(root, runtime=target)
    assert selected[0] == "-X"
    record = Path(selected[1].split("=", 1)[1]).parent / "owner.json"
    for data in (b"{}", b"x" * 16385):
        record.write_bytes(data)
        assert cache.policy_arguments(root, runtime=target) == ["-B"]
        assert record.read_bytes() == data


def test_real_interpreter_reuses_external_bytecode_and_unsealed_writes_nothing(
    tmp_path, local_cache,
):
    root = tmp_path / "installation"
    target = _installed(root)
    source = tmp_path / "source"
    source.mkdir()
    (source / "witness.py").write_text("VALUE = 42\n", encoding="utf-8")
    code = (
        "import sys,json; compiled=[]; "
        "sys.addaudithook(lambda e,a: compiled.append(1) if e=='compile' else None); "
        "sys.path.insert(0,sys.argv[1]); import witness; "
        "print(json.dumps([witness.VALUE,witness.__cached__,sys.dont_write_bytecode,len(compiled)]))"
    )

    def invoke(arguments):
        result = subprocess.run(
            [sys.executable, "-I", *arguments, "-c", code, str(source)],
            capture_output=True, text=True, timeout=20, check=True, **no_window_kwargs(),
        )
        return json.loads(result.stdout)

    arguments = cache.policy_arguments(root, runtime=target)
    cold, warm = invoke(arguments), invoke(arguments)
    assert cold[:3] == warm[:3] and cold[0] == 42 and cold[2] is False
    assert cold[3] > warm[3] and Path(cold[1]).is_file()
    assert Path(cold[1]).is_relative_to(local_cache)
    assert invoke(cache.policy_arguments(root, runtime=source))[2] is True
    assert not (source / "__pycache__").exists()
    assert not (root / "cache").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows native DACL protection")
def test_native_windows_shared_writable_cache_is_not_selected(tmp_path, local_cache):
    import ctypes
    from ctypes import wintypes

    root = tmp_path / "installation"
    target = _installed(root)
    permissions.mkdir(local_cache)
    owner = permissions._owner()
    user = owner._current_sids(os.getpid())[0]
    advapi, kernel = owner._apis()
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    advapi.SetFileSecurityW.restype = wintypes.BOOL
    descriptor = ctypes.c_void_p()
    assert advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"O:{user}D:P(A;;FA;;;{user})(A;;FA;;;AU)", 1, ctypes.byref(descriptor), None,
    )
    try:
        assert advapi.SetFileSecurityW(str(local_cache), 0x80000004, descriptor)
        assert cache.policy_arguments(root, runtime=target) == ["-B"]
        assert not (local_cache / "copilot-extensions").exists()
    finally:
        kernel.LocalFree(descriptor)
