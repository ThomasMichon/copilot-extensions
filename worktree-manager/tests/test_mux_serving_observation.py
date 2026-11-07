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


@pytest.mark.parametrize("phase", ["health", "release"])
def test_observation_bounds_fragmented_health_and_release(tmp_path, phase):
    import os
    import socket
    import threading
    import time
    from zdd import diagnostics, routing

    stop = threading.Event()
    errors = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(3)
        host, port = listener.getsockname()
        pid = os.getpid()
        birth = diagnostics.process_start_time(pid)
        assert birth
        routing.publish_active(
            mux_daemon_cutover.routing_dir(tmp_path), bind=host, port=port,
            pid=pid, version="1.2.3", process_start_time=birth,
        )
        mux_daemon_cutover.control_token_path(tmp_path).write_text("fixture-control", encoding="utf-8")
        health = json.dumps({
            "version": 1,
            "result": {
                "status": "ready", "active": True, "runtime_generation": "1.2.3",
                "pid": pid, "process_start_time": birth,
            },
        }).encode() + b"\n"

        def serve():
            try:
                with listener.accept()[0] as connection:
                    connection.settimeout(3)
                    assert connection.recv(65536)
                    if phase == "release":
                        connection.sendall(health)
                    else:
                        connection.sendall(b'{"version":1,')
                        deadline = time.monotonic() + 1.5
                        while time.monotonic() < deadline and not stop.wait(0.03):
                            connection.sendall(b" ")
                if phase == "release":
                    with listener.accept()[0] as connection:
                        connection.settimeout(3)
                        assert connection.recv(65536)
                        connection.sendall(b'{"version":1,')
                        deadline = time.monotonic() + 1.5
                        while time.monotonic() < deadline and not stop.wait(0.03):
                            connection.sendall(b" ")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        try:
            started = time.monotonic()
            if phase == "health":
                with pytest.raises(mux_serving_observation.ServingObservationError):
                    mux_serving_observation.observe(tmp_path, timeout=0.2)
            else:
                assert mux_serving_observation.observe(tmp_path, timeout=0.2) == {
                    "state": "ready", "generation": "1.2.3",
                }
            assert time.monotonic() - started < 1.0
        finally:
            stop.set()
            worker.join(timeout=3)
        assert not worker.is_alive()
        assert not errors


@pytest.mark.parametrize("elapsed", [0.75, 1.1])
def test_control_release_uses_only_the_remaining_completion_budget(tmp_path, monkeypatch, elapsed):
    from types import SimpleNamespace
    from work_coalescing_singleton import client

    clock = SimpleNamespace(value=10.0)
    monkeypatch.setattr(mux_daemon_cutover, "time", SimpleNamespace(monotonic=lambda: clock.value))
    releases = []

    def request(*_args, **kwargs):
        assert kwargs["completion_timeout_s"] == pytest.approx(1.0)
        clock.value += elapsed
        return {"status": "ready"}

    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr(client, "release", lambda *_args, **kwargs: releases.append(kwargs))
    owner = mux_daemon_cutover.ControlClient(
        "http://127.0.0.1:43123", root=tmp_path, token="fixture-control",
        timeout=5.0, completion_timeout_s=1.0,
    )
    if elapsed < 1:
        assert owner.health() == {"status": "ready"}
        assert releases[0]["timeout"] == pytest.approx(0.25)
        assert releases[0]["completion_timeout_s"] == pytest.approx(0.25)
    else:
        with pytest.raises(TimeoutError, match="completion deadline"):
            owner.health()
        assert not releases


