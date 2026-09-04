from __future__ import annotations

import copy
from pathlib import Path

import pytest

from agent_machines.authority import (
    AUTHORITY_MAX,
    AUTHORITY_MIN,
    AUTHORITY_MODE_OPAQUE_ADDITIVE,
    effective_authority,
)
from agent_machines.manifest import ManifestError, load_package, resolve_for_machine
from agent_machines.reconcile import (
    RestoreResult,
    RestoreValidationError,
    manifest_hash,
    plan,
    plan_to_dict,
    restore,
    restore_result_to_dict,
)
from agent_machines.resources import resolve_resources
from agent_machines.surfaces import apply_surfaces
from agent_machines.validator import has_errors, validate

from ._helpers import write_package


def _data(
    name: str,
    *,
    authority: int | None = None,
    manage: dict | None = None,
    resources: list[dict] | None = None,
    modules: list[dict] | None = None,
    schema_version: int = 4,
) -> dict:
    data = {
        "schema_version": schema_version,
        "package": name,
        "gate": ["*"],
        "manage": manage or {},
        "resources": resources or [],
        "modules": modules or [],
    }
    if authority is not None:
        data["authority"] = authority
    return data


def _pkg(tmp_path: Path, repo: str, data: dict):
    return load_package(
        write_package(tmp_path / repo, "package.yaml", data),
        source_repo=repo,
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update(authority=1),
        lambda data: data["manage"].update(
            {"copilot.settings": {
                "disposition": "enforce",
                "authority": 1,
                "values": {"model": "x"},
            }}
        ),
        lambda data: data["resources"].append(
            {"type": "package", "manager": "winget", "id": "x", "authority": 1}
        ),
        lambda data: data["modules"].append(
            {
                "name": "x",
                "authority": 1,
                "windows": {"command": ["example"]},
            }
        ),
    ],
)
def test_schema_v3_rejects_authority_everywhere(tmp_path, mutate):
    data = _data("example/a", schema_version=3)
    mutate(data)
    with pytest.raises(ManifestError, match="schema_version 4"):
        _pkg(tmp_path, "a", data)


def test_schema_v3_rejects_per_machine_authority_removal(tmp_path):
    data = _data("example/a", schema_version=3)
    data["per-machine"] = {
        "box": {
            "manage": {
                "copilot.settings": {"authority": None}
            }
        }
    }
    with pytest.raises(ManifestError, match="schema_version 4"):
        _pkg(tmp_path, "a", data)


@pytest.mark.parametrize("value", [True, False, 1.5, "1", -1001, 1001])
@pytest.mark.parametrize("location", ["package", "manage", "resource", "module"])
def test_authority_values_are_strict_and_bounded(tmp_path, value, location):
    data = _data("example/a")
    if location == "package":
        data["authority"] = value
    elif location == "manage":
        data["manage"] = {
            "copilot.settings": {
                "disposition": "enforce",
                "authority": value,
                "values": {"model": "x"},
            }
        }
    elif location == "resource":
        data["resources"] = [
            {"type": "package", "manager": "winget", "id": "x", "authority": value}
        ]
    else:
        data["modules"] = [
            {
                "name": "x",
                "authority": value,
                "windows": {"command": ["example"]},
            }
        ]
    with pytest.raises(ManifestError, match="must be an integer"):
        _pkg(tmp_path, "a", data)


@pytest.mark.parametrize("value", [AUTHORITY_MIN, 0, AUTHORITY_MAX])
def test_authority_accepts_documented_range(tmp_path, value):
    package = _pkg(tmp_path, "a", _data("example/a", authority=value))
    assert package.authority == value


