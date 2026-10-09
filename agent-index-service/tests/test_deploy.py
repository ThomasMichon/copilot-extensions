"""Installed-core zdd deployment with only owned temporary processes and state."""

from __future__ import annotations

import json
import os
import time

import httpx
import pytest
import yaml
from _service_process import owned_python
from test_hosted import _seed, _wait_route, warm_engine  # noqa: F401 - registers fixture

from agent_index_service import __version__
from agent_index_service.config import load_config


def _wait_file(path, process):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        assert process.poll() is None, "owned worker exited before attesting its identity"
        time.sleep(0.05)
    raise AssertionError("owned worker did not publish its identity within 15s")


def _wait_dead(pid):
    from agent_index.rendezvous import pid_alive

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.1)
    raise AssertionError(f"owned service pid {pid} did not exit within 15s")


def _assert_queue(store, active_id, worker_pid, pending_id):
    active = store.get_task(active_id)
    assert active.status == "processing"
    assert active.worker_pid == worker_pid
    assert active.attempt_count == 1
    pending = store.get_task(pending_id)
    assert pending.status == "queued"
    assert pending.source == "git:queued"
    assert store.get_pending_count() == 1


def _assert_successor(config, route, worker_id, chunk_id, calls):
    from agent_index.client import AgentIndexClient

    client = AgentIndexClient(route.base_url)
    health = client.health()
    assert health["pid"] == route.pid
    assert health["version"] == route.version == __version__
    assert health["promoted"] is True
    assert health["status"] == "ok"
    marker = json.loads((config.home / "running-version.json").read_text())
    assert marker["pid"] == route.pid
    assert marker["version"] == __version__
    status = httpx.get(f"{route.base_url}/status", timeout=20).json()
    assert status["pid"] == route.pid
    assert status["index"]["chunks"] == 1
    assert status["indexing"]["active_task_id"] == worker_id
    assert status["indexing"]["running"] is True
    result = client.search("successor-query", limit=5, source="git:fixture",
                           language=None, repo=None)
    assert result["hits"][0]["chunk_id"] == chunk_id
    assert calls[-1] == ("/embed", {"text": "successor-query"})
    return client


