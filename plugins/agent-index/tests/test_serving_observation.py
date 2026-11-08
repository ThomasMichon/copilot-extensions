from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from agent_index import serving_observation as subject


@pytest.fixture
def serving(monkeypatch, tmp_path):
    route = {
        "bind": "127.0.0.1", "port": 43123, "pid": 1234,
        "version": "1.2.3+repair.abc", "generation": 4,
        "process_start_time": "123456",
    }
    table = {"active": route, "previous": None}
    health = {
        "status": "ok", "plugin": "agent-index", "promoted": True,
        "version": route["version"], "pid": route["pid"], "instanceToken": "fixture",
    }
    calls = []

    @contextmanager
    def process(pid, start_time):
        assert (pid, start_time) == (route["pid"], route["process_start_time"])
        yield SimpleNamespace(wait=lambda _timeout: False)

    def status(url, **kwargs):
        calls.append((url, kwargs))
        return health.copy()

    monkeypatch.setattr(subject.routing, "read_table", lambda *_args, **_kwargs: table)
    monkeypatch.setattr(subject.diagnostics, "process_start_time", lambda _pid: route["process_start_time"])
    monkeypatch.setattr(subject.process_exit, "open_process", process)
    monkeypatch.setattr(subject, "_owned_service_status", status)
    return SimpleNamespace(root=tmp_path, route=route, table=table, health=health, calls=calls)


def test_missing_route_is_unknown_without_starting_or_creating_state(tmp_path):
    root = tmp_path / "not-created"
    result = subject.observe_local_serving(root)
    assert result["state"] == "unknown"
    assert result["generation"] is None
    assert not root.exists()


def test_serving_route_is_bound_to_native_process_and_health(serving):
    result = subject.observe_local_serving(serving.root)
    assert result["state"] == "ready"
    assert result["generation"] == serving.route["version"]
    assert result["pid"] == serving.route["pid"]
    assert len(serving.calls) == 2
    assert serving.calls[1][1]["expected_instance_token"] == "fixture"


@pytest.mark.parametrize(
    "change",
    [
        {"pid": None},
        {"generation": 0},
        {"process_start_time": None},
        {"version": "../foreign"},
        {"bind": "192.0.2.10"},
    ],
)
def test_unowned_or_nonlocal_route_is_unknown(serving, change):
    serving.route.update(change)
    assert subject.observe_local_serving(serving.root)["state"] == "unknown"


@pytest.mark.parametrize(
    "change",
    [
        {"status": "draining"},
        {"promoted": False},
        {"instanceToken": None},
        {"plugin": "foreign"},
    ],
)
def test_unready_or_foreign_health_is_unknown(serving, change):
    serving.health.update(change)
    assert subject.observe_local_serving(serving.root)["state"] == "unknown"


def test_route_change_during_observation_is_unknown(serving, monkeypatch):
    changed = {"active": {**serving.route, "generation": 5}, "previous": None}
    tables = iter((serving.table, changed))
    monkeypatch.setattr(subject.routing, "read_table", lambda *_args, **_kwargs: next(tables))
    assert subject.observe_local_serving(serving.root)["state"] == "unknown"


def test_instance_token_change_during_observation_is_unknown(serving, monkeypatch):
    states = iter((serving.health, {**serving.health, "instanceToken": "replacement"}))
    monkeypatch.setattr(subject, "_owned_service_status", lambda *_args, **_kwargs: next(states))
    assert subject.observe_local_serving(serving.root)["state"] == "unknown"


def test_process_exit_or_identity_change_is_unknown(serving, monkeypatch):
    monkeypatch.setattr(subject.diagnostics, "process_start_time", lambda _pid: "different")
    assert subject.observe_local_serving(serving.root)["state"] == "unknown"


def test_process_identity_change_after_health_is_unknown(serving, monkeypatch):
    identities = iter((serving.route["process_start_time"], "replacement"))
    monkeypatch.setattr(subject.diagnostics, "process_start_time", lambda _pid: next(identities))
    assert subject.observe_local_serving(serving.root)["state"] == "unknown"


def test_pending_cutover_is_unknown_and_evidence_is_untouched(serving):
    path = subject.breadcrumb.breadcrumb_path(serving.root)
    path.write_text("pending", encoding="utf-8")
    assert subject.observe_local_serving(serving.root)["state"] == "unknown"
    assert path.read_text(encoding="utf-8") == "pending"
    assert not serving.calls


@pytest.mark.parametrize("cutover_on_call", [1, 2])
def test_cutover_starting_during_health_is_unknown(serving, monkeypatch, cutover_on_call):
    status = subject._owned_service_status
    path = subject.breadcrumb.breadcrumb_path(serving.root)

    def start_cutover(*args, **kwargs):
        health = status(*args, **kwargs)
        if len(serving.calls) == cutover_on_call:
            path.write_text("pending", encoding="utf-8")
        return health

    monkeypatch.setattr(subject, "_owned_service_status", start_cutover)
    result = subject.observe_local_serving(serving.root)
    assert result["state"] == "unknown"
    assert result["reason"] == "cutover-pending"
    assert path.read_text(encoding="utf-8") == "pending"


def test_unreadable_route_is_unknown_without_exposing_diagnostics(serving, monkeypatch):
    def denied(*_args, **_kwargs):
        raise PermissionError("private diagnostic")

    monkeypatch.setattr(subject.routing, "read_table", denied)
    result = subject.observe_local_serving(serving.root)
    assert result["state"] == "unknown"
    assert "private" not in json.dumps(result)


def test_unconfigured_context_does_not_claim_a_local_host_is_inactive(tmp_path):
    env = dict(os.environ, AGENT_INDEX_HOME=str(tmp_path), AGENT_INDEX_ROUTING_DIR=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-m", "agent_index.serving_observation", "--json"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode != 0
    assert json.loads(result.stdout)["state"] == "unknown"
    assert not list(tmp_path.iterdir())