@pytest.mark.parametrize(
    "key,spec",
    [
        (
            "copilot.settings.plugin-activation",
            {
                "disposition": "ensure-absent",
                "keys": {"enabledPlugins": ["optional@example"]},
            },
        ),
        (
            "copilot.settings.plugin-tombstones",
            {
                "disposition": "enforce",
                "values": {"enabledPlugins": {"optional@example": False}},
            },
        ),
        (
            "copilot.settings.plugin-activation",
            {"disposition": "ignore"},
        ),
        (
            "copilot.settings.plugins",
            {
                "disposition": "ensure-present",
                "values": {"enabledPlugins": {"optional@example": True}},
            },
        ),
        (
            "copilot.settings.marketplaces",
            {
                "disposition": "ensure-present",
                "values": {"nested": {"extraKnownMarketplaces": {"example": {}}}},
            },
        ),
    ],
)
@pytest.mark.parametrize("authority_location", ["package", "declaration"])
def test_authority_forbidden_on_activation_and_removal_surfaces(
    tmp_path, key, spec, authority_location
):
    spec = copy.deepcopy(spec)
    data = _data("example/a", manage={key: spec})
    if authority_location == "package":
        data["authority"] = 1
    else:
        spec["authority"] = 1
    with pytest.raises(ManifestError, match="authority is not allowed"):
        _pkg(tmp_path, "a", data)


def test_plugin_activation_without_authority_remains_valid(tmp_path):
    data = _data(
        "example/a",
        schema_version=3,
        manage={
            "copilot.settings.plugin-activation": {
                "disposition": "ensure-absent",
                "keys": {"enabledPlugins": ["optional@example"]},
            }
        },
    )
    assert _pkg(tmp_path, "a", data).schema_version == 3


def test_package_declaration_and_machine_authority_resolution(tmp_path):
    data = _data(
        "example/a",
        authority=5,
        manage={
            "copilot.settings": {
                "disposition": "enforce",
                "authority": 7,
                "values": {"model": "base"},
            },
            "copilot.settings.other": {
                "disposition": "enforce",
                "values": {"effortLevel": "high"},
            },
        },
    )
    data["per-machine"] = {
        "override": {
            "manage": {"copilot.settings": {"authority": 9}}
        },
        "fallback": {
            "manage": {"copilot.settings": {"authority": None}}
        },
    }
    package = _pkg(tmp_path, "a", data)

    base = resolve_for_machine(package, "base")
    override = resolve_for_machine(package, "override")
    fallback = resolve_for_machine(package, "fallback")

    assert effective_authority(base, base.manage["copilot.settings"]) == 7
    assert effective_authority(
        base, base.manage["copilot.settings.other"]
    ) == 5
    assert effective_authority(
        override, override.manage["copilot.settings"]
    ) == 9
    assert effective_authority(
        fallback, fallback.manage["copilot.settings"]
    ) == 5
    assert override.authority == fallback.authority == 5
    assert override.resources == fallback.resources == []
    assert override.modules == fallback.modules == []


def test_per_machine_package_authority_resources_and_modules_are_not_introduced(
    tmp_path,
):
    for field, value in (
        ("authority", 1),
        ("resources", []),
        ("modules", []),
    ):
        data = _data("example/a")
        data["per-machine"] = {
            "box": {"manage": {}, field: value}
        }
        with pytest.raises(ManifestError, match="manage overlays only"):
            _pkg(tmp_path, field, data)


def _settings_package(
    tmp_path: Path,
    repo: str,
    name: str,
    authority: int,
    values: dict,
    *,
    disposition: str = "enforce",
):
    return _pkg(
        tmp_path,
        repo,
        _data(
            name,
            authority=authority,
            manage={
                f"copilot.settings.{repo}": {
                    "disposition": disposition,
                    "values": values,
                }
            },
        ),
    )


def test_settings_authority_is_deterministic_and_preserves_nonoverlap(tmp_path):
    low = _settings_package(
        tmp_path, "z-repo", "example/low", -10,
        {"model": "low", "theme": "retained"},
    )
    high = _settings_package(
        tmp_path, "a-repo", "example/high", 10,
        {"model": "high"},
    )
    first_home = tmp_path / "home-a"
    second_home = tmp_path / "home-b"

    apply_surfaces([low, high], home=first_home, dry_run=False)
    apply_surfaces([high, low], home=second_home, dry_run=False)

    expected = {"model": "high", "theme": "retained"}
    import json
    assert json.loads((first_home / "settings.json").read_text()) == expected
    assert json.loads((second_home / "settings.json").read_text()) == expected
    findings = validate([low, high])
    assert not has_errors(findings)
    assert any(f.code == "authority-supersession" for f in findings)


