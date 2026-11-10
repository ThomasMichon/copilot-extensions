"""The actual authenticated roster transport never computes current facts."""

from __future__ import annotations

import json
import socketserver
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from agent_worktrees import status_monitor_cutover as control
from agent_worktrees import worktrees_projection as projection
from agent_worktrees import worktrees_runtime as runtime


def request(**selection):
    return {"contract_version": runtime.CONTRACT_VERSION, "project": "project", **selection}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        request(contract_version=True),
        request(contract_version=3),
        request(project="../project"),
        request(include_missing=1),
        request(passive=1),
        request(worktree_id=""),
        request(history=True),
        request(refresh_id="a" * 32),
        request(worktree_id="one", refresh_id=True),
        request(worktree_id="one", refresh_id="a" * 32, passive=True),
        request(worktree_id="one", refresh_id="a" * 32, bootstrap=True),
    ],
)
def test_worktrees_runtime_rejects_malformed_requests_without_storage(tmp_path, payload):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    result = owner.handle(payload)
    assert result["ok"] is False and result["error"]["code"] == "invalid-request"
    assert not owner.store.db_path.exists()


def test_worktrees_runtime_cold_unready_and_deleted_roster_are_distinct(tmp_path):
    wake = threading.Event()
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3", wake=wake)
    assert owner.handle(request())["error"]["code"] == "unready"
    owner.activate()
    assert owner.handle(request())["error"]["code"] == "unready"
    assert wake.is_set() and owner.demand_projects() == {"project"}
    owner.publish("project", [{"id": "first"}], observed_at=1)
    assert owner.handle(request())["worktrees"] == [{"id": "first"}]
    owner.publish("project", [], observed_at=2)
    result = owner.handle(request())
    assert result["ok"] is True and result["worktrees"] == []
    assert result["projection"]["revision"] == 2
    owner.shutdown()
    assert owner.handle(request())["error"]["code"] == "closed"


def test_worktrees_runtime_authenticated_transport_and_drain(tmp_path):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "first", "title": "current"}], observed_at=1)
    actions = []

    def handle(action, payload):
        actions.append(action)
        if action == "health":
            return owner.health()
        if action == runtime.READ_ACTION:
            return owner.handle(payload)
        pytest.fail(f"unexpected action {action}")

    server = control.ControlServer(handle, token="fixture-token")
    server.start()
    try:
        client = control.ControlClient(f"http://127.0.0.1:{server.port}", token="fixture-token")
        assert runtime.CAPABILITY in client.health()["capabilities"]
        for _ in range(3):
            result = client.worktrees_read(request(worktree_id="first"))
            assert result["ok"] is True and result["worktrees"] == [
                {"id": "first", "title": "current"}
            ]
            assert result["projection"]["quality"] == "current"
        wrong = control.ControlClient(f"http://127.0.0.1:{server.port}", token="wrong-token")
        with pytest.raises(ValueError):
            wrong.worktrees_read(request())
        assert len(actions) == 4
        owner.close_admission()
        result = client.worktrees_read(request())
        assert result["ok"] is False and result["error"]["code"] == "draining"
        owner.activate()
        assert client.worktrees_read(request())["ok"] is True
    finally:
        server.close()
        owner.shutdown()


