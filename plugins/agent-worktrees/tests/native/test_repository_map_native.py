"""Real local map transport and cold CLI, without an operator daemon."""

import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from agent_procutil import no_window_flags

from agent_worktrees import (
    config as cfg, repository_map as maps, repos, status_monitor_cutover as control,
)


@pytest.fixture
def published(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    for name in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "PYTHONPATH"):
        monkeypatch.delenv(name, raising=False)
    root = cfg.install_dir()
    root.mkdir(parents=True)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    path = repos._repos_yaml_path()
    path.write_text(json.dumps({"repos": {
        f"library-{index:03d}": {"class": "reference", repos._current_platform(): str(checkout)}
        for index in range(100)
    }}), encoding="utf-8")
    control.control_token_path().write_text("fixture-map", encoding="utf-8")
    owner = maps.RepositoryMapRuntime(root)
    owner.activate()
    owner.refresh()
    server = control.ControlServer(
        lambda action, payload: owner.read(payload), token="fixture-map",
    )
    server.start()
    control.publish_route(server.port, pid=os.getpid(), version="fixture")
    try:
        yield owner, server, path, checkout
    finally:
        owner.close_admission("shutdown")
        server.close()


def test_actual_cli_reads_complete_map_and_observes_changes_and_admission(published, monkeypatch):
    owner, server, path, checkout = published
    read_registry = maps.read_registry
    monkeypatch.setattr(maps, "read_registry", lambda *a, **kw: pytest.fail("handler read registry"))
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "repos", "map", "--json"],
        cwd=checkout, capture_output=True, text=True, timeout=5,
        creationflags=no_window_flags(),
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert len(data["repos"]) == 100 and data["inventory_authority"] == "complete"
    assert data["projection"]["scope"] == owner.scope
    assert time.monotonic() - started < 5
    monkeypatch.setattr(maps, "read_registry", read_registry)
    path.write_text('{"repos":{}}', encoding="utf-8")
    owner.refresh()
    monkeypatch.setattr(maps, "read_registry", lambda *a, **kw: pytest.fail("handler read registry"))
    updated = control.ControlClient(
        f"http://127.0.0.1:{server.port}", token="fixture-map", timeout=1,
    ).repositories_read({"repository_map_version": 1, "scope": owner.scope})
    assert updated["repos"] == [] and updated["inventory_authority"] == "complete"
    assert updated["projection"]["source_revision"] != data["projection"]["source_revision"]
    owner.close_admission("shutdown")
    second = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "repos", "map", "--json"],
        cwd=checkout, capture_output=True, text=True, timeout=5,
        creationflags=no_window_flags(),
    )
    assert second.returncode == 2 and "admission is closed" in second.stdout
    assert server.current_read_count(maps.READ_ACTION) == 0


def test_independent_readers_and_drain_retain_transport_custody(published, monkeypatch):
    owner, server, path, checkout = published
    entered, release = threading.Event(), threading.Event()
    original = server._handle

    def blocked(action, payload):
        entered.set()
        assert release.wait(3)
        return original(action, payload)

    monkeypatch.setattr(server, "_handle", blocked)
    client = control.ControlClient(
        f"http://127.0.0.1:{server.port}", token="fixture-map", timeout=4,
    )
    request = {"repository_map_version": 1, "scope": owner.scope}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as readers:
        futures = [readers.submit(client.repositories_read, request) for _ in range(2)]
        try:
            assert entered.wait(2)
            deadline = time.monotonic() + 2
            while server.current_read_count(maps.READ_ACTION) != 2 and time.monotonic() < deadline:
                time.sleep(.01)
            assert server.current_read_count(maps.READ_ACTION) == 2
            assert server.current_read_count() == 0
            owner.close_admission("draining")
        finally:
            release.set()
        assert all(f.result(timeout=3)["error"]["code"] == "draining" for f in futures)
    with pytest.raises(ValueError):
        control.ControlClient(
            f"http://127.0.0.1:{server.port}", token="wrong", timeout=1,
        ).repositories_read(request)


def test_real_dispatch_catalog_consumes_one_whole_map(published, monkeypatch, tmp_path):
    provider_python = os.environ.get("REPOSITORY_MAP_DISPATCH_PYTHON")
    if not provider_python:
        pytest.skip("prepare Dispatch and set REPOSITORY_MAP_DISPATCH_PYTHON for cross-distribution acceptance")
    assert Path(provider_python).is_absolute() and Path(provider_python).is_file()
    owner, server, path, checkout = published
    templates = checkout / ".copilot-extensions" / "agent-dispatch" / "templates"
    templates.mkdir(parents=True)
    (templates / "source.json").write_text(json.dumps({
        "kind": "emitter", "spec": {"command": ["never-execute"]},
    }), encoding="utf-8")
    (templates / "index.json").write_text(json.dumps({
        "schema": "agent-dispatch.template-index", "version": 1,
        "templates": [{
            "id": "source", "title": "Native source", "description": "Inert fixture.",
            "kind": "emitter", "declaration": "source.json",
        }],
    }), encoding="utf-8")
    home = Path(os.environ["AGENT_HOME"])
    env = dict(os.environ)
    env.update({
        "HOME": str(home), "USERPROFILE": str(home),
        "AGENT_DISPATCH_REGISTRAR_DIR": str(tmp_path / "registrar"),
        "AGENT_DISPATCH_REGISTRAR_DROPINS_DIR": str(tmp_path / "dropins"),
        "AGENT_DISPATCH_OVERRIDES": str(tmp_path / "overrides.json"),
        "AGENT_DISPATCH_INSTALL_DIR": str(tmp_path / "install"),
        "AGENT_DISPATCH_RUN_DIR": str(tmp_path / "run"),
        "AGENT_DISPATCH_NO_SELFPROVISION": "1",
        "AGENT_DISPATCH_NO_AUTOSTART": "1",
        "MAP_ENGINE_PYTHON": sys.executable,
    })
    code = """
import json, os, time
from agent_dispatch import repository_resource_capture as capture, worker_templates
from agent_dispatch.workers_catalog import catalog
capture.agent_worktrees_launch_prefix = lambda: [os.environ['MAP_ENGINE_PYTHON'], '-m', 'agent_worktrees']
real = worker_templates.run_agent_worktrees_capture
calls = []
def observed(*args, **kwargs):
    calls.append(args)
    return real(*args, **kwargs)
worker_templates.run_agent_worktrees_capture = observed
started = time.monotonic()
data = catalog()
assert calls == [('repos', 'map', '--json')], calls
assert data['summary']['template_discovery']['state'] == 'complete', data['summary']
rows = [row for row in data['entries'] if row['title'] == 'Native source']
assert len(rows) == 100 and all(row['instantiable'] == 'false' for row in rows)
print(json.dumps({'map_calls': len(calls), 'templates': len(rows), 'seconds': time.monotonic() - started}))
"""
    result = subprocess.run(
        [provider_python, "-I", "-B", "-c", code], cwd=checkout,
        capture_output=True, text=True, timeout=20, env=env,
        creationflags=no_window_flags(),
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["map_calls"] == 1 and receipt["templates"] == 100
    assert receipt["seconds"] < 20
    assert not (tmp_path / "run").exists()
