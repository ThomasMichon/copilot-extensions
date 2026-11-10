from __future__ import annotations

import json
import os
import subprocess

import pytest

from agent_worktrees import repos_configuration, repos_configuration_catalog as catalog
from agent_worktrees import repos_knowledge_configuration as binding
from agent_worktrees import repos_knowledge_default as defaults
from agent_worktrees.repos_configuration_sources import ConfigurationError
from test_knowledge_preferences import native as native


def projection(native, *, status="suggested", target="shared"):
    return {
        "schema": defaults.SCHEMA, "version": 1, "read_only": True,
        "input_revision": "a" * 64, "accepted_revision": 1,
        "scope": {
            "machine": native.config.machine, "harness_repository_id": native.ids["first"],
            "identity_resolution": "not-performed",
        },
        "knowledge_preference": {
            "status": status, "knowledge_repository_id": native.ids[target] if target else None,
        },
        "provenance": "shared", "configuration_applied": False, "binding_changed": False,
    }


@pytest.fixture
def configured(native, monkeypatch):
    native.config.knowledge_preferences = {"state_dir": str(native.root / "state")}
    value = projection(native)
    monkeypatch.setattr(defaults, "_read", lambda *args: (dict(value), ["selected-machines"]))
    native.projection = value
    return native


def test_unconfigured_and_existing_binding_do_not_resolve_or_run_machines(native, monkeypatch):
    monkeypatch.setattr(defaults, "_read", lambda *a: pytest.fail("unselected provider"))
    assert defaults.observe("first") == {"status": "unconfigured"}
    native.config.knowledge_preferences = {"state_dir": str(native.root / "state")}
    native.config.knowledge_repo = "other"
    monkeypatch.setattr(defaults.state_root, "resolve_state_root", lambda *a, **k: type(
        "State", (), {"bound": True, "path": str(native.root), "error": None},
    )())
    assert defaults.observe("first") == {"status": "binding-preserved"}
    assert native.calls == []


@pytest.mark.parametrize("source", [{}, {"state_dir": "relative"}, {"state_dir": False}, {"command": "ignored"}])
def test_invalid_local_source_never_starts_a_provider(native, monkeypatch, source):
    native.config.knowledge_preferences = source
    monkeypatch.setattr(defaults, "_read", lambda *a: pytest.fail("invalid source"))
    with pytest.raises(ConfigurationError) as caught:
        defaults.observe("first")
    assert caught.value.code == "preference-source-invalid"
    assert native.calls == []


@pytest.mark.parametrize("status", ["absent", "suppressed"])
def test_absent_or_suppressed_default_never_invokes_native_binding(configured, status):
    configured.projection["knowledge_preference"] = {"status": status, "knowledge_repository_id": None}
    assert defaults.observe("first")["status"] == status
    with pytest.raises(ConfigurationError, match="changed"):
        defaults.preview("first", configured.ids["first"], configured.ids["shared"])
    assert configured.calls == []


def test_configuration_default_requires_confirmation_and_rechecks_selected_inputs(configured, capsys):
    args = [
        "--context-project", "first", "--harness-id", configured.ids["first"],
        "--knowledge-id", configured.ids["shared"], "--accepted-default",
    ]
    assert repos_configuration.command(["knowledge-preview", *args]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["accepted_default"]["accepted_revision"] == 1
    assert all(call[2] == "preview" for call in configured.calls)
    assert repos_configuration.command(["knowledge-apply", *args]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "missing-preview"
    configured.projection["input_revision"] = "b" * 64
    assert repos_configuration.command(["knowledge-apply", *args, "--preview-token", plan["token"]]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "preview-conflict"
    assert all(call[2] == "preview" for call in configured.calls)
    assert repos_configuration.command(["knowledge-preview", *args]) == 0
    fresh = json.loads(capsys.readouterr().out)
    assert repos_configuration.command(["knowledge-apply", *args, "--preview-token", fresh["token"]]) == 0
    assert json.loads(capsys.readouterr().out)["pointer_committed"]
    assert configured.calls[-1][2] == "apply"


def test_pointer_precondition_rechecks_accepted_inputs_after_native_preview(configured, monkeypatch):
    plan = defaults.observe("first")
    script = configured.root / "binding.py"
    script.touch()
    monkeypatch.setattr(binding, "owner_script", lambda project: script)
    monkeypatch.setattr(binding, "_owner_arguments", lambda *a: ["binding.py"])

    def selected(path, *, run_name, init_globals):
        check = init_globals["_NATIVE_BINDING_PRECONDITION"]
        check()
        configured.projection["input_revision"] = "b" * 64
        check()
        pytest.fail("changed inputs reached pointer commit")

    monkeypatch.setattr(binding.runpy, "run_path", selected)
    with pytest.raises(ConfigurationError) as caught:
        binding._apply_worker(
            "first", "shared", "a" * 64, str(script), plan["resolution"], plan["selection"],
        )
    assert caught.value.code == "preview-conflict"


@pytest.mark.parametrize("supported", [False, True])
def test_selected_runtime_descriptor_gates_the_new_parser_without_application_readiness(
    native, monkeypatch, supported,
):
    environment = native.root / "selected" / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    package = environment / (
        "Lib/site-packages/agent_machines" if os.name == "nt" else
        "lib/python3.11/site-packages/agent_machines"
    )
    package.mkdir(parents=True)
    if supported:
        (package / "preferences-repositories-v1.json").write_text(json.dumps({
            "schema": defaults.SCHEMA, "version": 1, "accepted_projection": 1,
            "read_only": True, "target_identity": "caller-native",
        }), encoding="utf-8")
    monkeypatch.setattr(defaults.machines_runtime, "selected", lambda **k: (["selected"], {}, python))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(projection(native)), "")

    monkeypatch.setattr(defaults, "run_caller_owned", run)
    source = {"state_dir": str(native.root / "state")}
    if supported:
        result, runtime = defaults._read(source, native.config.machine, native.ids["first"])
        assert runtime == ["selected"] and result["knowledge_preference"]["status"] == "suggested"
        assert calls == [[
            "selected", "preferences", "repositories", "--state-dir", source["state_dir"],
            "--machine", native.config.machine, "--harness", native.ids["first"],
        ]]
    else:
        with pytest.raises(ConfigurationError) as caught:
            defaults._read(source, native.config.machine, native.ids["first"])
        assert caught.value.code == "preference-owner-unsupported"
        assert not calls
    assert not (package / "installation-repair-v1.json").exists()


def test_optional_provider_failure_keeps_the_existing_configuration_catalog(native, monkeypatch):
    monkeypatch.setattr(catalog, "inspect_project", lambda *a: catalog.Inspection())
    monkeypatch.setattr(binding, "owner_status", lambda p: "available")
    monkeypatch.setattr(catalog, "defaults", lambda p: {})
    monkeypatch.setattr(catalog, "compose_preference", lambda p, n, *a: {
        "target": {"resource": f"{p}/{n}"}, "effective": "inherited", "sources": [],
    })
    native.config.knowledge_preferences = {"state_dir": str(native.root / "state")}

    def unavailable(*a):
        raise ConfigurationError("preference-owner-unsupported", "Selected Machines needs an update.")

    monkeypatch.setattr(defaults, "_read", unavailable)
    result = catalog.catalog("first")
    assert len(result["entries"]) == 4
    assert all(row["editable"] == "true" for row in result["entries"])
    assert all(row["preferred_knowledge_bindable"] == "false" for row in result["entries"])
    assert result["summary"]["knowledge_preference"]["code"] == "preference-owner-unsupported"
