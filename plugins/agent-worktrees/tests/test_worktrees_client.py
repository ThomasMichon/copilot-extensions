"""Every resident and unavailable branch reads bounded slots, not tracking."""

from __future__ import annotations

import argparse
import json
import runpy
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import list_cli, sessions, tracking
from agent_worktrees import status_monitor_cutover as control
from agent_worktrees import worktrees_client as client
from agent_worktrees import worktrees_projection as projection
from agent_worktrees import worktrees_runtime as runtime
from agent_worktrees.worktrees_projection_index import Selection
from agent_worktrees.worktrees_refresh import record_identity


def test_fresh_current_dispatch_cannot_acknowledge_bridge_live_refresh(
    tmp_path, monkeypatch, capfd, no_caller_compute,
) -> None:
    monkeypatch.setattr(list_cli.cfg, "detect_platform", lambda: "windows")
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "one"}], observed_at=time.time())
    monkeypatch.setattr(client, "read_current", lambda payload, **kw: owner.handle(payload))
    monkeypatch.setattr(list_cli.list_cache, "note_demand", lambda *a, **kw: None)
    monkeypatch.setattr(list_cli.cfg, "project_name", lambda: "project")
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


@pytest.mark.parametrize("surface", ["resident", "saved", "fallback", "passive"])
def test_semantic_archive_from_older_selection_is_refused_without_recompute(
    tmp_path, monkeypatch, capfd, no_caller_compute, surface,
):
    path = tmp_path / "worktrees-current.sqlite3"
    store = projection.WorktreesProjection(path)
    store.publish("project", [{
        "id": "archived", "status": "archived", "checkout_present": True,
    }], observed_at=1)
    payload = request(include_missing=False)
    try:
        if surface != "resident":
            original = client.read_advisory
            monkeypatch.setattr(
                client, "read_advisory",
                lambda db, project, **kw: original(db, project, include_missing=True),
            )
            if surface == "saved":
                with pytest.raises(projection.ProjectionUnavailable, match="selection"):
                    client.saved_result(payload, "legacy saved selection", db_path=path)
            else:
                monkeypatch.setattr(
                    client, "_resident",
                    lambda _: (_ for _ in ()).throw(ConnectionRefusedError("owner absent")),
                )
                monkeypatch.setattr(client.monitor, "_aw_runtime_home", lambda: tmp_path)
                monkeypatch.setattr(
                    client.monitor, "_ensure_status_monitor", lambda: pytest.fail("passive recovery"),
                )
                if surface == "fallback":
                    with pytest.raises(projection.ProjectionUnavailable, match="selection"):
                        client.read_current(payload, recover=False)
                else:
                    monkeypatch.setattr(client.cfg, "project_name", lambda: "project")
                    monkeypatch.setattr(
                        list_cli.list_cache, "note_demand", lambda *a, **k: pytest.fail("passive demand"),
                    )
                    assert list_cli.cmd_list(argparse.Namespace(
                        json=True, passive=True, tracking_status="all", all=False,
                        include_other_platforms=True,
                    )) == 1
                    result = json.loads(capfd.readouterr().out)
                    assert "selection" in result["error"]
                    assert "worktrees" not in result
        else:
            result = {"ok": True, "contract_version": 1, **store.read("project")}
            with pytest.raises(projection.ProjectionUnavailable, match="selection"):
                client.validate_result(result, payload)
    finally:
        store.close()


