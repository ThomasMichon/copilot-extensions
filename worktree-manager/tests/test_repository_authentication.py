"""Optional native authentication reads bind support to the executing slot."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from worktree_manager import agent_plugin_runtime, engine_client
from worktree_manager import repository_authentication as ui

pytestmark = pytest.mark.contract("worktree_manager.configuration_backstage")


@pytest.fixture
def selected(tmp_path, monkeypatch):
    root = tmp_path / ".agent-worktrees"
    slot = root / "versions" / "1.2.3"
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture interpreter")
    (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    (root / "deploy-manifest.json").write_text(json.dumps({
        "service": "agent-worktrees", "source": {"plugin": "agent-worktrees"},
    }), encoding="utf-8")
    package = slot / (
        "Lib/site-packages/agent_worktrees" if os.name == "nt"
        else "lib/python3.11/site-packages/agent_worktrees"
    )
    package.mkdir(parents=True)
    descriptor = package / "repository-authentication-v1.json"
    descriptor.write_text(json.dumps({
        "contract": ui.CONTRACT, "version": 1, "operations": ["inspect"],
        "read_only": True, "configuration": 1,
    }), encoding="utf-8")
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "WORKTREE_MANAGER_ENGINE_CMD",
                "WORKTREE_MANAGER_ENGINE_ARGV"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(engine_client, "_ENGINE_CMD_OVERRIDE", None)
    monkeypatch.setattr(engine_client, "_INHERITED_ENGINE_COMMAND", None)
    command = agent_plugin_runtime.resolve_installed_plugin_command("agent-worktrees")
    assert command is not None, (
        agent_plugin_runtime._policy_is_invalid(),
        agent_plugin_runtime._namespaced_resolution("agent-worktrees"),
    )
    assert command[0] == str(python)
    return root, slot, descriptor


def observation(project="sample", status="observed"):
    return {
        "contract": ui.CONTRACT, "version": 1, "status": status,
        "target": {
            "resource": f"{project}/{project}", "installation": "agent-worktrees:" + "a" * 64,
            "machine": "fixture",
        },
        "registry_revision": "b" * 64, "observed_at": "2026-01-01T00:00:00+00:00",
        "configuration": {
            "entries": [{"id": "repository", "title": "Repository metadata", "status": status,
                         "card": {"title": "Native observation", "body": "Provider-owned limitations"}}],
            "summary": {"status": status, "observed_at": "2026-01-01T00:00:00+00:00"},
        },
    }


@pytest.mark.parametrize("status", ["observed", "unavailable", "selection-required", "unsupported"])
def test_current_and_domain_failure_reads_use_one_qualified_invocation(selected, monkeypatch, status):
    calls = []
    result = observation(status=status)

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0 if status == "observed" else 1,
            stdout=json.dumps(result), stderr="must-not-render",
        )

    monkeypatch.setattr(ui, "run_caller_owned", run)
    rows = ui.inventory("sample")
    assert rows == result["configuration"]
    assert len(calls) == 1
    expected = agent_plugin_runtime.resolve_installed_plugin_command("agent-worktrees")
    assert calls[0][0] == [
        *expected, "repos", "authentication", "inspect",
        "--context-project", "sample", "--repository", "sample", "--json",
    ]
    assert calls[0][1]["timeout"] < 20
    assert "must-not-render" not in json.dumps(rows)


@pytest.mark.parametrize("change", ["absent", "invalid", "version", "bool", "projection", "read-only"])
def test_old_runtime_and_invalid_support_are_visible_without_invocation(selected, monkeypatch, change):
    _, _, descriptor = selected
    raw = json.loads(descriptor.read_text("utf-8"))
    if change == "absent":
        descriptor.unlink()
    elif change == "invalid":
        descriptor.write_text("{", encoding="utf-8")
    else:
        key, value = {
            "version": ("version", 2), "bool": ("version", True),
            "projection": ("configuration", None), "read-only": ("read_only", False),
        }[change]
        descriptor.write_text(json.dumps({**raw, key: value}), encoding="utf-8")
    monkeypatch.setattr(ui, "run_caller_owned", lambda *a, **k: pytest.fail("unsupported probe"))
    rows = ui.inventory("sample")
    assert rows["entries"][0]["status"] == "unsupported"
    assert "selected runtime" in rows["entries"][0]["card"]["body"]


def test_new_payload_and_explicit_foreign_engine_cannot_supply_support(selected, tmp_path, monkeypatch):
    _, _, descriptor = selected
    payload = tmp_path / "new-payload"
    payload.mkdir()
    (payload / descriptor.name).write_bytes(descriptor.read_bytes())
    descriptor.unlink()
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(payload))
    monkeypatch.setattr(ui, "run_caller_owned", lambda *a, **k: pytest.fail("foreign probe"))
    assert ui.inventory("sample")["entries"][0]["status"] == "unsupported"
    descriptor.write_bytes((payload / descriptor.name).read_bytes())
    monkeypatch.setattr(engine_client, "_ENGINE_CMD_OVERRIDE", ["legacy-engine"])
    assert ui.inventory("sample")["entries"][0]["status"] == "unsupported"


def test_provider_rollback_rechecks_selected_slot_not_cached_new_support(selected, monkeypatch):
    root, slot, _ = selected
    old = root / "versions" / "1.2.2"
    python = old / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"old interpreter")
    (old / ".install-complete.json").write_text("{}", encoding="utf-8")
    calls = []
    monkeypatch.setattr(ui, "run_caller_owned", lambda argv, **kwargs: (
        calls.append(argv) or SimpleNamespace(returncode=0, stdout=json.dumps(observation()), stderr="")
    ))
    assert ui.inventory("sample")["entries"][0]["status"] == "observed"
    (root / "current-version").write_text(old.name, encoding="utf-8")
    assert ui.inventory("sample")["entries"][0]["status"] == "unsupported"
    assert len(calls) == 1 and str(slot) in calls[0][0]


@pytest.mark.parametrize("change", ["nonjson", "wrong-target", "wrong-version", "no-projection", "exit"])
def test_bad_native_result_is_an_error_not_an_empty_or_authenticated_inventory(selected, monkeypatch, change):
    result = observation()
    if change == "wrong-target":
        result["target"]["resource"] = "other/other"
    if change == "wrong-version":
        result["version"] = True
    if change == "no-projection":
        result.pop("configuration")
    monkeypatch.setattr(ui, "run_caller_owned", lambda *a, **k: SimpleNamespace(
        returncode=9 if change == "exit" else 0,
        stdout="private-malformed-output" if change == "nonjson" else json.dumps(result),
        stderr="private-provider-stderr",
    ))
    with pytest.raises(ui.InspectionError) as caught:
        ui.inventory("sample")
    assert "private" not in str(caught.value)


def test_cleanup_failure_retains_the_original_custody(selected, monkeypatch):
    from agent_procutil.owned_process import OwnedCleanupError

    failure = OwnedCleanupError(None)

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(ui, "run_caller_owned", fail)
    with pytest.raises(OwnedCleanupError) as caught:
        ui.inventory("sample")
    assert caught.value is failure


@pytest.mark.parametrize("in_process", [False, True])
def test_list_factory_preserves_engine_override_precedence_over_handoff(
    selected, monkeypatch, capsys, in_process,
):
    inherited = agent_plugin_runtime.resolve_installed_plugin_command("agent-worktrees")
    monkeypatch.setattr(engine_client, "_INHERITED_ENGINE_COMMAND", inherited)
    monkeypatch.setenv(engine_client.ENGINE_CMD_ENV, "foreign-engine")
    if in_process:
        monkeypatch.setattr(engine_client, "_ENGINE_CMD_OVERRIDE", inherited)
    calls = []
    monkeypatch.setattr(ui, "run_caller_owned", lambda argv, **kwargs: (
        calls.append(argv) or SimpleNamespace(returncode=0, stdout=json.dumps(observation()), stderr="")
    ))
    command = ui.configuration_pivot().list_cmd
    arguments = [
        "sample" if value == "{repo}" else value
        for value in command[command.index("worktree_manager.repository_authentication") + 1:]
    ]
    assert ui.main(arguments) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["entries"][0]["status"] == ("observed" if in_process else "unsupported")
    assert len(calls) == int(in_process)


def test_invalid_forwarded_context_refuses_before_runtime_selection(selected, monkeypatch, capsys):
    root, _, _ = selected
    monkeypatch.setattr(
        agent_plugin_runtime, "resolve_installed_plugin_command",
        lambda *args, **kwargs: pytest.fail("invalid context selected a runtime"),
    )
    pointer = str(root / "missing-install.json")
    assert ui.main(["--engine-context", pointer, "sample"]) == 1
    captured = capsys.readouterr()
    assert "context is invalid or inactive" in captured.err
    assert pointer not in captured.err and captured.out == ""


@pytest.mark.parametrize("value", ["null", "[]", "false"])
def test_explicit_malformed_engine_argv_refuses_before_runtime_selection(
    selected, monkeypatch, capsys, value,
):
    monkeypatch.setattr(
        agent_plugin_runtime, "resolve_installed_plugin_command",
        lambda *args, **kwargs: pytest.fail("malformed handoff selected a runtime"),
    )
    assert ui.main(["--engine-argv", value, "sample"]) == 1
    captured = capsys.readouterr()
    assert "engine command is invalid" in captured.err and captured.out == ""
