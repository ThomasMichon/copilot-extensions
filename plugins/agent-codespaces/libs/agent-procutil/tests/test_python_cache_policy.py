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
from agent_procutil import python_cache_catalog as catalog
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


def test_interrupted_catalog_initialization_recovers_without_resetting_unknown_schema(
    tmp_path, local_cache, monkeypatch,
):
    root = tmp_path / "installation"
    target = _installed(root)
    connect = sqlite3.connect

    class Interrupted(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith("CREATE TABLE"):
                raise sqlite3.OperationalError("interrupted initialization")
            return super().execute(sql, *args)

    with monkeypatch.context() as patch:
        patch.setattr(catalog.sqlite3, "connect", lambda *a, **kw: connect(*a, factory=Interrupted, **kw))
        assert cache.policy_arguments(root, runtime=target) == ["-B"]
    assert cache.policy_arguments(root, runtime=target)[0] == "-X"


def test_interrupted_owner_publication_never_exposes_partial_record(tmp_path, local_cache, monkeypatch):
    root = tmp_path / "installation"
    target = _installed(root)

    def interrupted(*args, **kwargs):
        raise OSError("interrupted before immutable record publication")

    with monkeypatch.context() as patch:
        patch.setattr(store.os, "link", interrupted)
        assert cache.policy_arguments(root, runtime=target) == ["-B"]
    assert not list(local_cache.rglob("owner.json"))
    assert not list(local_cache.rglob(".owner-*"))
    assert cache.policy_arguments(root, runtime=target)[0] == "-X"


def test_unsealed_invocation_does_not_allocate_any_namespace(tmp_path, local_cache):
    root = tmp_path / "installation"
    for _ in range(50):
        assert cache.policy_arguments(root, runtime=tmp_path / "unsealed") == ["-B"]
    assert not local_cache.exists() and not root.exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows requires bounded descendant DACL admission")
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


def test_unresolved_home_uses_no_write_fallback(tmp_path, local_cache, monkeypatch):
    root = tmp_path / "installation"
    target = _installed(root)

    def unavailable():
        raise RuntimeError("current UID has no home")

    monkeypatch.setattr(store, "user_cache", unavailable)
    assert cache.policy_arguments(root, runtime=target) == ["-B"]
    assert not local_cache.exists()


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX private traversal boundary")
def test_traversable_bytecode_root_cannot_expose_writable_cached_files(tmp_path, local_cache):
    root = tmp_path / "installation"
    target = _installed(root)
    selected = cache.policy_arguments(root, runtime=target)
    assert selected[0] == "-X"
    bytecode = Path(selected[1].split("=", 1)[1])
    compiled = bytecode / "fixture.pyc"
    compiled.write_bytes(b"untouched cache fixture")
    compiled.chmod(0o666)
    bytecode.chmod(0o755)
    assert cache.policy_arguments(root, runtime=target) == ["-B"]
    assert compiled.read_bytes() == b"untouched cache fixture"


@pytest.mark.skipif(os.name == "nt", reason="POSIX sticky shared ancestor")
def test_fresh_override_under_sticky_parent_matches_warm_selection(tmp_path, local_cache):
    root = tmp_path / "installation"
    target = _installed(root)
    tmp_path.chmod(0o1777)
    try:
        selected = cache.policy_arguments(root, runtime=target)
        assert selected[0] == "-X"
        assert selected == cache.policy_arguments(root, runtime=target)
        assert local_cache.stat().st_mode & 0o077 == 0
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
@pytest.mark.parametrize("kind", [
    "base", "inherit-only", "file", "directory", "owner",
    "catalog", "catalog-journal", "catalog-wal", "catalog-shm",
])
def test_native_windows_shared_writable_cache_is_not_selected(tmp_path, local_cache, kind):
    import ctypes
    from ctypes import wintypes

    root = tmp_path / "installation"
    target = _installed(root)
    if kind != "base":
        selected = cache.policy_arguments(root, runtime=target)
        assert selected[0] == "-X"
        changed = Path(selected[1].split("=", 1)[1])
        if kind == "owner":
            changed = changed.parent / "owner.json"
        elif kind.startswith("catalog"):
            changed = changed.parent.parent / (kind.replace("catalog", "catalog.sqlite", 1))
            if kind != "catalog":
                changed.write_bytes(b"untouched metadata fixture")
        if kind in {"file", "directory"}:
            changed /= "existing"
            if kind == "file":
                changed.write_bytes(b"untouched bytecode fixture")
            else:
                permissions.mkdir(changed)
    else:
        permissions.mkdir(local_cache)
        changed = local_cache
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
        f"O:{user}D:P(A;;FA;;;{user})(A;{'OICIIO' if kind == 'inherit-only' else ''};FA;;;AU)",
        1, ctypes.byref(descriptor), None,
    )
    try:
        assert advapi.SetFileSecurityW(str(changed), 0x80000004, descriptor)
        assert cache.policy_arguments(root, runtime=target) == ["-B"]
        if kind == "base":
            assert not (local_cache / "copilot-extensions").exists()
        if kind == "file":
            assert changed.read_bytes() == b"untouched bytecode fixture"
    finally:
        kernel.LocalFree(descriptor)