def test_explicit_refresh_crosses_authenticated_resident_and_waits_for_its_publication(
    tmp_path, monkeypatch, capfd, no_caller_compute,
):
    wake = threading.Event()
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3", wake=wake)
    owner.activate()
    owner.publish("project", [{"id": "one", "session_bound_live": True}], observed_at=1)
    wake.clear()
    server = control.ControlServer(
        lambda action, payload: owner.health() if action == "health" else owner.handle(payload),
        token="fixture-token",
    )
    server.start()
    connection = control.ControlClient(f"http://127.0.0.1:{server.port}", token="fixture-token")
    monkeypatch.setattr(client, "_control_client", lambda: connection)
    monkeypatch.setattr(client.cfg, "project_name", lambda: "project")
    monkeypatch.setattr(list_cli.list_cache, "note_demand", lambda *a, **kw: None)
    errors = []

    def produce():
        try:
            assert wake.wait(3)
            pending = owner.refreshes.pending("project")
            assert len(pending) == 1 and pending[0][1] == Selection(worktree_id="one")
            owner.publish(
                "project", [{"id": "one"}], observed_at=2,
                refresh_requests=[(pending[0][0], record_identity({"id": "one"}))],
            )
        except BaseException as error:
            errors.append(error)
            owner.close_admission("closed")

    worker = threading.Thread(target=produce, daemon=True)
    worker.start()
    try:
        assert client.emit_current(argparse.Namespace(
            json=True, current=True, refresh=True, worktree_id="one",
            tracking_status="all", all=True, include_other_platforms=True,
        )) == 0
        result = json.loads(capfd.readouterr().out)
        assert result["worktrees"] == [{"id": "one", "platform": client.cfg.detect_platform()}]
        assert result["point_refresh"]["revision"] == result["projection"]["revision"] == 2
        worker.join(3)
        assert not worker.is_alive() and not errors
    finally:
        server.close()
        owner.shutdown()


def test_explicit_refresh_refuses_an_old_resident_before_demand_or_reconciliation(
    monkeypatch, capfd, no_caller_compute,
):
    monkeypatch.setattr(client, "read_health", lambda: {"capabilities": [runtime.CAPABILITY]})
    monkeypatch.setattr(client.cfg, "project_name", lambda: "project")
    monkeypatch.setattr(list_cli.list_cache, "note_demand", lambda *a, **kw: pytest.fail("demand"))
    monkeypatch.setattr(client, "read_current", lambda *a, **kw: pytest.fail("unnegotiated refresh"))
    assert client.emit_current(argparse.Namespace(
        refresh=True, worktree_id="one", tracking_status="all",
    )) == 1
    assert "worktrees-refresh-v1" in json.loads(capfd.readouterr().out)["error"]


def test_unavailable_point_refresh_returns_its_terminal_failure_without_repeating_work(
    tmp_path, monkeypatch, no_caller_compute,
):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    query = request(worktree_id="one", refresh_id="a" * 32)
    owner.handle(query)
    owner.refreshes.reject("a" * 32, "process observation unavailable", code="refresh-unavailable")
    monkeypatch.setattr(
        client, "_control_client",
        lambda: SimpleNamespace(worktrees_read=lambda payload: owner.handle(payload)),
    )
    monkeypatch.setattr(client.time, "sleep", lambda _: pytest.fail("retried a failed refresh"))
    try:
        with pytest.raises(projection.ProjectionUnavailable, match="refresh-unavailable"):
            client.read_current(query, wait=True)
        assert owner.refreshes.pending("project") == ()
    finally:
        owner.shutdown()


@pytest.mark.parametrize("changed", [
    "missing", "id", "generation", "revision", "unpublished",
    "record", "incarnation", "path", "empty",
])
def test_refresh_result_requires_its_own_same_generation_publication_receipt(tmp_path, changed):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    query = request(worktree_id="one", refresh_id="a" * 32)
    try:
        owner.handle(query)
        row = {"id": "one", "creation_nonce": "first", "path": "checkout"}
        owner.publish(
            "project", [row], observed_at=1,
            refresh_requests=[(query["refresh_id"], record_identity(row))],
        )
        result = owner.handle(query)
        assert client.validate_result(result, query) is result
        if changed == "missing":
            result.pop("point_refresh")
        elif changed == "empty":
            result["worktrees"] = []
            result["projection"]["selected_count"] = 0
        else:
            key, value = {
                "id": ("request_id", "b" * 32),
                "generation": ("generation", "b" * 32),
                "revision": ("revision", True),
                "unpublished": ("revision", result["projection"]["revision"] + 1),
                "record": ("worktree_id", "two-one"),
                "incarnation": ("creation_nonce", "second"),
                "path": ("path", "other-checkout"),
            }[changed]
            result["point_refresh"] = {**result["point_refresh"], key: value}
        with pytest.raises(projection.ProjectionUnavailable, match="requested point refresh"):
            client.validate_result(result, query)
    finally:
        owner.shutdown()


