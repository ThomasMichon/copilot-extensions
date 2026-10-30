"""Independent generation-46 expectations; no daemon, model, or remote mutation."""
from __future__ import annotations

import copy
import hashlib
import http.client
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import agent_bridge_contract_registry as registry_codec

from agent_bridge import __main__ as cli, protocol, startup_observation as observer
from agent_bridge import startup_progress as producer
from agent_bridge.client import BridgeClient
from agent_bridge.send_outcome import run_send
from agent_procutil.startup import PROGRESS_PREFIX, StartupClock, StartupTimeouts

CONTRACT = Path(__file__).resolve().parents[1] / "contract"
ROOT = Path(__file__).resolve().parents[3]
INPUT = CONTRACT / "inputs/http46/startup-observation.json"
CURRENT = CONTRACT / "fixtures/http/current"
pytestmark = pytest.mark.guard


def packet():
    return json.loads(INPUT.read_text(encoding="utf-8"))


def arguments(as_json=True):
    return SimpleNamespace(
        json=as_json, target="bridge-a", startup_communication_timeout=30,
        startup_process_timeout=300,
    )


def clock(item, now):
    item.clock = StartupClock(StartupTimeouts(), monotonic=lambda: now[0])


def frames(text):
    return [
        json.loads(line[len(PROGRESS_PREFIX):])
        for line in text.splitlines() if line.startswith(PROGRESS_PREFIX)
    ]


def test_http46_policy_and_capability_are_literal_contract_expectations():
    vector = packet()
    assert vector["response"] == {"protocol_version": 46, "min_protocol_version": 1}
    assert protocol.STARTUP_PROGRESS_PROTOCOL_VERSION == 46
    assert protocol.HTTP_PROTOCOL_VERSION >= protocol.STARTUP_PROGRESS_PROTOCOL_VERSION
    assert protocol.HTTP_PROTOCOL_MIN_SUPPORTED == 1
    assert protocol.KEYED_NATIVE_PROMPT_PROTOCOL_VERSION == 45
    assert vector["progress_prefix"] == PROGRESS_PREFIX == "@@agent-startup "
    assert vector["capability"] == {
        "name": "startup_progress", "required_protocol_version": 46,
        "previous_peer": 45, "legacy_fallback": False,
    }
    assert vector["ownership"] == {
        "startup_only_process_budget": True, "local_timer_is_contact": False,
        "unknown_events_are_contact": True,
        "contact_uses_local_receipt_not_payload_timestamp": True,
    }
    args = cli.build_parser().parse_args(vector["argv"])
    policy = StartupTimeouts()
    assert vector["policy"] == {
        "communication_seconds": policy.communication,
        "process_seconds": policy.process, "expected_seconds": policy.expected,
    } == {"communication_seconds": 30, "process_seconds": 300, "expected_seconds": 60}
    assert args.target == "bridge-a" and args.no_wait
    assert args.startup_communication_timeout == 30
    assert args.startup_process_timeout == 300