def test_real_deploy_survives_updater_and_preserves_config_queue(
    config_file, config_data, tmp_path, warm_engine,  # noqa: F811 - imported pytest fixture
):
    from agent_index.config import machine_id
    from agent_index.indexing.task_store import TaskStore

    port, calls = warm_engine
    config_data.update({
        "data": str(tmp_path / "selected-data"),
        "routing": str(tmp_path / "selected-routing"),
        "engine": {"port": port, "mode": "external"},
    })
    config_data["sources"].append({"name": "git:queued", "path": str(tmp_path / "queued")})
    config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")
    config = load_config(config_file)
    chunk_id = _seed(config)
    worker_identity = tmp_path / "worker-identity.json"
    worker_script = """
import json,os,sys,time
from pathlib import Path
path = Path(sys.argv[1])
temporary = path.with_suffix('.tmp')
temporary.write_text(json.dumps({'pid':os.getpid()}), encoding='utf-8')
temporary.replace(path)
while True:
    time.sleep(0.1)
"""
    # This owned synthetic worker exercises real PID adoption/queue arbitration,
    # not source ingestion or model execution.
    with (tmp_path / "worker.log").open("w", encoding="utf-8") as worker_output:
        with owned_python(
            ["-c", worker_script, str(worker_identity)], cwd=tmp_path, stdout=worker_output,
        ) as worker:
            identity = _wait_file(worker_identity, worker)
            assert identity["pid"] == worker.pid
            store = TaskStore(config.data / "tasks.db")
            active = store.enqueue(source="git:fixture", trigger_source="isolated-test")
            assert store.dequeue_next().id == active.id
            store.set_worker(active.id, worker.pid, machine_id(), __version__)
            pending = store.enqueue(source="git:queued", trigger_source="isolated-test")
            arguments = [
                "-m", "agent_index_service", "deploy", "--config", str(config_file),
                "--health-timeout", "45", "--drain-timeout", "10",
            ]
            contaminated = dict(os.environ)
            contaminated.update({
                "AGENT_INDEX_HOME": str(tmp_path / "unselected-home"),
                "AGENT_INDEX_DATA_DIR": str(tmp_path / "unselected-data"),
                "AGENT_INDEX_ROUTING_DIR": str(tmp_path / "unselected-routing"),
                "AGENT_INDEX_ENGINE_MODE": "subprocess",
                "AGENT_INDEX_ENGINE_PORT": "1",
                "AGENT_INDEX_SOURCES": "git:unselected",
                "AGENT_INDEX_RUNTIME_VERSION": "unselected-version",
                "AGENT_INDEX_REPO": str(tmp_path / "unselected-repo"),
            })
            with (
                (tmp_path / "deploy-first.log").open("w", encoding="utf-8") as first_log,
                (tmp_path / "deploy-first.err").open("w", encoding="utf-8") as first_error,
            ):
                with owned_python(
                    arguments, cwd=tmp_path, stdout=first_log,
                    stderr=first_error, env=contaminated,
                ) as first_updater:
                    first_updater.wait(timeout=90)
                    first_log.flush()
                    first_text = (tmp_path / "deploy-first.log").read_text(encoding="utf-8")
                    assert first_updater.returncode == 0, (
                        first_text + (tmp_path / "deploy-first.err").read_text(encoding="utf-8")
                    )
                    first_result = json.loads(first_text)
                    assert first_result["ok"] is True
                    first_route = _wait_route(None, config.routing)
                    assert first_route.pid != first_updater.pid
                    _assert_successor(config, first_route, active.id, chunk_id, calls)
                    _assert_queue(store, active.id, worker.pid, pending.id)
                    assert worker.poll() is None
                    # The updater is already gone; exercise its survivor over
                    # multiple requests before performing a second real cutover.
                    time.sleep(1)
                    _assert_successor(config, first_route, active.id, chunk_id, calls)
                    with (
                        (tmp_path / "deploy-second.log").open("w", encoding="utf-8") as second_log,
                        (tmp_path / "deploy-second.err").open(
                            "w", encoding="utf-8",
                        ) as second_error,
                    ):
                        with owned_python(
                            arguments, cwd=tmp_path, stdout=second_log,
                            stderr=second_error, env=contaminated,
                        ) as second_updater:
                            second_updater.wait(timeout=90)
                            second_log.flush()
                            second_text = (
                                tmp_path / "deploy-second.log"
                            ).read_text(encoding="utf-8")
                            assert second_updater.returncode == 0, (
                                second_text
                                + (tmp_path / "deploy-second.err").read_text(encoding="utf-8")
                            )
                            second_result = json.loads(second_text)
                            assert second_result["ok"] is True
                            second_route = _wait_route(None, config.routing)
                            assert second_route.pid not in {first_route.pid, second_updater.pid}
                            client = _assert_successor(
                                config, second_route, active.id, chunk_id, calls,
                            )
                            _wait_dead(first_route.pid)
                            _assert_queue(store, active.id, worker.pid, pending.id)
                            assert worker.poll() is None
                            assert [path for path, _ in calls] == ["/embed"] * 3
                            assert client.shutdown()["shutdown"] is True
                            _wait_dead(second_route.pid)
    assert worker.poll() is not None
    assert not (config.home / "data").exists()
    assert not (tmp_path / ".agent-index").exists()
    for name in ("unselected-home", "unselected-data", "unselected-routing", "unselected-repo"):
        assert not (tmp_path / name).exists()


def test_owned_tree_is_reaped_after_assertion_failure(tmp_path):
    receipt = tmp_path / "owned-tree.json"
    script = """
import json,os,subprocess,sys,time
from pathlib import Path
from agent_procutil import no_window_kwargs,windowless_python,windowless_python_env
env = dict(os.environ)
env.update(windowless_python_env(sys.executable))
child = subprocess.Popen(
    [windowless_python(sys.executable), '-c', 'import time; time.sleep(120)'],
    env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL, **no_window_kwargs(),
)
path = Path(sys.argv[1])
temporary = path.with_suffix('.tmp')
temporary.write_text(json.dumps({'pid':os.getpid(),'child':child.pid}), encoding='utf-8')
temporary.replace(path)
while True:
    time.sleep(0.1)
"""
    with (tmp_path / "owned-tree.log").open("w", encoding="utf-8") as output:
        with pytest.raises(AssertionError, match="intentional cleanup check"):
            with owned_python(
                ["-c", script, str(receipt)], cwd=tmp_path, stdout=output,
            ) as process:
                identity = _wait_file(receipt, process)
                assert identity["pid"] == process.pid
                raise AssertionError("intentional cleanup check")
    assert process.poll() is not None
    _wait_dead(identity["child"])
