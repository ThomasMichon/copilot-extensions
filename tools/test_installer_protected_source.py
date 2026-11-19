"""Inert ownership controls do not establish native privileged qualification."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import installer_protected_source as protected
import pytest
from test_local_install_source import application_adapter as application_adapter

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="Linux protected-provider source controls"
)


@pytest.mark.parametrize(
    "provider", ["current", "old", "malformed", "rolled-back", "old-entry"]
)
def test_required_contract_is_read_from_selected_owner_without_executing_code(
    tmp_path: Path,
    provider: str,
) -> None:
    declaration = dict(protected.PROTECTED_INSTALLATION_CONTRACT)
    if provider == "rolled-back":
        declaration["version"] = 0
    helper = tmp_path / "installer_protected_source.py"
    helper.write_text(
        "PROTECTED_INSTALLATION_CONTRACT = (\n"
        if provider == "malformed"
        else "pass\n"
        if provider == "old"
        else "PROTECTED_INSTALLATION_CONTRACT = " + repr(declaration) + "\n"
        "raise AssertionError('capability discovery executed provider code')\n"
    )
    entry = tmp_path / "bootstrap-protected-install.sh"
    entry.write_bytes(
        b"not executed"
        if provider == "old-entry"
        else b"#!/bin/bash -p\n# protected-installation-provider: linux-root/v1\n"
        b"exit 99 # discovery must never execute this entry\n"
    )
    if provider == "current":
        assert protected.select_entry(tmp_path) == entry
    else:
        with pytest.raises(ValueError, match="lacks the protected"):
            protected.select_entry(tmp_path)


@pytest.fixture
def administrative_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Model native metadata only; never change the executing process identity."""
    original = Path.lstat

    def info(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        fields = list(original(path, *args, **kwargs))
        fields[4] = 1001 if path.name == "foreign" else 0
        if path.name == "owned-socket":
            fields[0] = stat.S_IFSOCK | 0o660
        if path == tmp_path or path in tmp_path.parents:
            fields[0] = int(fields[0]) & ~0o022
        return os.stat_result(fields)

    monkeypatch.setattr(Path, "lstat", info)
    monkeypatch.setattr(sys, "platform", "linux")
    for name in ("getuid", "geteuid", "getgid", "getegid"):
        monkeypatch.setattr(os, name, lambda: 0, raising=False)
    mounts = tmp_path / "mountinfo"
    mounts.write_bytes(b"1 0 0:1 / / rw - ext4 /dev/fixture rw\n")
    monkeypatch.setattr(protected, "_MOUNTINFO", mounts)


@pytest.fixture
def policy(
    tmp_path: Path, administrative_metadata: None
) -> tuple[Path, dict[str, Any]]:
    code = tmp_path / "provider"
    code.mkdir(mode=0o700)
    record: dict[str, Any] = {
        "contract": dict(protected.PROTECTED_INSTALLATION_CONTRACT),
        "component": "demo-app",
        "health_module": "demo_app",
        "expected_version": "1.0.profile.fixture",
        "expected_hash": "a" * 64,
        "arguments": ["--setup-value"],
        "repair": False,
        "global_bin": None,
    }
    for name in ("snapshot", "root", "home", "cache", "temporary"):
        selected = tmp_path / ("selected-" + name)
        selected.mkdir(mode=0o700)
        record[name] = str(selected)
    uv = tmp_path / "uv"
    uv.write_bytes(b"inert tool; never executed")
    uv.chmod(0o700)
    record["uv"] = str(uv)
    path = code / "protected-installation.json"
    path.write_text(json.dumps(record))
    path.chmod(0o600)
    return path, record


def _admit(policy: tuple[Path, dict[str, Any]]) -> protected.ProtectedApplication:
    path, record = policy
    path.write_text(json.dumps(record))
    return protected.admit(path, code=path.parent)


def test_admission_retains_addressed_inputs_without_requiring_incumbent_receipts(
    policy: tuple[Path, dict[str, Any]],
) -> None:
    _, record = policy
    root = Path(record["root"])
    (root / "current").symlink_to(root / "versions" / "missing-incumbent")
    (root / "retained").write_bytes(b"preserved")
    (root / "retained").chmod(0o600)
    selected = _admit(policy)
    assert selected.root == root
    assert selected.arguments == ("--setup-value",)
    assert selected.snapshot == Path(record["snapshot"])
    assert (root / "retained").read_bytes() == b"preserved"
    assert sorted(path.name for path in root.iterdir()) == ["current", "retained"]


@pytest.mark.parametrize("role", ["root", "snapshot"])
def test_output_ipc_is_preserved_but_cannot_supply_executable_inputs(
    policy: tuple[Path, dict[str, Any]],
    role: str,
) -> None:
    _, record = policy
    placeholder = Path(record[role]) / "owned-socket"
    placeholder.write_bytes(b"inert socket metadata model; no IPC created")
    if role == "root":
        assert _admit(policy).root == Path(record["root"])
        assert (
            placeholder.read_bytes() == b"inert socket metadata model; no IPC created"
        )
    else:
        with pytest.raises(ValueError, match="replaceable content"):
            _admit(policy)


@pytest.mark.parametrize(
    "case",
    [
        "old-provider",
        "malformed-version",
        "extra-field",
        "unknown-operation",
        "relative-root",
        "root-traversal",
        "root-is-filesystem",
        "tool-not-executable",
        "changed-owner",
        "writable-code",
        "linked-source",
        "escaping-incumbent-link",
        "indirect-parent-link",
        "input-output-overlap",
        "output-role-overlap",
        "writable-command",
    ],
)
def test_invalid_selections_refuse_before_construction(
    policy: tuple[Path, dict[str, Any]],
    case: str,
) -> None:
    path, record = policy
    if case == "old-provider":
        record["contract"]["provider"] = "linux-root/v0"
    elif case == "malformed-version":
        record["contract"]["version"] = True
    elif case == "unknown-operation":
        record["contract"]["operation"] = "execute-controller"
    elif case == "extra-field":
        record["approved"] = True
    elif case == "relative-root":
        record["root"] = "relative"
    elif case == "root-traversal":
        record["root"] += "/../root"
    elif case == "root-is-filesystem":
        record["root"] = "/"
    elif case == "tool-not-executable":
        Path(record["uv"]).chmod(0o600)
    elif case == "changed-owner":
        (Path(record["snapshot"]) / "foreign").write_bytes(b"unadmitted")
    elif case == "writable-code":
        path.parent.chmod(0o770)
    elif case == "linked-source":
        (Path(record["snapshot"]) / "linked").symlink_to(record["uv"])
    elif case == "escaping-incumbent-link":
        (Path(record["root"]) / "current").symlink_to(record["snapshot"])
    elif case == "indirect-parent-link":
        (Path(record["root"]) / "current").symlink_to("nested/../versions")
    elif case == "input-output-overlap":
        record["root"] = record["snapshot"]
    elif case == "writable-command":
        commands = path.parent.parent / "commands"
        commands.mkdir(mode=0o700)
        (commands / "demo-app").write_bytes(b"already matching command bytes")
        (commands / "demo-app").chmod(0o660)
        record["global_bin"] = str(commands)
    else:
        record["cache"] = record["home"]
    before = sorted(Path(record["snapshot"]).iterdir())
    with pytest.raises(ValueError):
        _admit(policy)
    assert sorted(Path(record["snapshot"]).iterdir()) == before


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_unsupported_platform_refuses_before_reading_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    with pytest.raises(ValueError, match="Linux root identity only"):
        protected.admit(tmp_path / "absent", code=tmp_path)


def test_duplicate_policy_fields_and_withdrawn_policy_are_not_approval(
    policy: tuple[Path, dict[str, Any]],
) -> None:
    path, _ = policy
    path.write_text('{"contract": {}, "contract": {}}')
    with pytest.raises(ValueError, match="duplicate"):
        protected.admit(path, code=path.parent)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        protected.admit(path, code=path.parent)


def test_admission_inspection_keeps_its_finite_bound(
    policy: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = policy
    monkeypatch.setattr(protected, "_MAX_ENTRIES", 0)
    with pytest.raises(ValueError, match="inspection bound"):
        protected.admit(path, code=path.parent)


@pytest.mark.parametrize("filesystem", ["9p", "drvfs", "nfs4", "fuse", "cifs"])
def test_native_mode_emulation_is_not_protected_custody(
    policy: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    filesystem: str,
) -> None:
    _, record = policy
    with (tmp_path / "mountinfo").open("ab") as stream:
        stream.write(
            f"2 1 0:2 / {record['snapshot']} rw - {filesystem} fixture rw\n".encode(),
        )
    with pytest.raises(ValueError, match="native Linux filesystems"):
        _admit(policy)


def test_existing_os_interpreter_link_does_not_require_incumbent_provenance(
    policy: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, record = policy
    operating_system = tmp_path / "operating-system"
    operating_system.mkdir(mode=0o700)
    interpreter = operating_system / "python"
    interpreter.write_bytes(b"inert OS interpreter reference")
    interpreter.chmod(0o500)
    monkeypatch.setattr(sys, "base_prefix", str(operating_system))
    mount_reads: list[tuple[Path, ...]] = []
    original = protected._native_mounts

    def observe(paths: tuple[Path, ...]) -> None:
        mount_reads.append(paths)
        original(paths)

    monkeypatch.setattr(protected, "_native_mounts", observe)
    for index in range(10):
        (Path(record["root"]) / f"retained-python-{index}").symlink_to(interpreter)
    assert _admit(policy).root == Path(record["root"])
    assert len(mount_reads) == 2, "mount inspection must not repeat for every OS link"


@pytest.mark.parametrize(
    "outcome",
    [
        "installed",
        "changed-inputs",
        "outside-package",
        "constructor-collision",
        "unknown-start",
    ],
)
def test_protected_dispatch_reaches_actual_application_adapter_without_replay(
    application_adapter: SimpleNamespace,
    policy: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    fixture = application_adapter
    source = fixture.source
    snapshot = fixture.application.parent
    source.select_project(snapshot, "demo-app").payload = snapshot / "payload"
    selected = replace(
        _admit(policy),
        snapshot=snapshot,
        root=fixture.root,
        expected_version=fixture.selection["runtime_version"],
        expected_hash=fixture.selection["payload_hash"],
    )
    monkeypatch.setitem(sys.modules, "install_source", source)
    monkeypatch.setattr(sys, "path", sys.path[:])
    if outcome == "changed-inputs":
        selected = replace(selected, expected_hash="b" * 64)
        with pytest.raises(ValueError, match="selection changed"):
            protected.install(selected, code=policy[0].parent)
        assert fixture.versions == []
        assert fixture.children == []
    elif outcome == "outside-package":
        monkeypatch.setattr(source.engine, "_package_roots", lambda *_: (fixture.root,))
        with pytest.raises(ValueError, match="closure leaves"):
            protected.install(selected, code=policy[0].parent)
        assert fixture.versions == fixture.children == []
    elif outcome == "constructor-collision":
        collision = FileExistsError("retained candidate collision")
        fixture.failures.append(collision)
        with pytest.raises(FileExistsError) as collision_result:
            protected.install(selected, code=policy[0].parent)
        assert collision_result.value is collision
        assert fixture.versions == [fixture.old.name]
        assert fixture.children == []
    elif outcome == "unknown-start":
        error = RuntimeError("application-owned uncertain effect")
        fixture.child_error = error
        with pytest.raises(RuntimeError) as caught:
            protected.install(selected, code=policy[0].parent)
        assert caught.value is error
        assert len(fixture.versions) == len(fixture.children) == 1
    else:
        assert protected.install(selected, code=policy[0].parent) == {
            "state": "installed",
            "slot": str(fixture.old),
        }
        assert fixture.versions == [fixture.old.name]
        assert fixture.candidate_options[0]["uv"] == selected.uv
    assert (fixture.old / "preserved").read_bytes() == b"incumbent bytes"


@pytest.mark.skipif(
    sys.platform != "linux" or os.getuid() == 0,
    reason="unprivileged Linux entry control",
)
def test_native_entry_refuses_ordinary_controller_before_loading_python(
    tmp_path: Path,
) -> None:
    entry = Path(protected.__file__).with_name("bootstrap-protected-install.sh")
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / "installer_protected_source.py").write_text(
        "raise AssertionError('controller code ran')"
    )
    result = subprocess.run(
        ["/bin/bash", "-p", str(entry)],
        cwd=poison,
        env={"PATH": str(poison), "PYTHONPATH": str(poison)},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert "Linux root-owned native entry" in result.stderr
    assert "controller code ran" not in result.stderr