def test_settings_equal_highest_conflict_remains_error(tmp_path):
    a = _settings_package(tmp_path, "a", "example/a", 5, {"model": "a"})
    b = _settings_package(tmp_path, "b", "example/b", 5, {"model": "b"})
    assert any(f.code == "enforce-conflict" for f in validate([a, b]))


def test_settings_shape_conflict_ignores_authority(tmp_path):
    low = _settings_package(
        tmp_path, "low", "example/low", 0, {"sandbox": False}
    )
    high = _settings_package(
        tmp_path, "high", "example/high", 20,
        {"sandbox": {"enabled": True}},
    )
    findings = validate([low, high])
    assert has_errors(findings)
    assert any(f.code == "enforce-shape-conflict" for f in findings)


def test_parent_shape_conflict_does_not_hide_descendant_conflicts(tmp_path):
    low_a = _settings_package(
        tmp_path,
        "low-a",
        "example/low-a",
        0,
        {"sandbox": {"enabled": False}},
    )
    low_b = _settings_package(
        tmp_path,
        "low-b",
        "example/low-b",
        0,
        {"sandbox": {"enabled": True}},
    )
    high = _settings_package(
        tmp_path,
        "high",
        "example/high",
        10,
        {"sandbox": False},
    )

    findings = validate([low_a, low_b, high])

    assert has_errors(findings)
    assert any(
        finding.code == "enforce-shape-conflict"
        and "'copilot.settings.sandbox'" in finding.message
        for finding in findings
    )
    assert any(
        finding.code == "enforce-conflict"
        and "sandbox.enabled" in finding.message
        for finding in findings
    )


def test_authority_does_not_cross_settings_dispositions(tmp_path):
    floor = _settings_package(
        tmp_path,
        "floor",
        "example/floor",
        100,
        {"model": "floor"},
        disposition="ensure-present",
    )
    enforce = _settings_package(
        tmp_path,
        "enforce",
        "example/enforce",
        -100,
        {"model": "enforced"},
    )
    home = tmp_path / "home"
    apply_surfaces([floor, enforce], home=home, dry_run=False)
    import json
    assert json.loads((home / "settings.json").read_text())["model"] == "enforced"


def test_authority_does_not_reorder_ensure_present_settings(tmp_path):
    first = _settings_package(
        tmp_path,
        "a-repo",
        "example/first",
        -100,
        {"model": "first"},
        disposition="ensure-present",
    )
    second = _settings_package(
        tmp_path,
        "z-repo",
        "example/second",
        100,
        {"model": "second"},
        disposition="ensure-present",
    )
    home = tmp_path / "home"

    apply_surfaces([second, first], home=home, dry_run=False)

    import json
    assert json.loads((home / "settings.json").read_text())["model"] == "first"
    assert not any(
        decision["domain"] == "settings"
        for decision in plan([first, second], "box-1").authority_decisions
    )


def test_direct_restore_refuses_settings_conflict_before_mutation(tmp_path):
    first = _settings_package(
        tmp_path, "a", "example/a", 0, {"model": "first"}
    )
    second = _settings_package(
        tmp_path, "b", "example/b", 0, {"model": "second"}
    )

    with pytest.raises(RestoreValidationError, match="enforce-conflict"):
        restore(
            [first, second],
            "box-1",
            dry_run=False,
            plat="windows",
            home=tmp_path / "home",
        )

    assert not (tmp_path / "home" / "settings.json").exists()


def _resource_package(
    tmp_path: Path,
    repo: str,
    authority: int,
    resource: dict,
):
    return _pkg(
        tmp_path,
        repo,
        _data(f"example/{repo}", authority=authority, resources=[resource]),
    )