@pytest.fixture
def serving(tmp_path, monkeypatch):
    from agent_procutil import _daemon_instance_identity

    root = tmp_path.resolve()
    token_file = mux_daemon_cutover.control_token_path(root)
    token_file.write_text("fixture-control", encoding="utf-8")
    route = {
        "bind": "127.0.0.1", "port": 43123, "pid": 42, "generation": 3,
        "version": "1.2.3+repair." + "a" * 32, "process_start_time": "17",
    }
    table = {"active": route, "previous": None}
    health = {
        "status": "ready", "active": True, "runtime_generation": route["version"],
        "pid": 42, "process_start_time": "17",
    }
    monkeypatch.setattr(mux_serving_observation.routing, "read_table", lambda *_a, **_k: table)
    monkeypatch.setattr(mux_serving_observation.diagnostics, "process_start_time", lambda _pid: "17")
    monkeypatch.setattr(_daemon_instance_identity, "birth_identity", lambda _pid: "fixture-live")
    monkeypatch.setattr(
        mux_daemon_cutover, "load_or_create_control_token",
        lambda *_a, **_k: pytest.fail("observation attempted credential creation"),
    )
    monkeypatch.setattr(mux_serving_observation.mux_daemon, "read_lock_data", lambda _path: {
        "pid": 42, "start_time": "17", "manager_mux_endpoint": "127.0.0.1:43123",
        "manager_mux_token": "fixture-control",
    })

    class Client:
        def __init__(self, base_url, *, root, timeout, token, completion_timeout_s):
            assert base_url == "http://127.0.0.1:43123"
            assert token == "fixture-control"
            assert completion_timeout_s == timeout

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


@pytest.mark.parametrize("change", ["pid", "birth", "partial", "legacy", "legacy-lock"])
def test_observation_binds_health_to_routed_owner(serving, monkeypatch, change):
    root, table, health = serving
    if change == "pid":
        health["pid"] = 43
    elif change == "birth":
        health["process_start_time"] = "18"
    elif change == "partial":
        health.pop("process_start_time")
    else:
        health.pop("pid")
        health.pop("process_start_time")
        if change == "legacy-lock":
            monkeypatch.setattr(mux_serving_observation.mux_daemon, "read_lock_data", lambda _path: {
                "pid": 43, "start_time": "17", "manager_mux_endpoint": "127.0.0.1:43123",
                "manager_mux_token": "fixture-control",
            })
    if change == "legacy":
        assert mux_serving_observation.observe(root) == {
            "state": "ready", "generation": table["active"]["version"],
        }
    else:
        with pytest.raises(mux_serving_observation.ServingObservationError):
            mux_serving_observation.observe(root)


@pytest.mark.parametrize("exit_phase", ["before", "after-health"])
def test_observation_rejects_exited_owner_with_readable_birth(serving, monkeypatch, exit_phase):
    from agent_procutil import _daemon_instance_identity

    root, _table, health = serving
    exited = exit_phase == "before"
    calls = []

    def birth(pid):
        if exited:
            raise ProcessLookupError(pid)
        return "fixture-live"

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def health(self):
            nonlocal exited
            calls.append("health")
            exited = True
            return health

    monkeypatch.setattr(_daemon_instance_identity, "birth_identity", birth)
    monkeypatch.setattr(mux_daemon_cutover, "ControlClient", Client)
    with pytest.raises(mux_serving_observation.ServingObservationError):
        mux_serving_observation.observe(root)
    assert calls == ([] if exit_phase == "before" else ["health"])


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


@pytest.mark.parametrize("change", ["none", "selection", "incomplete", "foreign"])
def test_reconcile_requires_selected_complete_physical_manager(tmp_path, monkeypatch, change):
    import os
    import sys
    from types import SimpleNamespace
    from worktree_manager import mux_serving_reconcile, self_install

    root = tmp_path / "manager"
    slot = root / "versions" / "1.2.3"
    module = slot / "Lib" / "site-packages" / "worktree_manager" / "mux_serving_reconcile.py"
    module.parent.mkdir(parents=True)
    module.write_text("", encoding="utf-8")
    (slot / ".runtime-bootstrap").mkdir()
    python = slot / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    python.parent.mkdir()
    python.write_text("", encoding="utf-8")
    (root / "current-version").write_text("other" if change == "selection" else slot.name, encoding="utf-8")
    if change == "foreign":
        module = tmp_path / "foreign.py"
        module.write_text("", encoding="utf-8")
    runtime = SimpleNamespace(
        current_version=lambda _root: (root / "current-version").read_text(encoding="utf-8"),
        is_complete=lambda _root, _version: change != "incomplete",
        slot_python=lambda _root, _version: python,
    )
    monkeypatch.setattr(self_install, "_runtime_installer", lambda: SimpleNamespace(runtime=runtime))
    monkeypatch.setattr(sys, "prefix", str(slot))
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr(mux_serving_reconcile, "__file__", str(module))
    if change == "none":
        assert mux_serving_reconcile.require_selected_generation(root).samefile(slot)
    else:
        with pytest.raises(ValueError):
            mux_serving_reconcile.require_selected_generation(root)