def test_point_refresh_requires_its_reconciled_publication_not_an_unrelated_publish(tmp_path):
    wake = threading.Event()
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3", wake=wake)
    owner.activate()
    query = request(worktree_id="one", refresh_id="a" * 32)
    try:
        owner.publish("project", [{"id": "one", "session_bound_live": True}], observed_at=1)
        wake.clear()
        assert owner.handle(query)["error"]["code"] == "unready"
        assert wake.is_set()
        pending = owner.refreshes.pending("project")
        assert pending == (("a" * 32, "one"),)
        owner.publish("project", [{"id": "one"}], observed_at=2)
        assert owner.handle(query)["error"]["code"] == "unready"
        owner.publish("project", [{"id": "one"}], observed_at=3, refresh_requests=pending)
        result = owner.handle(query)
        assert result["point_refresh"] == {
            "request_id": "a" * 32,
            "generation": result["projection"]["generation"],
            "revision": 3,
        }
        assert owner.handle(query)["point_refresh"] == result["point_refresh"]
        assert owner.refreshes.pending("project") == ()
        changed = owner.handle({**query, "worktree_id": "other"})
        assert changed["error"]["code"] == "invalid-request"
        owner.close_admission()
        owner.activate()
        assert owner.handle(query)["error"]["code"] == "unready"
    finally:
        owner.shutdown()


def test_point_refresh_queue_is_bounded_and_never_evicts_pending_requests(tmp_path):
    from agent_worktrees.worktrees_refresh import MAX_REQUESTS

    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    try:
        for index in range(MAX_REQUESTS):
            assert owner.handle(request(
                worktree_id="one", refresh_id=f"{index:032x}",
            ))["error"]["code"] == "unready"
        overflow = request(worktree_id="one", refresh_id=f"{MAX_REQUESTS:032x}")
        assert owner.handle(overflow)["error"]["code"] == "invalid-request"
        pending = owner.refreshes.pending("project")
        assert len(pending) == MAX_REQUESTS
        owner.publish("project", [{"id": "one"}], observed_at=1, refresh_requests=pending[:1])
        assert owner.handle(overflow)["error"]["code"] == "unready"
        assert owner.refreshes.pending("project") == pending[1:] + ((overflow["refresh_id"], "one"),)
    finally:
        owner.shutdown()


def test_stale_writer_is_fenced_without_closing_admitted_cached_reads(tmp_path):
    election = {"writer": True, "reader": True}
    owner = runtime.WorktreesRuntime(
        tmp_path / "current.sqlite3",
        elected=lambda: election["writer"], admitted=lambda: election["reader"],
    )
    owner.activate()
    owner.publish("project", [{"id": "first"}], observed_at=1)
    election["writer"] = False
    for _ in range(2):
        with pytest.raises(projection.ProjectionUnavailable, match="no longer elected"):
            owner.publish("project", [{"id": "wrong"}], observed_at=2)
        assert not owner.store.superseded
        assert owner.handle(request())["worktrees"] == [{"id": "first"}]
    election["reader"] = False
    assert owner.handle(request())["error"]["code"] == "superseded"
    owner.shutdown()


def test_worktrees_runtime_old_publisher_cannot_reactivate_after_takeover(tmp_path):
    path = tmp_path / "current.sqlite3"
    old = runtime.WorktreesRuntime(path)
    old.activate()
    old.publish("project", [{"id": "first"}], observed_at=1)
    old.close_admission("superseded")
    new = runtime.WorktreesRuntime(path)
    new.activate()
    assert new.handle(request())["error"]["code"] == "unready"
    with pytest.raises(projection.ProjectionUnavailable, match="superseded"):
        old.activate()
    assert old.handle(request())["ok"] is False
    new.publish("project", [{"id": "second"}], observed_at=2)
    assert new.handle(request())["worktrees"] == [{"id": "second"}]
    assert projection.read_advisory(path, "project")["worktrees"] == [{"id": "second"}]
    old.shutdown()
    new.shutdown()


def test_worktrees_runtime_demand_linger_is_owner_accounting_not_read_compute(tmp_path):
    clock = [10.0]
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3", now=lambda: clock[0])
    owner.activate()
    owner.handle(request())
    clock[0] = 40
    assert owner.demand_projects() == {"project"}
    clock[0] = 40.001
    assert owner.demand_projects() == set()
    owner.shutdown()


