"""The daemon's own real generation id must reach ``running-version.json``
once its ``SessionManager`` boots (agent-bridge-unified-zdd-cutover effort's
abrupt-termination-drill real-id gap -- see ``runtime_version.py``'s own
module docstring for why this file, not ``/health``).

Drives the app through a REAL lifespan (``TestClient`` as a context manager,
not a bare app object) so this exercises the actual boot sequence:
``write_running_version()`` (no id yet) followed by ``session_manager_from_
config()`` constructing the real ``SessionManager``, then this module's
follow-up ``set_running_generation_id()`` call recording its real,
just-computed id into the SAME marker file.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from agent_bridge.app import create_app
from agent_bridge.models import ServiceConfig
from agent_bridge.runtime_version import RUNNING_VERSION_FILE


def test_lifespan_records_real_generation_id_into_running_version_marker(
    tmp_path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(runtime_dir))

    cfg = ServiceConfig(
        port=0, bind="127.0.0.1", db_path=str(tmp_path / "test.db"),
    )
    app = create_app(config=cfg, token="test-token")

    with TestClient(app) as c:
        mgr = app.state.session_manager
        real_generation_id = mgr._generation_id
        assert real_generation_id  # sanity: the manager actually computed one

        marker_path = runtime_dir / RUNNING_VERSION_FILE
        data = json.loads(marker_path.read_text(encoding="utf-8"))
        assert data["generation_id"] == real_generation_id
        # The earlier boot-time write's own fields must survive the
        # follow-up merge untouched.
        assert data["pid"]
        assert data["version"]
        assert data["started_at"]

        # Use the client so the ASGI lifespan teardown below has nothing
        # pending -- silences an unrelated "unclosed" resource warning.
        c.get("/ui")
