"""The daemon's own real generation id must reach ``running-version.json``
once its ``SessionManager`` boots (see ``runtime_version.py``'s own module
docstring for why this file, not ``/health``).

Drives the app through a REAL lifespan (``TestClient`` as a context manager,
not a bare app object) so this exercises the actual boot sequence:
``write_running_version()`` (no id yet) followed by ``session_manager_from_
config()`` constructing the real ``SessionManager``, then this module's
follow-up call recording its real, just-computed id.

Two ``enable_credential_relay=False`` shapes exist and must be told apart: a
``--passive`` ZDD-cutover successor (``app.state.passive = True``, the
existing launch-state flag ``service_start_cli.py`` already sets) DOES get
promoted and stages its id for later promotion-time consumption; an elevated
sub-daemon (``app.state.passive`` absent/``False``) shares the primary's
runtime dir and is NEVER promoted, so it must not touch anything at all.
"""

from __future__ import annotations

import json
import os

from fastapi.testclient import TestClient

from agent_bridge.app import create_app
from agent_bridge.models import ServiceConfig
from agent_bridge.runtime_version import (
    PENDING_GENERATION_IDS_FILE,
    RUNNING_VERSION_FILE,
)


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


def test_lifespan_stages_generation_id_for_a_passive_successor(
    tmp_path, monkeypatch
):
    # A --passive ZDD-cutover successor sets enable_credential_relay=False
    # (an unrelated relay-binding reason) but DOES get promoted -- its real
    # id must be staged (not yet written into the canonical marker, which
    # the earlier boot-time write_running_version() call is itself skipped
    # for, under this same enable_credential_relay=False reason -- it isn't
    # promoted yet).
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(runtime_dir))

    cfg = ServiceConfig(
        port=0, bind="127.0.0.1", db_path=str(tmp_path / "test.db"),
        enable_credential_relay=False,
    )
    app = create_app(config=cfg, token="test-token")
    app.state.passive = True  # set by service_start_cli.py's --passive path

    with TestClient(app) as c:
        mgr = app.state.session_manager
        real_generation_id = mgr._generation_id
        assert real_generation_id

        # Not written into the canonical marker yet -- it isn't promoted.
        marker_path = runtime_dir / RUNNING_VERSION_FILE
        assert not marker_path.exists()

        pending_path = runtime_dir / PENDING_GENERATION_IDS_FILE
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        assert pending[str(os.getpid())] == real_generation_id

        c.get("/ui")


def test_lifespan_does_not_stage_generation_id_for_elevated_sub_daemon(
    tmp_path, monkeypatch
):
    # The elevated sub-daemon ALSO sets enable_credential_relay=False, but
    # app.state.passive stays unset/False (it is never promoted) -- it
    # shares the primary's runtime dir and must never touch the marker OR
    # the pending-staging file, or it would silently steal either from
    # whichever daemon actually owns it.
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(runtime_dir))

    cfg = ServiceConfig(
        port=0, bind="127.0.0.1", db_path=str(tmp_path / "test.db"),
        enable_credential_relay=False,
    )
    app = create_app(config=cfg, token="test-token")
    # app.state.passive deliberately left unset (mirrors a real elevated
    # sub-daemon boot, which never sets it).

    with TestClient(app) as c:
        # No marker at all: the earlier boot-time write_running_version()
        # call is ALSO gated on enable_credential_relay, so an elevated
        # sub-daemon writes nothing here, by design (dotfiles #533 caveat).
        assert not (runtime_dir / RUNNING_VERSION_FILE).exists()
        assert not (runtime_dir / PENDING_GENERATION_IDS_FILE).exists()

        c.get("/ui")
