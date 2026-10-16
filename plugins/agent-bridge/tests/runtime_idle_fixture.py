"""Actual pending-manager graph used by the native ACP continuation fixture."""

from __future__ import annotations

import os
import socket
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from zdd.private_protocol import Deadline, write_frame
from zdd.private_receiver import Operation
from zdd.routing import Endpoint

from agent_bridge.agent_registry_resolver import AgentResolver
from agent_bridge.agent_registry_common import AgentConfig
from agent_bridge.app import create_app
from agent_bridge.app_routes import configure_serving_app
from agent_bridge.db import Database
from agent_bridge.effective_runtime import RuntimeOrigins, ServingRuntimeCapture
from agent_bridge.launch_runtime import executing_executable
from agent_bridge.models import ServiceConfig
from agent_bridge.runtime_controls import RuntimeControls
from agent_bridge.runtime_idle import PendingIdleSession
from agent_bridge.runtime_transfer import AuthenticatedChannel, PendingRuntime, _self, receive_runtime
from agent_bridge.session_host.host_index import HostRecord
from agent_bridge.session_host.private_receipt import VerifiedHostReceipt
from agent_bridge.session_manager import Session, session_manager_from_config
from agent_bridge.session_snapshot import SessionSnapshot
from test_roster_runtime import warm_roster
from test_runtime_transfer import MemoryChannel
from test_worktree_runtime import cache_graph
from test_live_runtime import represented_graph


def capture_runtime(
    root: Path, db: Database, session: Session, original: VerifiedHostReceipt,
) -> dict[str, object]:
    config = ServiceConfig(db_path=str(db.db_path), enable_credential_relay=False)
    app = configure_serving_app(config, "synthetic-runtime-auth", lifespan=None)
    app.middleware_stack = app.build_middleware_stack()
    app.state.ui_login_codes = {
        "unused-native-code": time.monotonic() + 60.0,
        "expired-native-code": time.monotonic() - 1.0,
    }
    manager = session_manager_from_config(
        db, config, read_only_rehydrate=True,
        captured_sessions=SessionSnapshot(1, "metadata-events-native-host-observations-v1", []),
    )
    resolver = AgentResolver({"actual": AgentConfig("actual")}, {})
    manager.set_resolver(resolver)
    manager._sessions = {session.session_id: session}
    host = original.private_payload(Deadline(10))
    manager._host_index._records[session.session_id] = HostRecord(
        session.session_id, host["data_endpoint"]["port"], host["host"]["pid"], host["child_pid"],
    )
    app.state.config, app.state.db = config, db
    app.state.session_manager, app.state.resolver = manager, resolver
    app.state.auth_token = "synthetic-runtime-auth"
    app.state.ready = app.state.topology_ready = True
    app.state.credential_relay = None
    warm_roster(app)
    cache_graph(app)
    app.state.live_event_store, _continuities = represented_graph()
    origins = RuntimeOrigins(
        root, root / "install", executing_executable(), False, 0, (),
    )
    capture = ServingRuntimeCapture(
        app, origins, include_resolver=True, include_relay=True, include_roster=True,
        include_sessions=True, host_receipts={session.session_id: original},
        include_worktrees=True, include_live_events=True,
        include_authentication=True,
        session_deadline=Deadline(10),
    )
    return capture.capture().private_payload()


async def prepare_runtime(
    stack: AsyncExitStack, capture: dict[str, Any], endpoint: Endpoint,
    native: AuthenticatedChannel, listener: socket.socket,
    original: VerifiedHostReceipt, deadline: Deadline,
) -> tuple[PendingRuntime, FastAPI, PendingIdleSession]:
    """Transfer parsing is isolated here; activation uses a real native grant."""
    memory = MemoryChannel(_self())
    namespace = "e" * 64
    write_frame(memory, {
        "operation": "prepare-candidate", "namespace": namespace,
        "executable": str(executing_executable()), "capture": capture["runtime"],
    }, deadline)
    pending = receive_runtime(memory, memory.peer, namespace=namespace, deadline=deadline)
    os.environ["AGENT_BRIDGE_CONFIG_DIR"] = str(pending.runtime.origins.config_root)
    os.environ["AGENT_BRIDGE_INSTALL_DIR"] = str(pending.runtime.origins.install_root)
    app = create_app(restored_runtime=pending.runtime)
    await stack.enter_async_context(app.router.lifespan_context(app))
    pending.acknowledge(app)
    assert app.state.worktree_discovery_cache is pending.runtime.worktrees
    assert pending.runtime.worktrees.get_all()["actual"][0].turn_count == 7
    assert app.state.live_event_store is pending.runtime.live_events
    assert app.state.live_event_store.get("a") is app.state.live_event_store.get("c")
    assert app.state.live_event_store.ingest("a", []) == 0
    assert app.state.auth_custody is pending.runtime.authentication.custody
    assert app.state.ui_login_codes is pending.runtime.authentication.codes
    assert app.state.ui_login_codes == capture["runtime"]["runtime"]["authentication"]["codes"]
    controls = RuntimeControls(
        app, listener, pending.runtime.origins, pending=pending, include_roster=True,
    )
    grant = controls.register(
        native.peer, Operation.ACTIVATE_RESOURCES,
        receiver=endpoint, active=endpoint, deadline=deadline,
    )
    assert controls.execute(native, {
        "grant": grant, "operation": Operation.ACTIVATE_RESOURCES.value,
    }) == {"resources_active": True, "ready": False}
    binding = pending.prepare_idle_session(
        app, endpoint, original, capture["continuation"], capture["durability"], deadline,
    )
    return pending, app, binding
