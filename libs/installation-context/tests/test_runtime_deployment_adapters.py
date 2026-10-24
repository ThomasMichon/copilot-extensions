"""Native deployment diagnostics retain the Python reference decision."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from test_installation_mode_governance import (
    ALL_RUNNERS,
    _activation,
    _api_arguments,
    _cell_layout,
    _cli_arguments,
    _environment,
    _load_module,
    _runner_command,
    _write_json,
)

ADMISSION_RUNNERS = (
    *ALL_RUNNERS,
    *(("posix32",) if os.name != "nt" and os.environ.get("INSTALLATION_CONTEXT_BASH32") else ()),
)


def _run_adapter(runner: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    command = (
        [
            os.environ["INSTALLATION_CONTEXT_BASH32"],
            str(Path(__file__).resolve().parents[1] / "installation-context.sh"),
            *arguments,
        ]
        if runner == "posix32" else _runner_command(runner, arguments)
    )
    return subprocess.run(
        command, capture_output=True, text=True,
        encoding="utf-8", env=environment, check=False, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


@pytest.mark.skipif(
    os.name == "nt" or not os.environ.get("INSTALLATION_CONTEXT_BASH32"),
    reason="an actual POSIX Bash 3.2 interpreter must be selected",
)
def test_stock_bash_management_retains_modern_shell_floor(tmp_path: Path) -> None:
    result = _run_adapter("posix32", ["stamp", "--durable-home", str(tmp_path)])
    assert result.returncode == 1, result.stdout + result.stderr
    assert not result.stdout
    assert "Bash 4.4 or newer is required for installation management" in result.stderr
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize("runner", ADMISSION_RUNNERS)
@pytest.mark.parametrize("case", [
    "missing", "managed", "standalone", "unsupported", "invalid-json", "duplicate",
    "wrong-case", "string-boolean", "array-boolean", "array-version", "array-owner-field",
    "foreign-source", "foreign-root", "foreign-environment", "directory",
    "array-policy-managed", "array-policy-standalone", "nested-array-policy",
    "empty-array-policy", "multiple-array-policies", "null-policy", "whitespace-policy",
    "profile-empty", "profile-cpu", "profile-accelerator", "profile-strict",
    "profile-invalid-boolean", "profile-unknown", "profile-array", "profile-array-backend",
    "profile-wrong-case", "profile-multiline-backend", "profile-url-backend",
    "profile-empty-key", "profile-newline-key", "profile-known-and-empty-key",
    "profile-known-and-suffixed-key", "profile-known-and-prefixed-key",
    "profile-known-fallback-and-suffixed-key",
])
def test_native_policy_decision_matches_reference(
    tmp_path: Path, runner: str, case: str,
) -> None:
    module = _load_module()
    layout = _cell_layout(tmp_path)
    current = _environment()
    profile = Path(current["homeRealPath"])
    legacy = tmp_path / "runtime"
    mode_policy = tmp_path / "diagnostic-mode-policy.json"
    arguments = _api_arguments(
        layout, profile, legacy, platform=current["platform"],
        policy_path=mode_policy, environment={}, wsl_distro=current["wslDistro"],
    )
    resolution = module.resolve_installation_mode(**arguments)
    assert resolution["status"] == "ready", resolution
    policy: dict[str, Any] = {
        "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
        "owner": module._deployment_owner(resolution, arguments), "managed": True,
    }
    path = legacy / module.RUNTIME_DEPLOYMENT_FILE
    if case == "standalone":
        policy["managed"] = False
    elif case == "unsupported":
        policy["version"] = 2
    elif case == "wrong-case":
        policy["Managed"] = policy.pop("managed")
    elif case == "string-boolean":
        policy["managed"] = "false"
    elif case == "array-boolean":
        policy["managed"] = [False]
    elif case == "array-version":
        policy["version"] = [1]
    elif case == "array-owner-field":
        policy["owner"]["pluginId"] = [policy["owner"]["pluginId"]]
    elif case == "foreign-source":
        policy["owner"]["sourceFingerprint"] = "sha256:" + "0" * 64
    elif case == "foreign-root":
        policy["owner"]["runtimeRoot"] = str(tmp_path / "other")
    elif case == "foreign-environment":
        policy["owner"]["environment"]["homeRealPath"] = str(tmp_path / "other-profile")
    elif case.startswith("profile-"):
        policy["runtimeProfiles"] = {
            "profile-empty": {},
            "profile-cpu": {"backend": "cpu"},
            "profile-accelerator": {"backend": "cuda12"},
            "profile-strict": {"allowSourceFallback": False, "backend": "cpu"},
            "profile-invalid-boolean": {"allowSourceFallback": "false"},
            "profile-unknown": {"approvals": {}},
            "profile-array": [],
            "profile-array-backend": {"backend": ["cpu"]},
            "profile-wrong-case": {"Backend": "cpu"},
            "profile-multiline-backend": {"backend": "cpu\n"},
            "profile-url-backend": {"backend": "https://public.example/simple"},
            "profile-empty-key": {"": False},
            "profile-newline-key": {"\n": False},
            "profile-known-and-empty-key": {"backend": "cpu", "": False},
            "profile-known-and-suffixed-key": {"backend": "cpu", "backend\n": False},
            "profile-known-and-prefixed-key": {"backend": "cpu", "\nbackend": False},
            "profile-known-fallback-and-suffixed-key": {
                "allowSourceFallback": False, "allowSourceFallback\n": True,
            },
        }[case]
        policy["managed"] = False
    if case not in {"missing", "directory"}:
        _write_json(path, policy)
    if case == "directory":
        path.mkdir(parents=True)
    elif case == "invalid-json":
        path.write_text("{", encoding="utf-8")
    elif case == "duplicate":
        path.write_text('{"managed":true,"managed":false}', encoding="utf-8")
    elif case == "array-policy-managed":
        _write_json(path, [policy])
    elif case == "array-policy-standalone":
        _write_json(path, [{**policy, "managed": False}])
    elif case == "nested-array-policy":
        _write_json(path, [[{**policy, "managed": False}]])
    elif case == "empty-array-policy":
        _write_json(path, [])
    elif case == "multiple-array-policies":
        _write_json(path, [policy, policy])
    elif case == "null-policy":
        _write_json(path, None)
    elif case == "whitespace-policy":
        path.write_text(" \t\r\n" + json.dumps({**policy, "managed": False}), encoding="utf-8")
    reference = module.resolve_runtime_deployment(**arguments)["deployment"]
    before = path.read_bytes() if path.is_file() else None
    result = _run_adapter(
        runner,
        _cli_arguments(
            layout, legacy, action="probe-plugin-publication", policy_path=mode_policy,
        ),
    )
    expected_code = 0 if reference["allowPluginPublication"] else 3
    assert result.returncode == expected_code, result.stdout + result.stderr
    value = json.loads(result.stdout)
    actual = value["deployment"]
    for field in ("path", "state", "managed", "allowPluginPublication", "reason", "owner", "recovery"):
        assert actual[field] == reference[field], (field, actual, reference)
    assert bool(actual["diagnostic"]) == bool(reference["diagnostic"])
    assert actual.get("runtimeProfiles") == reference.get("runtimeProfiles")
    if before is not None:
        assert path.read_bytes() == before
    if case == "missing":
        assert not legacy.exists()


@pytest.mark.parametrize("runner", ADMISSION_RUNNERS)
def test_namespaced_adapter_policy_uses_governed_actual_root(tmp_path: Path, runner: str) -> None:
    module = _load_module()
    layout = _cell_layout(tmp_path)
    current = _environment()
    profile = Path(current["homeRealPath"])
    legacy = tmp_path / "legacy"
    mode_policy = tmp_path / "missing-diagnostic-policy.json"
    _activation(layout, environment=current)
    arguments = _api_arguments(
        layout, profile, legacy, platform=current["platform"], policy_path=mode_policy,
        wsl_distro=current["wslDistro"],
    )
    resolution = module.resolve_installation_mode(**arguments)
    assert resolution["status"] == "ready", resolution
    assert resolution["actualMode"] == "namespaced"
    root = Path(resolution["runtimeRoot"])
    _write_json(root / module.RUNTIME_DEPLOYMENT_FILE, {
        "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
        "owner": module._deployment_owner(resolution, arguments), "managed": True,
    })
    _write_json(legacy / module.RUNTIME_DEPLOYMENT_FILE, {"managed": False})
    result = _run_adapter(
        runner, _cli_arguments(
            layout, legacy, action="probe-plugin-publication", policy_path=mode_policy,
        ),
    )
    assert result.returncode == 3, result.stdout + result.stderr
    decision = json.loads(result.stdout)["deployment"]
    assert decision["state"] == "valid", decision
    assert decision["path"] == str(root / module.RUNTIME_DEPLOYMENT_FILE)
    assert decision["allowPluginPublication"] is False


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX permission semantics")
@pytest.mark.parametrize("runner", ADMISSION_RUNNERS)
@pytest.mark.parametrize("blocked", ["policy", "root"])
def test_inaccessible_policy_cannot_become_missing(
    tmp_path: Path, runner: str, blocked: str,
) -> None:
    if os.geteuid() == 0:
        pytest.skip("An OS superuser can read the restricted fixture")
    module = _load_module()
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "runtime"
    path = legacy / module.RUNTIME_DEPLOYMENT_FILE
    _write_json(path, {"schema": module.RUNTIME_DEPLOYMENT_SCHEMA})
    denied = path if blocked == "policy" else legacy
    original = denied.stat().st_mode
    try:
        denied.chmod(0)
        result = _run_adapter(
            runner, _cli_arguments(
                layout, legacy, action="probe-plugin-publication",
                policy_path=tmp_path / "missing-diagnostic-policy.json",
            ),
        )
        if blocked == "root" and result.returncode == 1:
            # Mode governance can refuse the inaccessible tombstone before a
            # complete deployment diagnostic can be constructed.
            assert not result.stdout
            assert result.stderr
            assert "Traceback" not in result.stderr
            return
        assert result.returncode == 3, result.stdout + result.stderr
        decision = json.loads(result.stdout)["deployment"]
        assert decision["state"] == "invalid", decision
        assert decision["allowPluginPublication"] is False
        assert decision["diagnostic"]
    finally:
        denied.chmod(original)
