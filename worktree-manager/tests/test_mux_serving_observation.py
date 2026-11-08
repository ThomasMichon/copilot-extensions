from __future__ import annotations

import json
from pathlib import Path

import pytest

from worktree_manager import mux_daemon_cutover, mux_serving_observation

pytestmark = pytest.mark.guard


def test_inactive_observation_does_not_create_credentials(tmp_path: Path, capsys) -> None:
    assert mux_serving_observation.observe(tmp_path) == {"state": "inactive", "generation": None}
    assert not mux_daemon_cutover.control_token_path(tmp_path).exists()
    assert mux_serving_observation.main(["--root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema": mux_serving_observation.SCHEMA, "version": 1,
        "ok": True, "state": "inactive", "generation": None,
    }


@pytest.mark.parametrize("marker", [
    ("mux-daemon.lock",), ("mux-daemon.spawn.lock",),
    ("mux-daemon-routing", "cutover.lock"), ("mux-daemon-routing", "active.lock"),
])
def test_unrouted_rendezvous_is_not_assumed_inactive(tmp_path: Path, marker) -> None:
    path = tmp_path.joinpath(*marker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("unconfirmed", encoding="utf-8")
    with pytest.raises(mux_serving_observation.ServingObservationError):
        mux_serving_observation.observe(tmp_path)
    assert not mux_daemon_cutover.control_token_path(tmp_path).exists()


def test_unrecognized_route_file_is_not_assumed_inactive(tmp_path: Path) -> None:
    directory = mux_daemon_cutover.routing_dir(tmp_path)
    directory.mkdir()
    mux_serving_observation.routing.routing_table_path(directory).write_text(
        '{"unrecognized": true}', encoding="utf-8",
    )
    with pytest.raises(mux_serving_observation.ServingObservationError):
        mux_serving_observation.observe(tmp_path)


def test_explicit_control_credential_never_creates_one(tmp_path, monkeypatch):
    monkeypatch.setattr(
        mux_daemon_cutover, "load_or_create_control_token",
        lambda *_a, **_k: pytest.fail("unexpected credential creation"),
    )
    client = mux_daemon_cutover.ControlClient(
        "http://127.0.0.1:43123", root=tmp_path, token="fixture-control",
    )
    assert client.token == "fixture-control"
    assert not mux_daemon_cutover.control_token_path(tmp_path).exists()


@pytest.fixture
def serving(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    token_file = mux_daemon_cutover.control_token_path(root)
    token_file.write_text("fixture-control", encoding="utf-8")
    route = {
        "bind": "127.0.0.1", "port": 43123, "pid": 42, "generation": 3,
        "version": "1.2.3+repair." + "a" * 32, "process_start_time": "17",
    }
    table = {"active": route, "previous": None}
    health = {"status": "ready", "active": True, "runtime_generation": route["version"]}
    monkeypatch.setattr(mux_serving_observation.routing, "read_table", lambda *_a, **_k: table)
    monkeypatch.setattr(mux_serving_observation.diagnostics, "process_start_time", lambda _pid: "17")
    monkeypatch.setattr(
        mux_daemon_cutover, "load_or_create_control_token",
        lambda *_a, **_k: pytest.fail("observation attempted credential creation"),
    )

    class Client:
        def __init__(self, base_url, *, root, timeout, token):
            assert base_url == "http://127.0.0.1:43123"
            assert token == "fixture-control"

        def health(self):
            return dict(health)

    monkeypatch.setattr(mux_daemon_cutover, "ControlClient", Client)
    return root, table, health


def test_observation_confirms_active_generation_and_native_identity(serving):
    root, table, _health = serving
    assert mux_serving_observation.observe(root) == {
        "state": "ready", "generation": table["active"]["version"],
    }
    assert mux_daemon_cutover.control_token_path(root).read_text(encoding="utf-8") == "fixture-control"


def test_stale_previous_record_does_not_mask_current_serving_owner(serving):
    root, table, _health = serving
    table["previous"] = {"unreadable": True}
    assert mux_serving_observation.observe(root) == {
        "state": "ready", "generation": table["active"]["version"],
    }


@pytest.mark.parametrize("change", ["passive", "draining", "generation", "birth", "route"])
def test_unconfirmed_serving_state_is_not_reported_ready(serving, monkeypatch, change):
    root, table, health = serving
    if change == "passive":
        health["active"] = False
    elif change == "draining":
        health["status"] = "draining"
    elif change == "generation":
        health["runtime_generation"] = "other"
    elif change == "birth":
        monkeypatch.setattr(mux_serving_observation.diagnostics, "process_start_time", lambda _pid: "18")
    else:
        observations = iter((table, {"active": {**table["active"], "port": 43124}}))
        monkeypatch.setattr(
            mux_serving_observation.routing, "read_table", lambda *_a, **_k: next(observations),
        )
    with pytest.raises(mux_serving_observation.ServingObservationError):
        mux_serving_observation.observe(root)


def test_missing_control_credential_never_gets_created(serving):
    root, _table, _health = serving
    mux_daemon_cutover.control_token_path(root).unlink()
    with pytest.raises(mux_serving_observation.ServingObservationError):
        mux_serving_observation.observe(root)
    assert not mux_daemon_cutover.control_token_path(root).exists()


def test_changed_control_credential_refuses_observation(serving, monkeypatch):
    root, _table, health = serving

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def health(self):
            mux_daemon_cutover.control_token_path(root).write_text("changed", encoding="utf-8")
            return health

    monkeypatch.setattr(mux_daemon_cutover, "ControlClient", Client)
    with pytest.raises(mux_serving_observation.ServingObservationError):
        mux_serving_observation.observe(root)


def test_cli_sanitizes_owner_errors(serving, monkeypatch, capsys):
    root, _table, _health = serving

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def health(self):
            raise RuntimeError("private owner diagnostic")

    monkeypatch.setattr(mux_daemon_cutover, "ControlClient", Client)
    assert mux_serving_observation.main(["--root", str(root)]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "schema": mux_serving_observation.SCHEMA, "version": 1,
        "ok": False, "state": "unknown",
    }
    assert "private owner diagnostic" not in captured.out + captured.err
