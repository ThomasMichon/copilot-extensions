"""Focused admission controls; ordinary installed execution has its own witness."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

from agent_mcp import cutover, forward, incoming


@pytest.mark.parametrize("filename,publish,deploy", [
    ("init.ps1", "Publish-IncomingMcp", "Deploy-SelfProvisioningBinstub"),
    ("init.sh", "_publish_incoming_mcp || exit 1", "deploy_binstub"),
])
def test_ordinary_installers_publish_without_retirement_fallthrough(filename, publish, deploy):
    script = (Path(__file__).parents[1] / "scripts" / filename).read_text(encoding="utf-8")
    commands = "\n".join(line.partition("#")[0] for line in script.splitlines())
    assert commands.rindex(publish) < commands.rindex(deploy)
    assert "Predecessor retirement and slot collection deferred" in commands
    assert "reap_versions.py" not in commands
    assert not re.search(r"\b(?:cutover|reset-control|Invoke-VersionedGC|_versioned_gc|gc)\b", commands)


@pytest.fixture
def admission(monkeypatch, tmp_path):
    layout = ("incoming", tmp_path / "python", tmp_path)
    monkeypatch.setattr(incoming, "launch_generation", lambda: layout[0])
    monkeypatch.setattr(cutover, "_installed_slot", lambda: layout)
    monkeypatch.setattr(cutover, "_require_selected", lambda candidate: None)
    monkeypatch.setattr(forward, "_ensure_serve_enabled", lambda: True)
    monkeypatch.delenv("AGENT_MCP_NO_CUTOVER", raising=False)
    reply = {
        "version": "outgoing", "runtime_root": str(tmp_path),
        "fresh_admission": incoming.CONTRACT, "ready": True, "draining": False,
    }
    monkeypatch.setattr(incoming, "_ping", lambda handle: dict(reply))
    starts = []

    def startup(candidate, generation, **kwargs):
        starts.append((candidate, generation))
        reply["version"] = generation

    monkeypatch.setattr(cutover, "_run_slot_startup", startup)
    return layout, reply, starts


def test_capable_old_endpoint_requires_actual_generation_publication(admission):
    layout, _, starts = admission
    result = incoming.ensure_current(Path("private-handle"))
    assert starts == [(layout, "incoming")]
    assert result == incoming.Admission("incoming", str(layout[2]))


@pytest.mark.parametrize("policy", ["no-service", "no-cutover"])
def test_inhibited_fresh_call_refuses_stale_execution(admission, monkeypatch, policy):
    _, _, starts = admission
    if policy == "no-service":
        monkeypatch.setattr(forward, "_ensure_serve_enabled", lambda: False)
    else:
        monkeypatch.setenv("AGENT_MCP_NO_CUTOVER", "1")
    with pytest.raises(OSError, match="stale execution refused"):
        incoming.ensure_current(Path("private-handle"))
    assert not starts


def test_foreign_installation_is_not_taken_over(admission):
    layout, reply, starts = admission
    reply["runtime_root"] = str(layout[2] / "foreign")
    with pytest.raises(OSError, match="another installation"):
        incoming.ensure_current(Path("private-handle"))
    assert not starts


def test_selection_race_is_revalidated_after_publication(admission, monkeypatch):
    def changed(candidate):
        raise cutover.ControlError("selection changed")

    monkeypatch.setattr(cutover, "_require_selected", changed)
    with pytest.raises(incoming.AdmissionRefused, match="selection changed"):
        incoming.ensure_current(Path("private-handle"))


def test_failed_incoming_health_is_not_current_admission(admission, monkeypatch):
    def failed(*args, **kwargs):
        raise cutover.ControlError("candidate failed health")

    monkeypatch.setattr(cutover, "_run_slot_startup", failed)
    with pytest.raises(cutover.ControlError, match="failed health"):
        incoming.ensure_current(Path("private-handle"))


def test_changed_endpoint_after_startup_is_not_admitted(admission, monkeypatch):
    _, reply, _ = admission
    monkeypatch.setattr(cutover, "_run_slot_startup", lambda *args, **kwargs: None)
    reply["ready"] = False
    with pytest.raises(OSError, match="did not qualify"):
        incoming.ensure_current(Path("private-handle"))


def test_requested_exact_handle_is_forwarded_to_installed_startup(admission, monkeypatch):
    _, reply, _ = admission
    requested = Path("requested-handle")
    seen = []

    def startup(layout, generation, **kwargs):
        seen.append(kwargs["fixed_socket"])
        reply["version"] = generation

    monkeypatch.setattr(cutover, "_run_slot_startup", startup)
    assert incoming.ensure_current(requested)
    assert seen == [requested]


def test_legacy_serving_intent_publishes_with_empty_global_catalog(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location(
        "private_incoming_publisher",
        Path(__file__).parents[1] / "scripts" / "publish_incoming.py",
    )
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    layout = ("incoming", tmp_path / "python", tmp_path)
    legacy = tmp_path / "legacy"
    legacy.write_text("occupied legacy namespace", encoding="utf-8")
    monkeypatch.setattr(cutover, "_installed_slot", lambda: layout)
    monkeypatch.setattr(cutover, "_configured_readiness", lambda: {"state": "empty"})
    monkeypatch.setattr(cutover, "_fixed_handle_has_live_listener", lambda *args, **kwargs: False)
    monkeypatch.setattr(incoming.sockio, "legacy_socket_path", lambda: legacy)
    monkeypatch.delenv("AGENT_MCP_SERVE_SOCKET", raising=False)
    monkeypatch.delenv("AGENT_MCP_NO_SERVE", raising=False)
    monkeypatch.delenv("AGENT_MCP_NO_CUTOVER", raising=False)
    # Keep the publisher's process-local assignment private in this test process.
    monkeypatch.setenv("AGENT_MCP_INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path))
    starts = []

    def startup(candidate, generation, **kwargs):
        starts.append((candidate, generation))
        return {"ok": True}

    monkeypatch.setattr(cutover, "_run_slot_startup", startup)
    assert publisher.main(["--root", str(tmp_path)]) == 0
    assert starts == [(layout, "incoming")]
    assert '"ok": true' in capsys.readouterr().out
    assert legacy.read_text(encoding="utf-8") == "occupied legacy namespace"


def test_dead_exact_configured_sidecar_does_not_create_serving_intent(
    tmp_path, monkeypatch, capsys,
):
    spec = importlib.util.spec_from_file_location(
        "inactive_configured_publisher",
        Path(__file__).parents[1] / "scripts" / "publish_incoming.py",
    )
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    fixed = tmp_path / "configured" / "exact.sock"
    fixed.parent.mkdir()
    endpoint = incoming.sockio._endpoint_path(fixed)
    original = json.dumps({"port": 23456, "token": "fixture"}).encode()
    endpoint.write_bytes(original)
    monkeypatch.setenv("AGENT_MCP_SERVE_SOCKET", str(fixed))
    monkeypatch.setenv("AGENT_MCP_INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path / "configuration"))
    monkeypatch.delenv("AGENT_MCP_NO_SERVE", raising=False)
    monkeypatch.delenv("AGENT_MCP_NO_CUTOVER", raising=False)
    monkeypatch.setattr(cutover, "_installed_slot", lambda: ("incoming", tmp_path, tmp_path))
    monkeypatch.setattr(cutover, "_configured_readiness", lambda: {"state": "ready"})
    monkeypatch.setattr(cutover, "_fixed_handle_has_live_listener", lambda *args, **kwargs: False)
    assert cutover.source_fixed_socket() == fixed
    assert cutover.source_serving_intent() is False
    starts = []
    monkeypatch.setattr(cutover, "_run_slot_startup", lambda *args, **kwargs: starts.append(args) or {"ok": True})
    assert publisher.main(["--root", str(tmp_path)]) == 0
    assert not starts
    assert "serving remains on demand" in capsys.readouterr().out
    assert endpoint.read_bytes() == original
