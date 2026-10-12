"""Actual installer writers and hooks share a bounded POSIX migration policy."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from stamp_fixture import no_shared_write_umask  # noqa: F401
from test_install_engine_adoption import function
from test_session_stamp_security import _hook, _run, _stamp_module

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX mode migration")


def _installed(root: Path, payload: Path, home: Path) -> Path:
    from stamp_repair import installed_launcher

    root.mkdir(mode=0o775)
    root.chmod(0o775)
    for name, data in {
        ".publication.lock": b"old lock",
        "payload-dir": (str(payload) + "\n").encode(),
        "stamped-version": b"9.9.9\n",
    }.items():
        path = root / name
        path.write_bytes(data)
        path.chmod(0o664)
    local = home / ".local"
    local.mkdir()
    local.chmod(0o775)
    directory = local / "bin"
    directory.mkdir()
    directory.chmod(0o775)
    launcher = directory / "agent-index"
    launcher.write_bytes(installed_launcher())
    launcher.chmod(0o775)
    return launcher


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("mask", [0o002, 0o077])
def test_actual_installer_stamp_then_hook_under_inherited_mask(tmp_path, shell, mask):
    command, environment, repo = _hook(tmp_path, shell)
    root = Path(environment["AGENT_INDEX_HOME"])
    payload = Path(environment["AGENT_INDEX_PAYLOAD_ROOT"])
    home = Path(environment["HOME"])
    definitions = "\n".join(function(name, "sh") for name in (
        "_with_index_advisory_lock", "_with_index_publication_lock",
        "_index_publication_fresh", "_write_index_stamp_file", "deploy_binstub", "do_stamp",
    ))
    result = subprocess.run(
        ["bash", "-c", f"""
set -euo pipefail
{definitions}
INSTALL_DIR='{root}'; LOCAL_BIN='{home / ".local/bin"}'; STUB="$LOCAL_BIN/agent-index"
PLUGIN_DIR='{payload}'; SCRIPT_DIR="$PLUGIN_DIR/scripts"; SRC_VERSION=9.9.9; FORCE=0
_bootstrap_python() {{ printf '%s\\n' '{sys.executable}'; }}
_find_python() {{ printf '%s\\n' '{sys.executable}'; }}
_version_lt() {{ return 1; }}
_ok() {{ :; }}; _skip() {{ :; }}; _warn() {{ echo "$*" >&2; }}
umask {mask:03o}
do_stamp
[[ "$(umask)" == {mask:04o} ]]
"""],
        env=environment, cwd=repo, text=True, capture_output=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    for path in (root, home / ".local", home / ".local/bin", home / ".local/bin/agent-index",
                 root / ".publication.lock", root / "payload-dir", root / "stamped-version"):
        assert not path.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    for _ in range(2):
        result = _run(command, environment, repo)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "{}"


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("deployed", [False, True])
def test_actual_hook_repairs_known_legacy_artifacts_without_trusting_open_alias(tmp_path, shell, deployed):
    _stamp_module()
    command, environment, repo = _hook(tmp_path, shell)
    root = Path(environment["AGENT_INDEX_HOME"])
    payload = Path(environment["AGENT_INDEX_PAYLOAD_ROOT"])
    home = Path(environment["HOME"])
    launcher = _installed(root, payload, home)
    current = root / "current-version"
    if deployed:
        current.write_bytes(b"9.9.9\n")
    lock_before = (root / ".publication.lock").stat().st_ino
    marker = root / "payload-dir"
    with marker.open("r+b") as old_writer:
        first = _run(command, environment, repo)
        assert first.returncode == 0, first.stderr
        assert first.stdout == "{}"
        assert "repaired group-writable" in first.stderr
        assert marker.stat().st_ino != os.fstat(old_writer.fileno()).st_ino
        old_writer.seek(0)
        old_writer.write(b"hostile retained descriptor")
        old_writer.flush()
        assert marker.read_text() == str(payload) + "\n"
    assert (root / ".publication.lock").stat().st_ino == lock_before
    for path in (root, home / ".local", launcher.parent, launcher, marker,
                 root / "stamped-version", root / ".publication.lock"):
        assert not path.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    if deployed:
        assert current.read_bytes() == b"9.9.9\n"
    second = _run(command, environment, repo)
    assert second.returncode == 0, second.stderr
    assert second.stdout == "{}"
    assert "repaired" not in second.stderr and "regenerated" not in second.stderr


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("fault", ["version", "payload", "launcher", "deployed-version"])
def test_actual_hook_refuses_ambiguous_shared_content(tmp_path, shell, fault):
    _stamp_module()
    command, environment, repo = _hook(tmp_path, shell)
    root = Path(environment["AGENT_INDEX_HOME"])
    payload = Path(environment["AGENT_INDEX_PAYLOAD_ROOT"])
    launcher = _installed(root, payload, Path(environment["HOME"]))
    path = {"version": root / "stamped-version", "payload": root / "payload-dir",
            "launcher": launcher, "deployed-version": root / "current-version"}[fault]
    path.write_bytes(b"999.0.0\n" if "version" in fault else b"unknown writable bytes")
    path.chmod(0o664)
    before = path.stat()
    result = _run(command, environment, repo)
    assert result.returncode != 0
    assert result.stdout == "{}"
    assert "untrusted stamp content" in result.stderr or "not privately owned" in result.stderr
    after = path.stat()
    assert (before.st_ino, before.st_mode) == (after.st_ino, after.st_mode)
    assert path.read_bytes() == (b"999.0.0\n" if "version" in fault else b"unknown writable bytes")


@pytest.mark.parametrize("fault", ["symlink", "hardlink", "directory"])
def test_installer_publication_lock_refuses_alias_before_truncation(tmp_path, fault):
    command, environment, repo = _hook(tmp_path)
    root = Path(environment["AGENT_INDEX_HOME"])
    root.mkdir()
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"never truncate external bytes")
    target = root / ".publication.lock"
    if fault == "symlink":
        target.symlink_to(sentinel)
    elif fault == "hardlink":
        os.link(sentinel, target)
    else:
        target.mkdir()
    payload = Path(environment["AGENT_INDEX_PAYLOAD_ROOT"])
    callback = tmp_path / "callback"
    definitions = function("_with_index_advisory_lock", "sh")
    result = subprocess.run(
        ["bash", "-c", definitions + f"""
