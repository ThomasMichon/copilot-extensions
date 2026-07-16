"""Regression tests for ``session_manager_from_config``.

Both config-driven entrypoints -- the HTTP daemon (``app.py``) and ACP-agent
mode (``__main__._cmd_agent``) -- must build the SessionManager through this
single factory so session-host settings are honored identically. ACP-agent mode
previously constructed SessionManager inline and omitted ``session_host_enabled``,
silently disabling CodeSpace SSH-drop survival for that process (#145/#177).
"""

from __future__ import annotations

from agent_bridge.db import Database
from agent_bridge.models import ServiceConfig
from agent_bridge.session_manager import session_manager_from_config


def test_factory_honors_session_host_default_on(tmp_db: Database) -> None:
    # ServiceConfig default is session-host ON (#145/#177); the factory must
    # carry that through so a config-driven manager is survivable by default.
    mgr = session_manager_from_config(tmp_db, ServiceConfig())
    assert mgr._session_host_enabled is True


def test_factory_honors_explicit_opt_out(tmp_db: Database) -> None:
    mgr = session_manager_from_config(
        tmp_db, ServiceConfig(session_host_enabled=False)
    )
    assert mgr._session_host_enabled is False


def test_factory_wires_companion_session_host_params(tmp_db: Database) -> None:
    cfg = ServiceConfig(
        session_host_enabled=True,
        idle_reap_ttl_seconds=600,
        graceful_cancel_settle_seconds=45,
        live_stall_interrupt_after_s=900,
        session_host_awkward_reap_seconds=90,
    )
    mgr = session_manager_from_config(tmp_db, cfg)
    assert mgr._session_host_enabled is True
    assert mgr._idle_reap_ttl_seconds == 600
    assert mgr._graceful_cancel_settle_seconds == 45
    assert mgr._live_stall_interrupt_after_s == 900
    assert mgr._session_host_awkward_reap_seconds == 90
