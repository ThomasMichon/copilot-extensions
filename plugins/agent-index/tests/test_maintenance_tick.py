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
    """A tick must never race the live service's own in-flight indexing
    (e.g. a queued full reindex) with its own raw, unserialized `agent-index
    index` CLI call -- that's a concurrent-writer hazard against the same
    store, not just redundant work (observed in production)."""
    module = _load_module()

    healthy_service = {"role": "host", "state": "ready", "running": True}
    healthy_engine = {"healthy": True, "pid": 123}
    already_indexing = {"role": "host", "state": "ready", "running": True, "indexing": {"running": True}}

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
            if key == ("index",):
                raise AssertionError(
                    "maintenance tick must not call `index` while the service "
                    "already reports indexing.running=True"
                )
        raise AssertionError(f"unscripted command: {command}")

    worker = _FakeWorker()
    result = module.run_maintenance(worker, runner=sequenced_runner)

    assert result["reindex_skipped"] is True
    assert result["chunks_total"] is None
    assert "reindex-skipped" in worker.phases
    assert "reindex-started" not in worker.phases
    assert call_sequence == []  # both status calls were consumed


def test_run_maintenance_runs_reindex_when_service_is_idle():
    module = _load_module()

    healthy_service = {"role": "host", "state": "ready", "running": True}
    healthy_engine = {"healthy": True, "pid": 123}
    idle_status = {"role": "host", "state": "ready", "running": True, "indexing": {"running": False}}
    reindex_result = {"chunks_total": 42, "sources_failed": [], "sources_purged": []}

    status_calls = [healthy_service, idle_status]

    def runner(command, **kwargs):
        for length in range(len(command), 0, -1):
            key = tuple(command[-length:])
            if key == ("status",):
                return _completed(status_calls.pop(0))
            if key == ("engine", "status"):
                return _completed(healthy_engine)
            if key == ("index",):
                return _completed(reindex_result)
        raise AssertionError(f"unscripted command: {command}")

    worker = _FakeWorker()
    result = module.run_maintenance(worker, runner=runner)

    assert result["reindex_skipped"] is False
    assert result["chunks_total"] == 42
    assert "reindex-started" in worker.phases
    assert "reindex-complete" in worker.phases
    assert "reindex-skipped" not in worker.phases