@pytest.fixture
def demand_owner(tmp_path):
    clock = [10.0]
    wakes = []
    owner = runtime.WorktreesRuntime(
        tmp_path / "current.sqlite3",
        now=lambda: clock[0],
        wake=SimpleNamespace(set=lambda: wakes.append(1)),
    )
    owner.activate()
    wakes.clear()
    try:
        yield owner, clock, wakes
    finally:
        owner.shutdown()


@pytest.mark.parametrize("version", [1, 2])
def test_unavailable_read_retries_renew_demand_without_repeating_wakes(demand_owner, version):
    owner, clock, wakes = demand_owner
    for _ in range(1000):
        result = owner.handle(request(contract_version=version))
        assert result["ok"] is False and result["error"]["code"] == "unready"
        clock[0] += 1
    assert len(wakes) == 1
    assert owner.demand_projects() == {"project"}

    clock[0] += 29
    assert owner.handle(request())["error"]["code"] == "unready"
    assert len(wakes) == 1
    clock[0] += 30.001
    owner.handle(request())
    assert len(wakes) == 2
    clock[0] += 30.001
    assert owner.demand_projects() == set()
    owner.handle(request())
    assert len(wakes) == 3


def test_passive_reads_never_record_demand_or_wake_the_producer(demand_owner):
    owner, clock, wakes = demand_owner
    for _ in range(5):
        result = owner.handle(request(passive=True))
        assert result["ok"] is False and result["error"]["code"] == "unready"
        clock[0] += 1
    assert wakes == [] and owner.demand_projects() == set()

    owner.publish("project", [{"id": "first"}], observed_at=1)
    assert owner.handle(request(passive=True))["worktrees"] == [{"id": "first"}]
    assert owner.demand_projects() == set()

    assert owner.handle(request(project="other"))["error"]["code"] == "unready"
    assert wakes == [1] and owner.demand_projects() == {"other"}


def _freshness_owner(tmp_path, clock):
    owner = runtime.WorktreesRuntime(
        tmp_path / "current.sqlite3", now=lambda: clock[0],
        freshness_bound=runtime.freshness_bound_for(15),
    )
    owner.activate()
    return owner


def test_passive_read_goes_stale_when_only_another_project_keeps_the_resident_alive(tmp_path):
    from agent_worktrees import delegate_cli, worktrees_client

    clock = [100.0]
    owner = _freshness_owner(tmp_path, clock)
    try:
        owner.publish("idle", [], observed_at=1)
        owner.publish("busy", [{"id": "busy-1"}], observed_at=1)
        clock[0] += 10
        fresh = owner.handle(request(project="idle", passive=True))
        assert "availability" not in fresh
        assert fresh["projection"]["freshness"] == {"refreshed_age_s": 10.0, "bound_s": 45.0}
        # "idle"'s demand lapsed: the live resident keeps republishing only "busy".
        for _ in range(3):
            clock[0] += 15
            owner.publish("busy", [{"id": "busy-1"}], observed_at=clock[0])
        stale = owner.handle(request(project="idle", passive=True))
        assert stale["worktrees"] == []
        assert stale["availability"] == {
            "status": "stale", "reason": "current roster refresh overdue (55s > 45s)",
        }
        assert "availability" not in owner.handle(request(project="busy", passive=True))
        assert owner.demand_projects() == set()
        # The client accepts the labeled reply, and it cannot authorize finalization.
        validated = worktrees_client.validate_result(stale, request(project="idle", passive=True))
        host = {"reachable": True, **{k: validated[k] for k in ("projection", "availability")}}
        assert delegate_cli.host_roster_is_current(host) is False
        # A demand read keeps its unlabeled contract (and renews the producer).
        demand = owner.handle(request(project="idle"))
        assert "freshness" not in demand["projection"] and "availability" not in demand
    finally:
        owner.shutdown()