SCRIPT_DIR='{payload / "scripts"}'
_bootstrap_python() {{ printf '%s\\n' '{sys.executable}'; }}
_warn() {{ echo "$*" >&2; }}
callback() {{ echo forbidden > '{callback}'; }}
_with_index_advisory_lock '{root / "publication"}' 9 0 callback
"""], env=environment, cwd=repo, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode != 0
    assert "stamp" in result.stderr
    assert not callback.exists()
    assert sentinel.read_bytes() == b"never truncate external bytes"


def test_repair_policy_refuses_other_owners_and_world_write():
    _stamp_module()
    from stamp_repair import repairable

    for owner, mode, accepted in [
        (os.geteuid(), 0o775, True),
        (os.geteuid(), 0o664, True),
        (os.geteuid() + 1, 0o775, False),
        (os.geteuid(), 0o777, False),
        (os.geteuid(), 0o2664, False),
        (os.geteuid(), 0o1775, False),
    ]:
        info = os.stat_result((mode, 0, 0, 1, owner, 0, 0, 0, 0, 0))
        assert repairable(info) is accepted


def test_directory_repair_refuses_replacement_namespace_before_chmod(tmp_path, monkeypatch):
    module = _stamp_module()
    from stamp_repair import destinations
    root = tmp_path / "root"
    root.mkdir()
    root.chmod(0o775)
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    original = module.Directory._directory

    def bind(directory, path, parent):
        fd = original(directory, path, parent)
        if path == root:
            root.rename(tmp_path / "old-root")
            replacement.rename(root)
        return fd

    monkeypatch.setattr(module.Directory, "_directory", bind)
    with pytest.raises(ValueError, match="directory identity changed"):
        with module.Directory(root, repair_paths=destinations(root)):
            raise AssertionError("replaced directory must never be admitted")
    assert stat.S_IMODE((tmp_path / "old-root").stat().st_mode) == 0o775
    assert stat.S_IMODE(root.stat().st_mode) == 0o755


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("mask", [0o002, 0o077])
def test_actual_runtime_activation_markers_remain_admitted_by_hook(tmp_path, shell, mask):
    command, environment, repo = _hook(tmp_path, shell)
    root = Path(environment["AGENT_INDEX_HOME"])
    payload = Path(environment["AGENT_INDEX_PAYLOAD_ROOT"])
    slot = root / "versions/9.9.9"
    (slot / "bin").mkdir(parents=True)
    python = slot / "bin/python"
    python.write_text(f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n")
    python.chmod(0o755)
    helper = payload / "scripts/versioned_runtime.py"
    subprocess.run(
        [sys.executable, str(helper), "--root", str(root), "mark-complete", "9.9.9"],
        check=True, capture_output=True, text=True, timeout=10,
    )
    definitions = "\n".join(function(name, "sh") for name in (
        "_with_index_advisory_lock", "_with_index_publication_lock",
        "_index_publication_fresh", "_version_lt", "_versioned_activate",
    ))
    result = subprocess.run(
        ["bash", "-c", definitions + f"""
set -euo pipefail
INSTALL_DIR='{root}'; SCRIPT_DIR='{payload / "scripts"}'; VENV_DIR='{slot}'
SRC_VERSION=9.9.9; FORCE=0; VERSIONED_RUNTIME=1
_bootstrap_python() {{ printf '%s\\n' '{sys.executable}'; }}
_runtime_origin_under() {{ return 0; }}
_ok() {{ :; }}; _skip() {{ :; }}; _fail() {{ echo "$*" >&2; }}; _warn() {{ echo "$*" >&2; }}
umask {mask:03o}
_versioned_activate
[[ "$(umask)" == {mask:04o} ]]
"""], env=environment, cwd=repo, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    for name in ("current-version", "last-known-good"):
        path = root / name
        assert path.read_text() == "9.9.9\n"
        assert stat.S_IMODE(path.stat().st_mode) == 0o666 & ~(mask | 0o022)
    before = (root / "current-version").stat()
    result = _run(command, environment, repo)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "{}"
    after = (root / "current-version").stat()
    assert (before.st_ino, before.st_mtime_ns, before.st_mode) == (
        after.st_ino, after.st_mtime_ns, after.st_mode,
    )
