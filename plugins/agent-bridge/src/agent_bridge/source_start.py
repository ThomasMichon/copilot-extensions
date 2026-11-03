"""Start the selected source generation without controlling its predecessor."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import socket
import sys
import time

from zdd import diagnostics

from .serving_reconcile import ServingReconcileError

log = logging.getLogger("agent-bridge")


async def complete_source_start(app, published: asyncio.Event, initial_recovery_task) -> None:
    """Publish only a ready selected frontend, then observe predecessor self-retirement."""
    from . import __version__, lifecycle_hooks
    from .app import _retire_previous_daemon
    from .app_lifecycle import settled_thread_call
    from .config import config_dir
    from .elevated import is_subdaemon
    from .install_paths import install_dir
    from .routing_state import (
        ForwardedRouteRefused, publish_daemon_route_unless_forwarded,
        skip_forwarded_daemon_start,
    )
    from .runtime_version import write_running_version

    server = app.state.uvicorn_server
    try:
        while not getattr(app.state, "shutting_down", False) and not server.should_exit:
            _check_selection(install_dir(), __version__)
            if app.state.ready and server.started:
                break
            await asyncio.sleep(.1)
        else:
            return
        current, previous = await settled_thread_call(
            publish_daemon_route_unless_forwarded, config_dir(),
            bind=app.state.config.bind, port=app.state.bound_port,
            pid=os.getpid(), version=__version__,
        )
        if not is_subdaemon():
            write_running_version(
                generation_id=getattr(app.state.session_manager, "_generation_id", None),
            )
        app.state.lifecycle_started = lifecycle_hooks.record_start(
            config_dir(), __version__, app.state.bound_port,
        )
        app.state.cold_recovery_ready = previous is None
        app.state.cold_predecessor = previous
        published.set()
        if previous is not None:
            await _retire_previous_daemon(
                app, previous, current, initial_recovery_task=initial_recovery_task,
            )
            if app.state.cold_recovery_ready and not app.state.shutting_down:
                try:
                    await app.state.session_manager.reattach_session_hosts(recover_cold=False)
                except Exception:
                    log.warning("Post-retirement Session Host reattach deferred", exc_info=True)
        if (
            app.state.cold_recovery_ready and not app.state.shutting_down
            and app.state.config.enable_credential_relay
        ):
            await app.state.adopt_relay()
    except asyncio.CancelledError:
        raise
    except ForwardedRouteRefused as exc:
        skip_forwarded_daemon_start(app, exc)
    except Exception:
        app.state.readiness_error = "source startup failed"
        log.exception("Selected source startup failed; stopping this candidate")
        server.should_exit = True


def _core():
    from . import __main__ as core

    return core


def _check_selection(root: Path, version: str) -> None:
    try:
        selected = (root / "current-version").read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError) as exc:
        raise ServingReconcileError("selected source runtime marker is unavailable") from exc
    if selected != version:
        raise ServingReconcileError("executing source runtime is no longer selected")


def _selected_endpoint(core, version: str):
    endpoint = core._active_endpoint()
    if (
        endpoint is None or endpoint.version != version
        or type(endpoint.pid) is not int or endpoint.pid <= 0
        or not endpoint.process_start_time
        or diagnostics.process_start_time(endpoint.pid) != endpoint.process_start_time
    ):
        return None
    return endpoint


def _healthy_selected(core, version: str, *, pid: int | None = None, port: int | None = None) -> bool:
    endpoint = _selected_endpoint(core, version)
    if endpoint is None or (
        pid is not None and endpoint.pid != pid
        or port is not None and endpoint.port != port
    ):
        return False
    body = core._service_health_on_port(endpoint.port, host=endpoint.client_host)
    slot = body.get("slot") if isinstance(body, dict) else None
    return (
        isinstance(body, dict)
        and body.get("version") == version
        and body.get("ready") is True
        and body.get("draining") is not True
        and isinstance(slot, dict) and slot.get("pid") == endpoint.pid
        and slot.get("role") == "active" and slot.get("active") == endpoint.to_dict()
        and core._active_endpoint() == endpoint
        and diagnostics.process_start_time(endpoint.pid) == endpoint.process_start_time
    )


def start_selected_source(
    *, timeout: float = 30.0, selection_root: Path | None = None,
    selection_version: str | None = None,
) -> bool:
    from . import __version__
    from .config import config_dir, load_config
    from .install_paths import install_dir

    core = _core()
    root = selection_root or install_dir()
    selected = selection_version or __version__
    _check_selection(root, selected)
    if core._active_endpoint_is_forward():
        return False
    if _healthy_selected(core, __version__):
        return True
    deadline = time.monotonic() + timeout

    def wait(*, pid=None, port=None) -> bool:
        while time.monotonic() < deadline:
            _check_selection(root, selected)
            if core._active_endpoint_is_forward():
                return False
            if _healthy_selected(core, __version__, pid=pid, port=port):
                return True
            time.sleep(0.25)
        return False

    config_dir()
    fd = core._acquire_ensure_lock()
    if fd is None:
        return wait()
    try:
        _check_selection(root, selected)
        if core._active_endpoint_is_forward():
            return False
        if _healthy_selected(core, __version__):
            return True
        if _selected_endpoint(core, __version__) is not None:
            return wait()
        cfg = load_config()
        with socket.socket(socket.AF_INET6 if ":" in cfg.bind else socket.AF_INET) as probe:
            probe.bind((cfg.bind or "0.0.0.0", 0))
            port = probe.getsockname()[1]
        _check_selection(root, selected)
        handle = core._launch_bridge_daemon(
            [*core._daemon_launch_argv(), "--port", str(port)],
            display="agent-bridge selected source generation", contained_last_resort=False,
        )
        return wait(pid=handle.pid, port=port)
    finally:
        core._release_ensure_lock(fd)


def start_installed_slot() -> bool:
    if os.environ.get("AGENT_BRIDGE_NO_ENSURE") == "1":
        return True
    from importlib.metadata import version

    from agent_procutil._daemon_instance_identity import runtime_slot_paths

    from .install_paths import install_dir
    from .source_build import effective_runtime_version

    prefix = Path(sys.prefix)
    layout = runtime_slot_paths(prefix)
    if layout is None or not Path(__file__).resolve().is_relative_to(prefix.resolve()):
        raise ServingReconcileError("Bridge callback must execute from its installed slot")
    core = _core()
    root = Path(layout[2])
    if install_dir().resolve() != root.resolve():
        raise ServingReconcileError("Bridge callback installation root differs from its slot")
    package = version("agent-bridge")
    reported = effective_runtime_version(package, prefix=prefix, module_path=Path(__file__))
    normalized_package = package.lower().replace("-", ".").replace("_", ".")
    normalized_slot = layout[0].lower().replace("-", ".").replace("_", ".")
    if reported != layout[0] and normalized_package != normalized_slot:
        raise ServingReconcileError("Bridge callback package and slot generation differ")
    if core._active_endpoint_is_forward():
        return True
    return start_selected_source(selection_root=root, selection_version=layout[0])