def test_passive_read_goes_stale_after_the_bound_when_the_producer_hangs(tmp_path):
    clock = [0.0]
    owner = _freshness_owner(tmp_path, clock)
    try:
        owner.publish("project", [{"id": "first"}], observed_at=1)
        clock[0] = 45.0
        assert "availability" not in owner.handle(request(passive=True))
        # No sweep iteration runs again; the reader's own clock still ages it.
        clock[0] = 45.5
        result = owner.handle(request(passive=True))
        assert result["worktrees"] == [{"id": "first"}]
        assert result["availability"]["status"] == "stale"
        assert result["projection"]["freshness"]["refreshed_age_s"] == 45.5
        owner.publication_failed("project", OSError("producer fixture failed"))
        clock[0] = 46.0
        failed = owner.handle(request(passive=True))
        assert failed["availability"] == {"status": "stale", "reason": "producer fixture failed"}
    finally:
        owner.shutdown()


def test_freshness_ages_from_when_the_published_facts_were_collected(tmp_path):
    clock = [0.0]
    owner = _freshness_owner(tmp_path, clock)
    try:
        collected = owner.clock()
        clock[0] = 50.0  # collection stalled past the 45s bound before publishing
        owner.publish("project", [], observed_at=1, collected_from=collected)
        result = owner.handle(request(passive=True))
        assert result["projection"]["freshness"]["refreshed_age_s"] == 50.0
        assert result["availability"]["status"] == "stale"
        with pytest.raises(ValueError):
            owner.publish("project", [], observed_at=1, collected_from=float("nan"))
    finally:
        owner.shutdown()


def test_reactivated_resident_proves_freshness_only_by_its_own_publication(tmp_path):
    clock = [0.0]
    owner = _freshness_owner(tmp_path, clock)
    try:
        owner.publish("project", [{"id": "first"}], observed_at=1)
        owner.close_admission()
        owner.activate()
        result = owner.handle(request(passive=True))
        assert result["worktrees"] == [{"id": "first"}]
        assert result["projection"]["freshness"]["refreshed_age_s"] is None
        assert result["availability"]["status"] == "stale"
        owner.publish("project", [{"id": "first"}], observed_at=2)
        assert "availability" not in owner.handle(request(passive=True))
    finally:
        owner.shutdown()


def test_change_events_select_projects_to_republish_without_recording_demand(demand_owner):
    owner, _clock, wakes = demand_owner
    owner.note_changed("/wt/a")
    assert wakes == [1] and owner.demand_projects() == set()
    assert owner.take_changed_projects({"/wt/a": "a"}.get) == {"a"}
    owner.publish("a", [], observed_at=1)
    assert owner.take_changed_projects({"/wt/a": "a"}.get) == set()

    owner.publish("b", [], observed_at=1)

    def broken(_path):
        raise OSError("resolver fixture broken")

    # An event that cannot be attributed republishes everything served.
    for path, resolve in (("/wt/x", lambda _p: None), ("/wt/x", broken), (None, lambda _p: "c")):
        owner.note_changed(path)
        assert owner.take_changed_projects(resolve) == {"a", "b"}
    for index in range(runtime._CHANGED_PATH_LIMIT + 1):
        owner.note_changed(f"/wt/{index}")
    assert owner.take_changed_projects(lambda _p: "c") == {"a", "b"}
    assert owner.demand_projects() == set()

    owner.close_admission()
    owner.note_changed("/wt/a")
    assert owner.take_changed_projects({"/wt/a": "a"}.get) == set()


def test_duplicate_change_at_capacity_does_not_republish_everything(demand_owner):
    owner, _clock, _wakes = demand_owner
    owner.publish("a", [], observed_at=1)
    for index in range(runtime._CHANGED_PATH_LIMIT):
        owner.note_changed(f"/wt/{index}")
    owner.note_changed("/wt/0")
    assert owner.take_changed_projects(lambda _p: "c") == {"c"}


