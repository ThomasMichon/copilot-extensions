"""Real loopback hosting with temporary persistence and an isolated warm RPC fixture."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import numpy as np
import pytest
import yaml
from _service_process import owned_python
from agent_procutil import no_window_kwargs

from agent_index_service import __version__
from agent_index_service.composition import core_environment
from agent_index_service.config import load_config


@pytest.fixture
def warm_engine():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def send_json(self, value):
            encoded = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            assert self.path in ("/ready", "/health")
            self.send_json({"ready": True, "status": "ready"})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            calls.append((self.path, body))
            if self.path == "/embed":
                self.send_json({"vector": [1.0] + [0.0] * 767})
            elif self.path == "/embed/batch":
                self.send_json({"vectors": [[1.0] + [0.0] * 767 for _ in body["texts"]]})
            else:
                assert self.path == "/spinup", "unexpected engine lifecycle/mutation request"
                self.send_json({"ready": True, "status": "ready"})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _seed(config):
    from agent_index.chunking.base import Chunk
    from agent_index.index_config import IndexConfig
    from agent_index.store.multi_model_store import MultiModelStore

    with core_environment(config):
        index = IndexConfig()
        store = MultiModelStore(index.lance_dir, content_table=index.content_table)
        store.register_model(index.model_profiles["code"])
        chunk = Chunk(
            content="Standalone controller fixture", file_path="fixture.md",
            chunk_type="module", language="markdown", line_start=1, line_end=1,
            source="git:fixture",
        )
        vectors = np.array([[1.0] + [0.0] * 767], dtype=np.float32)
        assert store.upsert("code", [chunk], vectors) == 1
        return chunk.chunk_id


def _wait_route(process, directory):
    from zdd.routing import read_active_endpoint

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            pytest.fail(f"host exited prematurely: {process.returncode}")
        route = read_active_endpoint(directory)
        if route is not None:
            try:
                response = httpx.get(f"{route.base_url}/health", timeout=1)
                if response.status_code == 200 and response.json().get("pid") == route.pid:
                    return route
            except httpx.TransportError:
                pass
        time.sleep(0.1)
    pytest.fail("isolated service failed to advertise healthy routing within 30 seconds")


def test_real_native_cli_health_uses_configured_not_ambient_identity(
    config_file, config_data, tmp_path, warm_engine, monkeypatch,
):
    config_data["installation_id"] = "persistent-native-instance"
    config_data["engine"] = {"port": warm_engine[0], "mode": "external"}
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config_data["listener"] = {"host": "127.0.0.1", "port": port}
    config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "ambient-spoof")
    with (tmp_path / "identity-host.log").open("w", encoding="utf-8") as output:
        with owned_python(
            ["-c", "import faulthandler,sys,time; "
             "faulthandler.dump_traceback_later(10,repeat=True); "
             "print('native-cli-interpreter-start',time.monotonic(),flush=True); "
             "from agent_index_service.__main__ import main; "
             "raise SystemExit(main(sys.argv[1:]))", "serve", "--config", str(config_file)],
            cwd=tmp_path, stdout=output,
        ) as process:
            started = time.monotonic()
            deadline = started + 30
            health = None
            while time.monotonic() < deadline:
                try:
                    health = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
                    if health.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            output.flush()
            assert health is not None and health.status_code == 200, (
                tmp_path / "identity-host.log"
            ).read_text(encoding="utf-8", errors="replace")[-8000:]
            elapsed = time.monotonic() - started
            assert elapsed <= 30, f"measured native CLI startup {elapsed:.2f}s exceeds the original30s gate"
            assert health.json()["pid"] == process.pid
            assert health.json()["installationId"] == "persistent-native-instance"
            assert health.json()["status"] == "ok"


def test_long_lived_http_reader_sees_external_commits_without_restart(
    config_file, config_data, tmp_path, warm_engine,
):
    from agent_index.indexing.task_store import TaskStore, TaskStatus
    repo = tmp_path / "fixture"
    repo.mkdir()
    (repo / "fixture.py").write_text("def fresh_fixture():\n    return 'fresh visibility'\n")
    for arguments in (
        ["init", "--quiet"], ["config", "user.name", "Fixture"],
        ["config", "user.email", "fixture@example.invalid"], ["add", "."],
        ["commit", "--quiet", "-m", "Seed fixture"],
    ):
        subprocess.run(["git", *arguments], cwd=repo, check=True, capture_output=True,
                       timeout=15, **no_window_kwargs())
    config_data["sources"] = [{"name": "git:fixture", "path": str(repo)}]
    config_data["limits"] = {"indexer_nice": 0}
    config_data["engine"] = {"port": warm_engine[0], "mode": "external"}
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config_data["listener"] = {"host": "127.0.0.1", "port": port}
    config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")
    config = load_config(config_file)
    host_script = (
        "import sys; from agent_index_service.__main__ import main; "
        "raise SystemExit(main(sys.argv[1:]))"
    )
    writer_script = """