def test_waiting_read_starts_once_and_waits_past_old_recovery_budget(
    monkeypatch, capfd, no_caller_compute,
):
    ready = {"projection": {"quality": "current"}, "worktrees": [{"id": "one"}]}
    results = iter([
        ConnectionRefusedError("owner absent"),
        *[client._ResidentUnready("project cleanup pending") for _ in range(12)],
        {**ready, "availability": {"status": "stale", "reason": "producer busy"}},
        ready,
    ])
    clock = [0.0]
    starts = []

    def resident(payload):
        assert payload == request()
        assert capfd.readouterr().out == ""
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(client, "_resident", resident)
    monkeypatch.setattr(client.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(client.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    monkeypatch.setattr(client.monitor, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(client.monitor, "_ensure_status_monitor", lambda: starts.append(1) or True)
    monkeypatch.setattr(client, "read_advisory", lambda *a, **k: pytest.fail("returned saved rows"))
    assert client.read_current(request(), wait=True) is ready
    assert clock[0] > client.RECOVERY_WAIT
    assert starts == [1]


@pytest.mark.parametrize("failure", [
    projection.ProjectionUnavailable("authentication refused"),
    ValueError("malformed response"),
    KeyboardInterrupt(),
])
def test_waiting_read_preserves_protocol_failure_and_cancellation(monkeypatch, failure):
    monkeypatch.setattr(client, "_resident", lambda _: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(client.time, "sleep", lambda _: pytest.fail("retried a terminal failure"))
    with pytest.raises(type(failure)) as caught:
        client.read_current(request(), wait=True)
    assert caught.value is failure


@pytest.mark.parametrize("failure", [
    OSError("read denied"), ValueError("malformed JSON"), RecursionError("nested response"),
])
def test_json_list_emits_terminal_reader_errors_as_json(monkeypatch, capfd, failure):
    monkeypatch.setattr(client.cfg, "project_name", lambda: "project")
    monkeypatch.setattr(list_cli.list_cache, "note_demand", lambda *a, **k: None)
    monkeypatch.setattr(list_cli, "_status_monitor_enabled", lambda: True)

    def failed(*args, **kwargs):
        raise failure

    monkeypatch.setattr(client, "read_current", failed)
    assert list_cli.cmd_list(argparse.Namespace(
        json=True, tracking_status="all", all=True, include_other_platforms=True,
    )) == 1
    captured = capfd.readouterr()
    assert json.loads(captured.out)["error"] == str(failure)
    assert captured.err == ""


@pytest.mark.parametrize("stream,follow", [(False, False), (True, False), (False, True)])
def test_json_list_missing_control_token_fails_without_recovery(
    tmp_path, monkeypatch, capfd, stream, follow,
):
    token = tmp_path / "missing-token"
    monkeypatch.setattr(client.cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(
        client, "resident_endpoint", lambda _: SimpleNamespace(base_url="http://127.0.0.1:1"),
    )
    monkeypatch.setattr(control, "control_token_path", lambda _: token)
    monkeypatch.setattr(client.cfg, "project_name", lambda: "project")
    monkeypatch.setattr(list_cli.list_cache, "note_demand", lambda *a, **k: None)
    monkeypatch.setattr(list_cli, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(client.time, "sleep", lambda _: pytest.fail("retried a missing credential"))
    monkeypatch.setattr(
        client.monitor, "_ensure_status_monitor", lambda: pytest.fail("restarted a live owner"),
    )
    assert list_cli.cmd_list(argparse.Namespace(
        json=True, stream=stream, follow=follow, tracking_status="all", all=True,
        include_other_platforms=True,
    )) == 1
    captured = capfd.readouterr()
    result = json.loads(captured.out)
    message = result["error"]["message"] if follow else result["error"]
    assert token.name in message
    assert "worktrees" not in result
    if follow:
        assert result["type"] == "error"
        assert result["error"]["retry"] is False
    assert not token.exists()
    assert captured.err == ""


def test_waiting_read_retries_transport_timeout_without_restarting_live_owner(monkeypatch):
    ready = {"projection": {"quality": "current"}, "worktrees": []}
    replies = iter([TimeoutError("slow live owner"), ready])

    def resident(_request):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(client, "_resident", resident)
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        client.monitor, "_ensure_status_monitor", lambda: pytest.fail("restarted a slow live owner"),
    )
    assert client.read_current(request(), wait=True) is ready


def test_waiting_read_throttles_absent_owner_recovery_and_stays_cancellable(monkeypatch):
    clock = [0.0]
    starts = []
    monkeypatch.setattr(client, "_resident", lambda _: (_ for _ in ()).throw(ConnectionError()))
    monkeypatch.setattr(client.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(client.monitor, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(
        client.monitor, "_ensure_status_monitor", lambda: starts.append(clock[0]) or False,
    )

    def sleep(delay):
        clock[0] += delay
        if clock[0] > 12:
            raise KeyboardInterrupt

    monkeypatch.setattr(client.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        client.read_current(request(), wait=True)
    assert 2 <= len(starts) <= 3
    assert all(later - earlier >= 5 for earlier, later in zip(starts, starts[1:]))


@pytest.mark.parametrize("stream", [False, True])
def test_json_list_waits_for_real_cold_resident_publication(
    tmp_path, monkeypatch, capfd, no_caller_compute, stream,
):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    token = tmp_path / "token"
    token.write_text("fixture-token")
    server = control.ControlServer(lambda action, payload: owner.handle(payload), token="fixture-token")
    endpoint = []
    starts = []
    published = threading.Event()

    def publish():
        owner.publish("project", [{"id": "one"}], observed_at=time.time())
        published.set()

    publisher = threading.Timer(0.8, publish)

    def start():
        starts.append(1)
        server.start()
        endpoint.append(SimpleNamespace(base_url=f"http://127.0.0.1:{server.port}"))
        publisher.start()
        return True

    monkeypatch.setattr(client.cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(client.routing, "read_active_endpoint", lambda *a, **kw: endpoint[0] if endpoint else None)
    monkeypatch.setattr(control, "control_token_path", lambda *args: token)
    monkeypatch.setattr(client.monitor, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(client.monitor, "_ensure_status_monitor", start)
    monkeypatch.setattr(list_cli.list_cache, "note_demand", lambda *a, **kw: None)
    monkeypatch.setattr(list_cli.cfg, "project_name", lambda: "project")
    monkeypatch.setattr(list_cli, "_status_monitor_enabled", lambda: True)
    args = argparse.Namespace(
        json=True, stream=stream, tracking_status="all", all=True,
        include_other_platforms=True,
    )
    try:
        assert list_cli.cmd_list(args) == 0
        assert published.wait(1)
        assert starts == [1]
        raw = capfd.readouterr().out
        frames = [json.loads(line) for line in raw.splitlines()] if stream else [json.loads(raw)]
        if stream:
            assert [frame["type"] for frame in frames] == ["begin", "worktree", "done"]
            assert all(frame["projection"]["quality"] == "current" for frame in frames)
        else:
            assert len(frames) == 1
            assert frames[0]["worktrees"] == [{"id": "one", "platform": client.cfg.detect_platform()}]
            assert frames[0]["projection"]["quality"] == "current"
        assert all(not frame.get("availability") for frame in frames)
    finally:
        publisher.cancel()
        if publisher.ident is not None:
            publisher.join(5)
        server.close()
        owner.shutdown()


def test_waiting_json_list_has_no_output_before_callers_process_timeout(tmp_path):
    from agent_procutil.owned_process import run_caller_owned

    marker = tmp_path / "waiting"
    source = Path(__file__).resolve().parents[1] / "src"
    program = """
import argparse,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from agent_worktrees import list_cli,worktrees_client as client
client.cfg.project_name=lambda:'project'
list_cli.list_cache.note_demand=lambda *a,**k:None
list_cli._status_monitor_enabled=lambda:True
def unready(request):
    Path(sys.argv[2]).touch()
    raise client._ResidentUnready('project cleanup pending')
client._resident=unready
raise SystemExit(list_cli.cmd_list(argparse.Namespace(
    json=True,tracking_status='all',all=True,include_other_platforms=True,
)))
"""
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run_caller_owned(
            [sys.executable, "-I", "-B", "-c", program, str(source), str(marker)],
            timeout=4, max_stdout_bytes=4096, max_stderr_bytes=4096,
        )
    assert marker.is_file(), "the caller timed out before reaching the list wait"
    assert not caught.value.output
    assert not caught.value.stderr


def test_cold_read_starts_owner_and_returns_saved_db_without_waiting_for_live_probe(
    tmp_path, monkeypatch, no_caller_compute,
):
    monkeypatch.setattr(client.cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(client.monitor, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(client.monitor, "_status_monitor_enabled", lambda: True)
    starts = []
    monkeypatch.setattr(client.monitor, "_ensure_status_monitor", lambda: starts.append(1) or True)
    monkeypatch.setattr(client, "_resident",
                        lambda _: (_ for _ in ()).throw(ConnectionRefusedError("stopped")))
    monkeypatch.setattr(client.time, "sleep", lambda _: pytest.fail("waited behind live refresh"))
    store = projection.WorktreesProjection(tmp_path / "worktrees-current.sqlite3")
    store.publish("project", [{"id": "saved", "dirty": 9}], observed_at=1)
    result = client.read_current(request())
    assert starts == [1]
    assert result["projection"]["quality"] == "advisory"
    assert result["projection"]["observed_at"] == 1
    assert result["worktrees"] == [{"id": "saved", "dirty": 9}]
    assert result["availability"]["status"] == "advisory"


@pytest.mark.parametrize("saved", [True, False])
def test_passive_json_list_never_starts_or_awaits_an_absent_resident(
    tmp_path, monkeypatch, capfd, no_caller_compute, saved,
):
    """``list --json --passive`` (the ``fleet`` host read) makes one resident
    attempt; with the resident absent it returns the saved roster labeled
    advisory, or a JSON error when none is saved. It never starts, recovers
    or waits for the status-monitor, and records no list demand."""
    monkeypatch.setattr(client.monitor, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(client.monitor, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(list_cli, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(list_cli.cfg, "project_name", lambda: "project")
    monkeypatch.setattr(
        client.monitor, "_ensure_status_monitor", lambda: pytest.fail("started the resident"),
    )
    monkeypatch.setattr(client.time, "sleep", lambda _: pytest.fail("waited for the resident"))
    monkeypatch.setattr(
        list_cli.list_cache, "note_demand", lambda *a, **k: pytest.fail("recorded demand"),
    )
    monkeypatch.setattr(client, "_resident",
                        lambda _: (_ for _ in ()).throw(ConnectionRefusedError("absent")))
    if saved:
        store = projection.WorktreesProjection(tmp_path / "worktrees-current.sqlite3")
        store.publish("project", [{"id": "saved"}], observed_at=1)

    rc = list_cli.cmd_list(argparse.Namespace(
        json=True, passive=True, tracking_status="all", all=True, include_other_platforms=True,
    ))

    result = json.loads(capfd.readouterr().out)
    if saved:
        assert rc == 0
        assert result["worktrees"] == [{"id": "saved"}]
        assert result["availability"]["status"] == "advisory"
        assert result["projection"]["observed_at"] == 1
    else:
        assert rc == 1
        assert "worktrees" not in result
        assert "absent" in result["error"]
        assert not (tmp_path / "worktrees-current.sqlite3").exists()


def test_passive_list_rejects_follow(capfd):
    assert list_cli.cmd_list(argparse.Namespace(
        json=True, passive=True, follow=True, tracking_status="all",
    )) == 1
    assert "--passive cannot be combined" in json.loads(capfd.readouterr().out)["error"]


def _serve_passive_resident(tmp_path, monkeypatch, handler):
    """Serve ``handler`` on the real control transport for a passive ``cmd_list``."""
    token_path = tmp_path / "token"
    token_path.write_text("fixture-token")
    server = control.ControlServer(lambda action, payload: handler(payload), token="fixture-token")
    server.start()
    monkeypatch.setattr(
        client.routing, "read_active_endpoint",
        lambda *a, **kw: SimpleNamespace(base_url=f"http://127.0.0.1:{server.port}"),
    )
    monkeypatch.setattr(control, "control_token_path", lambda runtime_home=None: token_path)
    monkeypatch.setattr(client.monitor, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(client.monitor, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(list_cli, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(list_cli.cfg, "project_name", lambda: "project")
    monkeypatch.setattr(
        client.monitor, "_ensure_status_monitor", lambda: pytest.fail("started the resident"),
    )
    monkeypatch.setattr(client.time, "sleep", lambda _: pytest.fail("waited for the resident"))
    monkeypatch.setattr(
        list_cli.list_cache, "note_demand", lambda *a, **k: pytest.fail("recorded demand"),
    )
    return server


def _passive_list(capfd):
    rc = list_cli.cmd_list(argparse.Namespace(
        json=True, passive=True, tracking_status="all", all=True, include_other_platforms=True,
    ))
    return rc, json.loads(capfd.readouterr().out)


def test_passive_list_against_a_running_resident_records_no_producer_demand(
    tmp_path, monkeypatch, capfd, no_caller_compute,
):
    """A passive read of an unpublished project reaches the running resident
    with the demand-free marker, so it neither renews scan demand nor wakes the
    producer; an ordinary read of the same project still does both."""
    wakes = []
    owner = runtime.WorktreesRuntime(
        tmp_path / "worktrees-current.sqlite3", wake=SimpleNamespace(set=lambda: wakes.append(1)),
    )
    owner.activate()
    wakes.clear()
    seen = []

    def handle(payload):
        seen.append(dict(payload))
        return owner.handle(payload)

    server = _serve_passive_resident(tmp_path, monkeypatch, handle)
    try:
        rc, result = _passive_list(capfd)
        assert rc == 1 and "worktrees" not in result
        assert seen and all(payload["passive"] is True for payload in seen)
        assert wakes == [] and owner.demand_projects() == set()

        with pytest.raises(projection.ProjectionUnavailable):
            client.read_current(request(), recover=False)
        assert wakes == [1] and owner.demand_projects() == {"project"}
    finally:
        server.close()
        owner.shutdown()


def test_passive_list_falls_back_to_saved_roster_when_an_older_resident_rejects_it(
    tmp_path, monkeypatch, capfd, no_caller_compute,
):
    """A resident that predates the demand-free marker rejects unknown request
    fields; the passive read returns the labeled advisory saved roster instead
    of failing or retrying as a demand read."""
    owner = runtime.WorktreesRuntime(tmp_path / "worktrees-current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "saved"}], observed_at=1)
    old_fields = runtime._FIELDS - {"passive"}
    seen = []

    def older_resident(payload):
        seen.append(dict(payload))
        if set(payload) - old_fields:
            return owner._failure("invalid-request", "invalid current-roster request fields")
        return owner.handle(payload)

    server = _serve_passive_resident(tmp_path, monkeypatch, older_resident)
    try:
        rc, result = _passive_list(capfd)
        assert rc == 0
        assert result["worktrees"] == [{"id": "saved", "platform": client.cfg.detect_platform()}]
        assert result["availability"]["status"] == "advisory"
        assert "rejected the passive read" in result["availability"]["reason"]
        assert result["projection"]["quality"] == "advisory"
        assert len(seen) == 1 and owner.demand_projects() == set()

        ordinary = client.read_current(request(), recover=False)
        assert ordinary["projection"]["quality"] == "current"
        assert "passive" not in seen[-1]
    finally:
        server.close()
        owner.shutdown()


@pytest.mark.parametrize("birth", [None, "12345"])
def test_confirmed_dead_route_recovers_before_attempting_transport(
    tmp_path, monkeypatch, no_caller_compute, birth,
):
    from agent_worktrees import locks
    from zdd import process_exit

    stale = SimpleNamespace(pid=12345, process_start_time=birth, base_url="http://127.0.0.1:1")
    monkeypatch.setattr(client.cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(client.monitor, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(client.routing, "read_active_endpoint", lambda *a, **kw: stale)
    seen = []
    monkeypatch.setattr(locks, "lock_is_live", lambda identity: seen.append(identity["pid"]) or False)
    monkeypatch.setattr(
        process_exit, "observe_original_exit", lambda pid, token: seen.append(pid) or True,
    )
    monkeypatch.setattr(
        control, "ControlClient", lambda *a, **kw: pytest.fail("dialed confirmed dead route"),
    )
    starts = []
    monkeypatch.setattr(client.monitor, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(client.monitor, "_ensure_status_monitor", lambda: starts.append(1) or False)
    store = projection.WorktreesProjection(tmp_path / "worktrees-current.sqlite3")
    store.publish("project", [{"id": "one"}], observed_at=1)
    result = client.read_current(request())
    assert starts == [1]
    assert seen == [12345]
    assert result["projection"]["quality"] == "advisory"


@pytest.mark.parametrize("pid", [None, 12345])
@pytest.mark.parametrize("birth", [None, "12345"])
def test_live_or_unknown_route_timeout_never_restarts_owner(tmp_path, monkeypatch, pid, birth):
    from agent_worktrees import locks
    from zdd import process_exit

    endpoint = SimpleNamespace(pid=pid, process_start_time=birth, base_url="http://127.0.0.1:1")
    monkeypatch.setattr(client.cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(client.monitor, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(client.routing, "read_active_endpoint", lambda *a, **kw: endpoint)
    monkeypatch.setattr(locks, "lock_is_live", lambda _: True)
    monkeypatch.setattr(
        process_exit, "observe_original_exit",
        lambda *a: (_ for _ in ()).throw(PermissionError("native evidence unavailable")),
    )
    token = tmp_path / "token"
    token.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(control, "control_token_path", lambda _root: token)
    monkeypatch.setattr(
        control.ControlClient, "worktrees_read",
        lambda *a: (_ for _ in ()).throw(TimeoutError("live peer timed out")),
    )
    monkeypatch.setattr(
        client.monitor, "_ensure_status_monitor", lambda: pytest.fail("restarted live/unknown owner"),
    )
    with pytest.raises(projection.ProjectionUnavailable, match="live peer timed out"):
        client.read_current(request())


@pytest.mark.skipif(sys.platform not in {"win32", "linux"}, reason="native original-exit platforms")
def test_route_owner_exit_and_birth_mismatch_use_actual_process_identity(tmp_path, monkeypatch):
    from agent_procutil import no_window_kwargs
    from agent_worktrees import locks, status_monitor_reap_stale, status_monitor_runtime as monitor

    process = subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), "-c", "import time; time.sleep(30)"],
        **no_window_kwargs(),
    )
    try:
        birth = locks.process_start_time(process.pid)
        assert birth is not None
        lock = tmp_path / "monitor.lock"
        lock.write_text(json.dumps({
            "pid": process.pid, "start_time": birth, "prefix": sys.prefix, "mux": True,
        }), encoding="utf-8")
        monkeypatch.setattr(monitor, "_monitor_lock_path", lambda: lock)
        monkeypatch.setattr(monitor, "_core_helper", lambda _name, local: local)
        monkeypatch.setattr(monitor.status_updater_cli, "_runtime_superseded", lambda **kw: False)
        monkeypatch.setattr(status_monitor_reap_stale, "current_runtime_python", lambda: sys.executable)
        starts = []
        monkeypatch.setattr(monitor, "_spawn_detached", lambda argv: starts.append(argv) or True)
        assert monitor._ensure_status_monitor() is True
        assert starts == []
        endpoint = SimpleNamespace(pid=process.pid, process_start_time=birth)
        monkeypatch.setattr(client.routing, "read_active_endpoint", lambda *a, **kw: endpoint)
        assert client.resident_endpoint(tmp_path) is endpoint
        endpoint.process_start_time = str(int(birth) + 1)
        with pytest.raises(ConnectionError, match="exited or was replaced"):
            client.resident_endpoint(tmp_path)
        assert monitor._ensure_status_monitor() is True
        assert starts == []
        endpoint.process_start_time = birth
        process.terminate()
        process.wait(timeout=5)
        with pytest.raises(ConnectionError, match="exited or was replaced"):
            client.resident_endpoint(tmp_path)
        assert monitor._ensure_status_monitor() is True
        assert len(starts) == 1
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


@pytest.mark.parametrize("live", [True, False])
def test_unsupported_native_exit_probe_uses_existing_identity_check(tmp_path, monkeypatch, live):
    from agent_worktrees import locks
    from zdd import process_exit

    endpoint = SimpleNamespace(pid=12345, process_start_time="12345")
    monkeypatch.setattr(client.routing, "read_active_endpoint", lambda *a, **kw: endpoint)
    monkeypatch.setattr(
        process_exit, "observe_original_exit", lambda *a: (_ for _ in ()).throw(NotImplementedError()),
    )
    monkeypatch.setattr(locks, "lock_is_live", lambda identity: live)
    if live:
        assert client.resident_endpoint(tmp_path) is endpoint
    else:
        with pytest.raises(ConnectionError, match="exited or was replaced"):
            client.resident_endpoint(tmp_path)


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
            assert result["worktrees"] == [
                {"id": "worktree-one", "codename": "quiet-knob", "platform": client.cfg.detect_platform()},
            ]
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
    from agent_worktrees.worktrees_refresh import CAPABILITY

    monkeypatch.setattr(client, "read_health", lambda: {"capabilities": [CAPABILITY]})
    monkeypatch.setattr(
        client,
        "read_current",
        lambda payload, **kw: {
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
