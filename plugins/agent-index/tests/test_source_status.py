"""Real loopback HTTP status must share the selected serving read fence."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

import httpx
import numpy as np
import pytest
import uvicorn
from agent_procutil import no_window_kwargs
from test_source_aware_store import chunk, cold as cold

from agent_index.server import build_app
from agent_index.store.source_layout import MARKER, SourceLayoutError, validate_source_store


@contextmanager
def loopback_status(monkeypatch, config, tmp_path):
    monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(config.data_dir))
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(
        build_app(passive=True), log_config=None, access_log=False, lifespan="on",
    ))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(0.02)
        with httpx.Client(base_url=endpoint, trust_env=False, timeout=3) as client:
            yield client
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive()


@contextmanager
def held_writer(config, tmp_path):
    ready = tmp_path / "writer-ready"
    code = (
        "import sys,pathlib; from agent_index.store.multi_model_store import MultiModelStore;"
        " s=MultiModelStore(sys.argv[1]);"
        "\nwith s._source_domain.write(s.content_store.db):"
        "\n pathlib.Path(sys.argv[2]).touch(); sys.stdin.readline()"
    )
    child = subprocess.Popen(  # noqa: S603 -- fixed owned fixture, no live state
        [sys.executable, "-I", "-u", "-c", code, str(config.lance_dir), str(ready)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **no_window_kwargs(),
    )
    try:
        deadline = time.monotonic() + 20
        while not ready.exists():
            assert child.poll() is None and time.monotonic() < deadline
            time.sleep(0.02)
        yield
    finally:
        try:
            out, err = child.communicate("\n", timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=5)
            raise
        assert child.returncode == 0, out + err


@pytest.mark.timeout(90)
@pytest.mark.parametrize("refusal", ["dirty", "busy", "version-drift"])
def test_http_status_refuses_selected_inventory_under_serving_fence(
    cold, monkeypatch, tmp_path, refusal,
):
    config, store = cold
    item = chunk()
    store.upsert("small", [item], np.array([[1, 0]], dtype=np.float32))
    vector_name = store.get_vector_table("small").table_name
    domain = store._source_domain
    table = store.content_store._get_or_create_table()
    count, search = type(table).count_rows, type(table).search
    observed = []

    def counted(table, *args, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            assert getattr(domain._local, "reading", False)
            observed.append("count")
        return count(table, *args, **kwargs)

    def scanned(table, *args, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            assert getattr(domain._local, "reading", False)
            observed.append("scan")
        return search(table, *args, **kwargs)

    monkeypatch.setattr(type(table), "count_rows", counted)
    monkeypatch.setattr(type(table), "search", scanned)

    def measured(client):
        before = (config.lance_dir / MARKER).read_bytes()
        response = client.get("/status", params={"sources": "true"})
        assert response.status_code == 200
        assert response.json()["index"] == {
            "chunks": 1, "available": True,
            "tables": {"chunks": 1, vector_name: 1}, "sources": {"git:alpha": 1},
        }
        assert "count" in observed and "scan" in observed
        assert (config.lance_dir / MARKER).read_bytes() == before
        observed.clear()

    def refused(client):
        before = (config.lance_dir / MARKER).read_bytes()
        for params in ({}, {"sources": "true"}):
            response = client.get("/status", params=params)
            assert response.status_code == 200
            inventory = response.json()["index"]
            assert inventory["available"] is None and inventory["chunks"] is None
            assert inventory["tables"] == inventory["sources"] == {}
            assert inventory["error"].startswith("SourceLayoutError:")
        assert not observed
        assert (config.lance_dir / MARKER).read_bytes() == before

    with loopback_status(monkeypatch, config, tmp_path) as client:
        measured(client)
        if refusal == "busy":
            with held_writer(config, tmp_path):
                refused(client)
        elif refusal == "dirty":
            with pytest.raises(RuntimeError, match="before effect"):
                with domain.write(store.content_store.db):
                    raise RuntimeError("fixture interrupted before effect")
            refused(client)
            validate_source_store(store)
        else:
            vector = store.get_vector_table("small")._get_or_create_table()
            vector.update(values={"indexed_at": 123.0})
            refused(client)
            with pytest.raises(SourceLayoutError, match="version drift"):
                validate_source_store(store)
            return  # Unknown external drift is never repaired by the status request.
        measured(client)


def test_http_status_legacy_inventory_unchanged(monkeypatch, tmp_path):
    from agent_index.index_config import IndexConfig, ModelProfile
    from agent_index.store.multi_model_store import MultiModelStore

    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    profile = ModelProfile("small", "fixture-only", dim=2)
    config = IndexConfig(data_dir=tmp_path / "legacy", model_profiles={"small": profile})
    store = MultiModelStore(config.lance_dir)
    store.register_model(profile)
    store.upsert("small", [chunk()], np.array([[1, 0]], dtype=np.float32))
    with loopback_status(monkeypatch, config, tmp_path) as client:
        response = client.get("/status", params={"sources": "true"})
        assert response.status_code == 200
        assert response.json()["index"] == {
            "chunks": 1, "available": True,
            "tables": {"chunks": 1, store.get_vector_table("small").table_name: 1},
            "sources": {"git:alpha": 1},
        }


def test_fake_engine_http_surface_stays_dependency_light(tmp_path):
    code = """
import sys
from types import SimpleNamespace
class BlockStore:
    def find_spec(self, name, path, target=None):
        if name.split('.')[0] in ('pyarrow', 'lancedb', 'torch', 'transformers'):
            raise ImportError('fixture blocked store/model dependency: ' + name)
        return None
sys.meta_path.insert(0, BlockStore())
from agent_index.server import build_app
from agent_index.search import engine
from fastapi.testclient import TestClient
engine.create_search_engine = lambda: SimpleNamespace(search=lambda *a, **kw: [])
client = TestClient(build_app())
response = client.get('/search', params={'q': 'fixture'})
assert response.status_code == 200 and response.json()['available'] is True, response.text
response = client.get('/status', params={'sources': 'true'})
assert response.status_code == 200 and response.json()['index']['available'] is None
assert not {'pyarrow', 'lancedb', 'torch', 'transformers'} & sys.modules.keys()
"""
    env = os.environ.copy()
    env.pop("AGENT_INDEX_STORE_LAYOUT", None)
    env["AGENT_INDEX_HOME"] = str(tmp_path / "home")
    env["AGENT_INDEX_DATA_DIR"] = str(tmp_path / "data")
    result = subprocess.run(  # noqa: S603 -- fixed isolated fixture program
        [sys.executable, "-I", "-c", code], capture_output=True, text=True,
        check=False, timeout=30, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
