"""Every resident and unavailable branch reads bounded slots, not tracking."""

from __future__ import annotations

import argparse
import json
import runpy
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import list_cli, sessions, tracking
from agent_worktrees import status_monitor_cutover as control
from agent_worktrees import worktrees_client as client
from agent_worktrees import worktrees_projection as projection
from agent_worktrees import worktrees_runtime as runtime


def test_fresh_current_dispatch_cannot_acknowledge_bridge_live_refresh(
    tmp_path, monkeypatch, capfd, no_caller_compute,
) -> None:
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "one"}], observed_at=time.time())
    monkeypatch.setattr(client, "read_current", owner.handle)
    monkeypatch.setattr(list_cli.list_cache, "note_demand", lambda *a, **kw: None)
    monkeypatch.setattr(list_cli.cfg, "project_name", lambda: "project")
    monkeypatch.setattr(list_cli.cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(list_cli, "_status_monitor_enabled", lambda: True)
    bridge_source = (
        Path(__file__).resolve().parents[2] / "agent-bridge" / "src"
        / "agent_bridge" / "routes" / "worktree_refresh.py"
    )
    contract = runpy.run_path(str(bridge_source))
    try:
        assert list_cli.cmd_list(argparse.Namespace(
            json=True, fresh=True, tracking_status="all", all=True,
            include_other_platforms=True,
        )) == 0
        raw = capfd.readouterr().out
        assert contract["validate_inventory"](raw) is not None
        with pytest.raises(
            contract["DiscoveryReadError"], match="source-refresh-unsupported",
        ):
            contract["validate_inventory"](raw, fresh=True)
    finally:
        owner.shutdown()


def request(**extra):
    return {"contract_version": 1, "project": "project", **extra}


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("failure", ["invalid-request", "unready", "superseded"])
def test_worktrees_client_decodes_serialized_runtime_failures(tmp_path, version, failure):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    payload = request(contract_version=version)
    if failure == "invalid-request":
        payload.update(worktree_id="one", codename="quiet-knob")
    elif failure == "unready":
        owner.activate()
    else:
        owner.close_admission("superseded")
    try:
        response = json.loads(json.dumps(owner.handle(payload)))
        assert response["ok"] is False
        assert response["contract_version"] == 1
        assert response["error"]["code"] == failure
        expected = (
            projection.ProjectionError
            if failure == "invalid-request"
            else projection.ProjectionUnavailable
        )
        with pytest.raises(expected) as caught:
            client.validate_result(response, payload)
        assert response["error"]["message"] in str(caught.value)
        assert "incompatible" not in str(caught.value)
    finally:
        owner.shutdown()


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("change", [
    {"contract_version": 3},
    {"contract_version": True},
    {"ok": None},
    {"ok": 0},
    {"error": {}},
    {"error": {"code": "", "message": "unready"}},
    {"error": {"code": None, "message": "unready"}},
    {"error": {"code": "unready", "message": ""}},
])
def test_worktrees_client_rejects_malformed_shared_failure(change, version):
    response = {
        "contract_version": 1, "ok": False,
        "error": {"code": "unready", "message": "no publication"},
        **change,
    }
    with pytest.raises(projection.ProjectionUnavailable, match="malformed|incompatible|lacks"):
        client.validate_result(response, request(contract_version=version))


def test_worktrees_client_does_not_admit_v1_success_as_v2(tmp_path):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "one"}], observed_at=1)
    try:
        response = json.loads(json.dumps(owner.handle(request())))
        with pytest.raises(projection.ProjectionUnavailable, match="incompatible"):
            client.validate_result(response, request(contract_version=2))
    finally:
        owner.shutdown()


def test_worktrees_client_rejects_unnegotiated_v2_failure():
    response = {
        "contract_version": 2, "ok": False,
        "error": {"code": "unready", "message": "no publication"},
    }
    with pytest.raises(projection.ProjectionUnavailable, match="incompatible"):
        client.validate_result(response, request())
    with pytest.raises(projection.ProjectionUnavailable, match="unready: no publication"):
        client.validate_result(response, request(contract_version=2))


