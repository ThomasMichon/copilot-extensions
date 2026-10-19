"""Configured execution authority is not a detected canonical-looking label."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace

import pytest
import yaml
from machine_transport import MachineEntry

from agent_worktrees import config as cfg, context_cli, execution_identity_read


@pytest.fixture
def binding(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    local = tmp_path / "project" / "config.yaml"
    local.parent.mkdir()
    global_path = tmp_path / "global.yaml"
    data = {
        "repo_name": "sample", "platform": "windows",
        "repos": {"sample": {"anchor": str(repo)}},
    }
    local.write_text(yaml.safe_dump(data), encoding="utf-8")
    global_path.write_text("{}\n", encoding="utf-8")
    entries = {
        key: MachineEntry(
            key=key, display_name=key, physical_host="shared-host",
            hostname="shared-os-host", execution_platform=platform,
        )
        for key, platform in (("native-a", "windows"), ("native-b", "windows"), ("guest", "wsl"))
    }
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "sample")
    monkeypatch.setattr(cfg, "default_config_path", lambda: local)
    monkeypatch.setattr(cfg, "global_config_path", lambda: global_path)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(cfg, "detect_machine", lambda *a, **k: "native-a")
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *a, **k: entries)
    return local, global_path, data, entries


def test_detected_key_matching_registry_never_becomes_authority(binding):
    local, global_path, _, _ = binding
    before = local.read_bytes(), global_path.read_bytes()
    result = execution_identity_read.read_execution_identity()
    assert result["status"] == "unavailable"
    assert result["reason"] == "machine-binding-not-configured"
    assert "machine" not in result
    assert (local.read_bytes(), global_path.read_bytes()) == before


@pytest.mark.parametrize("source", ["local", "global"])
def test_configured_precedence_is_captured_by_existing_loader(binding, source, capsys):
    local, global_path, data, _ = binding
    global_path.write_text("machine: native-a\n", encoding="utf-8")
    if source == "local":
        data["machine"] = "native-b"
        local.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = local.read_bytes(), global_path.read_bytes()
    assert context_cli.cmd_get(argparse.Namespace(key="execution-identity")) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "schema": execution_identity_read.SCHEMA, "version": 1, "status": "ready",
        "configured": True, "source": f"{'project' if source == 'local' else 'global'}-machine-config",
        "project": "sample", "machine": "native-b" if source == "local" else "native-a",
        "platform": "windows",
    }
    assert (local.read_bytes(), global_path.read_bytes()) == before


def test_provenance_cannot_be_asserted_by_another_yaml_setting(binding):
    local, _, data, _ = binding
    data["_machine_binding"] = {"source": "project-machine-config"}
    data["configured"] = True
    local.write_text(yaml.safe_dump(data), encoding="utf-8")
    result = execution_identity_read.read_execution_identity()
    assert result["status"] == "unavailable"
    assert "machine" not in result


@pytest.mark.parametrize("machine", ["", None, 0, False, "   "])
@pytest.mark.parametrize("source", ["local", "global"])
def test_present_invalid_binding_is_refused_not_detected(binding, machine, source, capsys):
    local, global_path, data, _ = binding
    if source == "local":
        global_path.write_text("machine: native-a\n", encoding="utf-8")
        data["machine"] = machine
        local.write_text(yaml.safe_dump(data), encoding="utf-8")
    else:
        global_path.write_text(yaml.safe_dump({"machine": machine}), encoding="utf-8")
    assert execution_identity_read.cmd_get_execution_identity() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "refused"
    assert "nonempty string" in result["reason"]
    assert "machine" not in result


def test_readonly_provenance_does_not_change_ordinary_falsy_fallback(binding):
    local, global_path, data, _ = binding
    data["machine"] = ""
    local.write_text(yaml.safe_dump(data), encoding="utf-8")
    global_path.write_text("machine: native-b\n", encoding="utf-8")
    assert cfg._load_config_uncached(
        include_control_plane_related_pr=False, project="sample",
    ).machine == "native-b"


def test_guest_binding_is_separate_from_native_same_host(binding, monkeypatch):
    local, _, data, _ = binding
    data.update(machine="guest", platform="wsl")
    local.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    result = execution_identity_read.read_execution_identity()
    assert result["status"] == "ready"
    assert (result["machine"], result["platform"]) == ("guest", "wsl")


@pytest.mark.parametrize("machine", ["guest", "unknown-space", "shared-os-host", 42, ["native-a"]])
def test_known_configured_mismatch_is_a_compatible_refusal(binding, machine, capsys):
    local, _, data, _ = binding
    data["machine"] = machine
    local.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert execution_identity_read.cmd_get_execution_identity() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == execution_identity_read.SCHEMA
    assert result["version"] == 1 and result["status"] == "refused"
    assert "machine" not in result


def test_unscoped_source_still_reports_explicit_configuration_for_consumer_validation(binding):
    local, _, data, entries = binding
    data["machine"] = "native-a"
    local.write_text(yaml.safe_dump(data), encoding="utf-8")
    entry = replace(entries["native-a"], execution_platform="")
    entries.clear()
    entries["native-a"] = entry
    result = execution_identity_read.read_execution_identity()
    assert result["status"] == "ready"
    assert result["configured"] is True
    assert (result["machine"], result["platform"]) == ("native-a", "windows")


def test_missing_project_has_explicit_unavailable_status(binding, monkeypatch):
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", None)
    result = execution_identity_read.read_execution_identity()
    assert result["status"] == "unavailable"
    assert result["reason"] == "project-not-registered"
