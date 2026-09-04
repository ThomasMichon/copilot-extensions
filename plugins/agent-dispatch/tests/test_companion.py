from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_dispatch import companion
from agent_dispatch.companion import (
    CompanionError,
    CompanionIndeterminate,
    DefaultCompanionController,
    _base_environment,
    _resolve_command,
    resolve_companion,
)


def _registration(root: Path, **spec) -> dict:
    return {
        "id": "declared:test:companion",
        "kind": "plugin-companion",
        "spec": {"command": ["bin/service.py"], **spec},
        "machine": "machine-a",
        "env": "default",
        "plugin": {
            "root": str(root),
            "source_path": str(root / ".github" / "plugin" / "plugin.json"),
            "version": "1.0.0",
            "activation_scopes": ["repo"],
        },
        "runtime_revision": {
            "plugin_root": str(root),
            "plugin_version": "1.0.0",
        },
    }


def _write_script(path: Path, body: str = "pass\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_resolve_command_accepts_contained_regular_file(tmp_path):
    root = tmp_path / "plugin"
    script = root / "bin" / "service.py"
    _write_script(script)

    command = _resolve_command(root, ["bin\\service.py", "--serve"], field="command")

    assert command == (sys.executable, str(script), "--serve")


@pytest.mark.parametrize(
    "value",
    [
        ["../outside.py"],
        ["/outside.py"],
        ["C:outside.py"],
        ["C:\\outside.py"],
    ],
)
def test_resolve_command_rejects_non_relative_paths(tmp_path, value):
    root = tmp_path / "plugin"
    root.mkdir()
    with pytest.raises(CompanionError, match="contained relative path"):
        _resolve_command(root, value, field="command")


def test_resolve_command_rejects_symlink_component(tmp_path):
    root = tmp_path / "plugin"
    outside = tmp_path / "outside"
    _write_script(outside / "service.py")
    root.mkdir()
    try:
        (root / "bin").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available")

    with pytest.raises(CompanionError, match="symlink"):
        _resolve_command(root, ["bin/service.py"], field="command")


def test_sanitize_environment_removes_dispatch_authority_case_insensitively(
    monkeypatch,
):
    monkeypatch.setattr(
        os,
        "environ",
        {
            "PATH": "kept",
            "AGENT_DISPATCH_TOKEN": "secret",
            "agent_dispatch_url": "secret",
        },
    )
    sanitized = _base_environment({"id": "unit", "owner": "plugin"})
    assert sanitized == {
        "PATH": "kept",
        "COPILOT_COMPANION_ID": "unit",
        "COPILOT_COMPANION_OWNER": "plugin",
    }


def test_provider_active_supplies_runtime_arguments_and_environment(tmp_path):
    root = tmp_path / "plugin"
    _write_script(root / "bin" / "service.py")
    _write_script(root / "bin" / "provider.py")
    registration = _registration(
        root, config_provider=["bin/provider.py"], config_timeout_seconds=2
    )

    def runner(command, **kwargs):
        request = json.loads(kwargs["input_text"])
        assert request["machine"] == "machine-a"
        assert request["plugin_version"] == "1.0.0"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "schema_version": 1,
                    "active": True,
                    "arguments": ["--index", "repo"],
                    "environment": {"INDEX_MODE": "local"},
                }
            ),
            stderr="",
        )

    resolved = resolve_companion(registration, machine="machine-a", env="default", runner=runner)

    assert resolved is not None
    assert resolved.command[-2:] == ("--index", "repo")
    assert resolved.environment["INDEX_MODE"] == "local"
    assert resolved.registration["companion_runtime"]["arguments"] == [
        "--index",
        "repo",
    ]


def test_captured_provider_uses_owned_no_window_launch(monkeypatch):
    seen = {}

    class Process:
        returncode = 0
        pid = 123

        def communicate(self, input_text, timeout):
            return "{}", ""

    def fake_popen(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(companion.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        companion,
        "no_window_kwargs",
        lambda: {"creationflags": 0x08000000},
    )

    completed = companion._run_captured(
        ("python", "provider.py"),
        cwd=".",
        environment={},
        timeout=2,
        input_text="request",
    )

    assert completed.returncode == 0
    assert seen["command"] == ["python", "provider.py"]
    assert seen["kwargs"]["creationflags"] == 0x08000000
    assert "start_new_session" not in seen["kwargs"]


def test_provider_inactive_withdraws_companion(tmp_path):
    root = tmp_path / "plugin"
    _write_script(root / "bin" / "service.py")
    _write_script(root / "bin" / "provider.py")
    registration = _registration(root, config_provider=["bin/provider.py"])

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout='{"schema_version":1,"active":false}',
            stderr="",
        )

    assert (
        resolve_companion(registration, machine="machine-a", env="default", runner=runner) is None
    )


