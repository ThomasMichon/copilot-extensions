"""Actual hook refusal precedes any write through an unowned filesystem link."""
from __future__ import annotations

import errno
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_ensure_service_hooks import PLUGIN, _shell_command
from test_runtime_gate import _fixture
from stamp_fixture import no_shared_write_umask  # noqa: F401


def _hook(tmp_path: Path, shell: str = "bash") -> tuple[list[str], dict[str, str], Path]:
    repo = tmp_path / "repo"
    gate, environment = _fixture(repo, shell)
    scripts = gate.parent
    for name in ("session-stamp.py", "stamp_paths.py", "stamp_repair.py", "versioned_runtime.py",
                 "versioned_runtime_leases.py", "versioned_runtime_construction.py",
                 "versioned_runtime_source.py",
                 "versioned_runtime_publication.py",
                 "bootstrap-killswitch-guard.sh", "bootstrap-killswitch-guard.ps1"):
        shutil.copy2(PLUGIN / "scripts" / name, scripts / name)
    suffix = "sh" if shell == "bash" else "ps1"
    shutil.copy2(PLUGIN / "scripts" / f"bootstrap-check.{suffix}", scripts)
    (scripts.parent / "plugin.json").write_text('{"version":"9.9.9"}')
    environment.pop("TEST_PYTHON", None)
    environment["AGENT_INDEX_REPO"] = str(repo)
    environment["HOME"] = environment["USERPROFILE"] = str(tmp_path / "home")
    environment["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(
        tmp_path / "home/.copilot-extensions/bootstrap-killswitch.json"
    )
    Path(environment["HOME"]).mkdir()
    for kind in ("CACHE", "DATA", "CONFIG"):
        environment[f"XDG_{kind}_HOME"] = str(tmp_path / f"shell-{kind.lower()}")
    environment["COPILOT_PLUGIN_ROOT"] = str(scripts.parent)
    environment["AGENT_INDEX_PAYLOAD_ROOT"] = str(scripts.parent)
    hook = json.loads((PLUGIN / "hooks.json").read_text())["hooks"]["sessionStart"][1]
    executable = _shell_command(shell, scripts / f"bootstrap-check.{suffix}")[0]
    command = ([executable, "-c", hook["bash"]] if shell == "bash" else
               [executable, "-NoProfile", "-Command", hook["powershell"]])
    return command, environment, repo


def _run(command: list[str], environment: dict[str, str], repo: Path):
    return subprocess.run(command, cwd=repo, env=environment, capture_output=True,
                          text=True, timeout=20)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mutation permissions")
@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("mode", [0o707, 0o777])
def test_actual_hook_refuses_shared_writable_root_before_publication(
    tmp_path: Path, shell: str, mode: int,
) -> None:
    command, environment, repo = _hook(tmp_path, shell)
    root = Path(environment["AGENT_INDEX_HOME"])
    root.mkdir()
    root.chmod(mode)
    before = root.stat()
    result = _run(command, environment, repo)
    assert result.returncode != 0
    assert result.stdout == "{}"
    assert "not privately owned" in result.stderr
    assert list(root.iterdir()) == []
    after = root.stat()
    assert (after.st_ino, after.st_mtime_ns, after.st_mode) == (
        before.st_ino, before.st_mtime_ns, before.st_mode,
    )
    assert not (Path(environment["HOME"]) / ".local").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX mutation permissions")
@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("name", [".publication.lock", "payload-dir", "stamped-version"])
def test_actual_hook_refuses_shared_writable_metadata(
    tmp_path: Path, shell: str, name: str,
) -> None:
    command, environment, repo = _hook(tmp_path, shell)
    root = Path(environment["AGENT_INDEX_HOME"])
    root.mkdir()
    path = root / name
    path.write_bytes(b"unmodified shared file")
    path.chmod(0o666)
    before = path.stat()
    result = _run(command, environment, repo)
    assert result.returncode != 0
    assert result.stdout == "{}"
    assert "not privately owned" in result.stderr
    assert path.read_bytes() == b"unmodified shared file"
    after = path.stat()
    assert (after.st_ino, after.st_mtime_ns, after.st_mode) == (
        before.st_ino, before.st_mtime_ns, before.st_mode,
    )
    assert set(p.name for p in root.iterdir()) <= {name, ".publication.lock"}
    assert not (Path(environment["HOME"]) / ".local").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX mutation permissions")
