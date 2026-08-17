from __future__ import annotations

import sys

from fastapi.testclient import TestClient

from agent_index.server import build_app


def test_status_degrades_without_index(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setitem(sys.modules, "lancedb", None)

    response = TestClient(build_app()).get("/status")

    assert response.status_code == 200
    payload = response.json()
    # Store library unavailable => "unknown", NOT a fabricated empty index.
    assert payload["index"]["chunks"] is None
    assert payload["index"]["available"] is None