def test_change_event_survives_sweeps_that_fail_before_publishing(demand_owner):
    owner, _clock, _wakes = demand_owner
    resolve = {"/wt/a": "a"}.get
    owner.note_changed("/wt/a")
    # A transient listing failure or deferral takes the event without publishing.
    for _sweep in range(runtime._CHANGED_RETRY_SWEEPS):
        assert owner.take_changed_projects(resolve) == {"a"}
    # Retries are bounded; the unrefreshed roster then simply ages to stale.
    assert owner.take_changed_projects(resolve) == set()

    owner.note_changed("/wt/a")
    assert owner.take_changed_projects(resolve) == {"a"}
    owner.publish("a", [], observed_at=1)
    assert owner.take_changed_projects(resolve) == set()
    assert owner.demand_projects() == set()


def test_concurrent_unavailable_readers_coalesce_per_project_and_reactivate(demand_owner):
    owner, _clock, wakes = demand_owner
    projects = ["project", "other"] * 32
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda project: owner.handle(request(project=project)), projects))
    assert all(result["error"]["code"] == "unready" for result in results)
    assert len(wakes) == 2
    assert owner.demand_projects() == {"project", "other"}
    assert owner.handle(request(history=True))["error"]["code"] == "invalid-request"
    assert len(wakes) == 2
    owner.close_admission()
    assert owner.handle(request())["error"]["code"] == "draining"
    assert len(wakes) == 2
    owner.activate()
    wakes.clear()
    owner.handle(request())
    owner.handle(request())
    assert len(wakes) == 1


def test_publication_rearms_unavailable_wakes_but_passive_reads_do_not(demand_owner, monkeypatch):
    owner, _clock, wakes = demand_owner
    owner.handle(request())
    owner.publication_failed("project", OSError("producer fixture unavailable"))
    result = owner.handle(request())
    assert result["error"]["message"] == "producer fixture unavailable"
    assert len(wakes) == 1
    owner.publish("project", [{"id": "one"}], observed_at=1)
    assert owner.handle(request())["worktrees"] == [{"id": "one"}]
    assert len(wakes) == 1

    original = owner.store.read
    unavailable = [True]

    def read(*args, **kwargs):
        if unavailable[0]:
            raise projection.ProjectionUnavailable("read fixture unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(owner.store, "read", read)
    owner.handle(request())
    owner.handle(request())
    assert len(wakes) == 2
    unavailable[0] = False
    assert owner.handle(request())["ok"] is True
    unavailable[0] = True
    owner.handle(request())
    assert len(wakes) == 2
    owner.publish("project", [{"id": "two"}], observed_at=2)
    owner.handle(request())
    assert len(wakes) == 3


def test_unavailable_read_does_not_wake_after_admission_closes(demand_owner, monkeypatch):
    owner, _clock, wakes = demand_owner

    def closing_read(*_args, **_kwargs):
        owner.close_admission()
        raise projection.ProjectionUnavailable("closed during read")

    monkeypatch.setattr(owner.store, "read", closing_read)
    assert owner.handle(request())["error"]["code"] == "unready"
    assert not wakes


def test_worktrees_runtime_failed_producer_keeps_labeled_last_good_rows(tmp_path):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "one"}], observed_at=1)
    owner.publication_failed("project", OSError("producer fixture unavailable"))
    result = owner.handle(request())
    assert result["worktrees"] == [{"id": "one"}]
    assert result["projection"]["revision"] == 1
    assert result["availability"] == {
        "status": "stale",
        "reason": "producer fixture unavailable",
    }
    owner.publish("project", [{"id": "two"}], observed_at=2)
    assert "availability" not in owner.handle(request())
    owner.shutdown()


def test_worktrees_runtime_initial_producer_failure_surfaces_the_actual_reason(tmp_path):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publication_failed("project", OSError("fixture producer cannot enumerate mux sessions"))
    result = owner.handle(request())
    assert result["ok"] is False and result["error"] == {
        "code": "unavailable", "message": "fixture producer cannot enumerate mux sessions",
    }
    owner.shutdown()


