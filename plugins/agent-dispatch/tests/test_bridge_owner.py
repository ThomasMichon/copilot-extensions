"""Exact local owner reads exercise discovery, auth construction and HTTP parsing."""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from types import SimpleNamespace

import pytest

from agent_dispatch import bridge_owner, bridge_remote, tracking


class Response:
    def __init__(self, data):
        self.data = data
        self.closed = False

    def read(self):
        return self.data if isinstance(self.data, bytes) else json.dumps(self.data).encode()

    def close(self):
        self.closed = True


@pytest.fixture
def http(monkeypatch, tmp_path):
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("AGENT_BRIDGE_BASE_URL", raising=False)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "auth.yaml").write_text("token: fixture-token", encoding="utf-8")
    state = {"calls": [], "discoveries": [], "responses": [], "generation": 1,
             "pids": [], "pid": 10,
             "health": {"protocol_version": 1, "min_protocol_version": 1}}

    def endpoint(root, *, verify_listener):
        state["discoveries"].append((root, verify_listener))
        state["pids"].append(state["pid"])
        if state.get("missing"):
            return None
        return SimpleNamespace(base_url=f"http://127.0.0.1:{8000 + state['generation']}")

    def urlopen(request, *, timeout):
        assert request.get_method() == "GET"
        scheme = "Be" + "arer "
        assert request.get_header("Authorization") == scheme + "fixture-token"
        assert 0 < timeout <= 3.0
        state["calls"].append(request.full_url)
        if request.full_url.endswith("/health"):
            data = state["health"]
        else:
            handle = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)["handle"][0]
            data = state.get("data", {"session_id": f"s-{state['generation']}-{handle}", "worktree_id": handle})
            if "error" in state:
                status, detail = state["error"]
                raise urllib.error.HTTPError(request.full_url, status, "failure", {},
                    io.BytesIO(json.dumps({"detail": detail}).encode()))
            if state.get("down"):
                raise urllib.error.URLError("fixture unavailable")
        response = Response(data)
        state["responses"].append(response)
        return response

    monkeypatch.setattr("zdd.routing.read_active_endpoint", endpoint)
    monkeypatch.setattr(bridge_remote.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(bridge_owner, "_bounded_open",
        lambda request, *, timeout, deadline: bridge_remote.urllib.request.urlopen(request, timeout=timeout))
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "local")
    monkeypatch.setattr(tracking, "_run_capture", lambda *a, **k: pytest.fail("no process"))
    monkeypatch.setattr(tracking, "agent_bridge_launch_prefix", lambda: pytest.fail("no interpreter lookup"))
    return state


def test_many_owners_deduplicate_only_within_one_wave(http):
    tasks = [{"id": str(i), "status": "started", "owner": f"local/wt-{i % 40}"}
             for i in range(120)]
    out = tracking.enrich_tasks(tasks)
    assert len(http["calls"]) == 80  # one health + one exact lookup per unique owner
    assert len(http["discoveries"]) == 40
    assert all(row[1] for row in http["discoveries"])
    assert all("embodiment" in task for task in out)
    assert all("embodiment" not in task for task in tasks)
    assert all(response.closed for response in http["responses"])
    http["generation"] = 2
    out = tracking.enrich_tasks(tasks)
    assert len(http["calls"]) == 160  # no TTL or persisted session cache
    assert out[0]["embodiment"]["session_id"] == "s-2-wt-0"


@pytest.mark.parametrize("data", [b"", b"not-json", [], {}, {"session_id": ""},
    {"session_id": "s", "worktree_id": "other"}, {"worktree_id": "wt"}])
def test_bad_metadata_is_unknown_not_absent(http, data, caplog):
    http["data"] = data
    assert tracking.liveness_verdict("wt", owner_session_id="s") == tracking.UNKNOWN
    assert "liveness unknown" in caplog.text
    assert "fixture-token" not in caplog.text


@pytest.mark.parametrize("error", [(401, "denied"), (403, "denied"),
    (404, "Not Found"), (500, "unavailable")])
def test_failed_read_is_unknown(http, error):
    http["error"] = error
    assert tracking.liveness_verdict("wt", owner_session_id="s") == tracking.UNKNOWN


def test_documented_missing_owner_is_gone_only_with_captured_identity(http):
    http["error"] = (404, "no live session for handle")
    assert tracking.liveness_verdict("wt", owner_session_id="s") == tracking.GONE
    assert tracking.liveness_verdict("wt", owner_session_id=None) == tracking.UNKNOWN
    assert tracking.resolve_live_session("wt") is None


@pytest.mark.parametrize("mode", ["missing", "down"])
def test_missing_stale_or_down_service_is_unknown(http, mode):
    http[mode] = True
    assert tracking.liveness_verdict("wt", owner_session_id="s") == tracking.UNKNOWN
    assert len(http["calls"]) == (0 if mode == "missing" else 2)


