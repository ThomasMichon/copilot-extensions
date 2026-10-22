"""Real installation policy admission and durable lifecycle purpose."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from test_command_binding import (
    POWERSHELL, _lifecycle_lock_path, _prepare_catalog, _snapshot, _write,
    binding_fixture as binding_fixture,
    powershell_host as powershell_host,
)
from test_command_lifecycle import _real_installer


def _policy(fixture: dict, managed: bool = True) -> dict:
    primitive = fixture["primitive"]
    arguments = fixture["arguments"]
    result = primitive.resolve_runtime_deployment(**arguments)
    value = {
        "schema": primitive.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
        "managed": managed, "owner": primitive._deployment_owner(result, arguments),
    }
    _write(fixture["legacy"] / "runtime-deployment.json", value)
    return value


@pytest.mark.parametrize("phase", ["before", "after", "both"])
def test_lifecycle_accepts_native_powershell_utf8_manifests(
    binding_fixture: dict, phase: str,
) -> None:
    manifest = binding_fixture["legacy"] / "deploy-manifest.json"
    document = {
        "schema_version": 3, "service": "agent-worktrees", "source": {"version": "1.0.0"},
    }
    if phase in {"before", "both"}:
        manifest.write_text(json.dumps(document), encoding="utf-8-sig")
    command = [sys.executable, "-I", "-B", "-c", "pass"]
    if phase in {"after", "both"}:
        command[-1] = (
            "from pathlib import Path;"
            f"Path({str(manifest)!r}).write_text({json.dumps(document)!r},encoding='utf-8-sig')"
        )
    binding = binding_fixture["binding"]
    assert binding._load_lifecycle().run_lifecycle(
        binding, binding_fixture["primitive"], command, **binding_fixture["arguments"],
    ) == 0


def test_lifecycle_retains_older_manifest_reader(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    primitive = binding_fixture["primitive"]
    monkeypatch.delattr(primitive, "read_deployment_manifest")
    _write(binding_fixture["legacy"] / "deploy-manifest.json", {
        "schema_version": 3, "service": "agent-worktrees",
    })
    binding = binding_fixture["binding"]
    with binding._load_lifecycle().legacy_lifecycle(
        binding, primitive, **binding_fixture["arguments"],
    ) as lease:
        lease.assert_owned()


@pytest.mark.parametrize("reserve", [True, False])
@pytest.mark.parametrize("state,management", [
    (state, management)
    for state in ("managed", "invalid", "future", "foreign")
    for management in (False, True)
    if not (state == "managed" and management)
])
def test_command_binding_deployment_refuses_before_any_writes(
    binding_fixture: dict, reserve: bool, state: str, management: bool,
) -> None:
    policy = _policy(binding_fixture)
    if state == "invalid":
        policy["managed"] = "true"
    elif state == "future":
        policy["version"] = 2
    elif state == "foreign":
        policy["owner"]["sourceFingerprint"] = "sha256:" + "0" * 64
    _write(binding_fixture["legacy"] / "runtime-deployment.json", policy)
    before = _snapshot(binding_fixture["home"])
    binding = binding_fixture["binding"]
    with pytest.raises(binding.CommandOwnershipConflict, match="publication refused"):
        with binding._load_lifecycle().legacy_lifecycle(
            binding, binding_fixture["primitive"], reserve=reserve,
            managed_deployment=management, **binding_fixture["arguments"],
        ):
            pytest.fail("Refused policy reached publication")
    assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("managed", [False, True])
def test_command_binding_deployment_management_uses_existing_owner(
    binding_fixture: dict, managed: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _policy(binding_fixture, managed)
    binding = binding_fixture["binding"]
    primitive = binding_fixture["primitive"]
    lifecycle = binding._load_lifecycle()
    with lifecycle.legacy_lifecycle(
        binding, primitive, managed_deployment=True, **binding_fixture["arguments"],
    ) as lease:
        assert lease.purpose == {
            "version": 1, "origin": "management", "operation": "publish",
        }
        assert primitive.read_json(lease.lock.owner_path)["runtimeDeployment"] == lease.purpose
        assert "runtimeDeployment" not in lease.ticket
        token = lifecycle._CURRENT.set(None)
        try:
            monkeypatch.setenv(lifecycle.TICKET_ENV, json.dumps(lease.ticket))
            borrowed = lifecycle.borrowed_lease(binding, primitive, **binding_fixture["arguments"])
            assert borrowed.purpose == lease.purpose
            with lifecycle.legacy_lifecycle(
                binding, primitive, **binding_fixture["arguments"],
            ) as nested:
                nested.assert_owned()
        finally:
            lifecycle._CURRENT.reset(token)
    assert not _lifecycle_lock_path(binding_fixture["legacy"]).exists()


def test_command_binding_deployment_plugin_lease_cannot_upgrade(
    binding_fixture: dict,
) -> None:
    binding = binding_fixture["binding"]
    primitive = binding_fixture["primitive"]
    lifecycle = binding._load_lifecycle()
    with lifecycle.legacy_lifecycle(binding, primitive, **binding_fixture["arguments"]):
        with pytest.raises(binding.CommandOwnershipConflict, match="cannot change purpose"):
            with lifecycle.legacy_lifecycle(
                binding, primitive, managed_deployment=True, **binding_fixture["arguments"],
            ):
                pytest.fail("Plugin lease upgraded to management")


@pytest.mark.parametrize("change", ["policy", "purpose"])
def test_command_binding_deployment_revalidates_held_lease(
    binding_fixture: dict, change: str,
) -> None:
    binding = binding_fixture["binding"]
    primitive = binding_fixture["primitive"]
    lifecycle = binding._load_lifecycle()
    with pytest.raises(binding.CommandOwnershipConflict):
        with lifecycle.legacy_lifecycle(binding, primitive, **binding_fixture["arguments"]) as lease:
            if change == "policy":
                _policy(binding_fixture)
            else:
                owner = primitive.read_json(lease.lock.owner_path)
                owner["runtimeDeployment"]["origin"] = "management"
                primitive._atomic_write_json(lease.lock.owner_path, owner, lock=lease.lock)
            lease.assert_owned()


def test_command_binding_deployment_rechecks_after_real_lock_acquisition(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = binding_fixture["binding"]
    primitive = binding_fixture["primitive"]
    lifecycle = binding._load_lifecycle()
    original = lifecycle._lock

    @contextmanager
    def changed_policy(*args):
        with original(*args) as lock:
            _policy(binding_fixture)
            yield lock

    monkeypatch.setattr(lifecycle, "_lock", changed_policy)
    with pytest.raises(binding.CommandOwnershipConflict, match="publication refused"):
        with lifecycle.legacy_lifecycle(binding, primitive, **binding_fixture["arguments"]):
            pytest.fail("Policy changed before admission")
    assert not (binding_fixture["legacy"] / "deploy-manifest.json").exists()
    assert not _lifecycle_lock_path(binding_fixture["legacy"]).exists()


def test_command_binding_deployment_teardown_retains_policy_independence(
    binding_fixture: dict,
) -> None:
    _write(binding_fixture["legacy"] / "runtime-deployment.json", {"version": 999})
    binding = binding_fixture["binding"]
    lifecycle = binding._load_lifecycle()
    with lifecycle.legacy_lifecycle(
        binding, binding_fixture["primitive"], teardown=True, reserve=False,
        **binding_fixture["arguments"],
    ) as lease:
        lease.assert_owned()
        with pytest.raises(binding.CommandOwnershipConflict, match="does not authorize publish"):
            with lifecycle.legacy_lifecycle(
                binding, binding_fixture["primitive"], reserve=False,
                **binding_fixture["arguments"],
            ):
                pytest.fail("Teardown lease admitted publication")
        with pytest.raises(binding.CommandOwnershipConflict, match="does not authorize publish"):
            binding.write_legacy_manifest(
                binding_fixture["primitive"], {"schema_version": 3, "service": "agent-worktrees"},
                **binding_fixture["arguments"],
            )


def test_command_binding_deployment_manifest_writer_checks_policy_without_lease(
    binding_fixture: dict,
) -> None:
    _policy(binding_fixture)
    binding = binding_fixture["binding"]
    before = _snapshot(binding_fixture["home"])
    with pytest.raises(binding.CommandOwnershipConflict, match="publication refused"):
        binding.write_legacy_manifest(
            binding_fixture["primitive"], {"schema_version": 3, "service": "agent-worktrees"},
            **binding_fixture["arguments"],
        )
    assert _snapshot(binding_fixture["home"]) == before


def test_command_binding_deployment_old_helper_requires_absent_policy(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    primitive = binding_fixture["primitive"]
    monkeypatch.delattr(primitive, "resolve_runtime_deployment")
    binding = binding_fixture["binding"]
    lifecycle = binding._load_lifecycle()
    with lifecycle.legacy_lifecycle(binding, primitive, **binding_fixture["arguments"]):
        pass
    _write(binding_fixture["legacy"] / "runtime-deployment.json", {})
    before = _snapshot(binding_fixture["home"])
    with pytest.raises(binding.CommandOwnershipConflict, match="capable installation helper"):
        with lifecycle.legacy_lifecycle(binding, primitive, **binding_fixture["arguments"]):
            pytest.fail("Old helper ignored policy")
    assert _snapshot(binding_fixture["home"]) == before


def test_command_binding_deployment_environment_cannot_grant_management(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _policy(binding_fixture)
    binding = binding_fixture["binding"]
    lifecycle = binding._load_lifecycle()
    monkeypatch.setenv("COPILOT_EXTENSIONS_MANAGED_DEPLOYMENT", "1")
    monkeypatch.setenv(lifecycle.TICKET_ENV, json.dumps({
        "token": "a" * 32, "pid": os.getpid(), "host": "fixture",
        "root": str(binding_fixture["legacy"]), "owner": {}, "runtimeDeployment": {
            "version": 1, "origin": "management", "operation": "publish",
        },
    }))
    before = _snapshot(binding_fixture["home"])
    with pytest.raises(binding.CommandOwnershipConflict, match="ticket"):
        with lifecycle.legacy_lifecycle(binding, binding_fixture["primitive"], **binding_fixture["arguments"]):
            pytest.fail("Environment forged management admission")
    assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("management", [False, True])
def test_command_binding_deployment_run_carries_durable_purpose_to_child(
    binding_fixture: dict, management: bool,
) -> None:
    _policy(binding_fixture)
    binding = binding_fixture["binding"]
    lifecycle = binding._load_lifecycle()
    marker = binding_fixture["legacy"] / "child-purpose.json"
    lock_owner = _lifecycle_lock_path(binding_fixture["legacy"]) / "owner.json"
    command = [
        sys.executable, "-I", "-S", "-B", "-c",
        "import json;from pathlib import Path;"
        f"owner=json.loads(Path({str(lock_owner)!r}).read_text());"
        f"Path({str(marker)!r}).write_text(json.dumps(owner['runtimeDeployment']))",
    ]
    if management:
        assert lifecycle.run_lifecycle(
            binding, binding_fixture["primitive"], command, managed_deployment=True,
            **binding_fixture["arguments"],
        ) == 0
        assert json.loads(marker.read_text())["origin"] == "management"
    else:
        with pytest.raises(binding.CommandOwnershipConflict, match="publication refused"):
            lifecycle.run_lifecycle(
                binding, binding_fixture["primitive"], command, **binding_fixture["arguments"],
            )
        assert not marker.exists()


@pytest.mark.parametrize("options", [
    [], ["--check-lifecycle"], ["--publish-legacy"], ["--run-lifecycle", "--teardown"],
])
def test_command_binding_deployment_cli_restricts_explicit_flag(
    binding_fixture: dict, options: list[str],
) -> None:
    _prepare_catalog(binding_fixture)
    payload = binding_fixture["payload"]
    before = _snapshot(binding_fixture["home"])
    result = subprocess.run([
        sys.executable, "-I", "-B", str(payload / "scripts/command_binding.py"),
        "--payload-root", str(payload), "--plugin-id", "agent-worktrees",
        "--legacy-root", str(binding_fixture["legacy"]), "--managed-deployment", *options,
    ], capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 2
    assert "--managed-deployment requires" in result.stderr
    assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("shell", [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX installer")),
    pytest.param("powershell", marks=pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")),
])
def test_command_binding_deployment_real_native_installer_refuses_before_staging(
    binding_fixture: dict, shell: str, powershell_host,
) -> None:
    installer, environment = _real_installer(binding_fixture, shell, "installed")
    _policy(binding_fixture)
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])
    for action in ("install", "update", "provision", "stamp"):
        command = (
            ["bash", str(installer), action] if shell == "bash" else
            [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(installer), action]
        )
        result = host.run(command) if host else subprocess.run(
            command, env=environment, cwd=binding_fixture["home"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert "deployment-managed" in result.stdout + result.stderr
        assert _snapshot(binding_fixture["home"]) == before
    if host:
        host.close()
        assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("value", [
    {}, {"version": True, "origin": "management", "operation": "publish"},
    {"version": 1, "origin": [], "operation": "publish"},
    {"version": 1, "origin": "management", "operation": []},
])
def test_command_binding_deployment_invalid_durable_purpose_refuses(
    binding_fixture: dict, value: dict,
) -> None:
    binding = binding_fixture["binding"]
    primitive = binding_fixture["primitive"]
    lifecycle = binding._load_lifecycle()
    with pytest.raises(binding.CommandOwnershipConflict, match="lease purpose"):
        with lifecycle.legacy_lifecycle(binding, primitive, **binding_fixture["arguments"]) as lease:
            primitive._atomic_write_json(
                lease.lock.owner_path, {**lease.lock._owner(), "runtimeDeployment": value},
                lock=lease.lock,
            )
            lease.assert_owned()


@pytest.mark.parametrize("shell", [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX installer")),
    pytest.param("powershell", marks=pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")),
])
def test_command_binding_deployment_explicit_cli_native_reentry(
    binding_fixture: dict, shell: str,
) -> None:
    installer, environment = _real_installer(binding_fixture, shell, "installed")
    _policy(binding_fixture)
    payload = binding_fixture["payload"]
    native = (
        [shutil.which("bash"), str(installer), "stamp"] if shell == "bash" else
        [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(installer), "stamp"]
    )
    interpreter = (
        sys.executable if os.name == "nt" else
        str(binding_fixture["home"] / "test-bin/python3")
    )
    result = subprocess.run([
        interpreter, "-I", "-B", str(payload / "scripts/command_binding.py"),
        "--payload-root", str(payload), "--plugin-id", "agent-worktrees",
        "--legacy-root", str(binding_fixture["legacy"]),
        "--run-lifecycle", "--deferred-runtime", "--managed-deployment", "--", *native,
    ], env=environment, cwd=binding_fixture["home"],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    smoke = json.loads((binding_fixture["legacy"] / "smoke.json").read_text())
    assert smoke["staged"] is True
    assert Path(smoke["staged_from"]) == payload
    assert not _lifecycle_lock_path(binding_fixture["legacy"]).exists()


@pytest.mark.parametrize("shell", [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX installer")),
    pytest.param("powershell", marks=pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")),
])
def test_command_binding_deployment_native_teardown_ticket_cannot_publish(
    binding_fixture: dict, shell: str, powershell_host,
) -> None:
    installer, environment = _real_installer(binding_fixture, shell, "installed")
    _policy(binding_fixture)
    binding = binding_fixture["binding"]
    lifecycle = binding._load_lifecycle()
    with lifecycle.legacy_lifecycle(
        binding, binding_fixture["primitive"], reserve=False, teardown=True,
        **binding_fixture["arguments"],
    ) as lease:
        environment[lifecycle.TICKET_ENV] = json.dumps(lease.ticket)
        host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
        before = _snapshot(binding_fixture["home"])
        for action in ("stamp", "install", "update", "provision", "publish-runtime-helpers"):
            command = (
                ["bash", str(installer), action] if shell == "bash" else
                [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(installer), action]
            )
            result = host.run(command) if host else subprocess.run(
                command, env=environment, cwd=binding_fixture["home"],
                capture_output=True, text=True, timeout=30,
            )
            assert result.returncode != 0, result.stdout + result.stderr
            assert "does not authorize publish" in result.stdout + result.stderr
            assert _snapshot(binding_fixture["home"]) == before
        if host:
            host.close()
            assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("shell", [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX installer")),
    pytest.param("powershell", marks=pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")),
])
def test_command_binding_deployment_native_teardown_ticket_admits_uninstall(
    binding_fixture: dict, shell: str,
) -> None:
    installer, environment = _real_installer(binding_fixture, shell, "installed")
    _policy(binding_fixture)
    binding = binding_fixture["binding"]
    lifecycle = binding._load_lifecycle()
    with lifecycle.legacy_lifecycle(
        binding, binding_fixture["primitive"], reserve=False, teardown=True,
        **binding_fixture["arguments"],
    ) as lease:
        command = (
            ["bash", str(installer), "uninstall"] if shell == "bash" else
            [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(installer), "uninstall"]
        )
        result = subprocess.run(
            command, env={**environment, lifecycle.TICKET_ENV: json.dumps(lease.ticket)},
            cwd=binding_fixture["home"], capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        smoke = json.loads((binding_fixture["legacy"] / "smoke.json").read_text())
        assert smoke["staged"] is True
        lease.assert_owned()