RESOURCE_CASES = [
    (
        {"type": "package", "manager": "winget", "id": "tool", "state": "absent"},
        {"type": "package", "manager": "winget", "id": "tool", "state": "present"},
        lambda desired: desired["state"],
        "present",
    ),
    (
        {"type": "package", "manager": "winget", "id": "tool", "version": "1"},
        {"type": "package", "manager": "winget", "id": "tool", "version": "2"},
        lambda desired: desired["version"],
        "2",
    ),
    (
        {"type": "file", "path": "$HOME/a", "format": "text", "content": "{}"},
        {"type": "file", "path": "$HOME/a", "format": "json", "content": "{}"},
        lambda desired: desired["format"],
        "json",
    ),
    (
        {"type": "file", "path": "$HOME/a", "strategy": "enforce", "content": "low"},
        {"type": "file", "path": "$HOME/a", "strategy": "enforce", "content": "high"},
        lambda desired: desired["content"],
        "high",
    ),
    (
        {
            "type": "file", "path": "$HOME/a", "strategy": "managed-block",
            "block": "x", "state": "absent", "content": "same",
        },
        {
            "type": "file", "path": "$HOME/a", "strategy": "managed-block",
            "block": "x", "state": "present", "content": "same",
        },
        lambda desired: desired["state"],
        "present",
    ),
    (
        {
            "type": "file", "path": "$HOME/a", "strategy": "managed-block",
            "block": "x", "content": "low",
        },
        {
            "type": "file", "path": "$HOME/a", "strategy": "managed-block",
            "block": "x", "content": "high",
        },
        lambda desired: desired["content"],
        "high",
    ),
    (
        {"type": "registry", "path": "HKCU\\Software\\Example", "state": "absent"},
        {"type": "registry", "path": "HKCU\\Software\\Example", "state": "present"},
        lambda desired: desired["state"],
        "present",
    ),
    (
        {"type": "registry", "path": "HKCU\\Software\\Example", "value": "low"},
        {"type": "registry", "path": "HKCU\\Software\\Example", "value": "high"},
        lambda desired: desired["value"],
        "high",
    ),
    (
        {
            "type": "registry", "path": "HKCU\\Software\\Example",
            "value": "1", "value_type": "String",
        },
        {
            "type": "registry", "path": "HKCU\\Software\\Example",
            "value": "1", "value_type": "DWord",
        },
        lambda desired: desired["value_type"],
        "DWord",
    ),
    (
        {
            "type": "feature", "manager": "windows-optional-feature",
            "id": "Example", "state": "absent",
        },
        {
            "type": "feature", "manager": "windows-optional-feature",
            "id": "Example", "state": "present",
        },
        lambda desired: desired["state"],
        "present",
    ),
    (
        {
            "type": "power-setting", "subgroup": "SUB_BUTTONS",
            "setting": "LIDACTION", "ac": "sleep",
        },
        {
            "type": "power-setting", "subgroup": "SUB_BUTTONS",
            "setting": "LIDACTION", "ac": "do-nothing",
        },
        lambda desired: desired["ac"],
        0,
    ),
    (
        {
            "type": "power-setting", "subgroup": "SUB_BUTTONS",
            "setting": "LIDACTION", "dc": "sleep",
        },
        {
            "type": "power-setting", "subgroup": "SUB_BUTTONS",
            "setting": "LIDACTION", "dc": "do-nothing",
        },
        lambda desired: desired["dc"],
        0,
    ),
]


def test_direct_restore_refuses_resource_conflict_before_mutation(tmp_path):
    first = _resource_package(
        tmp_path,
        "a",
        0,
        {"type": "file", "path": "$HOME/conf", "content": "first"},
    )
    second = _resource_package(
        tmp_path,
        "b",
        0,
        {"type": "file", "path": "$HOME/conf", "content": "second"},
    )

    with pytest.raises(RestoreValidationError, match="resource-conflict"):
        restore(
            [first, second],
            "box-1",
            dry_run=False,
            plat="windows",
            home=tmp_path / "home",
        )

    assert not (tmp_path / "home" / "conf").exists()


@pytest.mark.parametrize("low_resource,high_resource,read_value,expected", RESOURCE_CASES)
def test_resource_fields_select_highest_authority_and_equal_highest_conflicts(
    tmp_path, low_resource, high_resource, read_value, expected
):
    low = _resource_package(tmp_path, "low", 0, low_resource)
    high = _resource_package(tmp_path, "high", 10, high_resource)
    resolved, findings = resolve_resources([high, low], "box", "windows")
    assert read_value(resolved[0].desired) == expected
    assert not any(f.level == "error" for f in findings)
    assert any(f.code == "authority-supersession" for f in findings)
    assert resolved[0].authority_decisions
    assert resolved[0].contributor_details == [
        {"package": "example/high", "source_repo": "high", "authority": 10},
        {"package": "example/low", "source_repo": "low", "authority": 0},
    ]

    equal = _resource_package(tmp_path, "equal", 10, low_resource)
    _, equal_findings = resolve_resources([equal, high], "box", "windows")
    assert any(
        f.level == "error" and f.code == "resource-conflict"
        for f in equal_findings
    )