@pytest.mark.parametrize("health", [b"broken", [], {"protocol_version": "bad"},
    b'{"protocol_version": 1e309, "min_protocol_version": 1}',
    b'{"protocol_version": 50, "min_protocol_version": -1e309}',
    {"protocol_version": 50, "min_protocol_version": 2}])
def test_unavailable_contract_is_unknown(http, health):
    http["health"] = health
    assert tracking.liveness_verdict("wt", owner_session_id="s") == tracking.UNKNOWN
    assert len(http["calls"]) == 1


def test_listener_and_session_replacement_are_reread(http):
    assert tracking.liveness_verdict("wt", owner_session_id="s-1-wt") == tracking.LIVE
    http["generation"] = 2
    http["pid"] = 20
    assert tracking.liveness_verdict("wt", owner_session_id="s-1-wt") == tracking.GONE
    assert tracking.liveness_verdict("wt", owner_session_id="s-2-wt") == tracking.LIVE
    assert ":8001/" in http["calls"][0] and ":8002/" in http["calls"][2]
    assert http["pids"] == [10, 20, 20]


def test_explicit_caller_url_is_preserved(http, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_BASE_URL", "http://localhost:8010/")
    assert tracking.resolve_live_session("wt")
    assert http["discoveries"] == []
    assert all(url.startswith("http://localhost:8010/") for url in http["calls"])


def test_owner_admission_failure_has_no_http_or_process(http, monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "invalid-receipt")
    assert tracking.liveness_verdict("wt", owner_session_id="s") == tracking.UNKNOWN
    assert http["calls"] == [] and http["discoveries"] == []


def test_scoped_install_without_context_refuses(http, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "marketplaces" / "cell" / "plugins" / "agent-dispatch"))
    assert tracking.liveness_verdict("wt", owner_session_id="s") == tracking.UNKNOWN
    assert http["calls"] == []


@pytest.mark.parametrize("corrupt", [None, "inactive", "foreign"])
def test_same_cell_uses_existing_receipt_and_governance_validation(http, monkeypatch, tmp_path, corrupt):
    from agent_dispatch import peer_launch
    from tests.test_procutil import FIRST_MARKETPLACE_ID, _make_namespaced_context

    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    own = _make_namespaced_context(cell)
    peer = _make_namespaced_context(cell, plugin_id="agent-bridge")
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(own.parent))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own))
    monkeypatch.setattr(peer_launch, "_peer_python", lambda *a: pytest.fail("no runtime resolver"))
    if corrupt:
        data = json.loads(peer.read_text(encoding="utf-8"))
        if corrupt == "inactive":
            data["state"] = "inactive"
        else:
            data["marketplaceId"] = "foreign"
        peer.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(bridge_remote.RemoteBridgeUnavailable, match="context refused"):
            bridge_owner._config_dir()
    else:
        assert bridge_owner._config_dir() == peer.parent
        (peer.parent / "auth.yaml").write_text("token: fixture-token", encoding="utf-8")
        assert tracking.resolve_live_session("wt")
        assert http["discoveries"] == [(peer.parent, True)]
    assert len(http["calls"]) == (0 if corrupt else 2)


def test_read_failure_after_headers_is_unknown(http, monkeypatch):
    monkeypatch.setattr(Response, "read", lambda self: (_ for _ in ()).throw(TimeoutError()))
    assert tracking.liveness_verdict("wt", owner_session_id="s") == tracking.UNKNOWN


def test_remote_transport_is_not_deduplicated_or_replaced(http, monkeypatch):
    calls = []
    monkeypatch.setattr(tracking, "_satellite_entry_for", lambda _: None)
    monkeypatch.setattr(bridge_remote.LocalBridgeRemoteClient, "resolve_live_session",
        lambda self, host, wt, **k: calls.append((host, wt)) or {"session_id": "remote", "worktree_id": wt})
    tasks = [{"status": "started", "owner": "peer/wt"}] * 2
    assert all("embodiment" in t for t in tracking.enrich_tasks(tasks))
    assert calls == [("peer", "wt"), ("peer", "wt")]
    assert http["calls"] == []


def test_health_404_cannot_masquerade_as_missing_owner(http, monkeypatch):
    def urlopen(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 404, "failure", {},
            io.BytesIO(json.dumps({"detail": "no live session for handle"}).encode()))
    monkeypatch.setattr(bridge_remote.urllib.request, "urlopen", urlopen)
    assert tracking.liveness_verdict("wt", owner_session_id="s") == tracking.UNKNOWN


