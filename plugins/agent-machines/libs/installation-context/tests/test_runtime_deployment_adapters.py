"""Native deployment diagnostics retain the Python reference decision."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
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

DOWNLOAD_PROFILES = {
    "profile-download-empty": {"downloadOrigins": []},
    "profile-download-origins": {"downloadOrigins": [
        "https://other.example:8443", "https://FILES.example:443/",
    ]},
    "profile-download-ipv6": {"downloadOrigins": ["https://[2001:0DB8:0000::1]:443/"]},
    "profile-download-ipv6-boundaries": {"downloadOrigins": [
        "https://[::]", "https://[::1]", "https://[1::]",
    ]},
    "profile-download-ipv6-trailing-group": {"downloadOrigins": ["https://[1:2:3:4:5:6:7:8:]"]},
    "profile-download-ipv6-leading-group": {"downloadOrigins": ["https://[:1::2]"]},
    "profile-download-duplicate": {"downloadOrigins": ["https://files.example", "https://FILES.example:443/"]},
    "profile-download-string": {"downloadOrigins": "https://files.example"},
    "profile-download-userinfo": {"downloadOrigins": ["https://user:password@files.example"]},
    "profile-download-insecure": {"downloadOrigins": ["http://files.example"]},
    "profile-download-path": {"downloadOrigins": ["https://files.example/path"]},
    "profile-download-wildcard": {"downloadOrigins": ["https://*.example"]},
    "profile-download-query": {"downloadOrigins": ["https://files.example?hidden=credential"]},
    "profile-download-fragment": {"downloadOrigins": ["https://files.example#fragment"]},
    "profile-download-port-zero": {"downloadOrigins": ["https://files.example:0"]},
    "profile-download-port-overflow": {"downloadOrigins": ["https://files.example:65536"]},
    "profile-download-unicode": {"downloadOrigins": ["https://\u2603.example"]},
    "profile-download-count": {"downloadOrigins": ["https://files.example"] * 257},
    "profile-download-long": {"downloadOrigins": ["https://" + "a" * 2048]},
    "profile-download-underscore": {"downloadOrigins": ["https://files_example"]},
    "profile-download-ipv6-invalid": {"downloadOrigins": ["https://[2001:::1]"]},
    "profile-download-port-alias": {"downloadOrigins": ["https://files.example:00443/"]},
}


def _run_adapter(
    runner: str, arguments: list[str], *, trace_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
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
    if (
        runner == "powershell" and trace_root is not None
        and environment.get("INSTALLATION_CONTEXT_POLICY_TRACE") == "1"
    ):
        script_index = command.index("-File") + 1
        argument_file = trace_root / "native-policy-arguments.json"
        argument_file.write_text(json.dumps(command[script_index + 1:]), encoding="utf-8")
        command = [
            *command[:script_index],
            str(Path(__file__).with_name("native_policy_trace.ps1")),
            "-Adapter", command[script_index], "-ArgumentsFile", str(argument_file),
        ]
    started = datetime.now(timezone.utc).isoformat()
    try:
        result = subprocess.run(
            command, capture_output=True, text=True,
            encoding="utf-8", env=environment, check=False, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr or b""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        exc.__notes__ = [
            *getattr(exc, "__notes__", []),
            f"Native policy child started at {started}; phase evidence:\n{stderr}",
        ]
        raise
    if runner == "powershell" and environment.get("INSTALLATION_CONTEXT_POLICY_TRACE") == "1":
        print(f"Native policy child started at {started}; phase evidence:\n{result.stderr}")
    return result


def test_native_timeout_retains_phase_evidence_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    failure = subprocess.TimeoutExpired(
        ["native-reader"], 30, stderr=b"native-policy-phase=Add-Type;elapsed_ms=50\n",
    )
    monkeypatch.setattr(failure, "add_note", None, raising=False)
    failure.__notes__ = ["existing diagnostic"]

    def timeout(command: list[str], **kwargs: Any) -> None:
        calls.append((command, kwargs))
        raise failure

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired) as captured:
        _run_adapter("python", ["probe-plugin-publication"])
    assert captured.value is failure
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 30
    assert "native-policy-phase=Add-Type" in "\n".join(failure.__notes__)
    assert failure.__notes__[0] == "existing diagnostic"
    assert "Native policy child started at " in failure.__notes__[-1]


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
    *DOWNLOAD_PROFILES,
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
            **DOWNLOAD_PROFILES,
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
        trace_root=tmp_path,
    )
    expected_code = 0 if reference["allowPluginPublication"] else 3
    assert result.returncode == expected_code, result.stdout + result.stderr
    if runner == "powershell" and os.environ.get("INSTALLATION_CONTEXT_POLICY_TRACE") == "1":
        for stage in ("process-entry", "Add-Type", "Resolve-InstallationStatus"):
            assert f"native-policy-phase={stage};" in result.stderr, result.stderr
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