@pytest.mark.skipif(os.name != "nt", reason="Windows bounded descendant admission")
def test_windows_descendant_admission_budgets_fall_back_without_mutation(
    tmp_path, local_cache, monkeypatch,
):
    root = tmp_path / "installation"
    target = _installed(root)
    selected = cache.policy_arguments(root, runtime=target)
    assert selected[0] == "-X"
    bytecode = Path(selected[1].split("=", 1)[1])
    compiled = bytecode / "fixture.pyc"
    compiled.write_bytes(b"untouched")
    assert cache.policy_arguments(root, runtime=target) == selected
    real_check = permissions.check_bytecode
    for limits in ({"max_entries": 0}, {"seconds": 0}):
        monkeypatch.setattr(permissions, "check_bytecode", lambda path: real_check(path, **limits))
        assert cache.policy_arguments(root, runtime=target) == ["-B"]
        assert compiled.read_bytes() == b"untouched"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS native named-user ACL protection")
def test_macos_named_user_acl_does_not_override_private_mode(tmp_path, local_cache):
    root = tmp_path / "installation"
    target = _installed(root)
    selected = cache.policy_arguments(root, runtime=target)
    assert selected[0] == "-X"
    bytecode = Path(selected[1].split("=", 1)[1])
    subprocess.run(
        ["/bin/chmod", "+a", "user:nobody allow write,delete_child", str(bytecode)],
        check=True, capture_output=True, timeout=20,
    )
    assert bytecode.stat().st_mode & 0o077 == 0
    assert cache.policy_arguments(root, runtime=target) == ["-B"]


@pytest.mark.parametrize("tags,accepted", [([], True), ([2, 2], True), ([1], False), ([2, 1], False)])
def test_darwin_acl_abi_uses_zero_success_and_negative_next_entry(monkeypatch, tags, accepted):
    import ctypes
    import errno

    calls = []

    class Function:
        def __init__(self, callback):
            self.callback = callback

        def __call__(self, *args):
            return self.callback(*args)

    def entry(_acl, selector, output):
        assert selector == (0 if not calls else -1)
        calls.append(selector)
        if len(calls) > len(tags):
            ctypes.set_errno(errno.EINVAL)
            return -1
        output._obj.value = len(calls)
        return 0

    def tag(pointer, output):
        output._obj.value = tags[pointer.value - 1]
        return 0

    from types import SimpleNamespace
    library = SimpleNamespace(
        acl_get_file=Function(lambda *_: 1), acl_get_entry=Function(entry),
        acl_get_tag_type=Function(tag), acl_free=Function(lambda *_: 0),
    )
    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_kw: library)
    if accepted:
        permissions._macos_check(Path("fixture"))
        assert len(calls) == len(tags) + 1
    else:
        with pytest.raises(PermissionError, match="ACL grant"):
            permissions._macos_check(Path("fixture"))