@pytest.fixture
def no_caller_compute(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("ordinary current read reached caller-side tracking/fact computation")

    monkeypatch.setattr(tracking, "list_records", forbidden)
    monkeypatch.setattr(tracking, "load_record", forbidden)
    monkeypatch.setattr(sessions, "scan_sessions_fast", forbidden)
    monkeypatch.setattr(sessions, "mux_status_many", forbidden)
    monkeypatch.setattr(list_cli, "_classify_records", forbidden)
    monkeypatch.setattr(list_cli, "_refresh_list_record", forbidden)
    monkeypatch.setattr(list_cli, "resolve_worktree_id_by_codename", forbidden)


def test_worktrees_client_real_transport_uses_existing_auth_only(
    tmp_path,
    monkeypatch,
    no_caller_compute,
):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "worktree-one", "codename": "quiet-knob"}], observed_at=1)
    token_path = tmp_path / "token"
    token_path.write_text("fixture-token")
    server = control.ControlServer(
        lambda action, payload: owner.handle(payload),
        token="fixture-token",
    )
    server.start()
    monkeypatch.setattr(
        client.routing,
        "read_active_endpoint",
        lambda *a, **kw: SimpleNamespace(
            base_url=f"http://127.0.0.1:{server.port}",
        ),
    )
    monkeypatch.setattr(control, "control_token_path", lambda runtime_home=None: token_path)
    monkeypatch.setattr(
        control, "load_or_create_control_token", lambda: pytest.fail("reader minted auth")
    )
    try:
        for extra in ({}, {"worktree_id": "one"}, {"codename": "quiet-knob"}):
            result = client.read_current(request(**extra))
            assert result["projection"]["quality"] == "current"
            assert result["worktrees"] == [{"id": "worktree-one", "codename": "quiet-knob"}]
    finally:
        server.close()
        owner.shutdown()


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionError("no endpoint"),
        TimeoutError("live resident timed out"),
        ValueError("malformed reply"),
        projection.ProjectionUnavailable("incompatible"),
    ],
)
def test_worktrees_client_failure_is_advisory_or_explicit_not_compute(
    tmp_path,
    monkeypatch,
    no_caller_compute,
    failure,
):
    monkeypatch.setattr(client.monitor, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(client, "_resident", lambda _: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(
        client.monitor, "_ensure_status_monitor", lambda: pytest.fail("live failure rebooted")
    )
    with pytest.raises(projection.ProjectionUnavailable, match="no usable advisory"):
        client.read_current(request(worktree_id="one"), recover=False)
    store = projection.WorktreesProjection(tmp_path / "worktrees-current.sqlite3")
    store.publish("project", [{"id": "one"}], observed_at=1)
    result = client.read_current(request(worktree_id="one"), recover=False)
    assert result["worktrees"] == [{"id": "one"}]
    assert result["projection"]["quality"] == "advisory"
    assert result["availability"]["status"] == "advisory"
    assert str(failure) in result["availability"]["reason"]


def test_worktrees_client_post_start_crash_and_failed_recovery_remains_advisory(
    tmp_path,
    monkeypatch,
    no_caller_compute,
):
    monkeypatch.setattr(client.monitor, "_aw_runtime_home", lambda: tmp_path)
    store = projection.WorktreesProjection(tmp_path / "worktrees-current.sqlite3")
    store.publish("project", [{"id": "one"}], observed_at=1)
    monkeypatch.setattr(
        client, "_resident", lambda _: (_ for _ in ()).throw(ConnectionError("crashed"))
    )
    monkeypatch.setattr(client.monitor, "_status_monitor_enabled", lambda: True)
    starts = []
    monkeypatch.setattr(
        client.monitor, "_ensure_status_monitor", lambda: starts.append(1) or False
    )
    result = client.read_current(request(worktree_id="one"))
    assert starts == [1]
    assert result["projection"]["quality"] == "advisory"
    assert "usable current roster" in result["availability"]["reason"]


@pytest.mark.parametrize(
    "change",
    [
        {"worktrees": None},
        {"contract_version": True},
        {"ok": False},
        {"projection": {}},
        {"worktrees": [{"id": "one", "resources": []}]},
    ],
)
def test_worktrees_client_rejects_incompatible_or_malformed_live_response(tmp_path, change):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "one"}], observed_at=1)
    result = owner.handle(request())
    result.update(change)
    with pytest.raises(projection.ProjectionUnavailable):
        client.validate_result(result, request())
    owner.shutdown()


@pytest.mark.parametrize(
    "shape",
    [
        {"classify": True},
        {"cache_only": True},
        {"fresh": True},
        {"worktree_id": "one", "refresh": True},
        {"codename": "quiet-knob"},
    ],
)
@pytest.mark.parametrize("explicit_current", [False, True])
def test_worktrees_current_cli_routes_all_read_modes_before_record_resolution(
    monkeypatch,
    capfd,
    no_caller_compute,
    shape,
    explicit_current,
):
    monkeypatch.setattr(
        client,
        "read_current",
        lambda payload: {
            "ok": True,
            "contract_version": 1,
            "worktrees": [{"id": "one"}],
            "projection": {"quality": "current"},
        },
    )
    monkeypatch.setattr(
        client.monitor, "_ensure_status_monitor", lambda: pytest.fail("ordinary read booted")
    )
    monkeypatch.setattr(list_cli.list_cache, "note_demand", lambda *a, **kw: None)
    monkeypatch.setattr(list_cli.cfg, "project_name", lambda: "project")
    monkeypatch.setattr(list_cli.cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(list_cli, "_status_monitor_enabled", lambda: True)
    args = argparse.Namespace(
        current=explicit_current,
        json=True,
        tracking_status="all",
        all=True,
        include_other_platforms=False,
        **shape,
    )
    assert list_cli.cmd_list(args) == 0
    assert json.loads(capfd.readouterr().out)["worktrees"] == [{"id": "one"}]