def test_reconcile_inactive_manager_never_starts_a_daemon(tmp_path, monkeypatch, capsys):
    from worktree_manager import mux_serving_reconcile

    slot = tmp_path / "slot"
    slot.mkdir()
    monkeypatch.setattr(mux_serving_reconcile, "require_selected_generation", lambda _root: slot)
    monkeypatch.setattr(mux_serving_observation, "observe", lambda *_args, **_kwargs: {
        "state": "inactive", "generation": None,
    })
    monkeypatch.setattr(
        mux_daemon_cutover, "activate_after_update", lambda **_kwargs: pytest.fail("unexpected activation"),
    )
    assert mux_serving_reconcile.main(["--root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema": mux_serving_reconcile.SCHEMA, "version": 1, "ok": True,
        "state": "inactive", "generation": None,
    }


@pytest.mark.parametrize("selection_changes", [False, True])
def test_reconcile_confirms_selected_manager_after_cutover(tmp_path, monkeypatch, capsys, selection_changes):
    from worktree_manager import mux_serving_reconcile

    slot = tmp_path / "versions" / "1.2.3"
    slot.mkdir(parents=True)
    raw = {
        "bind": "127.0.0.1", "port": 43123, "pid": 42, "generation": 3,
        "version": "1.2.2", "process_start_time": "17",
    }
    changed = False
    observations = []

    def selected(_root):
        if changed:
            raise ValueError("selection changed")
        return slot

    def observe(*_args, **_kwargs):
        nonlocal changed
        observations.append(True)
        if len(observations) == 1:
            return {"state": "ready", "generation": "1.2.2"}
        changed = selection_changes
        return {"state": "ready", "generation": slot.name}

    def activate(**kwargs):
        assert kwargs["expected_active"] == raw
        assert kwargs["lock_timeout"] == 0.5
        kwargs["verify_selected"]()
        return {"action": "cutover", "result": {"ok": True}}

    monkeypatch.setattr(mux_serving_reconcile, "require_selected_generation", selected)
    monkeypatch.setattr(mux_serving_observation, "observe", observe)
    monkeypatch.setattr(mux_daemon_cutover.routing, "read_table", lambda *_args, **_kwargs: {"active": raw})
    monkeypatch.setattr(mux_daemon_cutover, "activate_after_update", activate)
    code = mux_serving_reconcile.main(["--root", str(tmp_path), "--timeout", "0.5"])
    result = json.loads(capsys.readouterr().out)
    if selection_changes:
        assert code == 1 and result["state"] == "unknown"
        assert result["reason"] == "selection-unavailable"
    else:
        assert code == 0 and result["state"] == "ready" and result["generation"] == slot.name


def test_guarded_reconcile_refuses_exited_incumbent_before_spawn(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from zdd import routing

    raw = {
        "bind": "127.0.0.1", "port": 43123, "pid": 42, "generation": 3,
        "version": "1.2.2", "process_start_time": "17",
    }
    table = {"active": raw, "previous": None}
    calls = []
    observations = iter((
        {"state": "ready", "generation": "1.2.2"},
        mux_serving_observation.ServingObservationError("incumbent exited"),
    ))

    def observe(*_args, **_kwargs):
        value = next(observations)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(mux_serving_observation, "observe", observe)
    monkeypatch.setattr(mux_serving_observation, "_existing_token", lambda _root: ("fixture", (1,)))
    monkeypatch.setattr(mux_daemon_cutover.routing, "read_table", lambda *_args, **_kwargs: table)
    monkeypatch.setattr(mux_daemon_cutover.routing, "publication_witness", lambda *_args, **_kwargs: "fixture")
    monkeypatch.setattr(mux_daemon_cutover, "routed_endpoint", lambda _root: routing.Endpoint.from_dict(raw))
    monkeypatch.setattr(mux_daemon_cutover, "_already_at_version", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(mux_daemon_cutover, "_acquire_cutover_lock", lambda *_args, **_kwargs: SimpleNamespace(
        release=lambda: calls.append("release"),
    ))
    monkeypatch.setattr(mux_daemon_cutover, "pick_free_port", lambda: 43124)
    monkeypatch.setattr(mux_daemon_cutover, "spawn_passive", lambda *_args, **_kwargs: calls.append("spawn"))
    monkeypatch.setattr(
        mux_daemon_cutover, "load_or_create_control_token", lambda *_args, **_kwargs: pytest.fail("mutation"),
    )
    with pytest.raises(mux_serving_observation.ServingObservationError, match="incumbent exited"):
        mux_daemon_cutover.activate_after_update(
            root=tmp_path, slot=tmp_path / "slot", version="1.2.3",
            expected_active=raw, verify_selected=lambda: None, lock_timeout=0.2,
        )
    assert calls == ["release"]


def test_manager_cutover_lock_wait_respects_deadline(tmp_path, monkeypatch):
    from types import SimpleNamespace

    clock = SimpleNamespace(value=0.0)
    clock.monotonic = lambda: clock.value
    clock.sleep = lambda seconds: setattr(clock, "value", clock.value + seconds)
    monkeypatch.setattr(mux_daemon_cutover, "time", clock)
    monkeypatch.setattr(mux_daemon_cutover, "_try_lock_file_once", lambda _handle: False)
    with pytest.raises(TimeoutError):
        mux_daemon_cutover._acquire_cutover_lock(tmp_path, timeout_s=0.03, poll_s=0.2)
    assert clock.value <= 0.03


def test_candidate_health_cannot_succeed_after_its_deadline(tmp_path, monkeypatch):
    from types import SimpleNamespace

    clock = SimpleNamespace(value=0.0)
    clock.monotonic = lambda: clock.value
    clock.sleep = lambda seconds: setattr(clock, "value", clock.value + seconds)
    monkeypatch.setattr(mux_daemon_cutover, "time", clock)

    def slow_health(*_args, **_kwargs):
        clock.value = 1.0
        return True

    monkeypatch.setattr(mux_daemon_cutover, "_health_check", slow_health)
    assert not mux_daemon_cutover._await_health(
        "127.0.0.1", 43124, root=tmp_path, timeout=0.1,
        generation="1.2.3", expected_pid=43, expected_birth="18",
    )


@pytest.mark.parametrize(
    "change", ["none", "route", "selection", "credential", "exited", "candidate-exited", "candidate-reused"],
)
def test_guarded_reconcile_fences_publication(tmp_path, monkeypatch, change):
    from types import SimpleNamespace
    from zdd import diagnostics, routing

    directory = mux_daemon_cutover.routing_dir(tmp_path)
    altered, running, calls = False, True, []

    def birth(pid):
        if pid == 43 and altered and change == "candidate-reused":
            return "20"
        return {42: "17", 43: "18", 44: "19"}[pid]

    monkeypatch.setattr(diagnostics, "process_start_time", birth)
    old = routing.publish_active(
        directory, bind="127.0.0.1", port=43123, pid=42, version="1.2.2",
        process_start_time="17",
    )
    raw = routing.read_table(directory, strict=True)["active"]
    def selected():
        if altered and change == "selection":
            raise ValueError("selection changed")

    def token(_root):
        return ("fixture", (2 if altered and change == "credential" else 1,))

    def terminate():
        nonlocal running
        running = False
        calls.append("terminate")

    incoming = SimpleNamespace(
        pid=43, poll=lambda: None if running and not (altered and change == "candidate-exited") else 0,
        terminate=terminate,
        wait=lambda timeout: calls.append("wait") or 0,
    )
    monkeypatch.setattr(mux_serving_observation, "observe", lambda *_args, **_kwargs: {
        "state": "ready", "generation": "1.2.2",
    })
    monkeypatch.setattr(mux_serving_observation, "_existing_token", token)
    monkeypatch.setattr(mux_serving_observation, "_live_owner", lambda _owner: not (altered and change == "exited"))
    monkeypatch.setattr(mux_daemon_cutover, "routed_endpoint", lambda _root: old)
    monkeypatch.setattr(mux_daemon_cutover, "_already_at_version", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(mux_daemon_cutover, "_acquire_cutover_lock", lambda *_args, **_kwargs: SimpleNamespace(
        release=lambda: calls.append("release"),
    ))
    monkeypatch.setattr(mux_daemon_cutover, "pick_free_port", lambda: 43124)
    monkeypatch.setattr(mux_daemon_cutover, "spawn_passive", lambda *_args, **_kwargs: incoming)
    monkeypatch.setattr(mux_daemon_cutover, "_await_health", lambda *_args, **_kwargs: True)
    original_publish = routing.publish_active_with_previous_guarded

    def publish(*args, **kwargs):
        nonlocal altered
        altered = True
        if change == "route":
            routing.publish_active(
                directory, bind="127.0.0.1", port=43125, pid=44, version="other",
                process_start_time="19",
            )
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(routing, "publish_active_with_previous_guarded", publish)
    options = dict(
        root=tmp_path, slot=tmp_path / "slot", version="1.2.3",
        expected_active=raw, verify_selected=selected, lock_timeout=0.2, health_timeout=0.2,
    )
    if change == "none":
        result = mux_daemon_cutover.activate_after_update(**options)
        assert result["result"]["ok"] is True
        assert routing.read_table(directory, strict=True)["active"]["pid"] == incoming.pid
        assert calls == ["release"]
    elif change == "candidate-reused":
        with pytest.raises(RuntimeError, match="candidate publication or exit is unconfirmed"):
            mux_daemon_cutover.activate_after_update(**options)
        assert routing.read_table(directory, strict=True)["active"]["pid"] != incoming.pid
        assert calls == ["release"]
    else:
        with pytest.raises(routing.ActivePublicationRefused):
            mux_daemon_cutover.activate_after_update(**options)
        assert routing.read_table(directory, strict=True)["active"]["pid"] != incoming.pid
        assert calls == (["release"] if change == "candidate-exited" else ["terminate", "wait", "release"])


@pytest.mark.parametrize("candidate_active", [False, True])
def test_guarded_reconcile_refuses_untrusted_candidate_health(tmp_path, monkeypatch, candidate_active):
    from types import SimpleNamespace
    from zdd import diagnostics, routing

    directory = mux_daemon_cutover.routing_dir(tmp_path)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: {42: "17", 43: "18"}[pid])
    old = routing.publish_active(
        directory, bind="127.0.0.1", port=43123, pid=42, version="1.2.2",
        process_start_time="17",
    )
    raw = routing.read_table(directory, strict=True)["active"]
    running, calls = True, []

    def terminate():
        nonlocal running
        running = False
        calls.append("terminate")

    incoming = SimpleNamespace(
        pid=43, poll=lambda: None if running else 0, terminate=terminate,
        wait=lambda timeout: calls.append("wait") or 0,
    )

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def health(self):
            return {
                "status": "ready", "runtime_generation": "1.2.3",
                "pid": 43 if candidate_active else 44,
                "process_start_time": "18" if candidate_active else "19",
                "active": candidate_active,
            }

    monkeypatch.setattr(mux_serving_observation, "observe", lambda *_args, **_kwargs: {
        "state": "ready", "generation": "1.2.2",
    })
    monkeypatch.setattr(mux_serving_observation, "_existing_token", lambda _root: ("fixture", (1,)))
    monkeypatch.setattr(mux_serving_observation, "_live_owner", lambda _owner: True)
    monkeypatch.setattr(mux_daemon_cutover, "ControlClient", Client)
    monkeypatch.setattr(mux_daemon_cutover, "routed_endpoint", lambda _root: old)
    monkeypatch.setattr(mux_daemon_cutover, "_already_at_version", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(mux_daemon_cutover, "_acquire_cutover_lock", lambda *_args, **_kwargs: SimpleNamespace(
        release=lambda: calls.append("release"),
    ))
    monkeypatch.setattr(mux_daemon_cutover, "pick_free_port", lambda: 43124)
    monkeypatch.setattr(mux_daemon_cutover, "spawn_passive", lambda *_args, **_kwargs: incoming)
    options = dict(
        root=tmp_path, slot=tmp_path / "slot", version="1.2.3",
        expected_active=raw, verify_selected=lambda: None, lock_timeout=0.2, health_timeout=0.05,
    )
    if candidate_active:
        with pytest.raises(RuntimeError, match="candidate activity"):
            mux_daemon_cutover.activate_after_update(**options)
    else:
        assert mux_daemon_cutover.activate_after_update(**options)["result"]["ok"] is False
    assert routing.read_table(directory, strict=True)["active"]["pid"] == old.pid
    assert calls == (["release"] if candidate_active else ["terminate", "wait", "release"])


@pytest.mark.parametrize("publication", ["uncertain", "unverified", "unpublished", "history-refused"])
def test_guarded_reconcile_cleans_up_only_unpublished_candidates(tmp_path, monkeypatch, publication):
    import time
    from types import SimpleNamespace
    from zdd import diagnostics, publication_history, routing, routing_persistence

    directory = mux_daemon_cutover.routing_dir(tmp_path)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "17" if pid == 42 else "18")
    old = routing.publish_active(
        directory, bind="127.0.0.1", port=43123, pid=42, version="1.2.2",
        process_start_time="17",
    )
    raw = routing.read_table(directory, strict=True)["active"]
    calls = []
    running = True

    def terminate():
        nonlocal running
        with pytest.raises(routing.RoutingLockTimeout):
            with routing.routing_lock(directory, deadline=time.monotonic()):
                pass
        calls.append("terminate")
        running = False

    incoming = SimpleNamespace(
        pid=43, poll=lambda: None if running else 0, terminate=terminate,
        wait=lambda timeout: calls.append("wait") or 0,
    )
    monkeypatch.setattr(mux_serving_observation, "observe", lambda *_args, **_kwargs: {
        "state": "ready", "generation": "1.2.2",
    })
    monkeypatch.setattr(mux_serving_observation, "_existing_token", lambda _root: ("fixture", (1,)))
    monkeypatch.setattr(mux_serving_observation, "_live_owner", lambda _owner: True)
    monkeypatch.setattr(mux_daemon_cutover, "routed_endpoint", lambda _root: old)
    monkeypatch.setattr(mux_daemon_cutover, "_already_at_version", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(mux_daemon_cutover, "_acquire_cutover_lock", lambda *_args, **_kwargs: SimpleNamespace(
        release=lambda: calls.append("release"),
    ))
    monkeypatch.setattr(mux_daemon_cutover, "pick_free_port", lambda: 43124)
    monkeypatch.setattr(mux_daemon_cutover, "spawn_passive", lambda *_args, **_kwargs: incoming)
    monkeypatch.setattr(mux_daemon_cutover, "_await_health", lambda *_args, **_kwargs: (
        publication in {"uncertain", "history-refused"}
    ))
    original_publish = routing.publish_active_with_previous_guarded

    def uncertain(*args, **kwargs):
        original_publish(*args, **kwargs)
        raise routing_persistence.PublicationCommitUncertain("fixture")

    if publication == "uncertain":
        monkeypatch.setattr(routing, "publish_active_with_previous_guarded", uncertain)
        monkeypatch.setattr(routing, "read_active_endpoint", lambda *_args, **_kwargs: None)
    elif publication in {"unverified", "history-refused"}:
        monkeypatch.setattr(publication_history, "never_published", lambda *_args, **_kwargs: False)
    options = dict(
        root=tmp_path, slot=tmp_path / "slot", version="1.2.3",
        expected_active=raw, verify_selected=lambda: None, lock_timeout=0.2,
        health_timeout=0.2,
    )
    if publication == "unpublished":
        result = mux_daemon_cutover.activate_after_update(**options)
        assert result["result"]["ok"] is False
        assert calls == ["terminate", "wait", "release"]
        assert routing.read_table(directory, strict=True)["active"]["pid"] == old.pid
    else:
        with pytest.raises((OSError, RuntimeError)):
            mux_daemon_cutover.activate_after_update(**options)
        assert "terminate" not in calls
        if publication == "uncertain":
            assert routing.read_table(directory, strict=True)["active"]["pid"] == incoming.pid
        elif publication == "history-refused":
            assert routing.read_table(directory, strict=True)["active"]["pid"] == old.pid