@pytest.mark.parametrize("stage", ["health", "metadata"])
def test_truncated_read_is_unknown_and_enrichment_degrades(http, monkeypatch, stage, caplog):
    from http.client import IncompleteRead

    original = Response.read

    def read(response):
        health = isinstance(response.data, dict) and "protocol_version" in response.data
        if health == (stage == "health"):
            raise IncompleteRead(b"partial", 10)
        return original(response)

    monkeypatch.setattr(Response, "read", read)
    assert tracking.liveness_verdict("wt", owner_session_id="s") == tracking.UNKNOWN
    requests = 1 if stage == "health" else 2
    assert len(http["calls"]) == requests
    tasks = [{"id": str(i), "status": "started", "owner": "local/wt"} for i in range(12)]
    assert tracking.enrich_tasks(tasks) == tasks
    assert len(http["calls"]) == requests * 2
    assert all(response.closed for response in http["responses"])
    assert "liveness unknown" in caplog.text


@pytest.mark.parametrize("token", ["bad\nheader", "non-latin-\u2603"])
def test_invalid_auth_header_is_unknown_with_real_validation(http, monkeypatch, token):
    from http.client import HTTPConnection
    from pathlib import Path
    import os
    import yaml

    root = Path(os.environ["AGENT_BRIDGE_CONFIG_DIR"])
    (root / "auth.yaml").write_text(yaml.safe_dump({"token": token}), encoding="utf-8")

    def validate_header(request, **kwargs):
        connection = HTTPConnection("127.0.0.1", 1)
        try:
            connection.putrequest("GET", "/health")
            connection.putheader("Authorization", request.get_header("Authorization"))
            pytest.fail("Invalid header must fail before connecting")
        finally:
            connection.close()

    monkeypatch.setattr(bridge_remote.urllib.request, "urlopen", validate_header)
    assert tracking.liveness_verdict("wt", owner_session_id="s") == tracking.UNKNOWN
    tasks = [{"status": "started", "owner": "local/wt"}]
    assert tracking.enrich_tasks(tasks) == tasks


def test_installed_owner_reads_reuse_code_but_reread_authority(http, monkeypatch, tmp_path):
    from agent_dispatch import peer_launch
    from tests.test_procutil import FIRST_MARKETPLACE_ID, _make_namespaced_context

    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    own = _make_namespaced_context(cell)
    peer = _make_namespaced_context(cell, plugin_id="agent-bridge")
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(own.parent))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own))
    (peer.parent / "auth.yaml").write_text("token: fixture-token", encoding="utf-8")
    counts = {"context": 0, "governance": 0}
    active = peer_launch._active_context
    governance = peer_launch._governance

    def read_context(*args, **kwargs):
        counts["context"] += 1
        return active(*args, **kwargs)

    def read_governance(*args, **kwargs):
        counts["governance"] += 1
        return governance(*args, **kwargs)

    monkeypatch.setattr(peer_launch, "_active_context", read_context)
    monkeypatch.setattr(peer_launch, "_governance", read_governance)
    monkeypatch.setattr(peer_launch, "_load_primitive", lambda *a: pytest.fail("no code reload"))
    # Count the exact reader work independently of the display-only wave budget.
    # Wave deduplication and elapsed-budget behavior have their own controls.
    for i in range(40):
        assert tracking.resolve_live_session(f"wt-{i}")
    assert counts == {"context": 80, "governance": 80}
    assert len(http["calls"]) == 80
    # Changed authority must refuse on the very next lookup, not after a TTL.
    value = json.loads(own.read_text(encoding="utf-8"))
    value["state"] = "inactive"
    own.write_text(json.dumps(value), encoding="utf-8")
    assert tracking.liveness_verdict("wt-0", owner_session_id="s") == tracking.UNKNOWN
    assert len(http["calls"]) == 80


@pytest.mark.parametrize("corrupt", ["inline", "root", "peer"])
def test_cached_validator_code_preserves_owner_and_source_refusals(http, monkeypatch, tmp_path, corrupt):
    from tests.test_procutil import FIRST_MARKETPLACE_ID, _make_namespaced_context

    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    own = _make_namespaced_context(cell)
    peer = _make_namespaced_context(cell, plugin_id="agent-bridge")
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(own.parent))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own))
    if corrupt == "inline":
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT",
            json.dumps({"installReceipt": str(own), "marketplaceId": "foreign"}))
    elif corrupt == "root":
        monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(own.parent / "foreign"))
    else:
        value = json.loads(peer.read_text(encoding="utf-8"))
        value["marketplaceId"] = "foreign"
        peer.write_text(json.dumps(value), encoding="utf-8")
    assert tracking.liveness_verdict("wt", owner_session_id="s") == tracking.UNKNOWN
    assert http["calls"] == []


def test_packaged_owner_validation_matches_native_boundary(http, monkeypatch, tmp_path):
    from agent_dispatch import _installation_context as primitive, peer_launch
    from tests.test_procutil import FIRST_MARKETPLACE_ID, _make_namespaced_context

    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    own = _make_namespaced_context(cell)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(own.parent))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own))
    expected = peer_launch.validate_owner("agent-dispatch", own.parent, str(own))
    actual = bridge_owner._owner_context(primitive, peer_launch, own.parent, str(own))
    assert actual == expected