@pytest.mark.parametrize(
    "completed",
    [
        subprocess.CompletedProcess(["provider"], 1, stdout="", stderr="failed"),
        subprocess.CompletedProcess(["provider"], 0, stdout="not-json", stderr=""),
        subprocess.CompletedProcess(
            ["provider"],
            0,
            stdout='{"schema_version":2,"active":true}',
            stderr="",
        ),
    ],
)
def test_provider_failure_is_indeterminate(tmp_path, completed):
    root = tmp_path / "plugin"
    _write_script(root / "bin" / "service.py")
    _write_script(root / "bin" / "provider.py")
    registration = _registration(root, config_provider=["bin/provider.py"])

    with pytest.raises(CompanionIndeterminate):
        resolve_companion(
            registration,
            machine="machine-a",
            env="default",
            runner=lambda *args, **kwargs: completed,
        )


def test_provider_cannot_restore_reserved_authority(tmp_path):
    root = tmp_path / "plugin"
    _write_script(root / "bin" / "service.py")
    _write_script(root / "bin" / "provider.py")
    registration = _registration(root, config_provider=["bin/provider.py"])

    with pytest.raises(CompanionError, match="reserved"):
        resolve_companion(
            registration,
            machine="machine-a",
            env="default",
            runner=lambda *args, **kwargs: subprocess.CompletedProcess(
                args[0],
                0,
                stdout=json.dumps(
                    {
                        "schema_version": 1,
                        "active": True,
                        "environment": {"AGENT_DISPATCH_TOKEN": "forbidden"},
                    }
                ),
                stderr="",
            ),
        )


def test_health_contract_distinguishes_confirmed_and_indeterminate(tmp_path):
    root = tmp_path / "plugin"
    _write_script(root / "bin" / "service.py")
    _write_script(root / "bin" / "health.py")
    registration = _registration(root, health_probe=["bin/health.py"])
    resolution = resolve_companion(registration, machine="machine-a", env="default")
    assert resolution is not None

    outcomes = iter(
        [
            '{"schema_version":1,"healthy":true}',
            '{"schema_version":1,"healthy":false,"detail":"down"}',
            "invalid",
        ]
    )
    controller = DefaultCompanionController(
        tmp_path / "state",
        runner=lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout=next(outcomes), stderr=""
        ),
    )

    assert controller.health(resolution) is True
    assert controller.health(resolution) is False
    with pytest.raises(CompanionIndeterminate):
        controller.health(resolution)


class _FakeProcess:
    def __init__(self, pid: int = 123):
        self.pid = pid
        self.terminated = False
        self.returncode = None

    def poll(self):
        return self.returncode

    def release(self):
        return None

    def terminate(self):
        self.terminated = True
        self.returncode = -1

    def wait(self, timeout=None):
        return self.returncode or 0


def test_recovery_requires_matching_process_start_identity(tmp_path, monkeypatch):
    root = tmp_path / "plugin"
    _write_script(root / "bin" / "service.py")
    resolution = resolve_companion(_registration(root), machine="machine-a", env="default")
    assert resolution is not None
    controller = DefaultCompanionController(
        tmp_path / "state", token_source=lambda pid: "new-token"
    )
    receipt = {
        "schema_version": 1,
        "registration_id": resolution.registration["id"],
        "fingerprint": "fingerprint",
        "pid": 123,
        "start_token": "old-token",
        "command_digest": companion._command_digest(resolution.command),
    }
    controller.receipt_dir.mkdir()
    controller._receipt_path(resolution.registration["id"]).write_text(
        json.dumps(receipt), encoding="utf-8"
    )
    monkeypatch.setattr(companion, "_process_exists", lambda pid: True)

    assert controller._recover(resolution, fingerprint="fingerprint") is None
    assert not controller._receipt_path(resolution.registration["id"]).exists()


def test_recovery_refuses_to_guess_when_identity_is_unavailable(tmp_path, monkeypatch):
    root = tmp_path / "plugin"
    _write_script(root / "bin" / "service.py")
    resolution = resolve_companion(_registration(root), machine="machine-a", env="default")
    assert resolution is not None
    controller = DefaultCompanionController(tmp_path / "state", token_source=lambda pid: None)
    receipt = {
        "schema_version": 1,
        "registration_id": resolution.registration["id"],
        "fingerprint": "fingerprint",
        "pid": 123,
        "start_token": "old-token",
        "command_digest": companion._command_digest(resolution.command),
    }
    controller.receipt_dir.mkdir()
    controller._receipt_path(resolution.registration["id"]).write_text(
        json.dumps(receipt), encoding="utf-8"
    )
    monkeypatch.setattr(companion, "_process_exists", lambda pid: True)

    with pytest.raises(CompanionIndeterminate, match="identity"):
        controller._recover(resolution, fingerprint="fingerprint")


def test_receipt_retirement_signals_only_matching_creation_identity(tmp_path, monkeypatch):
    calls: list[int] = []
    controller = DefaultCompanionController(
        tmp_path / "state", token_source=lambda pid: "matching"
    )
    if os.name == "nt":
        monkeypatch.setattr(companion, "_terminate_windows_tree", lambda pid: calls.append(pid))
    else:
        monkeypatch.setattr(companion, "_terminate_posix_group", lambda pid: calls.append(pid))

    controller._retire_receipt({"pid": 123, "start_token": "matching"})
    controller.token_source = lambda pid: "different"
    controller._retire_receipt({"pid": 456, "start_token": "matching"})

    assert calls == [123]


