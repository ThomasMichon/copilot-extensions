from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "maintenance_tick.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("agent_index_maintenance_tick", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_maintenance_actions_only_heals_unhealthy_components():
    module = _load_module()

    healthy = module.plan_maintenance_actions(
        {"role": "host", "state": "ready", "running": True},
        {"healthy": True},
    )
    assert healthy == {"recover_service": False, "start_engine": False}

    service_down = module.plan_maintenance_actions(
        {"role": "host", "state": "unreachable", "running": False},
        {"healthy": True},
    )
    assert service_down == {"recover_service": True, "start_engine": False}

    engine_down = module.plan_maintenance_actions(
        {"role": "host", "state": "ready", "running": True},
        {"healthy": False},
    )
    assert engine_down == {"recover_service": False, "start_engine": True}


class _FakeWorker:
    def __init__(self) -> None:
        self.phases: list[str] = []

    def progress(self, *, phase: str, summary: str) -> None:
        self.phases.append(phase)


def _completed(stdout: dict) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(stdout), stderr="")


def test_run_maintenance_skips_reindex_when_service_already_indexing():
    """A tick must never trigger a redundant reindex while the service
    already has one in flight."""
    module = _load_module()

    healthy_service = {"role": "host", "state": "ready", "running": True}
    healthy_engine = {"healthy": True, "pid": 123}
    already_indexing = {
        "role": "host",
        "state": "ready",
        "running": True,
        "indexing": {"running": True},
        "endpoint": "http://127.0.0.1:12345",
    }

    # The SECOND ("status",) call (the pre-index re-check) must return the
    # "already indexing" status, not the first healthy_service payload again.
    call_sequence = [healthy_service, already_indexing]

    def sequenced_runner(command, **kwargs):
        for length in range(len(command), 0, -1):
            key = tuple(command[-length:])
            if key == ("status",):
                payload = call_sequence.pop(0)
                return _completed(payload)
            if key == ("engine", "status"):
                return _completed(healthy_engine)
        raise AssertionError(f"unscripted command: {command}")

    def unexpected_post(url, payload, **kwargs):
        raise AssertionError(f"must not POST /reindex while already indexing: {url}")

    def unexpected_get(url, **kwargs):
        raise AssertionError(f"must not poll /status while already indexing: {url}")

    worker = _FakeWorker()
    result = module.run_maintenance(
        worker, runner=sequenced_runner, post_json=unexpected_post, get_json=unexpected_get
    )

    assert result["reindex_skipped"] is True
    assert result["chunks_total"] is None
    assert "reindex-skipped" in worker.phases
    assert "reindex-started" not in worker.phases
    assert call_sequence == []  # both status calls were consumed


def test_run_maintenance_triggers_reindex_via_service_endpoint_when_idle():
    """The tick must route its reindex through the live service's own
    POST /reindex endpoint -- never a second, raw `agent-index index` CLI
    process -- so the service's single-task queue is the only place
    serialization needs to happen (a raw CLI call is invisible to
    `indexing.running` and can race a reindex triggered before OR after it
    starts; both directions were observed in production)."""
    module = _load_module()

    healthy_service = {"role": "host", "state": "ready", "running": True}
    healthy_engine = {"healthy": True, "pid": 123}
    idle_status = {
        "role": "host",
        "state": "ready",
        "running": True,
        "indexing": {"running": False},
        "endpoint": "http://127.0.0.1:12345",
        "index": {"chunks": 100},
    }
    done_status = {
        "state": "ready",
        "indexing": {"running": False, "stats": {"completed": 1, "failed": 0}},
        "index": {"chunks": 142},
    }

    status_calls = [healthy_service, idle_status]

    def runner(command, **kwargs):
        for length in range(len(command), 0, -1):
            key = tuple(command[-length:])
            if key == ("status",):
                return _completed(status_calls.pop(0))
            if key == ("engine", "status"):
                return _completed(healthy_engine)
        raise AssertionError(f"unscripted command: {command}")

    posted: list[tuple[str, dict]] = []

    def post_json(url, payload, **kwargs):
        posted.append((url, payload))
        return {"task": {"id": "task-abc", "status": "queued"}}

    get_calls = {"n": 0}

    def get_json(url, **kwargs):
        get_calls["n"] += 1
        assert url == "http://127.0.0.1:12345/status"
        return done_status

    worker = _FakeWorker()
    result = module.run_maintenance(
        worker,
        runner=runner,
        post_json=post_json,
        get_json=get_json,
        sleep=lambda _s: None,
    )

    assert posted == [("http://127.0.0.1:12345/reindex", {"full": False})]
    assert get_calls["n"] == 1
    assert result["reindex_skipped"] is False
    assert result["reindex_still_running"] is False
    assert result["reindex_task_id"] == "task-abc"
    assert result["chunks_total"] == 142
    assert result["sources_failed"] == 0
    assert "reindex-started" in worker.phases
    assert "reindex-complete" in worker.phases
    assert "reindex-skipped" not in worker.phases


def test_run_maintenance_gives_up_waiting_after_poll_timeout():
    """A reindex that outlives this tick's bounded poll window must not
    block the tick forever -- it hands off to the service and reports
    still-running, trusting the NEXT tick's skip-check to see it in flight."""
    module = _load_module()

    healthy_service = {"role": "host", "state": "ready", "running": True}
    healthy_engine = {"healthy": True, "pid": 123}
    idle_status = {
        "role": "host",
        "state": "ready",
        "running": True,
        "indexing": {"running": False},
        "endpoint": "http://127.0.0.1:12345",
        "index": {"chunks": 100},
    }
    still_running_status = {
        "state": "ready",
        "indexing": {"running": True},
        "index": {"chunks": 120},
    }

    status_calls = [healthy_service, idle_status]

    def runner(command, **kwargs):
        for length in range(len(command), 0, -1):
            key = tuple(command[-length:])
            if key == ("status",):
                return _completed(status_calls.pop(0))
            if key == ("engine", "status"):
                return _completed(healthy_engine)
        raise AssertionError(f"unscripted command: {command}")

    def post_json(url, payload, **kwargs):
        return {"task": {"id": "task-xyz", "status": "queued"}}

    fake_clock = {"t": 0.0}

    def fake_monotonic():
        return fake_clock["t"]

    def fake_sleep(seconds):
        fake_clock["t"] += seconds

    def get_json(url, **kwargs):
        return still_running_status

    worker = _FakeWorker()
    module_time = module.time
    original_monotonic = module_time.monotonic
    module_time.monotonic = fake_monotonic
    try:
        result = module.run_maintenance(
            worker,
            runner=runner,
            post_json=post_json,
            get_json=get_json,
            sleep=fake_sleep,
            poll_timeout_s=10.0,
            poll_interval_s=5.0,
        )
    finally:
        module_time.monotonic = original_monotonic

    assert result["reindex_still_running"] is True
    assert result["reindex_task_id"] == "task-xyz"
    assert result["chunks_total"] is None
    assert "reindex-still-running" in worker.phases
    assert "reindex-complete" not in worker.phases
