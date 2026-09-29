"""Tests for the schedule registry + job-lease (schedule-management, shape 1).

Two surfaces are covered:

* the persisted **registry** -- recurring schedules registered / listed /
  inspected / paused / removed as first-class objects (vs. a hand-edited spec),
  and a lease-gated **registry tick** that produces occurrences idempotently; and
* the **job-lease** -- single-producer election per scope, *pin-not-failover*:
  a first writer wins the scope and renews it, a different caller is refused and
  never auto-steals it, and reassignment is an explicit (``force``) operator act.
"""

from __future__ import annotations

import concurrent.futures
import subprocess
import threading
from types import SimpleNamespace

import pytest

from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.producers import schedule
from agent_dispatch.queue import TaskError, TaskQueue
from tests._helpers import TEST_REPO


def _entry(sid="nightly", **over) -> dict:
    entry = {
        "id": sid,
        "title": "Chronicle the day",
        "prompt": "Summarize.",
        "repo": TEST_REPO,
        "interval_seconds": 3600,
    }
    entry.update(over)
    return entry


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


# -- registry: queue-level semantics -----------------------------------------


def test_register_and_get_roundtrips_the_entry(q):
    rec = q.register_schedule(_entry())
    assert rec.id == "nightly"
    assert rec.paused is False
    assert rec.entry["repo"] == TEST_REPO
    assert q.get_schedule("nightly").entry["interval_seconds"] == 3600
    assert q.get_schedule("absent") is None


def test_register_upserts_and_preserves_created_at_and_paused(q):
    q.register_schedule(_entry(prompt="v1"), now=100.0)
    q.set_schedule_paused("nightly", True)
    second = q.register_schedule(_entry(prompt="v2"), now=200.0)
    assert second.entry["prompt"] == "v2"
    assert second.created_at == 100.0  # created_at preserved across upsert
    assert second.updated_at == 200.0
    assert q.get_schedule("nightly").paused is True  # paused flag survives upsert


@pytest.mark.parametrize(
    "bad, needle",
    [
        ({"title": "t", "repo": TEST_REPO, "interval_seconds": 1}, "id"),
        ({"id": "x", "repo": TEST_REPO, "interval_seconds": 1}, "title"),
        ({"id": "x", "title": "t", "interval_seconds": 1}, "repo"),
        ({"id": "x", "title": "t", "repo": TEST_REPO}, "interval_seconds"),
        (
            {"id": "x", "title": "t", "repo": TEST_REPO, "interval_seconds": 1, "at": ["09:00"]},
            "one of",
        ),
    ],
)
def test_register_validates_eagerly(q, bad, needle):
    with pytest.raises(TaskError) as exc:
        q.register_schedule(bad)
    assert needle in str(exc.value)


def test_list_and_pause_and_remove(q):
    q.register_schedule(_entry("a"))
    q.register_schedule(_entry("b"))
    assert [r.id for r in q.list_schedules()] == ["a", "b"]

    q.set_schedule_paused("b", True)
    active = q.list_schedules(include_paused=False)
    assert [r.id for r in active] == ["a"]

    assert q.remove_schedule("a") is True
    assert q.remove_schedule("a") is False
    assert [r.id for r in q.list_schedules()] == ["b"]


def test_pause_unknown_raises(q):
    with pytest.raises(TaskError):
        q.set_schedule_paused("nope", True)


# -- job-lease: pin-not-failover ---------------------------------------------


def test_first_writer_wins_and_renews(q):
    lease, granted = q.acquire_schedule_lease("chronicle", "cloud1", now=10.0)
    assert granted is True
    assert lease.holder == "cloud1"
    assert lease.acquired_at == 10.0

    # same holder renews (refreshes renewed_at, keeps acquired_at)
    lease2, granted2 = q.acquire_schedule_lease("chronicle", "cloud1", now=20.0)
    assert granted2 is True
    assert lease2.acquired_at == 10.0
    assert lease2.renewed_at == 20.0