def test_publication_rechecks_root_mutation_permissions(tmp_path: Path) -> None:
    module = _stamp_module()
    root = tmp_path / "root"
    root.mkdir()
    with module.Directory(root) as directory:
        root.chmod(0o777)
        with pytest.raises(ValueError, match="not privately owned"):
            directory.publish("payload-dir", b"not published")
    assert list(root.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="native Windows ownership revalidation")
def test_publication_rechecks_windows_directory_ownership(tmp_path: Path, monkeypatch) -> None:
    module = _paths_module()
    root = tmp_path / "root"
    root.mkdir()
    with module.Directory(root) as directory:
        monkeypatch.setattr(module, "_windows_owned", lambda fd: False)
        with pytest.raises(ValueError, match="not privately owned"):
            directory.publish("payload-dir", b"not published")
    assert list(root.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory replacement adversaries")
@pytest.mark.parametrize("when", ["before-acquisition", "during-acquisition"])
def test_publication_lease_root_replacement_never_writes_external_tree(
    tmp_path: Path, monkeypatch, when: str,
) -> None:
    module = _paths_module()
    root, external = tmp_path / "root", tmp_path / "external"
    root.mkdir()
    external.mkdir()
    sentinel = external / "payload-dir"
    sentinel.write_bytes(b"external sentinel")
    before = sentinel.stat()

    def replace_root():
        root.rename(tmp_path / "admitted-root")
        root.symlink_to(external, target_is_directory=True)

    original = os.mkdir

    def mkdir(path, *args, **kwargs):
        if Path(path).name == ".publication-leases":
            replace_root()
        return original(path, *args, **kwargs)

    with module.Directory(root) as directory:
        if when == "before-acquisition":
            replace_root()
        else:
            monkeypatch.setattr(os, "mkdir", mkdir)
        with pytest.raises((ValueError, OSError)):
            directory.publish("payload-dir", b"not published")
    assert set(path.name for path in external.iterdir()) == {"payload-dir"}
    assert sentinel.read_bytes() == b"external sentinel"
    after = sentinel.stat()
    assert (before.st_ino, before.st_mtime_ns, before.st_mode) == (
        after.st_ino, after.st_mtime_ns, after.st_mode,
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX ordinary directory replacement")
def test_lease_directory_uses_admitted_parent_not_reopened_path(tmp_path: Path, monkeypatch) -> None:
    module = _paths_module()
    root, replacement = tmp_path / "root", tmp_path / "replacement"
    root.mkdir()
    replacement.mkdir()
    sentinel = replacement / "payload-dir"
    sentinel.write_bytes(b"replacement sentinel")
    before = sentinel.stat()
    original = module.Directory

    def substitute(path, *args, **kwargs):
        assert path == root / ".publication-leases"
        root.rename(tmp_path / "admitted-root")
        replacement.rename(root)
        return original(path, *args, **kwargs)

    with original(root) as directory:
        monkeypatch.setattr(module, "Directory", substitute)
        with pytest.raises(ValueError, match="directory identity changed"):
            directory.publish("payload-dir", b"not published")
    assert set(path.name for path in root.iterdir()) == {"payload-dir"}
    sentinel = root / "payload-dir"
    assert sentinel.read_bytes() == b"replacement sentinel"
    after = sentinel.stat()
    assert (before.st_ino, before.st_mtime_ns, before.st_mode) == (
        after.st_ino, after.st_mtime_ns, after.st_mode,
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX lease storage link adversaries")
@pytest.mark.parametrize("fault", ["directory-link", "file-link", "file-hardlink"])
def test_publication_refuses_unowned_lease_storage(tmp_path: Path, fault: str) -> None:
    import hashlib

    module = _paths_module()
    root, external = tmp_path / "root", tmp_path / "external"
    root.mkdir()
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_bytes(b"external lease sentinel")
    before = sentinel.stat()
    lease_dir = root / ".publication-leases"
    if fault == "directory-link":
        lease_dir.symlink_to(external, target_is_directory=True)
    else:
        lease_dir.mkdir()
        lease = lease_dir / (hashlib.sha256(b"redirects").hexdigest() + ".lock")
        if fault == "file-link":
            lease.symlink_to(sentinel)
        else:
            os.link(sentinel, lease)
    entries = {path.name for path in lease_dir.iterdir()}
    with module.Directory(root) as directory:
        with pytest.raises((ValueError, OSError)):
            directory.publish("payload-dir", b"not published")
    assert not (root / "payload-dir").exists()
    assert {path.name for path in external.iterdir()} == {"sentinel"}
    assert {path.name for path in lease_dir.iterdir()} == entries
    assert sentinel.read_bytes() == b"external lease sentinel"
    after = sentinel.stat()
    assert (before.st_ino, before.st_mtime_ns, before.st_mode) == (
        after.st_ino, after.st_mtime_ns, after.st_mode,
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX link and inode adversaries")
@pytest.mark.parametrize("fault", [
    "root-link", "ancestor-link", "lock-link", "lock-hardlink", "lock-directory",
    "root-file",
])
@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_actual_hook_refuses_links_before_external_mutation(tmp_path, fault, shell):
    command, environment, repo = _hook(tmp_path, shell)
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / ".publication.lock"
    sentinel.write_bytes(b"external sentinel must not be truncated\n")
    before = sentinel.stat()
    entries = sorted(p.name for p in external.iterdir())
    root = Path(environment["AGENT_INDEX_HOME"])
    if fault == "root-link":
        root.symlink_to(external, target_is_directory=True)
    elif fault == "ancestor-link":
        link = tmp_path / "ancestor"
        link.symlink_to(external, target_is_directory=True)
        root = link / "uncreated-root"
        environment["AGENT_INDEX_HOME"] = str(root)
    elif fault == "root-file":
        root.write_bytes(b"not a directory")
    else:
        root.mkdir()
        lock = root / ".publication.lock"
        if fault == "lock-link":
            lock.symlink_to(sentinel)
        elif fault == "lock-hardlink":
            os.link(sentinel, lock)
        else:
            lock.mkdir()
    # No external flock executable is needed, even on a no-flock host.
    tools = tmp_path / "tools"
    tools.mkdir()
    flock = tools / "flock"
    flock.write_text("#!/bin/sh\nexit 99\n")
    flock.chmod(0o755)
    environment["PATH"] = str(tools) + os.pathsep + environment["PATH"]
    result = _run(command, environment, repo)
    assert result.returncode != 0
    assert result.stdout == "{}"
    assert "session stamp failed" in result.stderr
    after = sentinel.stat()
    assert sentinel.read_bytes() == b"external sentinel must not be truncated\n"
    assert (before.st_ino, before.st_size, before.st_mtime_ns) == (
        after.st_ino, after.st_size, after.st_mtime_ns,
    )
    assert sorted(p.name for p in external.iterdir()) == entries
    if fault.startswith("lock-"):
        assert set(p.name for p in root.iterdir()) == {".publication.lock"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock interoperability")
@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_actual_hook_preserves_lock_inode_bytes_and_contended_noop(tmp_path, shell):
    import fcntl

    command, environment, repo = _hook(tmp_path, shell)
    root = Path(environment["AGENT_INDEX_HOME"])
    root.mkdir()
    path = root / ".publication.lock"
    path.write_bytes(b"installer publication lock sentinel")
    before = path.stat()
    with path.open("rb") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _run(command, environment, repo)
        assert result.returncode == 0, result.stderr
        assert set(p.name for p in root.iterdir()) == {".publication.lock"}
    result = _run(command, environment, repo)
    assert result.returncode == 0, result.stderr
    assert (root / "stamped-version").read_text() == "9.9.9\n"
    after = path.stat()
    assert path.read_bytes() == b"installer publication lock sentinel"
    assert (before.st_ino, before.st_mtime_ns, before.st_mode) == (
        after.st_ino, after.st_mtime_ns, after.st_mode,
    )


def _stamp_module():
    spec = importlib.util.spec_from_file_location("secure_stamp", PLUGIN / "scripts/session-stamp.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _paths_module():
    sys.path.insert(0, str(PLUGIN / "scripts"))
    spec = importlib.util.spec_from_file_location("stamp_paths_test", PLUGIN / "scripts/stamp_paths.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("attributes,accepted", [
    (0xC, True), (0xF, True), (0x7, False), (0x8, False),
    (0x4, False), (0x1F, False), (0, False),
])
def test_windows_owner_requires_enabled_owner_capable_not_deny_only(attributes, accepted):
    module = _paths_module()
    owners = module._windows_owner_sids("user", [("group", attributes)])
    assert ("group" in owners) is accepted
    assert "user" in owners
    assert "foreign" not in owners


@pytest.mark.parametrize("kind,flags,mask,sid,accepted", [
    (0, 0, 0x1F01FF, "user", True),
    (0, 0, 0x1F01FF, "owner-group", True),
    (0, 0, 0x1F01FF, "S-1-5-18", True),
    (0, 0, 0x1F01FF, "S-1-5-32-544", True),
    (0, 0, 0x1F01FF, "S-1-3-4", True),
    (0, 0, 0x120089, "ordinary-group", True),
    (0, 8, 0x1F01FF, "ordinary-group", True),
    (1, 0, 0x1F01FF, "ordinary-group", True),
    (0, 0, 0x1F01FF, "ordinary-group", False),
    (0, 0, 0x40000000, "ordinary-group", False),
    (0, 0, 0x10000000, "ordinary-group", False),
    (0, 0, 0x40000, "ordinary-group", False),
    (0, 0, 0x80000, "ordinary-group", False),
    (0, 0, 0x10000, "ordinary-group", False),
    (0, 0, 0x40, "ordinary-group", False),
    (0, 0, 0x100, "ordinary-group", False),
    (0, 0, 0x10, "ordinary-group", False),
    (0, 0, 0x4, "ordinary-group", False),
    (0, 0, 0x2, "ordinary-group", False),
    (9, 0, 0x1F01FF, "user", False),
])
def test_windows_dacl_does_not_admit_shared_mutation(kind, flags, mask, sid, accepted):
    assert _paths_module()._windows_private_dacl(
        [(kind, flags, mask, sid)], {"user", "owner-group"},
    ) is accepted


@pytest.mark.skipif(os.name != "nt", reason="native Windows SID and ACL APIs")
@pytest.mark.parametrize("owner,acl,accepted", [
    ("user", "(A;;FA;;;OW)", True),
    ("owner-group", "(A;;FA;;;OW)", True),
    ("ordinary-group", "(A;;FA;;;OW)", False),
    ("S-1-5-21-1-2-3-1001", "(A;;FA;;;OW)", False),
    ("user", "(A;;FA;;;WD)", False),
    ("owner-group", "(A;;FA;;;WD)", False),
    ("user", "NO_ACCESS_CONTROL", False),
    ("user", "(A;;FR;;;WD)(A;;FA;;;OW)", True),
    ("user", "(D;;FA;;;WD)(A;;FA;;;WD)", False),
])
def test_native_windows_owner_and_acl_admission_without_acl_mutation(
    tmp_path, monkeypatch, owner, acl, accepted,
):
    import ctypes
    from ctypes import wintypes
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_QUERY,
    )
    try:
        user = win32security.ConvertSidToStringSid(
            win32security.GetTokenInformation(token, win32security.TokenUser)[0],
        )
        groups = [(win32security.ConvertSidToStringSid(sid), attributes) for sid, attributes
                  in win32security.GetTokenInformation(token, win32security.TokenGroups)]
    finally:
        token.Close()
    if owner == "owner-group":
        eligible = [sid for sid, attributes in groups
                    if attributes & 0xC == 0xC and not attributes & 0x10]
        if not eligible:
            pytest.skip("current standard token has no enabled owner-capable group")
        owner_sid = eligible[0]
    elif owner == "ordinary-group":
        owner_sid = next(sid for sid, attributes in groups
                         if attributes & 4 and not attributes & 8)
    else:
        owner_sid = user if owner == "user" else owner
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID),
        wintypes.LPVOID,
    )
    security.GetSecurityDescriptorOwner.argtypes = (
        wintypes.LPVOID, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL),
    )
    security.GetSecurityDescriptorDacl.argtypes = (
        wintypes.LPVOID, ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL),
    )

    def descriptor_for_test(handle, kind, information, owner_out, group, dacl_out, sacl, out):
        descriptor = wintypes.LPVOID()
        assert security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"O:{owner_sid}D:{acl}", 1, ctypes.byref(descriptor), None,
        )
        ctypes.cast(out, ctypes.POINTER(wintypes.LPVOID))[0] = descriptor
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        assert security.GetSecurityDescriptorOwner(descriptor, owner_out, ctypes.byref(defaulted))
        assert security.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), dacl_out, ctypes.byref(defaulted),
        )
        return 0

    class SecurityProxy:
        GetSecurityInfo = staticmethod(descriptor_for_test)

        def __getattr__(self, name):
            return getattr(security, name)

    original = ctypes.WinDLL
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kwargs:
                        SecurityProxy() if name == "advapi32" else original(name, **kwargs))
    module = _paths_module()
    fd = module._windows_open(tmp_path, directory=True)
    try:
        assert module._windows_owned(fd) is accepted
    finally:
        os.close(fd)


