"""Protected local materialization never mints or replaces a key."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest
from agent_procutil import no_window_kwargs

from agent_vault import cli, ssh_key, ssh_key_files
from . import test_ssh_key as key_fixtures
from .test_ssh_key import request, vault

isolated = key_fixtures.isolated
record = key_fixtures.record


def pair(record):
    data = ssh_key.decode_record(record)
    return data["private_key"].encode("ascii"), (data["public_key"] + "\n").encode("ascii")


def matches(path: Path, value: bytes):
    if path.read_bytes() != value:
        raise AssertionError("Installed key bytes do not match the protected record")


def test_native_creation_reuse_and_openssh_consumer(tmp_path, record):
    directory = tmp_path / "key space"
    private, public = pair(record)
    result = ssh_key_files.install(directory, private, public)
    assert result["status"] == "installed"
    paths = [Path(result[name]) for name in ("private_key_path", "public_key_path")]
    before = [(path.stat().st_ino, path.stat().st_mtime_ns) for path in paths]
    repeated = ssh_key_files.install(directory, private, public)
    assert repeated["status"] == "reused"
    assert before == [(path.stat().st_ino, path.stat().st_mtime_ns) for path in paths]
    windows = ssh_key_files.WindowsSecurity() if os.name == "nt" else None
    ssh_key_files._require(directory, windows, directory=True)
    for path, expected in zip(paths, (private, public)):
        ssh_key_files._require(path, windows, directory=False)
        matches(path, expected)
    executable = shutil.which("ssh-keygen")
    if not executable:
        pytest.skip("Native OpenSSH key consumer unavailable; file contracts were exercised")
    derived = subprocess.run(
        [executable, "-y", "-f", str(paths[0])], stdin=subprocess.DEVNULL,
        capture_output=True, timeout=10, **no_window_kwargs(),
    )
    assert derived.returncode == 0, "Native OpenSSH refused the protected private key"
    if derived.stdout.strip() != public.strip():
        raise AssertionError("Native OpenSSH derived a different public key")


@pytest.mark.parametrize("survivor", ["id_ed25519", "id_ed25519.pub"])
def test_matching_partial_pair_is_recovered(tmp_path, record, survivor):
    directory = tmp_path / "keys"
    private, public = pair(record)
    ssh_key_files.install(directory, private, public)
    missing = directory / ("id_ed25519.pub" if survivor == "id_ed25519" else "id_ed25519")
    missing.unlink()
    kept = directory / survivor
    before = kept.stat().st_mtime_ns
    assert ssh_key_files.install(directory, private, public)["status"] == "recovered"
    assert kept.stat().st_mtime_ns == before
    matches(directory / "id_ed25519", private)
    matches(directory / "id_ed25519.pub", public)


@pytest.mark.parametrize("conflict", ["id_ed25519", "id_ed25519.pub"])
def test_different_key_fails_before_any_missing_file_is_written(tmp_path, record, conflict):
    directory = tmp_path / "keys"
    private, public = pair(record)
    ssh_key_files.install(directory, private, public)
    kept = directory / conflict
    kept.write_bytes(b"existing unrelated key\n")
    missing = directory / ("id_ed25519.pub" if conflict == "id_ed25519" else "id_ed25519")
    missing.unlink()
    with pytest.raises(FileExistsError):
        ssh_key_files.install(directory, private, public)
    assert kept.read_bytes() == b"existing unrelated key\n" and not missing.exists()


def test_failure_after_private_publication_retains_recoverable_key(tmp_path, monkeypatch, record):
    directory = tmp_path / "keys"
    private, public = pair(record)
    original = ssh_key_files._write

    def fail_public(folder, target, value, windows):
        if target.suffix == ".pub":
            raise OSError("fixture publication interruption")
        return original(folder, target, value, windows)

    monkeypatch.setattr(ssh_key_files, "_write", fail_public)
    with pytest.raises(OSError):
        ssh_key_files.install(directory, private, public)
    matches(directory / "id_ed25519", private)
    assert not (directory / "id_ed25519.pub").exists()
    assert not list(directory.glob("*.tmp"))
    monkeypatch.setattr(ssh_key_files, "_write", original)
    assert ssh_key_files.install(directory, private, public)["status"] == "recovered"


def test_same_record_concurrent_publication_does_not_overwrite(tmp_path, record):
    directory = tmp_path / "keys"
    private, public = pair(record)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(
            lambda _: ssh_key_files.install(directory, private, public), range(3),
        ))
    assert all(result["status"] in ("installed", "recovered", "reused") for result in results)
    matches(directory / "id_ed25519", private)
    assert not list(directory.glob("*.tmp"))


@pytest.mark.skipif(os.name == "nt", reason="Native POSIX directory-flock publication contract")
def test_posix_processes_serialize_the_hard_link_publication_window(tmp_path, record):
    private, public = pair(record)
    seed = tmp_path / "seed"
    ssh_key_files.install(seed, private, public)
    target = tmp_path / "target"
    child = (
        "import importlib.util,pathlib,sys,time;"
        "s=importlib.util.spec_from_file_location('files',sys.argv[1]);"
        "f=importlib.util.module_from_spec(s);s.loader.exec_module(f);"
        "original=f.os.link;"
        "f.os.link=lambda *a,**kw:(original(*a,**kw),time.sleep(0.1))[0];"
        "seed=pathlib.Path(sys.argv[2]);"
        "f.install(pathlib.Path(sys.argv[3]),(seed/'id_ed25519').read_bytes(),"
        "(seed/'id_ed25519.pub').read_bytes())"
    )

    def install_child(_):
        result = subprocess.run(
            [sys.executable, "-c", child, ssh_key_files.__file__, str(seed), str(target)],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=15, **no_window_kwargs(),
        )
        assert result.returncode == 0, "Concurrent native POSIX publication failed"

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(install_child, range(3)))
    matches(target / "id_ed25519", private)
    matches(target / "id_ed25519.pub", public)
    assert not list(target.glob("*.tmp"))


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_native_broad_permissions_are_refused_not_repaired(tmp_path, record, kind):
    directory = tmp_path / "keys"
    private, public = pair(record)
    ssh_key_files.install(directory, private, public)
    target = directory if kind == "directory" else directory / "id_ed25519"
    if os.name == "nt":
        from ctypes import wintypes as w
        import ctypes

        windows = ssh_key_files.WindowsSecurity()
        descriptor = w.LPVOID()
        windows.checked(windows.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"O:{windows.sid}D:P(A;;FA;;;WD)", 1, ctypes.byref(descriptor), None,
        ))
        set_security = windows.security.SetFileSecurityW
        set_security.argtypes, set_security.restype = [w.LPCWSTR, w.DWORD, w.LPVOID], w.BOOL
        try:
            windows.checked(set_security(str(target), 0x80000004, descriptor))
        finally:
            windows.kernel.LocalFree(descriptor)
    else:
        target.chmod(0o755 if kind == "directory" else 0o644)
    with pytest.raises(PermissionError):
        ssh_key_files.install(directory, private, public)
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == (0o755 if kind == "directory" else 0o644)


def test_linked_key_is_not_followed(tmp_path, record):
    directory = tmp_path / "keys"
    private, public = pair(record)
    ssh_key_files.install(directory, private, public)
    original = directory / "id_ed25519"
    alias = directory / "alias"
    os.link(original, alias)
    with pytest.raises(PermissionError):
        ssh_key_files.install(directory, private, public)
    matches(original, private)


@pytest.mark.parametrize("shape", ["directory", "ancestor", "file"])
def test_native_symlinks_are_refused(tmp_path, record, shape):
    directory = tmp_path / "keys"
    private, public = pair(record)
    outside = tmp_path / "outside"
    outside.mkdir()
    if shape == "file":
        ssh_key_files.install(directory, private, public)
        (directory / "id_ed25519").unlink()
        target = directory / "id_ed25519"
        source = outside / "unrelated"
        source.write_bytes(b"untouched")
    else:
        target, source = directory, outside
    try:
        target.symlink_to(source, target_is_directory=shape != "file")
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 1314:
            pytest.skip("Native Windows symlink creation privilege unavailable")
        raise
    with pytest.raises(PermissionError):
        ssh_key_files.install(directory / "child" if shape == "ancestor" else directory, private, public)
    assert list(outside.iterdir()) == ([source] if shape == "file" else [])


def test_flush_failure_never_publishes_partial_private_file(tmp_path, monkeypatch, record):
    directory = tmp_path / "keys"
    private, public = pair(record)
    monkeypatch.setattr(ssh_key_files.os, "fsync", Mock(side_effect=OSError("fixture flush failed")))
    with pytest.raises(OSError):
        ssh_key_files.install(directory, private, public)
    assert not list(directory.iterdir())


@pytest.mark.parametrize("value,code", [
    (None, "entry_missing"), ("invalid", "malformed_key"),
])
def test_strict_service_read_never_generates_caches_or_mutates(isolated, monkeypatch, value, code):
    svc = vault(monkeypatch)
    monkeypatch.setattr(svc.cli, "read_identity_key", lambda *a: value)
    monkeypatch.setattr(svc.cli, "create_identity_key", Mock(side_effect=AssertionError))
    monkeypatch.setattr(ssh_key, "_generate_record", Mock(side_effect=AssertionError))
    response = request(svc, isolated, action="ssh-key-read")
    assert response["code"] == code and not svc.cache


def test_strict_service_read_is_existing_validated_record_only(isolated, monkeypatch, record):
    svc = vault(monkeypatch)
    monkeypatch.setattr(svc.cli, "read_identity_key", lambda *a: record)
    monkeypatch.setattr(svc.cli, "create_identity_key", Mock(side_effect=AssertionError))
    response = request(svc, isolated, action="ssh-key-read")
    if response != {"ok": True, "value": record}:
        raise AssertionError("Strict read did not retain the protected record")
    assert not svc.cache
    assert request(svc, isolated, action="ssh-key-read", initialize=True,
                   confirm_initialization=True)["code"] == "confirmation_required"
    monkeypatch.setattr(svc, "ensure_unlocked", lambda *a: False)
    assert request(svc, isolated, action="ssh-key-read")["code"] == "needs_unlock"


def test_cli_installs_with_selected_context_and_public_only_output(
    isolated, monkeypatch, capsys, record, tmp_path,
):
    destination = tmp_path / "keys"
    sent = []
    monkeypatch.setattr(cli, "ensure_service", Mock(side_effect=AssertionError))

    def send(payload, **kwargs):
        sent.append((payload, kwargs))
        return {"ok": True} if payload["action"] == "ping" else {"ok": True, "value": record}

    monkeypatch.setattr(cli, "send_command", send)
    assert cli.main([
        "ssh-key", "install", "--entry", "SSH/node-a", "--directory", str(destination),
        "--require-running", "--json",
    ]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "installed"
    assert record not in output.out + output.err and "BEGIN OPENSSH" not in output.out + output.err
    payload, kwargs = sent[1]
    assert payload == {
        "action": "ssh-key-read", "kpdb": isolated, "entry": "SSH/node-a",
        "group": "", "allow_prompt": False,
    }
    assert kwargs == {"timeout": 90.0, "strict_endpoint": True}


@pytest.mark.parametrize("response,error", [
    (None, "service_unavailable"), ({"ok": False, "error": "PRIVATE"}, "service_unavailable"),
    ({"ok": True, "value": "PRIVATE"}, "malformed_key"),
])
def test_cli_service_errors_never_create_destination(
    isolated, monkeypatch, capsys, tmp_path, response, error,
):
    destination = tmp_path / "keys"
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(cli, "send_command", lambda *a, **kw: response)
    assert cli.main([
        "ssh-key", "install", "--entry", "SSH/node-a", "--directory", str(destination),
    ]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["error"] == error and "PRIVATE" not in output.out + output.err
    assert not destination.exists()


def test_required_stopped_service_never_activates_or_creates(isolated, monkeypatch, capsys, tmp_path):
    destination = tmp_path / "keys"
    monkeypatch.setattr(cli, "ensure_service", Mock(side_effect=AssertionError))
    monkeypatch.setattr(cli, "send_command", lambda *a, **kw: None)
    assert cli.main([
        "ssh-key", "install", "--entry", "SSH/node-a", "--directory", str(destination),
        "--require-running",
    ]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "service_unavailable"
    assert not destination.exists()