def test_worktrees_runtime_late_initial_activation_cannot_steal_new_election(tmp_path):
    path = tmp_path / "current.sqlite3"
    active = runtime.WorktreesRuntime(path)
    active.activate()
    active.publish("project", [{"id": "active"}], observed_at=1)
    late = runtime.WorktreesRuntime(path, elected=lambda: False)
    with pytest.raises(projection.ProjectionUnavailable, match="no longer elected"):
        late.activate()
    assert runtime.owner_generation(path) == active.store.generation
    assert active.handle(request())["worktrees"] == [{"id": "active"}]
    assert late.handle(request())["ok"] is False
    active.shutdown()
    late.shutdown()


def test_worktrees_runtime_elected_activation_rechecks_a_raced_owner_once(tmp_path, monkeypatch):
    path = tmp_path / "current.sqlite3"
    previous = runtime.WorktreesRuntime(path)
    previous.activate()
    observed = iter([None, previous.store.generation])
    monkeypatch.setattr(runtime, "owner_generation", lambda _: next(observed))
    owner = runtime.WorktreesRuntime(path, elected=lambda: True)
    owner.activate()
    owner.publish("project", [{"id": "new"}], observed_at=1)
    assert owner.handle(request())["worktrees"] == [{"id": "new"}]
    with pytest.raises(projection.ProjectionUnavailable, match="superseded"):
        previous.store.publish("project", [{"id": "old"}], observed_at=2)
    previous.shutdown()
    owner.shutdown()


def test_worktrees_runtime_point_wire_bound_uses_utf8_not_ascii_expansion(tmp_path):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    row = {
        "id": "one",
        **dict.fromkeys(
            (
                "title",
                "summary",
                "activity",
                "branch",
                "path",
                "repo",
                "machine",
                "status",
                "platform",
                "started_at",
                "last_resumed_at",
                "status_note_at",
                "activity_at",
                "live_intent",
                "live_rest",
            ),
            chr(0x754C) * 1365,
        ),
    }
    owner.publish("project", [row], observed_at=1)
    server = control.ControlServer(lambda action, payload: owner.handle(payload), token="fixture")
    server.start()
    try:
        client = control.ControlClient(f"http://127.0.0.1:{server.port}", token="fixture")
        result = client.worktrees_read(request(worktree_id="one"))
        assert result["worktrees"] == [row]
    finally:
        server.close()
        owner.shutdown()


def test_worktrees_runtime_slow_progress_cannot_extend_the_read_deadline():
    class SlowReply(socketserver.StreamRequestHandler):
        def handle(self):
            self.rfile.readline()
            reply = json.dumps({"version": 1, "result": {"ok": True}}).encode() + b"\n"
            try:
                for byte in reply:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.03)
            except OSError:
                return

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SlowReply)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = control.ControlClient(
            f"http://127.0.0.1:{server.server_address[1]}",
            token="fixture",
            timeout=0.1,
        )
        with pytest.raises(TimeoutError):
            client.worktrees_read(request())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_unavailable_wake_remains_inside_admission_fence(demand_owner, monkeypatch):
    owner, _clock, wakes = demand_owner
    entered = threading.Event()
    finish = threading.Event()
    original = owner._wake.set

    def paused_wake():
        entered.set()
        assert finish.wait(timeout=5), "fixture wake was not released"
        original()

    monkeypatch.setattr(owner._wake, "set", paused_wake)
    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(owner.handle, request())
        try:
            assert entered.wait(timeout=5), "reader did not reach notification"
            acquired = owner._lock.acquire(blocking=False)
            if acquired:
                owner._lock.release()
        finally:
            finish.set()
        result = response.result(timeout=5)
    assert not acquired, "close_admission could return before the accepted wake is sent"
    assert result["error"]["code"] == "unready"
    assert len(wakes) == 1