def test_a_different_holder_is_refused_and_never_steals(q):
    q.acquire_schedule_lease("chronicle", "cloud1", now=10.0)
    lease, granted = q.acquire_schedule_lease("chronicle", "dev6", ttl=1.0, now=99999.0)
    assert granted is False
    assert lease.holder == "cloud1"  # NOT stolen, even long after any ttl


def test_release_by_holder_and_forced_reassign(q):
    q.acquire_schedule_lease("chronicle", "cloud1")
    # a non-holder cannot release without force
    with pytest.raises(TaskError):
        q.release_schedule_lease("chronicle", "dev6")
    # force lets an operator reassign a stuck lease
    assert q.release_schedule_lease("chronicle", "dev6", force=True) is True
    assert q.get_schedule_lease("chronicle") is None
    # releasing an unheld scope is a no-op, not an error
    assert q.release_schedule_lease("chronicle", "cloud1") is False


def test_get_and_list_leases(q):
    assert q.get_schedule_lease("chronicle") is None
    q.acquire_schedule_lease("chronicle", "cloud1")
    q.acquire_schedule_lease("sweep", "dev6")
    assert {leaf.scope for leaf in q.list_schedule_leases()} == {"chronicle", "sweep"}


def test_lease_acquire_is_atomic_under_concurrency(q):
    """Many machines racing for one scope -> exactly one holder."""
    barrier = threading.Barrier(16)

    def race(name):
        barrier.wait()
        _, granted = q.acquire_schedule_lease("chronicle", name)
        return granted

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        grants = list(pool.map(race, [f"m{i}" for i in range(16)]))

    assert sum(1 for g in grants if g) == 1
    assert len(q.list_schedule_leases()) == 1


# -- HTTP surface + end-to-end registry tick ---------------------------------