def test_receipt_paths_are_collision_safe(tmp_path):
    controller = DefaultCompanionController(tmp_path / "state")
    first = controller._receipt_path("declared:a-b:c")
    second = controller._receipt_path("declared:a:b-c")
    assert first != second


def test_reconcile_receipts_retires_only_unadopted_units(tmp_path, monkeypatch):
    calls: list[int] = []
    controller = DefaultCompanionController(
        tmp_path / "state", token_source=lambda pid: f"token-{pid}"
    )
    controller.receipt_dir.mkdir()
    for registration_id, pid in (("adopted", 123), ("stale", 456)):
        controller._receipt_path(registration_id).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "registration_id": registration_id,
                    "pid": pid,
                    "start_token": f"token-{pid}",
                }
            ),
            encoding="utf-8",
        )
    if os.name == "nt":
        monkeypatch.setattr(companion, "_terminate_windows_tree", lambda pid: calls.append(pid))
    else:
        monkeypatch.setattr(companion, "_terminate_posix_group", lambda pid: calls.append(pid))

    controller.reconcile_receipts({"adopted"})

    assert calls == [456]
    assert controller._receipt_path("adopted").exists()
    assert not controller._receipt_path("stale").exists()


def test_reconcile_receipts_retains_leaderless_posix_group(tmp_path, monkeypatch):
    controller = DefaultCompanionController(tmp_path / "state", token_source=lambda pid: None)
    controller.receipt_dir.mkdir()
    receipt = controller._receipt_path("stale")
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "registration_id": "stale",
                "pid": 456,
                "start_token": "old-token",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(companion, "_process_exists", lambda pid: False)
    monkeypatch.setattr(companion, "_process_group_exists", lambda pgid: True)

    with pytest.raises(CompanionIndeterminate, match="without its identity leader"):
        controller.reconcile_receipts(set())

    assert receipt.exists()


def test_recovered_process_rechecks_identity_before_signaling(monkeypatch):
    calls: list[int] = []
    tokens = iter(["matching", "different"])
    process = companion._IdentityProcess(123, "matching", token_source=lambda pid: next(tokens))
    monkeypatch.setattr(companion, "_terminate_posix_group", lambda pid: calls.append(pid))

    process.terminate()
    process.terminate()

    assert calls == [123]


def test_receipt_write_failure_retires_blocked_process(tmp_path, monkeypatch):
    root = tmp_path / "plugin"
    _write_script(root / "bin" / "service.py")
    resolution = resolve_companion(_registration(root), machine="machine-a", env="default")
    assert resolution is not None
    process = _FakeProcess()
    monkeypatch.setattr(companion, "_launch_gated", lambda _resolution: process)
    monkeypatch.setattr(
        companion,
        "write_json_object_atomic",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    controller = DefaultCompanionController(tmp_path / "state", token_source=lambda pid: "token")

    with pytest.raises(CompanionIndeterminate, match="receipt"):
        controller.launch(resolution, fingerprint="fingerprint")
    assert process.terminated is True


def test_startup_exit_retires_process_tree_before_dropping_receipt(tmp_path, monkeypatch):
    root = tmp_path / "plugin"
    _write_script(root / "bin" / "service.py")
    resolution = resolve_companion(_registration(root), machine="machine-a", env="default")
    assert resolution is not None
    process = _FakeProcess()
    process.returncode = 1
    monkeypatch.setattr(companion, "_launch_gated", lambda _resolution: process)
    controller = DefaultCompanionController(tmp_path / "state", token_source=lambda pid: "token")

    with pytest.raises(CompanionError, match="startup"):
        controller.launch(resolution, fingerprint="fingerprint")

    assert process.terminated is True
    assert not controller._receipt_path(resolution.registration["id"]).exists()


def test_gated_launch_runs_only_after_receipt_and_stops_tree(tmp_path):
    root = tmp_path / "plugin"
    marker = root / "started.txt"
    _write_script(
        root / "bin" / "service.py",
        "from pathlib import Path\n"
        "import time\n"
        f"Path({str(marker)!r}).write_text('started', encoding='utf-8')\n"
        "time.sleep(60)\n",
    )
    resolution = resolve_companion(_registration(root), machine="machine-a", env="default")
    assert resolution is not None
    controller = DefaultCompanionController(tmp_path / "state")

    launched = controller.launch(resolution, fingerprint="fingerprint")
    receipt = controller._receipt_path(resolution.registration["id"])
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert receipt.exists()
        assert marker.read_text(encoding="utf-8") == "started"
    finally:
        controller.stop(resolution, launched.process)
    assert launched.process.poll() is not None
    assert not receipt.exists()
