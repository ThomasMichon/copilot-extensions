"""Real cross-project processes must share each Copilot permission-file lock."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_worktrees import permissions, tracking


_WORKER = """
import json, sys, time
from pathlib import Path
from agent_worktrees import permissions, tracking
home, prefix, method, args, pause, timeout = sys.argv[1:]
home, prefix = Path(home), Path(prefix)
permissions._copilot_dir = lambda: home
permissions._PERMISSION_LOCK_TIMEOUT = float(timeout)
original_try = tracking._RecordLock._sidecar_try
def observed_try(self):
    acquired = original_try(self)
    if not acquired:
        prefix.with_suffix('.contending').touch()
    return acquired
tracking._RecordLock._sidecar_try = observed_try
original_write = permissions._atomic_json_write
def observed_write(*args, **kwargs):
    prefix.with_suffix('.loaded').touch()
    if pause == 'yes':
        deadline = time.monotonic() + 6
        while not prefix.with_suffix('.release').exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('test writer was not released')
            time.sleep(0.01)
    return original_write(*args, **kwargs)
permissions._atomic_json_write = observed_write
result = getattr(permissions, method)(*json.loads(args))
prefix.with_suffix('.result').write_text(json.dumps(result), encoding='utf-8')
"""


def _start(
    home: Path, prefix: Path, method: str, args: list[str],
    *, pause: bool = False, timeout: float = 2,
) -> subprocess.Popen:
    env = os.environ.copy()
    source = str(Path(permissions.__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [source, env.get("PYTHONPATH")]))
    prefix.mkdir()
    return subprocess.Popen(
        [
            sys.executable, "-c", _WORKER, str(home), str(prefix), method,
            json.dumps(args), "yes" if pause else "no", str(timeout),
        ],
        cwd=prefix, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def _wait_for(path: Path, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 4
    while not path.exists():
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            pytest.fail(f"permission worker exited early: {stdout}\n{stderr}")
        assert time.monotonic() < deadline, f"worker did not create {path.name}"
        time.sleep(0.01)


def _finish(process: subprocess.Popen) -> str:
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, f"{stdout}\n{stderr}"
    return stderr


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=3)


@pytest.mark.parametrize(
    "first,first_args,second,second_args,initial",
    [
        (
            "add_trusted_folder", ["/project-a/worktrees/new"],
            "add_trusted_folder", ["/project-b/worktrees/new"],
            {"trustedFolders": ["/existing"]},
        ),
        (
            "remove_trusted_folder", ["/obsolete"],
            "add_trusted_folder", ["/project-b/worktrees/new"],
            {"trustedFolders": ["/obsolete", "/existing"]},
        ),
        (
            "ensure_extension_permission_approvals", ["/project-a/worktrees/new"],
            "ensure_extension_permission_approvals", ["/project-b/worktrees/new"],
            None,
        ),
        (
            "clone_permissions", ["/anchor", "/project-a/worktrees/new"],
            "ensure_extension_permission_approvals", ["/project-b/worktrees/new"],
            {"locations": {"/anchor": {"tool_approvals": [{"kind": "read"}]}}},
        ),
        (
            "merge_permissions", ["/anchor", "/obsolete"],
            "clone_permissions", ["/anchor", "/project-b/worktrees/new"],
            {"locations": {
                "/anchor": {"tool_approvals": [{"kind": "read"}]},
                "/obsolete": {"tool_approvals": [{"kind": "write"}]},
            }},
        ),
    ],
    ids=["trust-trust", "remove-trust", "first-approvals", "clone-approvals", "merge-clone"],
)
def test_permission_serialization_competing_projects(
    tmp_path: Path, first: str, first_args: list[str], second: str,
    second_args: list[str], initial: dict | None,
) -> None:
    home = tmp_path / ".copilot"
    home.mkdir()
    config = first.endswith("trusted_folder")
    path = home / ("config.json" if config else "permissions-config.json")
    header = "// Keep managed header.\n\n" if config else ""
    if initial is not None:
        initial["unrelated"] = {"url": "https://example.test/path"}
        path.write_text(header + json.dumps(initial), encoding="utf-8")
    a, b = tmp_path / "project-a", tmp_path / "project-b"
    processes: list[subprocess.Popen] = []
    try:
        processes.append(_start(home, a, first, first_args, pause=True, timeout=6))
        _wait_for(a.with_suffix(".loaded"), processes[0])
        processes.append(_start(home, b, second, second_args, timeout=6))
        _wait_for(b.with_suffix(".contending"), processes[1])
        assert not b.with_suffix(".loaded").exists()
        assert not b.with_suffix(".result").exists()
        a.with_suffix(".release").touch()
        for process in processes:
            assert _finish(process) == ""
        first_result = json.loads(a.with_suffix(".result").read_text())
        assert first_result if first == "merge_permissions" else first_result is True
        assert json.loads(b.with_suffix(".result").read_text()) is True
    finally:
        a.with_suffix(".release").touch()
        for process in processes:
            _stop(process)

    text = path.read_text(encoding="utf-8")
    assert text.startswith(header)
    data = json.loads(text[len(header):])
    if initial is not None:
        assert data["unrelated"] == initial["unrelated"]
    if config:
        assert "/existing" in data["trustedFolders"]
        assert second_args[0] in data["trustedFolders"]
        assert (first_args[0] in data["trustedFolders"]) == first.startswith("add")
    else:
        locations = data["locations"]
        assert "/obsolete" not in locations
        assert second_args[-1] in locations
        if first != "merge_permissions":
            assert first_args[-1] in locations
        else:
            assert {"kind": "write"} in locations["/anchor"]["tool_approvals"]
            assert locations[second_args[-1]] == locations["/anchor"]
    assert path.with_suffix(".lock").exists()
    assert not list(home.glob("*.tmp"))


@pytest.mark.parametrize(
    "method,args,filename,skipped",
    [
        ("clone_permissions", ["/anchor", "/new"], "permissions-config.json", False),
        ("merge_permissions", ["/anchor", "/old"], "permissions-config.json", []),
        ("ensure_extension_permission_approvals", ["/new"], "permissions-config.json", False),
        ("add_trusted_folder", ["/new"], "config.json", False),
        ("remove_trusted_folder", ["/old"], "config.json", False),
    ],
)
def test_permission_serialization_timeout_visible(
    tmp_path: Path, method: str, args: list[str], filename: str, skipped: object,
) -> None:
    home = tmp_path / ".copilot"
    home.mkdir()
    path = home / filename
    original = json.dumps({
        "trustedFolders": ["/old"],
        "locations": {"/anchor": {"tool_approvals": [{"kind": "read"}]}},
    })
    path.write_text(original, encoding="utf-8")
    prefix = tmp_path / "waiter"
    with tracking._RecordLock(path, require_sidecar=True):
        process = _start(home, prefix, method, args, timeout=0.1)
        try:
            stderr = _finish(process)
        finally:
            _stop(process)
    assert "RuntimeWarning" in stderr
    assert "could not lock" in stderr
    assert "TimeoutError" in stderr
    assert json.loads(prefix.with_suffix(".result").read_text()) == skipped
    assert path.read_text(encoding="utf-8") == original
    assert not prefix.with_suffix(".loaded").exists()


def test_permission_serialization_lock_open_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / ".copilot"
    home.mkdir()
    path = home / "permissions-config.json"
    path.with_suffix(".lock").mkdir()
    monkeypatch.setattr(permissions, "_copilot_dir", lambda: home)
    with pytest.warns(RuntimeWarning, match="could not lock"):
        assert permissions.ensure_extension_permission_approvals("/new") is False
    assert not path.exists()


def test_permission_serialization_missing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / ".copilot"
    monkeypatch.setattr(permissions, "_copilot_dir", lambda: home)
    assert permissions.add_trusted_folder("/new") is False
    assert permissions.remove_trusted_folder("/new") is False
    assert permissions.clone_permissions("/anchor", "/new") is False
    assert permissions.merge_permissions("/anchor", "/new") == []
    assert permissions.ensure_extension_permission_approvals("/new") is False
    assert not home.exists()
    home.mkdir()
    assert permissions.add_trusted_folder("/new") is False
    assert permissions.remove_trusted_folder("/new") is False
    assert permissions.clone_permissions("/anchor", "/new") is False
    assert permissions.merge_permissions("/anchor", "/new") == []
    assert not (home / "config.json").exists()
    assert not (home / "permissions-config.json").exists()
    assert permissions.ensure_extension_permission_approvals("/new") is True


def test_permission_serialization_no_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / ".copilot"
    home.mkdir()
    monkeypatch.setattr(permissions, "_copilot_dir", lambda: home)
    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.setitem(sys.modules, "msvcrt", None)
    with pytest.warns(RuntimeWarning, match="could not lock"):
        assert permissions.ensure_extension_permission_approvals("/new") is False
    assert not (home / "permissions-config.json").exists()


def test_permission_serialization_files_are_independent(tmp_path: Path) -> None:
    home = tmp_path / ".copilot"
    home.mkdir()
    config = home / "config.json"
    config.write_text('{"trustedFolders": []}', encoding="utf-8")
    prefix = tmp_path / "trust"
    with tracking._RecordLock(home / "permissions-config.json", require_sidecar=True):
        process = _start(home, prefix, "add_trusted_folder", ["/new"], timeout=0.1)
        try:
            assert _finish(process) == ""
        finally:
            _stop(process)
    assert json.loads(prefix.with_suffix(".result").read_text()) is True
    assert json.loads(config.read_text())["trustedFolders"] == ["/new"]


def test_permission_serialization_reentrant_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / ".copilot"
    home.mkdir()
    monkeypatch.setattr(permissions, "_copilot_dir", lambda: home)
    path = home / "permissions-config.json"
    with tracking._RecordLock(path, require_sidecar=True):
        assert permissions.ensure_extension_permission_approvals("/new") is True
    assert "/new" in json.loads(path.read_text())["locations"]


def test_permission_serialization_failed_replace_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.json"
    original = '// Header.\n{"unrelated": true}'
    path.write_text(original, encoding="utf-8")

    def refuse_replace(*args: object) -> None:
        raise OSError("fixture replacement refused")

    monkeypatch.setattr(permissions.os, "replace", refuse_replace)
    with pytest.raises(OSError, match="fixture replacement refused"):
        permissions._atomic_json_write(path, {"trustedFolders": ["/new"]})
    assert path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("fresh", [False, True])
def test_strict_trust_serializes_first_and_existing_config(tmp_path: Path, fresh: bool) -> None:
    home = tmp_path / ".copilot"
    home.mkdir()
    config = home / "config.json"
    header = "// Preserve native settings.\n\n"
    if not fresh:
        config.write_text(
            header + json.dumps({"trustedFolders": ["/existing"], "other": True}),
            encoding="utf-8",
        )
    roots = [tmp_path / "checkout-a", tmp_path / "checkout-b"]
    for root in roots:
        root.mkdir()
    a, b = tmp_path / "writer-a", tmp_path / "writer-b"
    processes = []
    try:
        processes.append(_start(home, a, "ensure_trusted_folder", [str(roots[0])], pause=True, timeout=6))
        _wait_for(a.with_suffix(".loaded"), processes[0])
        processes.append(_start(home, b, "ensure_trusted_folder", [str(roots[1])], timeout=6))
        _wait_for(b.with_suffix(".contending"), processes[1])
        assert not b.with_suffix(".loaded").exists()
        a.with_suffix(".release").touch()
        for process in processes:
            assert _finish(process) == ""
    finally:
        a.with_suffix(".release").touch()
        for process in processes:
            _stop(process)
    text = config.read_text()
    prefix = "" if fresh else header
    assert text.startswith(prefix)
    data = json.loads(text[len(prefix):])
    assert data["trustedFolders"] == ([] if fresh else ["/existing"]) + [
        str(root.resolve()) for root in roots
    ]
    if not fresh:
        assert data["other"] is True


def test_strict_trust_timeout_raises_and_never_writes(tmp_path: Path) -> None:
    home = tmp_path / ".copilot"
    home.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    config = home / "config.json"
    config.write_text('{"other": true}')
    prefix = tmp_path / "strict-waiter"
    with tracking._RecordLock(config, require_sidecar=True):
        process = _start(home, prefix, "ensure_trusted_folder", [str(checkout)], timeout=0.1)
        try:
            stdout, stderr = process.communicate(timeout=5)
            assert process.returncode != 0, stdout
            assert "FolderTrustError" in stderr
            assert "Could not lock" in stderr
            assert "TimeoutError" in stderr
        finally:
            _stop(process)
    assert config.read_text() == '{"other": true}'
    assert not prefix.with_suffix(".loaded").exists()


def test_confirmed_removal_while_ensure_waits_cannot_restore_trust(tmp_path, monkeypatch):
    home = tmp_path / ".copilot"
    home.mkdir()
    monkeypatch.setattr(permissions, "_copilot_dir", lambda: home)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    config = home / "config.json"
    permissions.ensure_trusted_folder(str(checkout))
    prefix = tmp_path / "late-ensure"
    with tracking._RecordLock(config, require_sidecar=True):
        process = _start(home, prefix, "ensure_trusted_folder", [str(checkout)], timeout=6)
        try:
            _wait_for(prefix.with_suffix(".contending"), process)
            checkout.rmdir()
            assert permissions.remove_trusted_folder(str(checkout))
        except BaseException:
            _stop(process)
            raise
    try:
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode != 0, stdout
        assert "FolderTrustError" in stderr
        assert "checkout disappeared" in stderr
    finally:
        _stop(process)
    assert json.loads(config.read_text())["trustedFolders"] == []
    assert not prefix.with_suffix(".loaded").exists()