@pytest.fixture
def client(tmp_path):
    # A real uvicorn server on an ephemeral port so the sync DispatchClient can
    # be exercised over real HTTP (matches test_coordinator's server fixture).
    import socket
    import time

    import uvicorn

    app = create_app(TaskQueue(tmp_path / "tasks.db"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    c = DispatchClient(url)
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            c.health()
            break
        except Exception:  # server still starting
            time.sleep(0.05)
    else:
        c.close()
        server.should_exit = True
        raise RuntimeError("coordinator did not start")

    yield c

    c.close()
    server.should_exit = True
    thread.join(timeout=5)


def test_http_register_list_inspect_remove(client):
    rec = client.register_schedule(_entry())
    assert rec["id"] == "nightly"
    assert [r["id"] for r in client.list_schedules()] == ["nightly"]

    client.set_schedule_paused("nightly", True)
    assert client.list_schedules(include_paused=False) == []

    removed = client.remove_schedule("nightly")
    assert removed["removed"] is True


def test_http_register_malformed_400(client):
    with pytest.raises(Exception) as exc:
        client.register_schedule({"id": "x", "title": "t"})  # no repo / cadence
    assert getattr(exc.value, "status_code", None) == 400


def test_http_lease_acquire_refuse_release(client):
    first = client.acquire_schedule_lease("chronicle", "cloud1")
    assert first["granted"] is True

    second = client.acquire_schedule_lease("chronicle", "dev6")
    assert second["granted"] is False
    assert second["lease"]["holder"] == "cloud1"

    assert client.get_schedule_lease("chronicle")["holder"] == "cloud1"
    released = client.release_schedule_lease("chronicle", "cloud1")
    assert released["released"] is True
    assert client.get_schedule_lease("chronicle") is None


def test_registry_tick_produces_and_is_idempotent(client):
    client.register_schedule(_entry("hourly", interval_seconds=3600))
    client.register_schedule(_entry("paused", interval_seconds=3600))
    client.set_schedule_paused("paused", True)

    first = schedule.run_registry_tick(client, now=7200.0)
    assert first["errors"] == []
    assert first["created"]  # non-empty
    # only the non-paused schedule produced
    assert all(t["origin_ref"] == "schedule/hourly" for t in first["created"])

    second = schedule.run_registry_tick(client, now=7200.0)
    ids_first = {t["id"] for t in first["created"]}
    ids_second = {t["id"] for t in second["created"]}
    assert ids_second <= ids_first  # same occurrences -> dedup, no new tasks


def test_registry_tick_threads_exclusive_resource_fields(client):
    client.register_schedule(
        _entry(
            "exclusive",
            exclusive_key="scheduled-resource",
            supersede_exclusive_key=True,
        )
    )

    result = schedule.run_registry_tick(client, now=7200.0)

    assert result["errors"] == []
    assert result["created"]
    assert all(
        task["exclusive_key"] == "scheduled-resource"
        for task in result["created"]
    )


def test_register_from_spec_bakes_default_repo(client):
    spec = {
        "default_repo": TEST_REPO,
        "schedules": [
            {"id": "a", "title": "A", "interval_seconds": 3600},  # inherits default_repo
            {"id": "bad", "title": "B"},  # no cadence -> error, not registered
        ],
    }
    result = schedule.register_from_spec(client, spec)
    assert [r["id"] for r in result["registered"]] == ["a"]
    assert result["errors"][0]["id"] == "bad"
    assert client.get_schedule("a")["entry"]["repo"] == TEST_REPO


# -- lease-gated serve idles a non-holder ------------------------------------


def test_serve_registry_ticks_only_while_lease_held(client, monkeypatch):
    """serve_registry ticks when granted, idles when the scope is held elsewhere."""
    client.register_schedule(_entry("hourly", interval_seconds=3600))
    # a different machine already holds the scope
    client.acquire_schedule_lease("chronicle", "other-host")

    ticks: list[dict] = []

    def on_tick(result):
        ticks.append(result)
        raise KeyboardInterrupt  # one iteration then stop

    monkeypatch.setattr(schedule, "DispatchClient", lambda *_a, **_k: client)
    # avoid a real sleep after the (interrupted) first iteration
    schedule.serve_registry(
        url="http://test",
        interval=0,
        lease_scope="chronicle",
        holder="cloud1",
        on_tick=on_tick,
    )
    assert ticks and ticks[0]["held"] is False  # refused -> idled, did not tick


# -- schedule.serve() shells out to `schedule tick` fresh every cycle -------
#
# Regression coverage (see the matching emitter.serve
# tests): a long-lived schedule timer must never build/hold a DispatchClient
# (or any resolved coordinator address) across its sleep boundary. Each tick
# now re-invokes ``agent-dispatch schedule tick <spec>`` in a fresh
# subprocess, which re-discovers the coordinator exactly like any other
# one-shot CLI invocation would.
#
# serve_registry() is different: its cross-machine lease-gating is
# orchestrated by the loop itself (there is no reusable one-shot CLI
# primitive that atomically acquires the lease and conditionally ticks), so
# it keeps the in-process ``resolve_target`` fix instead (still re-resolving
# fresh every tick, just not via a forked subprocess) -- see its own tests
# below, unchanged.


def test_schedule_serve_runs_schedule_tick_as_a_fresh_subprocess_every_cycle(tmp_path):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text('{"schedules": []}')

    seen_argv: list[list[str]] = []
    seen_kwargs: dict = {}
    payloads = iter(
        ['{"created": [], "errors": []}', '{"created": [{"id": "t-1"}], "errors": []}']
    )

    def fake_runner(argv, **kwargs):
        seen_argv.append(list(argv))
        seen_kwargs.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=next(payloads))

    ticks: list[dict] = []
    calls = {"n": 0}

    def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise KeyboardInterrupt

    schedule.serve(
        spec_path,
        cli_argv=["fake-python", "-m", "agent_dispatch"],
        runner=fake_runner,
        interval=0,
        on_tick=lambda result: ticks.append(result),
        sleep=fake_sleep,
    )

    assert seen_argv == [
        ["fake-python", "-m", "agent_dispatch", "schedule", "tick", str(spec_path)],
        ["fake-python", "-m", "agent_dispatch", "schedule", "tick", str(spec_path)],
    ]
    assert [len(t["created"]) for t in ticks] == [0, 1]
    # Only stdout is piped -- stderr is left to inherit ours directly (real
    # OS-level passthrough, no Python-side buffering) so any diagnostic the
    # tick process writes there streams straight through.
    assert seen_kwargs["stdout"] is subprocess.PIPE
    assert "stderr" not in seen_kwargs
    assert "capture_output" not in seen_kwargs


def test_schedule_serve_synthesizes_an_error_from_a_hard_subprocess_failure(tmp_path):
    """A hard failure (nonzero exit, no JSON on stdout -- its own traceback
    already streamed live to our inherited stderr) becomes a clean
    synthetic error entry rather than crashing the loop."""
    spec_path = tmp_path / "spec.json"
    spec_path.write_text('{"schedules": []}')

    def fake_runner(_argv, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="")

    ticks: list[dict] = []

    def fake_sleep(_seconds):
        raise KeyboardInterrupt

    schedule.serve(
        spec_path,
        cli_argv=["fake-python", "-m", "agent_dispatch"],
        runner=fake_runner,
        interval=0,
        on_tick=lambda result: ticks.append(result),
        sleep=fake_sleep,
    )

    assert ticks[0]["errors"] == [
        {"error": "schedule tick produced no valid JSON result (exit 1)"}
    ]


def test_schedule_serve_rejects_a_zero_exit_payload_missing_created_or_errors(
    tmp_path,
):
    """A zero-exit tick whose stdout doesn't decode to a dict with
    list-valued ``created``/``errors`` is a protocol violation, not a
    vacuous success -- it must surface as an error, never silently
    normalize to an empty, error-free tick that would lose the failure
    entirely."""
    spec_path = tmp_path / "spec.json"
    spec_path.write_text('{"schedules": []}')

    def fake_runner(_argv, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="")

    ticks: list[dict] = []

    def fake_sleep(_seconds):
        raise KeyboardInterrupt

    schedule.serve(
        spec_path,
        cli_argv=["fake-python", "-m", "agent_dispatch"],
        runner=fake_runner,
        interval=0,
        on_tick=lambda result: ticks.append(result),
        sleep=fake_sleep,
    )

    assert ticks[0]["created"] == []
    assert ticks[0]["errors"] == [
        {"error": "schedule tick produced no valid JSON result (exit 0)"}
    ]


def test_serve_registry_requires_url_or_resolve_target():
    with pytest.raises(ValueError):
        schedule.serve_registry(lease_scope="chronicle", holder="cloud1")


def test_serve_registry_re_resolves_target_every_tick(monkeypatch):
    resolved = iter(
        [("http://127.0.0.1:1111", None), ("http://127.0.0.1:2222", None)]
    )
    seen_urls: list[str] = []

    class _RecordingClient:
        def __init__(self, url, token=None):
            self.url = url

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def acquire_schedule_lease(self, scope, holder, **kwargs):
            seen_urls.append(self.url)
            return {"granted": False, "lease": {"scope": scope, "holder": "other"}}

    monkeypatch.setattr(schedule, "DispatchClient", _RecordingClient)

    calls = {"n": 0}

    def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise KeyboardInterrupt

    schedule.serve_registry(
        resolve_target=lambda: next(resolved),
        interval=0,
        lease_scope="chronicle",
        holder="cloud1",
        on_tick=lambda _result: None,
        sleep=fake_sleep,
    )

    assert seen_urls == ["http://127.0.0.1:1111", "http://127.0.0.1:2222"]
