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

Two ``enable_credential_relay=False`` shapes exist and must be told apart
(review-caught gap): a ``--passive`` ZDD-cutover successor (``is_passive=
True``) DOES get promoted and needs its id recorded; an elevated sub-daemon
(``is_passive=False``) shares the primary's runtime dir and is NEVER
promoted, so it must not touch the marker at all.
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


def test_lifespan_records_generation_id_for_a_passive_successor(
    tmp_path, monkeypatch
):
    # A --passive ZDD-cutover successor sets enable_credential_relay=False
    # (an unrelated relay-binding reason) but DOES get promoted -- its
    # is_passive=True must still get its real id recorded, even though the
    # earlier boot-time write_running_version() call is itself skipped for
    # this same enable_credential_relay=False reason.
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(runtime_dir))

    cfg = ServiceConfig(
        port=0, bind="127.0.0.1", db_path=str(tmp_path / "test.db"),
        enable_credential_relay=False, is_passive=True,
    )
    app = create_app(config=cfg, token="test-token")

    with TestClient(app) as c:
        mgr = app.state.session_manager
        real_generation_id = mgr._generation_id
        assert real_generation_id

        marker_path = runtime_dir / RUNNING_VERSION_FILE
        data = json.loads(marker_path.read_text(encoding="utf-8"))
        assert data["generation_id"] == real_generation_id

        c.get("/ui")


def test_lifespan_does_not_record_generation_id_for_elevated_sub_daemon(
    tmp_path, monkeypatch
):
    # The elevated sub-daemon ALSO sets enable_credential_relay=False, but
    # is_passive stays False (it is never promoted) -- it shares the
    # primary's runtime dir and must never write into the shared marker at
    # all, not even just the generation_id field, or it would silently
    # steal the marker from whichever daemon actually owns it.
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(runtime_dir))

    cfg = ServiceConfig(
        port=0, bind="127.0.0.1", db_path=str(tmp_path / "test.db"),
        enable_credential_relay=False, is_passive=False,
    )
    app = create_app(config=cfg, token="test-token")

    with TestClient(app) as c:
        # No marker at all: the earlier boot-time write_running_version()
        # call is ALSO gated on enable_credential_relay, so an elevated
        # sub-daemon writes nothing here, by design (dotfiles #533 caveat).
        marker_path = runtime_dir / RUNNING_VERSION_FILE
        assert not marker_path.exists()

        c.get("/ui")