import sys,numpy as np
from pathlib import Path
from agent_index_service.config import load_config
from agent_index_service.composition import core_environment
with core_environment(load_config(Path(sys.argv[1]))):
    from agent_index.index_config import IndexConfig
    from agent_index.chunking.base import Chunk
    from agent_index.store.multi_model_store import MultiModelStore
    config=IndexConfig()
    store=MultiModelStore(config.lance_dir,content_table=config.content_table)
    store.register_model(config.model_profiles['code'])
    chunk=Chunk(content='fresh visibility '+sys.argv[2],file_path=sys.argv[2]+'.md',
                chunk_type='module',language='markdown',line_start=1,line_end=1,
                source='git:fixture')
    assert store.upsert('code',[chunk],np.array([[1.0]+[0.0]*767],dtype=np.float32))==1
"""
    with (tmp_path / "visibility-host.log").open("w", encoding="utf-8") as output:
        with owned_python(
            ["-c", host_script, "serve", "--config", str(config_file)],
            cwd=tmp_path, stdout=output,
        ) as process:
            endpoint = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 30
            health = None
            while time.monotonic() < deadline:
                try:
                    health = httpx.get(endpoint + "/health", timeout=1)
                    if health.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            output.flush()
            assert health is not None and health.status_code == 200, (
                tmp_path / "visibility-host.log"
            ).read_text(encoding="utf-8", errors="replace")[-8000:]
            server_pid = health.json()["pid"]
            assert server_pid == process.pid
            initial = httpx.get(endpoint + "/search", params={"q": "fresh", "limit": 10}, timeout=20)
            assert initial.status_code == 200 and initial.json()["hits"] == []
            accepted = httpx.post(endpoint + "/reindex", json={
                "source": "git:fixture", "full": True,
            }, timeout=10).json()
            assert accepted["accepted"] is True
            tasks = TaskStore(config.data / "tasks.db")
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                task = tasks.get_task(accepted["task"]["id"])
                if task.status in (TaskStatus.COMPLETE, TaskStatus.PARTIAL, TaskStatus.FAILED):
                    break
                time.sleep(0.1)
            worker_log = config.data / "worker.log"
            assert task.status == TaskStatus.COMPLETE, (
                task, worker_log.read_text(encoding="utf-8", errors="replace")[-8000:]
                if worker_log.exists() else "no worker log",
            )
            ingested = httpx.get(endpoint + "/search", params={"q": "fresh", "limit": 10}, timeout=20)
            assert ingested.status_code == 200 and ingested.json()["available"] is True
            baseline_count = len(ingested.json()["hits"])
            assert baseline_count > 0
            assert any(path == "/embed/batch" for path, body in warm_engine[1])
            for number in (1, 2):
                written = subprocess.run(
                    [sys.executable, "-c", writer_script, str(config_file), f"record-{number}"],
                    cwd=tmp_path, capture_output=True, text=True, timeout=30,
                    **no_window_kwargs(),
                )
                assert written.returncode == 0, written.stderr
                response = httpx.get(endpoint + "/search", params={"q": "fresh", "limit": 10}, timeout=20)
                assert response.status_code == 200 and response.json()["available"] is True
                assert len(response.json()["hits"]) == baseline_count + number
                assert httpx.get(endpoint + "/health", timeout=5).json()["pid"] == server_pid


def test_real_host_legacy_client_native_persistence_and_warm_engine(
    config_file, config_data, tmp_path, warm_engine,
):
    from agent_index.client import AgentIndexClient

    port, calls = warm_engine
    config_data["engine"] = {"port": port, "mode": "external"}
    config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")
    config = load_config(config_file)
    chunk_id = _seed(config)
    script = """