@pytest.mark.skipif(os.name != "nt", reason="native Windows pinned ownership and lock APIs")
def test_native_windows_owned_directory_and_lock_preserve_identity(tmp_path):
    module = _paths_module()
    command, environment, repo = _hook(tmp_path, "powershell")
    root = Path(environment["AGENT_INDEX_HOME"])
    root.mkdir()
    path = root / ".publication.lock"
    path.write_bytes(b"native installer lock sentinel")
    before = path.stat()
    with module.Directory(root) as directory:
        fd = directory.open(".publication.lock", create=True)
        try:
            assert module.lock(fd)
            result = _run(command, environment, repo)
            assert result.returncode == 0, result.stderr
            assert set(p.name for p in root.iterdir()) == {".publication.lock"}
        finally:
            os.close(fd)
    result = _run(command, environment, repo)
    assert result.returncode == 0, result.stderr
    assert (root / "stamped-version").read_text() == "9.9.9\n"
    assert path.read_bytes() == b"native installer lock sentinel"
    assert (path.stat().st_ino, path.stat().st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


@pytest.mark.skipif(os.name == "nt", reason="POSIX lock failure injection")
def test_unknown_lock_error_is_not_contention(tmp_path, monkeypatch):
    import fcntl

    module = _stamp_module()
    def fail(*args):
        raise OSError(errno.EIO, "injected lock I/O failure")
    monkeypatch.setattr(fcntl, "flock", fail)
    with pytest.raises(OSError, match="injected lock I/O failure"):
        module.stamp(tmp_path / "root", PLUGIN, "legacy")
    assert set(p.name for p in (tmp_path / "root").iterdir()) == {".publication.lock"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX lock identity race")
def test_lock_swapped_after_open_is_refused_without_external_write(tmp_path, monkeypatch):
    module = _stamp_module()
    root = tmp_path / "root"
    root.mkdir()
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"untouched")
    before = sentinel.stat()
    original = os.open
    def swap(name, flags, *args, **kwargs):
        fd = original(name, flags, *args, **kwargs)
        if name == ".publication.lock":
            (root / name).unlink()
            (root / name).symlink_to(sentinel)
        return fd
    monkeypatch.setattr(os, "open", swap)
    with pytest.raises(ValueError, match="identity changed|single-link regular"):
        module.stamp(root, PLUGIN, "legacy")
    assert sentinel.read_bytes() == b"untouched"
    assert sentinel.stat().st_mtime_ns == before.st_mtime_ns
    assert set(p.name for p in root.iterdir()) == {".publication.lock"}


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_actual_hook_unknown_lock_io_error_exits_nonzero(tmp_path, shell):
    command, environment, repo = _hook(tmp_path, shell)
    helper = Path(environment["AGENT_INDEX_PAYLOAD_ROOT"]) / "scripts/stamp_paths.py"
    with helper.open("a") as stream:
        stream.write('\ndef lock(fd):\n    raise OSError(5, "fixture lock I/O failure")\n')
    result = _run(command, environment, repo)
    assert result.returncode != 0
    assert result.stdout == "{}"
    assert "session stamp failed" in result.stderr
    assert "fixture lock I/O failure" in result.stderr
    root = Path(environment["AGENT_INDEX_HOME"])
    assert set(p.name for p in root.iterdir()) == {".publication.lock"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX ancestor identity race")
def test_root_replaced_after_admission_never_writes_external_tree(tmp_path, monkeypatch):
    module = _stamp_module()
    root = tmp_path / "root"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "payload-dir"
    sentinel.write_bytes(b"untouched")
    before = sentinel.stat()
    original = module.lock
    def swap(fd):
        held = original(fd)
        root.rename(tmp_path / "admitted-root")
        root.symlink_to(external, target_is_directory=True)
        return held
    monkeypatch.setattr(module, "lock", swap)
    with pytest.raises(ValueError, match="directory identity changed"):
        module.stamp(root, PLUGIN, "legacy")
    assert sentinel.read_bytes() == b"untouched"
    assert sentinel.stat().st_mtime_ns == before.st_mtime_ns
    assert set(p.name for p in external.iterdir()) == {"payload-dir"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX read-only deployed marker")
def test_deployed_readonly_marker_remains_untouched(tmp_path):
    module = _stamp_module()
    marker = tmp_path / "current-version"
    marker.write_text("0.0.1\n")
    marker.chmod(0o444)
    before = marker.stat()
    module.stamp(tmp_path, PLUGIN, "legacy")
    after = marker.stat()
    assert (before.st_ino, before.st_mtime_ns, before.st_mode) == (
        after.st_ino, after.st_mtime_ns, after.st_mode,
    )
    assert set(p.name for p in tmp_path.iterdir()) == {"current-version", ".publication.lock"}


def test_parent_traversal_is_refused_before_directory_creation(tmp_path):
    module = _stamp_module()
    with pytest.raises(ValueError, match="parent traversal"):
        module.stamp(tmp_path / "not-created/../root", PLUGIN, "legacy")
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="native Windows handle/reparse contract")
@pytest.mark.parametrize("fault", ["root-link", "ancestor-link", "lock-link", "lock-hardlink"])
def test_native_windows_actual_hook_refuses_unowned_links(tmp_path, fault):
    command, environment, repo = _hook(tmp_path, "powershell")
    root = Path(environment["AGENT_INDEX_HOME"])
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / ".publication.lock"
    sentinel.write_bytes(b"native Windows sentinel")
    try:
        if fault == "root-link":
            root.symlink_to(external, target_is_directory=True)
        elif fault == "ancestor-link":
            link = tmp_path / "ancestor"
            link.symlink_to(external, target_is_directory=True)
            environment["AGENT_INDEX_HOME"] = str(link / "uncreated")
        else:
            root.mkdir()
            if fault == "lock-hardlink":
                os.link(sentinel, root / ".publication.lock")
            else:
                (root / ".publication.lock").symlink_to(sentinel)
    except OSError as error:
        if error.winerror == 1314:
            pytest.skip("fixture symlink creation needs Windows developer mode or privilege")
        raise
    before = sentinel.stat()
    result = _run(command, environment, repo)
    assert result.returncode != 0
    assert "session stamp failed" in result.stderr
    expected = "single-link regular file" if fault == "lock-hardlink" else "linked stamp destination"
    assert expected in result.stderr
    assert sentinel.read_bytes() == b"native Windows sentinel"
    assert sentinel.stat().st_mtime_ns == before.st_mtime_ns
    assert set(p.name for p in external.iterdir()) == {".publication.lock"}
