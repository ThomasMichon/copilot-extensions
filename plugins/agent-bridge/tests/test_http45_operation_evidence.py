"""Independent operation inputs, replayed without a model or creation backend."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest
from test_native_keyed_admission import keyed as _production_manager_fixture

ROOT = Path(__file__).resolve().parents[3]
INPUTS = Path(__file__).resolve().parents[1] / "contract" / "inputs" / "http45"
pytestmark = pytest.mark.guard
proposed_keyed = _production_manager_fixture


def test_proposed_keyed_input_replays_through_production_admission(proposed_keyed):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agent_bridge.models import SessionStatus
    from agent_bridge.routes.sessions import router
    proposed = os.environ.get("BRIDGE_HTTP45_PROPOSED_KEYED")
    path = Path(proposed) if proposed else INPUTS / "native-keyed-admission.json"
    raw = path.read_bytes()
    if proposed:
        assert hashlib.sha256(raw).hexdigest() == os.environ["BRIDGE_HTTP45_PROPOSED_SHA256"]
    vector = json.loads(raw)
    manager, session, _ = proposed_keyed
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(router)
    endpoint = "/api/v1/sessions/bridge-a/turns/native-bound/keyed"
    with TestClient(app) as api:
        missing = api.post(endpoint + "/receipt", json=vector["request"])
        assert missing.status_code == 404
        assert missing.json() == vector["missing_receipt_refusal"]
        first = api.post(endpoint, json=vector["request"])
        assert first.status_code == 200
        assert first.json() == vector["admission_response"]
        events = manager.db.execute_read("SELECT * FROM events")
        for settled in (False, True):
            if settled:
                session.status = SessionStatus.IDLE
            for suffix in ("", "/receipt"):
                replay = api.post(endpoint + suffix, json=vector["request"])
                assert replay.status_code == 200
                assert replay.json() == vector["admission_response"]
            conflict = api.post(endpoint, json={**vector["request"], "prompt": "different"})
            assert conflict.status_code == 409
            assert conflict.json() == vector["changed_prompt_refusal"]
        assert manager.db.execute_read("SELECT * FROM events") == events
        assert len(manager.db.get_turns(session.session_id)) == session.turn_count == 1
        manager._run_prompt.assert_awaited_once()


def test_capture_rejects_tampered_proposed_keyed_before_write(monkeypatch, tmp_path, proposed_keyed):
    from agent_procutil import owned_process
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location("http45_proposed_capture", ROOT / "tools/capture_bridge_http45.py")
    capture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(capture)
    snapshot = tmp_path / "capture-source"
    contract = "plugins/agent-bridge/contract"
    registry = json.loads((ROOT / contract / "registry.json").read_text())
    current = contract + "/fixtures/http/current/"
    retained = {
        current + name for name in (
            "ordinary-native-admission-generation-45.json",
            "native-bound-extracted-routes-prompts-generation-44.json",
            "managed-drain-generation-44.json",
            "provider-creation-refresh-generation-44.json",
        )
    }
    http = registry["contracts"][0]
    http["declared_range"]["current"] = 45
    http["fixtures"] = [row for row in http["fixtures"] if row["path"] in retained]
    sources = {
        capture.PREFIX + name for name in (
            "native_keyed_admission.py", "native_keyed_client.py", "session_targeting_cli.py",
            "routes/prompts.py", "session_prompts.py", "db_schema.py", "db_events.py",
            "workspace_creation_cli.py",
        )
    }
    for path in retained:
        value = json.loads((ROOT / path).read_text())
        sources.add(value["captured_from"]["source_path"])
    http["source_paths"] = [row for row in http["source_paths"] if row["path"] in sources]
    for path in (*retained, contract + "/inputs/http45/native-keyed-admission.json",
                 contract + "/inputs/http45/workspace-creation-cli.json"):
        destination = snapshot / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination)
    (snapshot / contract / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    shutil.copyfile(ROOT / "plugins/agent-bridge/plugin.json",
                    snapshot / "plugins/agent-bridge/plugin.json")
    for source in registry["contracts"][0]["source_paths"]:
        destination = snapshot / source["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source["path"], destination)
    proposed = snapshot / contract / "inputs/http45/native-keyed-admission.json"
    vector = json.loads(proposed.read_text(encoding="utf-8-sig"))
    vector["changed_prompt_refusal"]["detail"]["code"] = "unchecked-replacement-code"
    proposed.write_text(json.dumps(vector), encoding="utf-8")
    def output_hashes():
        files = [snapshot / contract / "registry.json",
                 *(snapshot / contract / "fixtures").rglob("*.json")]
        return {str(path.relative_to(snapshot)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in files}

    before = output_hashes()
    calls = []

    def contained_replay(argv, **kwargs):
        import subprocess
        calls.append(argv)
        try:
            test_proposed_keyed_input_replays_through_production_admission(proposed_keyed)
        except AssertionError as error:
            return subprocess.CompletedProcess(argv, 1, str(error), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(capture, "ROOT", snapshot)
    monkeypatch.setattr(owned_process, "run_caller_owned", contained_replay)
    # Only repository identity is fixture-local; production admission replay stays real.
    fixture_commit = "1" * 40

    def fixture_source(commit, path):
        assert commit == fixture_commit and path in sources
        return (snapshot / path).read_bytes()

    def fixture_blob(commit, path):
        data = fixture_source(commit, path)
        return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data,
                            usedforsecurity=False).hexdigest()

    monkeypatch.setattr(capture.evidence, "git_file_sha256",
                        lambda commit, path: capture.evidence.sha256_bytes(fixture_source(commit, path)))
    monkeypatch.setattr(capture.evidence, "git_blob", fixture_blob)
    monkeypatch.setattr(capture.evidence, "git",
                        lambda *_args: pytest.fail("repository lookup escaped the test fixture"))
    monkeypatch.setattr(sys, "argv", [
        "capture_bridge_http45.py", "--source-commit",
        fixture_commit, "--write",
    ])
    refusal = None
    try:
        capture.main()
    except ValueError as error:
        refusal = error
    after = output_hashes()
    assert after == before, "unchecked proposed input changed capture output hashes"
    assert refusal is not None and "operation replay" in str(refusal)
    assert len(calls) == 1


def test_workspace_cli_creation_family_uses_real_parser_builder_and_complete_receipt(monkeypatch, capsys):
    from agent_bridge import __main__ as core, session_start_cli, workspace_creation_cli
    from agent_bridge.client import BridgeClient, BridgeClientError
    from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
    from agent_bridge.session_preferences import SOURCE_ENV, CONTEXT_ENV
    vector = json.loads((INPUTS / "workspace-creation-cli.json").read_text(encoding="utf-8-sig"))
    monkeypatch.delenv(SOURCE_ENV, raising=False)
    monkeypatch.delenv(CONTEXT_ENV, raising=False)
    monkeypatch.setattr(session_start_cli, "_read_agent_inventory", lambda _: {})
    monkeypatch.setattr(session_start_cli, "_prepare_sender_preference",
                        lambda *_a, **_kw: SimpleNamespace(project="example"))
    monkeypatch.setattr(core, "_get_caller_session_id", lambda: None)
    monkeypatch.setattr(core, "_worktrees_get", lambda _: None)
    monkeypatch.setattr(core, "_startup_request_timeout", lambda: None)
    monkeypatch.setattr(core, "_connection_identity",
                        lambda *_a: {"repo": "example", "worktree_id": "worktree-a"})
    for daemon in (45, vector["floor_refusal"]["daemon_protocol"]):
        calls = []
        client = BridgeClient.__new__(BridgeClient)
        client._base = "http://testserver"
        client._daemon_proto = (45, 1)
        client._connect_grace = 0

        def request(method, path, body=None, **kwargs):
            if path == "/health":
                return {"protocol_version": daemon, "min_protocol_version": 1}
            calls.append((method, path, body))
            assert (method, path) == ("POST", "/api/v1/sessions/workspace-creations")
            assert body == {**vector["request"]["wire"], "protocol_version": HTTP_PROTOCOL_VERSION}
            return copy.deepcopy(vector["server_response"])

        client._request = request
        client.get_session = lambda _: {"status": "idle", "turn_count": 0}
        args = core.build_parser().parse_args(vector["request"]["argv"])
        if daemon == 45:
            workspace_creation_cli.create(args, client, caller_id="caller")
            assert json.loads(capsys.readouterr().out) == vector["complete_receipt"]
            assert len(calls) == 1
        else:
            with pytest.raises(BridgeClientError) as refused:
                workspace_creation_cli.create(args, client, caller_id="caller")
            assert refused.value.status == vector["floor_refusal"]["status"]
            assert len(calls) == vector["floor_refusal"]["creation_requests"]


def _family_validator():
    spec = importlib.util.spec_from_file_location("http45_family_check", ROOT / "tools" / "bridge_http45_evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capture_text_rule_preserves_escaped_prompt_bytes(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location("http45_capture", ROOT / "tools/capture_bridge_http45.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = b'{"prompt":"a\\r\\nb"}\n'
    assert module.same_text(expected.replace(b"\n", b"\r\n"), expected)
    assert not module.same_text(b'{"prompt":"a\\nb"}\n', expected)
    original = tmp_path / "original.json"
    original.write_bytes(expected)
    alias = tmp_path / "aliased.json"
    alias.hardlink_to(original)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="aliased"):
        module.safe("aliased.json")


@pytest.mark.parametrize("change", ["unlinked", "wrong-family", "missing-creation", "changed-predecessor"])
def test_family_validator_rejects_hash_consistent_semantic_substitution(change):
    helper = _family_validator()
    vector = json.loads((INPUTS / "workspace-creation-cli.json").read_text(encoding="utf-8-sig"))
    vector["captured_from"] = {"source_path": "plugins/agent-bridge/src/agent_bridge/workspace_creation_cli.py"}
    if change == "unlinked":
        vector.pop("evidence_family")
    elif change == "wrong-family":
        vector["evidence_family"] = helper.KEYED
    elif change == "missing-creation":
        vector["complete_receipt"].pop("creation")
    else:
        old = copy.deepcopy(vector)
        old["captured_from"]["source_path"] = "different-owner.py"
        vector["reattested_from"] = {"path": "old.json", "sha256": "same-hash"}
    loaded = {"new.json": ("new-hash", vector)}
    if change == "changed-predecessor":
        loaded["old.json"] = ("same-hash", old)
    errors = []
    helper.validate_families([{"generation": 45, "path": "new.json"}], loaded, errors)
    assert any(error.startswith("new.json:") for error in errors)