def test_http46_public_health_advertisement_matches_new_operation_packet(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agent_bridge.routes import health

    monkeypatch.setattr(health, "get_default_manager", lambda: SimpleNamespace(
        carrier_diagnostics=lambda: {},
    ))
    monkeypatch.setattr(health, "config_dir", lambda: CONTRACT)
    monkeypatch.setattr(health, "slot_descriptor", lambda *args, **kwargs: None)
    app = FastAPI()
    app.include_router(health.router)
    with TestClient(app) as api:
        response = api.get("/health")
    assert response.status_code == 200
    assert response.json()["protocol_version"] == protocol.HTTP_PROTOCOL_VERSION
    assert response.json()["protocol_version"] >= packet()["response"]["protocol_version"]
    assert response.json()["min_protocol_version"] == packet()["response"]["min_protocol_version"]


def test_http46_producer_and_observer_frames_replay_independent_vectors(monkeypatch, capsys):
    vector = packet()
    monkeypatch.setattr(producer, "time", SimpleNamespace(monotonic=lambda: 0.0))
    emitted = []
    progress = producer.StartupProgress(lambda event, data: emitted.append({"event": event, "data": data}))
    progress.update("launch/prep", "running")
    progress.update("ready", "finished")
    assert emitted == vector["producer_events"]
    item = observer.StartupObservation(StartupTimeouts())
    now = [0.0]
    clock(item, now)
    item.local_phase("lifecycle-lock")
    item.received("launch/prep", event="startup_progress")
    item.received(event="future-startup-v100")
    item.received(event="_heartbeat")
    item.ready()
    now[0] = 600
    item.check()
    assert item.clock.ready and item.clock.expired() is None
    assert frames(capsys.readouterr().err) == vector["observer_frames"]


@pytest.mark.parametrize("as_json", [True, False])
def test_http46_old_peer_refusal_precedes_mutation_in_both_renderers(monkeypatch, capsys, as_json):
    vector = packet()
    client = BridgeClient("http://fixture.invalid", "fixture")
    client._daemon_proto = (vector["capability"]["previous_peer"], 1)
    calls = []
    monkeypatch.setattr(client, "_request", lambda *a, **k: calls.append((a, k)))
    args = arguments(as_json)
    with observer.observing_send(args):
        with pytest.raises(SystemExit) as raised:
            run_send(args, lambda _: observer.configure_client(client, args))
    assert raised.value.code == vector["floor_refusal"]["exit_code"] == 69
    assert len(calls) == vector["floor_refusal"]["requests"] == 0
    output = capsys.readouterr()
    if as_json:
        assert json.loads(output.out) == vector["floor_refusal"]["json"]
    else:
        assert output.out == vector["floor_refusal"]["non_json_stdout"] == ""
        assert vector["floor_refusal"]["json"]["error"] in output.err


def test_http46_real_get_preflight_cannot_renew_a_late_response(monkeypatch):
    vector = packet()
    now = [0.0]
    calls = []
    client = BridgeClient("http://fixture.invalid", "fixture")

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            now[0] = vector["preflight"]["late_receipt_seconds"]
            return json.dumps(vector["response"]).encode()

    def urlopen(request, *, timeout):
        calls.append((request.method, request.selector, timeout))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with observer.observing_send(arguments()):
        item = observer.current()
        clock(item, now)
        with pytest.raises(observer.StartupObservationError) as raised:
            client._request("GET", vector["preflight"]["path"])
        assert raised.value.clock == vector["preflight"]["clock"] == "communication"
        assert raised.value.elapsed == 31
        assert item.clock.expired().silence == 31
    assert len(calls) == 1
    assert calls[0][:2] == ("GET", "/health")
    assert 0 < calls[0][2] <= vector["preflight"]["maximum_socket_timeout_seconds"]
    assert sum(method != "GET" for method, _, _ in calls) == vector["preflight"]["mutations"] == 0


def test_http46_lost_ack_posts_once_after_ready_without_retry_or_resume(monkeypatch, capsys):
    vector = packet()
    now = [0.0]
    client = BridgeClient("http://fixture.invalid", "fixture")
    calls = []

    def request(method, path, body, **kwargs):
        calls.append({"method": method, "path": path, "body": body, **kwargs})
        raise http.client.RemoteDisconnected("synthetic lost acknowledgement")

    monkeypatch.setattr(client, "_request", request)
    with observer.observing_send(arguments()):
        item = observer.current()
        clock(item, now)
        item.received("ready-target")
        item.ready()
        now[0] = 600
        with pytest.raises(SystemExit) as raised:
            run_send(arguments(), lambda _: client.submit_prompt("bridge-a", "one exact continuation"))
        assert item.clock.ready
    expected = copy.deepcopy(vector["prompt_request"])
    count = expected.pop("count")
    assert len(calls) == count == 1
    assert calls == [expected]
    assert raised.value.code == vector["lost_ack"]["exit_code"] == 70
    output = capsys.readouterr()
    assert json.loads(output.out) == vector["lost_ack"]["json"]
    assert "one exact continuation" not in output.err


@pytest.mark.parametrize("index", range(7))
def test_http46_incomplete_resume_ack_cannot_authorize_prompt(monkeypatch, index):
    expected = packet()["resume_acknowledgement"]
    client = BridgeClient("http://fixture.invalid", "fixture")
    calls = []
    release = threading.Event()

    class Stream:
        def __iter__(self):
            yield {"event": "_heartbeat", "data": {}}
            release.wait(2)

        def close(self):
            pass

    def request(method, path, *args, **kwargs):
        calls.append((method, path, kwargs))
        return expected["invalid_results"][index]

    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(client, "get_cursor_info", lambda *a, **kw: {
        "head_id": 0, "continuity_id": None,
    })
    monkeypatch.setattr(client, "stream_events", lambda *a, **kw: Stream())
    try:
        with observer.observing_send(arguments()):
            with pytest.raises(observer.StartupObservationError) as raised:
                client.resume_session(expected["session_id"])
                client.submit_prompt(expected["session_id"], "must not be submitted")
            assert raised.value.reason == expected["reason"] == "resume_unknown"
            assert observer.current().clock.ready is expected["ready"] is False
        assert len(calls) == expected["resume_posts"] == 1
        assert calls[0][:2] == ("POST", "/api/v1/sessions/bridge-a/resume")
        assert calls[0][2]["allow_retry"] is False
        assert sum(path.endswith("/turns") for _, path, _ in calls) == expected["prompt_posts"] == 0
    finally:
        release.set()


def test_http46_native_reference_is_canonical_before_observed_resume():
    from agent_bridge.session_targeting_cli import _resolve_known_session

    expected = packet()["canonical_session"]
    resumed = []
    client = SimpleNamespace(resume_session=lambda session_id, **kw: resumed.append(session_id))
    result = _resolve_known_session(
        client, expected["reference"],
        {"session_id": expected["session_id"], "status": expected["status"]}, force=False,
    )
    assert result == expected["session_id"]
    assert resumed == expected["resumed_ids"]


@pytest.mark.parametrize("index", range(2))
def test_http46_invalid_admission_status_remains_unknown(monkeypatch, index, capsys):
    status = packet()["invalid_admission_statuses"][index]
    client = BridgeClient("http://fixture.invalid", "fixture")
    calls = []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return {"session_id": "bridge-a", "turn_index": 0, "status": status}

    monkeypatch.setattr(client, "_request", request)
    with observer.observing_send(arguments()):
        observer.current().ready()
        with pytest.raises(SystemExit) as raised:
            run_send(arguments(), lambda _: client.submit_prompt("bridge-a", "exactly once"))
    assert raised.value.code == 70
    assert len(calls) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "admission_unknown"


def test_http46_local_timer_does_not_renew_and_process_budget_is_independent(capsys):
    item = observer.StartupObservation(StartupTimeouts())
    now = [0.0]
    clock(item, now)
    now[0] = 29
    item.local_phase("lock: prep - waiting")
    now[0] = 30
    with pytest.raises(observer.StartupObservationError) as raised:
        item.check()
    assert raised.value.clock == "communication"
    clock(item, now := [0.0])
    for instant in range(20, 300, 20):
        now[0] = instant
        item.received("launch/prep", event="future-startup-v100")
    now[0] = 300
    with pytest.raises(observer.StartupObservationError) as raised:
        item.check()
    assert raised.value.clock == "process"
    assert frames(capsys.readouterr().err)[-1]["event"] == "process_timeout"


def _hash(path):
    return hashlib.sha256(path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").encode()).hexdigest()


def _body(value):
    return {key: data for key, data in value.items()
            if key not in ("captured_from", "reattested_from")}


def test_http46_generation46_attestations_link_unchanged_families_without_rewriting_history():
    registry = registry_codec.load(ROOT).materialized
    http = next(item for item in registry["contracts"] if item["id"] == "agent-bridge.http-wire")
    assert http["declared_range"]["current"] >= 46
    registered = {row["path"]: row for row in http["fixtures"]}
    seen = set()
    for row in http["fixtures"]:
        if row["generation"] != 46:
            continue
        fixture = json.loads((ROOT / row["path"]).read_text())
        assert _hash(ROOT / row["path"]) == row["sha256"]
        predecessor = fixture.get("reattested_from")
        if predecessor is None:
            actual = _body(fixture)
            shared = actual.pop("shared_operation_evidence", None)
            assert actual == packet()
            if shared is not None:
                assert registered[shared["path"]]["sha256"] == shared["sha256"]
                primary = json.loads((ROOT / shared["path"]).read_text())
                assert _body(primary) == packet()
            continue
        assert registered[predecessor["path"]]["generation"] == 45
        assert registered[predecessor["path"]]["sha256"] == predecessor["sha256"]
        assert _hash(ROOT / predecessor["path"]) == predecessor["sha256"]
        old = json.loads((ROOT / predecessor["path"]).read_text())
        assert fixture["captured_from"]["source_path"] == old["captured_from"]["source_path"]
        assert _body(fixture) == _body(old)
        seen.add(predecessor["path"])
    # Every preserved family stays literal: its old advertisement remains evidence
    # of that old packet, not a claim that HTTP-46 independently recaptured it.
    assert seen == {row["path"] for row in http["fixtures"] if row["generation"] == 45}


def test_current_generation_attestations_cover_semantic_sources_and_moved_prompt_owner():
    registry = registry_codec.load(ROOT).materialized["contracts"][0]
    generation = registry["declared_range"]["current"]
    proofs = {}
    for row in registry["fixtures"]:
        if row["generation"] == generation:
            fixture = json.loads((ROOT / row["path"]).read_text())
            proof = fixture["captured_from"]
            proofs.setdefault(proof["source_path"], set()).add(proof["source_sha256"])
    for source in registry["source_paths"]:
        assert source["sha256"] == _hash(ROOT / source.get("current_path", source["path"]))
        if source["semantic"]:
            assert source["sha256"] in proofs[source["path"]]
    moved = "plugins/agent-bridge/src/agent_bridge/client_prompt.py"
    assert next(row for row in registry["source_paths"] if row["path"] == moved)["semantic"]
    assert registry["capability_versions"]["startup_progress"] == 46
