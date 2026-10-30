"""Installation-owned plugin-publication policy, independent of mode and source selection."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from test_installation_mode_governance import (
    _activation,
    _api_arguments,
    _cell_layout,
    _cli_arguments,
    _load_module,
    _policy,
    _profile_environment,
    _write_json,
)


@pytest.fixture
def deployment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, dict[str, Any], Path, dict[str, Any]]:
    module = _load_module()
    # This fixture describes a standalone host, not the runner's WSL account.
    monkeypatch.setattr(module, "_is_wsl_guest", lambda: False)
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    layout = _cell_layout(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    legacy = profile / ".agent-example"
    arguments = _api_arguments(layout, profile, legacy)
    arguments["platform"] = _profile_environment(profile)["platform"]
    arguments["wsl_distro"] = _profile_environment(profile)["wslDistro"]
    resolution = module.resolve_installation_mode(**arguments)
    assert resolution["status"] == "ready"
    owner = module._deployment_owner(resolution, arguments)
    policy = {
        "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
        "owner": owner, "managed": True,
    }
    return module, arguments, legacy / module.RUNTIME_DEPLOYMENT_FILE, policy


@pytest.mark.installation_context_smoke
def test_missing_policy_preserves_standalone_without_writes(deployment: Any) -> None:
    module, arguments, path, _ = deployment
    before = module.resolve_installation_mode(**arguments)
    result = module.resolve_runtime_deployment(**arguments)
    assert {key: value for key, value in result.items() if key != "deployment"} == before
    assert result["deployment"] == {
        "path": str(path), "state": "missing", "managed": False,
        "allowPluginPublication": True, "reason": "deployment-default-standalone",
        "owner": None, "diagnostic": None, "recovery": None,
    }
    assert not path.parent.exists()


@pytest.mark.parametrize("managed", [True, False])
@pytest.mark.installation_context_smoke
def test_exact_owner_policy_does_not_change_mode_or_select_a_runtime(
    deployment: Any, managed: bool,
) -> None:
    module, arguments, path, policy = deployment
    policy["managed"] = managed
    _write_json(path, policy)
    before = path.read_bytes()
    result = module.resolve_runtime_deployment(**arguments)
    decision = result.pop("deployment")
    assert result == module.resolve_installation_mode(**arguments)
    assert decision["state"] == "valid"
    assert decision["managed"] is managed
    assert decision["allowPluginPublication"] is not managed
    assert decision["owner"] == policy["owner"]
    assert bool(decision["recovery"]) is managed
    assert path.read_bytes() == before
    assert sorted(entry.name for entry in path.parent.iterdir()) == [path.name]


@pytest.mark.parametrize("field,value", [
    ("sourceFingerprint", "sha256:" + "0" * 64),
    ("pluginId", "agent-other"),
    ("mode", "namespaced"),
    ("runtimeRoot", "/another/installation"),
    ("environment", {"platform": "posix", "homeRealPath": "/another/user", "wslDistro": None}),
])
def test_foreign_owner_cannot_disable_or_enable_policy(
    deployment: Any, field: str, value: Any,
) -> None:
    module, arguments, path, policy = deployment
    policy["owner"][field] = value
    policy["managed"] = False
    _write_json(path, policy)
    decision = module.resolve_runtime_deployment(**arguments)["deployment"]
    assert decision["state"] == "invalid"
    assert decision["allowPluginPublication"] is False
    assert "another installation" in decision["diagnostic"]


@pytest.mark.parametrize("change", [
    {"schema": "other"},
    {"version": True},
    {"version": 0},
    {"version": "1"},
    {"managed": None},
    {"managed": 0},
    {"Managed": True},
    {"surprise": True},
    {"owner": None},
])
def test_malformed_policy_fails_closed(deployment: Any, change: dict[str, Any]) -> None:
    module, arguments, path, policy = deployment
    _write_json(path, {**policy, **change})
    decision = module.resolve_runtime_deployment(**arguments)["deployment"]
    assert decision["state"] == "invalid"
    assert decision["allowPluginPublication"] is False
    assert decision["managed"] is None
    assert decision["diagnostic"]
    assert decision["recovery"]


def test_future_policy_is_not_standalone(deployment: Any) -> None:
    module, arguments, path, policy = deployment
    policy["version"] = 2
    _write_json(path, policy)
    decision = module.resolve_runtime_deployment(**arguments)["deployment"]
    assert decision["state"] == "unsupported"
    assert decision["allowPluginPublication"] is False
    assert decision["diagnostic"]


@pytest.mark.parametrize("text", ['{"managed":true,"managed":false}', "{", "[]", "\ufeff{}"])
def test_invalid_json_is_not_standalone(deployment: Any, text: str) -> None:
    module, arguments, path, _ = deployment
    path.parent.mkdir()
    path.write_text(text, encoding="utf-8")
    decision = module.resolve_runtime_deployment(**arguments)["deployment"]
    assert decision["state"] == "invalid"
    assert decision["allowPluginPublication"] is False


def test_policy_directory_is_not_standalone(deployment: Any) -> None:
    module, arguments, path, _ = deployment
    path.mkdir(parents=True)
    decision = module.resolve_runtime_deployment(**arguments)["deployment"]
    assert decision["state"] == "invalid"
    assert decision["allowPluginPublication"] is False


def test_unresolved_source_cannot_bypass_existing_policy(deployment: Any) -> None:
    module, arguments, path, policy = deployment
    _write_json(path, policy)
    arguments = {**arguments, "source_descriptor": None}
    result = module.resolve_runtime_deployment(**arguments)
    assert result["status"] == "provenance-blocked"
    assert result["deployment"]["allowPluginPublication"] is False
    assert result["deployment"]["diagnostic"]


def test_unattributed_missing_policy_preserves_standalone_default(deployment: Any) -> None:
    module, arguments, path, _ = deployment
    result = module.resolve_runtime_deployment(**{**arguments, "source_descriptor": None})
    assert result["status"] == "provenance-blocked"
    assert result["deployment"]["state"] == "missing"
    assert result["deployment"]["allowPluginPublication"] is True
    assert not path.parent.exists()


def test_wrong_case_owner_field_fails_closed(deployment: Any) -> None:
    module, arguments, path, policy = deployment
    policy["owner"]["PluginId"] = policy["owner"].pop("pluginId")
    _write_json(path, policy)
    decision = module.resolve_runtime_deployment(**arguments)["deployment"]
    assert decision["state"] == "invalid"
    assert decision["allowPluginPublication"] is False


def test_policy_symlink_is_not_followed(deployment: Any, tmp_path: Path) -> None:
    module, arguments, path, policy = deployment
    target = tmp_path / "external-policy.json"
    _write_json(target, {**policy, "managed": False})
    path.parent.mkdir()
    try:
        path.symlink_to(target)
    except OSError as error:
        pytest.skip(f"Symlinks are unavailable: {error}")
    decision = module.resolve_runtime_deployment(**arguments)["deployment"]
    assert decision["state"] == "invalid"
    assert decision["allowPluginPublication"] is False
    assert decision["diagnostic"]


def test_no_environment_hint_grants_publication(deployment: Any) -> None:
    module, arguments, path, policy = deployment
    _write_json(path, policy)
    arguments["environment"] = {
        "COPILOT_EXTENSIONS_MANAGED_DEPLOYMENT": "0",
        "COPILOT_EXTENSIONS_DEPLOYMENT_AUTHORIZED": "1",
    }
    assert module.resolve_runtime_deployment(**arguments)["deployment"]["allowPluginPublication"] is False


def test_namespaced_policy_uses_actual_root_not_legacy_root(deployment: Any, tmp_path: Path) -> None:
    module, arguments, legacy_policy, policy = deployment
    layout = _cell_layout(tmp_path / "namespaced")
    profile = arguments["os_profile"]
    _activation(layout, environment=_profile_environment(profile))
    _write_json(
        profile / ".copilot-extensions" / "installation-mode.json",
        _policy(True, layout["marketplace_id"]),
    )
    arguments = _api_arguments(layout, profile, arguments["legacy_root"])
    arguments["platform"] = _profile_environment(profile)["platform"]
    arguments["wsl_distro"] = _profile_environment(profile)["wslDistro"]
    resolution = module.resolve_installation_mode(**arguments)
    assert resolution["status"] == "ready", resolution
    assert resolution["actualMode"] == "namespaced"
    policy = copy.deepcopy(policy)
    policy["owner"] = module._deployment_owner(resolution, arguments)
    selected = Path(resolution["runtimeRoot"]) / module.RUNTIME_DEPLOYMENT_FILE
    _write_json(selected, policy)
    _write_json(legacy_policy, {"managed": False})
    decision = module.resolve_runtime_deployment(**arguments)["deployment"]
    assert decision["path"] == str(selected)
    assert decision["state"] == "valid"
    assert decision["allowPluginPublication"] is False


@pytest.mark.parametrize("action", ["deployment-status", "probe-plugin-publication"])
@pytest.mark.parametrize("managed", [True, False])
def test_cli_reports_decision_and_probe_exit_status(
    deployment: Any, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], tmp_path: Path, action: str, managed: bool,
) -> None:
    module, arguments, path, policy = deployment
    layout = _cell_layout(tmp_path / "cli")
    for key in ("payload_root", "durable_home", "source_descriptor", "marketplace_key"):
        arguments[key] = _api_arguments(layout, arguments["os_profile"], arguments["legacy_root"])[key]
    policy["owner"] = module._deployment_owner(
        module.resolve_installation_mode(**arguments), arguments,
    )
    policy["managed"] = managed
    _write_json(path, policy)
    current_environment = module._current_environment

    def fixture_environment(**values: Any) -> Any:
        return current_environment(
            **{
                **values, "os_profile": arguments["os_profile"],
                "platform": arguments["platform"], "wsl_distro": arguments["wsl_distro"],
            },
        )

    monkeypatch.setattr(module, "_current_environment", fixture_environment)
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    code = module.main(_cli_arguments(layout, arguments["legacy_root"], action=action))
    expected = 3 if action == "probe-plugin-publication" and managed else 0
    assert code == expected
    output = capsys.readouterr()
    assert not output.err
    result = json.loads(output.out)
    assert result["deployment"]["state"] == "valid"
    assert result["deployment"]["allowPluginPublication"] is not managed