import importlib.abc, importlib.machinery, sys
class NoModelStack(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch','sentence_transformers','agent_index_engine'}:
            return importlib.machinery.ModuleSpec(fullname, self)
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        raise AssertionError('model stack imported by controller: ' + module.__name__)
sys.meta_path.insert(0, NoModelStack())
from agent_index_service.__main__ import main
raise SystemExit(main(sys.argv[1:]))
"""
    log_path = tmp_path / "host.log"
    with log_path.open("w", encoding="utf-8") as output:
        with owned_python(
            ["-c", script, "serve", "--config", str(config_file)],
            cwd=tmp_path, stdout=output,
        ) as process:
            route = _wait_route(process, config.routing)
            assert route.pid == process.pid
            assert route.version == __version__
            client = AgentIndexClient(route.base_url)
            health = client.health()
            assert health["plugin"] == "agent-index"
            assert health["version"] == __version__
            assert health["status"] == "ok"
            marker = json.loads((config.home / "running-version.json").read_text())
            assert marker["version"] == __version__
            assert marker["pid"] == process.pid
            status = httpx.get(f"{route.base_url}/status", timeout=20).json()
            assert status["index"]["chunks"] == 1
            assert status["indexing"]["running"] is False  # No active indexing job.
            assert status["indexing"]["paused"] is False
            assert status["indexing"]["active_task_id"] is None
            assert (config.data / "tasks.db").is_file()
            observed = subprocess.run(
                [sys.executable, "-m", "agent_index_service", "status",
                 "--config", str(config_file)],
                env=dict(os.environ), cwd=tmp_path, capture_output=True, text=True,
                timeout=30, **no_window_kwargs(),
            )
            assert observed.returncode == 0, observed.stderr
            snapshot = json.loads(observed.stdout)
            assert snapshot["version"] == __version__
            assert snapshot["invoked_version"] == __version__
            assert snapshot["pid"] == process.pid
            result = client.search(
                "standalone", limit=5, source="git:fixture", language=None, repo=None,
            )
            assert result["hits"][0]["chunk_id"] == chunk_id
            assert calls == [("/embed", {"text": "standalone"})]
            assert client.drain(timeout=5, poll=0.1, force=False)["drained"] is True
            assert client.health()["status"] == "draining"
            with pytest.raises(httpx.HTTPStatusError) as exc:
                client.search("blocked", limit=5, source=None, language=None, repo=None)
            assert exc.value.response.status_code == 503
            assert client.undrain() == {"draining": False}
            assert client.adopt_relay() == {"adopted": False, "reason": "agent-index has no relay"}
            result = client.search(
                "still-warm", limit=5, source="git:fixture", language=None, repo=None,
            )
            assert result["hits"][0]["chunk_id"] == chunk_id
            assert [path for path, _body in calls] == ["/embed", "/embed"]
            assert client.shutdown()["shutdown"] is True
            process.wait(timeout=15)
            assert process.returncode == 0
    assert not (config.home / "running-version.json").exists()
    assert not (tmp_path / ".agent-index").exists()
    assert "Task runner startup skipped" not in log_path.read_text(encoding="utf-8")


def test_explicit_source_specs_use_paths_not_registry(config_file, monkeypatch):
    from agent_index import config as core_config
    from agent_index.indexing.engine import _connector_kwargs, configured_source_specs

    config = load_config(config_file)
    monkeypatch.setattr(core_config, "_agent_worktrees_home", lambda: pytest.fail("registry"))
    monkeypatch.setattr(core_config, "_local_project_roots", lambda: pytest.fail("repo sweep"))
    with core_environment(config):
        specs = configured_source_specs()
        assert len(specs) == 1
        assert specs[0].name == "git:fixture"
        assert _connector_kwargs(specs[0]) == {"repo_path": config.sources[0]["_repo_path"]}


def test_existing_passive_promotion_and_persistent_queue(config_file):
    from agent_index.server import build_app
    from fastapi.testclient import TestClient

    config = load_config(config_file)
    with core_environment(config):
        app = build_app(passive=True)
        with TestClient(app) as client:
            assert client.get("/health").json()["status"] == "passive"
            assert client.post("/reindex", json={"source": "git:fixture"}).status_code == 503
            assert client.post("/promote").json()["promoted"] is True
            assert client.get("/health").json()["version"] == __version__
            runner = app.state.task_runner

            async def pause_dequeue():
                await runner.drain(timeout=5, poll=0.1)

            client.portal.call(pause_dequeue)
            first = client.post("/reindex", json={
                "source": "git:fixture", "full": False,
            }).json()
            second = client.post("/reindex", json={
                "source": "git:fixture", "full": True,
            }).json()
            assert first["accepted"] is True
            assert second["accepted"] is True
            assert first["task"]["id"] != second["task"]["id"]
            assert app.state.task_store.get_pending_count() == 1
            assert runner.active_task_id is None
            assert (config.data / "tasks.db").is_file()
