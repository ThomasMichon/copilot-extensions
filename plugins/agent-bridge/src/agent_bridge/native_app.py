"""Compose native hosting with existing provider and daemon ownership."""
from __future__ import annotations


def initialize(app, mgr, db_path):
    from .native_manager import NativeManager
    from .session_manager import _codespace_claim_key, _ACTIVE_STATES

    def native_provider_command(namespace="codespace"):
        provider = app.state.resolver.namespace_resolvers.get(namespace)
        return getattr(provider, "management_command", None)

    def acp_busy(codespace):
        for session in getattr(mgr, "_sessions", {}).values():
            container = getattr(session.target, "container", None)
            if isinstance(container, dict) and codespace == f"container:{container.get('name')}" and session.status in _ACTIVE_STATES:
                return True
            key = _codespace_claim_key(session.target)
            target_codespace = getattr(session.target, "codespace", None)
            name = target_codespace.get("name") if isinstance(target_codespace, dict) else (key[0] if key else None)
            if name == codespace and session.status in _ACTIVE_STATES:
                return True
        return False

    def owns_native_control():
        if not app.state.ready or getattr(mgr, "_draining", False):
            return False
        if getattr(app.state, "publish_on_ready", False):
            import os
            from zdd.routing import read_active_endpoint
            from .config import config_dir

            endpoint = read_active_endpoint(config_dir(), verify_listener=False)
            return endpoint is not None and endpoint.pid == os.getpid()
        return True

    app.state.native_manager = NativeManager(
        db_path.parent / "native-executions", native_provider_command,
        acp_busy=acp_busy, serving=owns_native_control,
    )
    mgr.native_manager = app.state.native_manager
    mgr.native_guard = app.state.native_manager.assert_acp_allowed
    app.state.native_manager.start_monitor()