def test_resource_compatible_safety_fields_union_across_authority(tmp_path):
    low = _resource_package(
        tmp_path,
        "low",
        0,
        {
            "type": "package", "manager": "winget", "id": "tool",
            "state": "absent", "pin": True,
            "process_guard": {"names": ["LOW.EXE"]},
        },
    )
    high = _resource_package(
        tmp_path,
        "high",
        10,
        {
            "type": "package", "manager": "winget", "id": "tool",
            "state": "present",
            "process_guard": {"names": ["high.exe"]},
        },
    )
    resolved, findings = resolve_resources([low, high], "box", "windows")
    desired = resolved[0].desired
    assert desired["state"] == "present"
    assert desired["pin"] is True
    assert desired["process_guard"] == {"names": ["high.exe", "low.exe"]}
    assert not any(f.level == "error" for f in findings)


def test_managed_block_marker_conflict_ignores_authority(tmp_path):
    low = _resource_package(
        tmp_path,
        "low",
        0,
        {
            "type": "file", "path": "$HOME/a", "strategy": "managed-block",
            "block": "x", "begin": "low-b", "end": "low-e", "content": "same",
        },
    )
    high = _resource_package(
        tmp_path,
        "high",
        10,
        {
            "type": "file", "path": "$HOME/a", "strategy": "managed-block",
            "block": "x", "begin": "high-b", "end": "high-e", "content": "same",
        },
    )

    resolved, findings = resolve_resources([low, high], "box", "windows")

    assert any(
        finding.level == "error" and finding.code == "resource-conflict"
        for finding in findings
    )
    assert not any(
        decision["identity"]["field"] == "markers"
        for decision in resolved[0].authority_decisions
    )


def test_file_format_and_content_resolve_with_winning_strategy(tmp_path):
    enforce = _resource_package(
        tmp_path,
        "enforce",
        0,
        {
            "type": "file",
            "path": "$HOME/a",
            "strategy": "enforce",
            "format": "text",
            "content": "plain text",
        },
    )
    floor = _resource_package(
        tmp_path,
        "floor",
        10,
        {
            "type": "file",
            "path": "$HOME/a",
            "strategy": "ensure-present",
            "format": "json",
            "content": "{}",
        },
    )

    resolved, findings = resolve_resources([floor, enforce], "box", "windows")

    assert resolved[0].desired == {
        "path": "$HOME/a",
        "format": "text",
        "strategy": "enforce",
        "content": "plain text",
    }
    assert not any(finding.level == "error" for finding in findings)


def test_invalid_json_file_content_is_an_error_result(tmp_path):
    package = _resource_package(
        tmp_path,
        "invalid",
        0,
        {
            "type": "file",
            "path": "$HOME/a.json",
            "strategy": "enforce",
            "format": "json",
            "content": "not json",
        },
    )

    result = restore(
        [package],
        "box",
        dry_run=True,
        plat="windows",
        home=tmp_path,
    )

    assert result.ok is False
    assert result.resource_results[0].status == "error"
    assert "content is not valid JSON" in result.resource_results[0].detail


def test_whole_file_and_managed_block_conflict_ignores_authority(tmp_path):
    whole = _resource_package(
        tmp_path,
        "whole",
        100,
        {"type": "file", "path": "$HOME/a", "content": "whole"},
    )
    block = _resource_package(
        tmp_path,
        "block",
        -100,
        {
            "type": "file", "path": "$HOME/a", "strategy": "managed-block",
            "block": "x", "content": "block",
        },
    )
    _, findings = resolve_resources([whole, block], "box", "windows")
    assert any(
        f.level == "error" and "whole-file and managed-block" in f.message
        for f in findings
    )


