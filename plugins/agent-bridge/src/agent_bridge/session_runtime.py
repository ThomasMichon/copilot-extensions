"""Session-manager construction and effective runtime policy boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .db import Database
from .models import ServiceConfig

if TYPE_CHECKING:
    from .session_manager import SessionManager
    from .session_snapshot import SessionSnapshot


class _SessionRuntimePolicyMixin:
    def effective_runtime_settings(self) -> dict[str, Any]:
        """Copy the manager's current policy, not a reloaded config's defaults.

        Called synchronously on the owning event loop by private capture.
        Session/host ownership, draining and running tasks are not policy.
        """
        return {
            "context_thresholds": self._thresholds.model_dump(mode="json"),
            "auto_handoff": self._auto_handoff.model_dump(mode="json"),
            "timeouts": self._timeouts.model_dump(mode="json"),
            "retention": self._retention.model_dump(mode="json"),
            "session_host_state_dir": str(self._host_index._path.parent.absolute()),
            "session_host_stale_reap_seconds": self._session_host_stale_reap_seconds,
            "graceful_cancel_settle_seconds": self._graceful_cancel_settle_seconds,
            "cancel_turns_on_redeploy": self._cancel_turns_on_redeploy,
            "idle_reap_ttl_seconds": self._idle_reap_ttl_seconds,
            "live_stall_interrupt_after_s": self._live_stall_interrupt_after_s,
            "session_host_unexpected_reap_seconds": self._session_host_unexpected_reap_seconds,
            "session_host_active_reap_seconds": self._session_host_active_reap_seconds,
            "drain_auto_release_s": self._drain_auto_release_s,
            "drain_warn_interval_s": self._drain_warn_interval_s,
        }


def session_manager_from_config(
    db: Database, cfg: ServiceConfig, *, session_host_state_dir: str | None = None,
    drain_auto_release_s: float | None = None, drain_warn_interval_s: float | None = None,
    read_only_rehydrate: bool = False,
    captured_sessions: SessionSnapshot | None = None,
) -> SessionManager:
    """Build a :class:`SessionManager` wired from service config.

    The **single** construction site for a config-driven manager, so every
    entrypoint -- the HTTP daemon (``app.py``) and ACP-agent mode
    (``__main__._cmd_agent``) -- wires the same session-host settings. Session
    Hosts are always on (dotfiles#1478); this factory forwards the operator's
    host tunables (reap/idle/stall budgets). Route every config-driven
    construction through here.

    Private successor restoration supplies captured manager-only settings and
    read-only hydration. Ordinary callers retain the existing defaults.
    """
    from .session_manager import SessionManager

    return SessionManager(
        db,
        session_host_state_dir=session_host_state_dir,
        drain_auto_release_s=drain_auto_release_s,
        drain_warn_interval_s=drain_warn_interval_s,
        read_only_rehydrate=read_only_rehydrate,
        captured_sessions=captured_sessions,
        context_thresholds=cfg.context_thresholds,
        auto_handoff=cfg.auto_handoff,
        timeouts=cfg.timeouts,
        retention=cfg.retention,
        remote_acp_targets=cfg.remote_acp,
        session_host_stale_reap_seconds=cfg.session_host_stale_reap_seconds,
        graceful_cancel_settle_seconds=cfg.graceful_cancel_settle_seconds,
        cancel_turns_on_redeploy=cfg.cancel_turns_on_redeploy,
        idle_reap_ttl_seconds=cfg.idle_reap_ttl_seconds,
        live_stall_interrupt_after_s=cfg.live_stall_interrupt_after_s,
        session_host_unexpected_reap_seconds=cfg.session_host_unexpected_reap_seconds,
        session_host_active_reap_seconds=cfg.session_host_active_reap_seconds,
    )
