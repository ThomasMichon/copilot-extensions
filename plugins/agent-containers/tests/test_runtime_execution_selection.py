"""Immutable provider declaration controls; no provider or native execution."""

import json
from pathlib import Path

import pytest

from . import test_runtime_profile as profile_fixtures

commands = profile_fixtures.commands
payload = profile_fixtures.payload
profiles = profile_fixtures.profiles
_prepare_source = profile_fixtures._prepare_source
_provision = profile_fixtures._provision


def _descriptor(result):
    return Path(result["venv"]) / "runtime-profile.json"


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _snapshot(root):
    return {path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("profile", ["base", "provider-exec"])
@pytest.mark.parametrize("prepared", [False, True])
def test_selected_slot_owns_declaration_without_enabling_retained_custody(
    profiles, payload, commands, tmp_path, profile, prepared,
):
    if prepared:
        _prepare_source(profiles, payload)
    root = tmp_path / "runtime"
    result = _provision(profiles, root, payload, requested=profile)
    value = json.loads(_descriptor(result).read_text())
    assert value["executionContract"] == {
        "schema": profiles.EXECUTION_SCHEMA, "version": 1, "retainedCreation": False,
    }
    before = _snapshot(root)
    commands.clear()
    selected = profiles.select_execution(root)
    assert selected.python == Path(result["python"])
    assert selected.runtime_version == result["runtimeVersion"]
    assert selected.payload_hash == value["payloadHash"]
    assert selected.profile == profile
    assert selected.contract_version == 1
    assert selected.retained_creation is False
    assert _snapshot(root) == before
    assert commands == []


def test_selection_rebinds_to_actual_runtime_and_does_not_cache_payload_support(
    profiles, payload, commands, tmp_path,
):
    root = tmp_path / "runtime"
    first = _provision(profiles, root, payload)
    old_path = _descriptor(first)
    legacy = json.loads(old_path.read_text())
    del legacy["executionContract"]
    _write(old_path, legacy)
    old_bytes = old_path.read_bytes(), old_path.stat().st_mtime_ns
    (payload / "pyproject.toml").write_text('version = "1.0.1"\n', encoding="utf-8")
    second = _provision(profiles, root, payload)
    selected = profiles.select_execution(root)
    assert selected.python == Path(second["python"])
    profiles.runtime.activate(root, first["runtimeVersion"], link_free=True)
    before = _snapshot(root)
    with pytest.raises(RuntimeError, match="no provider execution declaration"):
        profiles.select_execution(root)
    assert _snapshot(root) == before
    assert old_bytes == (old_path.read_bytes(), old_path.stat().st_mtime_ns)
    assert selected.python != Path(first["python"])


def test_new_installer_reuses_legacy_profile_without_retroactive_declaration(
    profiles, payload, commands, tmp_path,
):
    root = tmp_path / "runtime"
    first = _provision(profiles, root, payload)
    path = _descriptor(first)
    value = json.loads(path.read_text())
    del value["executionContract"]
    _write(path, value)
    before = path.read_bytes(), path.stat().st_mtime_ns
    commands.clear()
    again = _provision(profiles, root, payload)
    assert again["reused"]
    assert before == (path.read_bytes(), path.stat().st_mtime_ns)
    assert all("-c" in command for command in commands)
    with pytest.raises(RuntimeError, match="no provider execution declaration"):
        profiles.select_execution(root)


@pytest.mark.parametrize("declaration", [
    None, [], {}, True,
    {"schema": "other", "version": 1, "retainedCreation": False},
    {"schema": "copilot-extensions.agent-containers.provider-execution",
     "version": True, "retainedCreation": False},
    {"schema": "copilot-extensions.agent-containers.provider-execution",
     "version": 2, "retainedCreation": False},
    {"schema": "copilot-extensions.agent-containers.provider-execution",
     "version": 1, "retainedCreation": "false"},
    {"schema": "copilot-extensions.agent-containers.provider-execution",
     "version": 1, "retainedCreation": 0},
    {"schema": "copilot-extensions.agent-containers.provider-execution",
     "version": 1, "retainedCreation": False, "unknown": True},
])
def test_malformed_declaration_refuses_read_only_and_reuse(
    profiles, payload, commands, tmp_path, declaration,
):
    root = tmp_path / "runtime"
    first = _provision(profiles, root, payload)
    path = _descriptor(first)
    value = json.loads(path.read_text())
    value["executionContract"] = declaration
    _write(path, value)
    before = _snapshot(root)
    commands.clear()
    with pytest.raises(RuntimeError, match="Invalid Containers provider execution declaration"):
        profiles.select_execution(root)
    assert _snapshot(root) == before
    with pytest.raises(RuntimeError, match="Invalid Containers provider execution declaration"):
        _provision(profiles, root, payload)
    assert commands == []


@pytest.mark.parametrize("change", [
    "hash", "marker", "profile-link", "slot-link", "duplicate", "profile", "package",
])
def test_invalid_slot_evidence_never_implies_execution_support(
    profiles, payload, commands, tmp_path, change,
):
    root = tmp_path / "runtime"
    result = _provision(profiles, root, payload)
    path = _descriptor(result)
    if change == "hash":
        value = json.loads(path.read_text())
        value["payloadHash"] = "0" * 64
        _write(path, value)
    elif change == "marker":
        (path.parent / ".install-complete.json").write_text("{}")
    elif change == "profile-link":
        target = tmp_path / "descriptor.json"
        path.rename(target)
        path.symlink_to(target)
    elif change == "slot-link":
        target = tmp_path / "linked-slot"
        path.parent.rename(target)
        path.parent.symlink_to(target, target_is_directory=True)
    elif change in ("profile", "package"):
        value = json.loads(path.read_text())
        value["profile" if change == "profile" else "packageVersion"] = (
            "provider-exec" if change == "profile" else "2.0.0"
        )
        _write(path, value)
    else:
        text = path.read_text()
        path.write_text(text.replace('"retainedCreation": false',
                                     '"retainedCreation": false, "retainedCreation": true'))
    before = _snapshot(root)
    commands.clear()
    with pytest.raises(RuntimeError):
        profiles.select_execution(root)
    assert _snapshot(root) == before
    assert commands == []


def test_missing_runtime_does_not_self_provision(profiles, commands, tmp_path):
    root = tmp_path / "absent"
    with pytest.raises(RuntimeError, match="No completed"):
        profiles.select_execution(root)
    assert not root.exists()
    assert commands == []


def test_current_changes_after_selection_do_not_redirect_immutable_interpreter(
    profiles, payload, commands, tmp_path, monkeypatch,
):
    root = tmp_path / "runtime"
    first = _provision(profiles, root, payload)
    second = _provision(profiles, root, payload, requested="provider-exec")
    profiles.runtime.activate(root, first["runtimeVersion"], link_free=True)
    resolve = profiles.runtime.resolve_python

    def select_then_change(root):
        selected = resolve(root)
        profiles.runtime.activate(root, second["runtimeVersion"], link_free=True)
        return selected

    monkeypatch.setattr(profiles.runtime, "resolve_python", select_then_change)
    selection = profiles.select_execution(root)
    assert selection.python == Path(first["python"])
    assert selection.runtime_version == first["runtimeVersion"]
    assert (root / "current-version").read_text().strip() == second["runtimeVersion"]


def test_mutable_dev_slot_cannot_supply_immutable_execution_declaration(
    profiles, payload, commands, tmp_path, monkeypatch,
):
    root = tmp_path / "runtime"
    result = _provision(profiles, root, payload)
    monkeypatch.setattr(
        profiles.runtime, "resolve_python",
        lambda root: root / "versions" / "dev" / Path(result["python"]).parent.name
        / Path(result["python"]).name,
    )
    before = _snapshot(root)
    with pytest.raises(RuntimeError, match="requires an immutable slot"):
        profiles.select_execution(root)
    assert _snapshot(root) == before


def test_reuse_cannot_promote_conflicting_retained_support(
    profiles, payload, commands, tmp_path,
):
    root = tmp_path / "runtime"
    result = _provision(profiles, root, payload)
    path = _descriptor(result)
    value = json.loads(path.read_text())
    value["executionContract"]["retainedCreation"] = True
    _write(path, value)
    before = _snapshot(root)
    commands.clear()
    with pytest.raises(RuntimeError, match="conflicting declaration"):
        _provision(profiles, root, payload)
    assert _snapshot(root) == before
    assert commands == []