def test_modules_remain_same_name_additive_and_opaque(tmp_path):
    module = {"name": "same", "windows": {"command": ["example"]}}
    low = _pkg(
        tmp_path, "low", _data("example/low", authority=0, modules=[module])
    )
    high_module = copy.deepcopy(module)
    high_module["authority"] = 20
    high = _pkg(
        tmp_path, "high", _data("example/high", authority=10, modules=[high_module])
    )
    output = plan([low, high], "box", plat="windows")
    assert len(output.modules) == 2
    assert [item["name"] for item in output.modules] == ["same", "same"]
    assert [item["authority"] for item in output.modules] == [20, 0]
    assert {
        item["authority_mode"] for item in output.modules
    } == {AUTHORITY_MODE_OPAQUE_ADDITIVE}


def test_nested_module_authority_payload_changes_drift_key(tmp_path):
    def package(value: str):
        return _pkg(
            tmp_path,
            value,
            _data(
                f"example/{value}",
                modules=[
                    {
                        "name": "opaque",
                        "authority": 10,
                        "windows": {
                            "command": [
                                "example",
                                {"authority": value},
                            ]
                        },
                    }
                ],
            ),
        )

    first = package("first")
    second = package("second")
    second.name = first.name
    second.source_repo = first.source_repo

    assert plan([first], "box", plat="windows").drift_key != plan(
        [second], "box", plat="windows"
    ).drift_key


def test_plan_restore_provenance_is_stable_and_hashes_are_distinct(tmp_path):
    low = _settings_package(tmp_path, "z", "example/low", 0, {"model": "low"})
    high = _settings_package(tmp_path, "a", "example/high", 10, {"model": "high"})

    first = plan([low, high], "box", plat="windows")
    second = plan([high, low], "box", plat="windows")
    assert plan_to_dict(first) == plan_to_dict(second)
    assert first.authority_decisions == sorted(
        first.authority_decisions,
        key=lambda item: (item["domain"], str(item["identity"])),
    )
    assert first.package_authorities == [
        {"package": "example/high", "source_repo": "a", "authority": 10},
        {"package": "example/low", "source_repo": "z", "authority": 0},
    ]
    restored = restore_result_to_dict(RestoreResult(plan=first))
    assert restored["authority_decisions"] == first.authority_decisions
    assert restored["plan"]["authority_decisions"] == first.authority_decisions
    assert first.provenance_hash == manifest_hash([high, low])

    changed_low = _settings_package(
        tmp_path, "z-changed", "example/low", 0, {"model": "different-low"}
    )
    changed_low.source_repo = "z"
    changed = plan([changed_low, high], "box", plat="windows")
    assert changed.drift_key == first.drift_key
    assert changed.provenance_hash != first.provenance_hash

    floor = _settings_package(
        tmp_path,
        "floor",
        "example/floor",
        0,
        {"model": "high"},
        disposition="ensure-present",
    )
    assert plan([floor], "box", plat="windows").drift_key != plan(
        [high], "box", plat="windows"
    ).drift_key

    removal_a = _pkg(
        tmp_path,
        "removal-a",
        _data(
            "example/removal-a",
            manage={
                "copilot.settings.plugin-activation": {
                    "disposition": "ensure-absent",
                    "keys": {"enabledPlugins": ["a@example"]},
                }
            },
        ),
    )
    removal_b = _pkg(
        tmp_path,
        "removal-b",
        _data(
            "example/removal-b",
            manage={
                "copilot.settings.plugin-activation": {
                    "disposition": "ensure-absent",
                    "keys": {"enabledPlugins": ["b@example"]},
                }
            },
        ),
    )
    assert plan([removal_a], "box", plat="windows").drift_key != plan(
        [removal_b], "box", plat="windows"
    ).drift_key

    authority_changed = _settings_package(
        tmp_path, "a-authority", "example/high", 11, {"model": "high"}
    )
    authority_changed.source_repo = "a"
    assert manifest_hash([authority_changed]) != manifest_hash([high])


@pytest.mark.parametrize("schema_version", [1, 2, 3])
def test_legacy_packages_without_authority_retain_behavior(tmp_path, schema_version):
    package = _pkg(
        tmp_path,
        f"legacy-{schema_version}",
        _data(
            f"example/v{schema_version}",
            schema_version=schema_version,
            manage={
                "copilot.settings": {
                    "disposition": "enforce",
                    "values": {"model": "legacy"},
                }
            },
        ),
    )
    assert package.authority == 0
    assert plan([package], "box").package_authorities[0]["authority"] == 0
