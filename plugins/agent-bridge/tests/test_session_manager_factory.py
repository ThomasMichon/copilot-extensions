"""Regression tests for ``session_manager_from_config``.

Both config-driven entrypoints -- the HTTP daemon (``app.py``) and ACP-agent
mode (``__main__._cmd_agent``) -- must build the SessionManager through this
single factory so session-host settings are wired identically. Session Hosts
are always on (dotfiles#1478); the factory forwards the operator's host tunables.
"""

from __future__ import annotations

from agent_bridge.db import Database
from agent_bridge.models import ServiceConfig
from agent_bridge.session_manager import session_manager_from_config


def test_factory_builds_host_backed_manager(tmp_db: Database) -> None:
    # Session Hosts are the only mode: a config-driven manager is always
    # survivable (it always stands up a host index).
    mgr = session_manager_from_config(tmp_db, ServiceConfig())
    assert mgr._host_index is not None


def test_factory_wires_companion_session_host_params(tmp_db: Database) -> None:
    cfg = ServiceConfig(
        idle_reap_ttl_seconds=600,
        graceful_cancel_settle_seconds=45,
        live_stall_interrupt_after_s=900,
        session_host_unexpected_reap_seconds=90,
    )
    mgr = session_manager_from_config(tmp_db, cfg)
    assert mgr._idle_reap_ttl_seconds == 600
    assert mgr._graceful_cancel_settle_seconds == 45
    assert mgr._live_stall_interrupt_after_s == 900
    assert mgr._session_host_unexpected_reap_seconds == 90
