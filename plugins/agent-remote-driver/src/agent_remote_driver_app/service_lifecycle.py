"""Installed, user-mode lifecycle for the small Driver front door."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from types import ModuleType
from typing import Any, Iterator
import urllib.request

from agent_procutil import spawn_daemon
from agent_procutil.daemon_instances import (
    DaemonInstanceReceipt, DaemonInstanceSpec, admit_daemon_instance, cleanup_instance,
    default_registry_root,
)
from agent_procutil.daemon_observation import observe_instances, publish_file_observation
from agent_procutil.process_retirement import PinnedProcess
from agent_remote_driver.service import DriverService
from agent_remote_driver.session_host.host_state import write_host_state
from agent_remote_driver.session_host.osutil import process_start_time
from single_instance_lease import SingleInstance
from single_instance_lease.operation import OperationLock


SERVICE = "agent-remote-driver-service"
ROOT_ENV = "AGENT_REMOTE_DRIVER_HOME"
LOCATOR = "observation_driver_service_state_path"


def _ordinary(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    if (
        path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    ):
        raise ValueError("Driver installation requires ordinary paths")


def _same_directory(first: Path, second: Path) -> bool:
    try:
        return first.is_absolute() and first.samefile(second)
    except OSError:
        return False


def load_provider(slot: Path) -> ModuleType:
    path = slot / "install.py"
    _ordinary(path)
    spec = importlib.util.spec_from_file_location("_driver_application_provider", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Driver installed application provider is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def selected_source(deadline: float) -> Any:
    """Use the canonical installed-application reader, never a source fallback."""
    prefix = Path(os.path.abspath(sys.prefix))
    selected = os.environ.get(ROOT_ENV)
    if prefix.name != ".venv" or prefix.parent.parent.name != "versions":
        raise ValueError("Driver service requires its selected numbered application")
    slot = prefix.parent
    root = Path(selected) if selected else slot.parent.parent
    if not root.is_absolute() or not root.samefile(slot.parent.parent):
        raise ValueError("Driver service installation root differs from its application")
    for path in (root, slot.parent, slot, prefix):
        _ordinary(path, directory=True)
    return load_provider(slot).qualify_installed_application(
        slot, component="agent-remote-driver", command="agent-remote-driver",
        entry_point="agent_remote_driver_app:main", deadline=deadline,
    )


@contextmanager
def retain_host_runtime() -> Iterator[None]:
    """Keep an installed Host's code alive independently of the central service."""
    prefix = Path(os.path.abspath(sys.prefix))
    if prefix.name != ".venv" or prefix.parent.parent.name != "versions":
        yield
        return
    slot, root = prefix.parent, prefix.parent.parent.parent
    configured = os.environ.get(ROOT_ENV)
    if configured and not _same_directory(Path(configured), root):
        raise ValueError("Driver Host installation root differs from its application")
    for path in (root, slot.parent, slot, prefix):
        _ordinary(path, directory=True)
    provider = load_provider(slot)
    record = provider._application(slot)
    if record["name"] != "agent-remote-driver" or record["module"] != "agent_remote_driver_app:main":
        raise ValueError("Driver Host application has a different entry point")
    runtime, _engine = provider._resources(slot)
    marker = runtime.read_marker(root, slot.name)
    if marker is None:
        if os.path.lexists(runtime.marker_path(root, slot.name)):
            raise RuntimeError("Driver Host application completion is unconfirmed")
        # Explicit --stage remains usable; removal admits only completed slots.
        yield
        return
    construction = record["construction"]
    payload_hash = construction.get("payload_hash") if isinstance(construction, dict) else None
    if (
        not isinstance(payload_hash, str) or re.fullmatch(r"[a-f0-9]{64}", payload_hash) is None
        or marker.get("payload_hash") != payload_hash
    ):
        raise ValueError("Driver Host completion differs from its Source/profile")
    with runtime.retention_lease(root, slot.name, payload_hash=payload_hash, timeout=0):
        yield


def _selected(binding: Any) -> str:
    return (Path(binding.root) / "current-version").read_text(encoding="utf-8-sig").strip()


@dataclass(frozen=True)
class ServiceEndpoint:
    instance_id: str
    generation: str
    pid: int
    birth: str
    port: int
    nonce: str = field(repr=False)


def endpoints(root: Path, *, generation: str | None = None) -> list[ServiceEndpoint]:
    """Observe only this installation's original, birth-matched native owners."""
    found = []
    for record in observe_instances(service=SERVICE):
        runtime = Path(record.get("runtime_root", ""))
        if (
            not _same_directory(runtime.parent, root / "versions")
            or generation is not None and runtime.name != generation
        ):
            continue
        if record.get("process_identity_state") in {"dead", "reused"}:
            continue
        if record.get("state") != "running" or record.get("process_identity_state") != "matching":
            raise RuntimeError("Driver service startup outcome is unresolved")
        locator = record.get(LOCATOR)
        if not isinstance(locator, str):
            raise RuntimeError("Driver service startup outcome is unresolved")
        path = Path(locator)
        original_location = (
            path.name == "service.json" and path.parent.name == record.get("instance_id")
            and _same_directory(path.parent.parent, runtime / "service")
        )
        sealed_location = (
            path.name == f"driver-service-{record.get('instance_id')}.json"
            and _same_directory(path.parent, runtime / ".runtime-bootstrap")
        )
        if (
            not path.is_absolute() or not (original_location or sealed_location)
        ):
            raise ValueError("Driver service endpoint location differs from its owner")
        _ordinary(path.parent, directory=True)
        _ordinary(path)
        with path.open("rb") as stream:
            data = stream.read(16385)
        if len(data) > 16384:
            raise ValueError("Driver service endpoint exceeds its byte limit")
        state = json.loads(data)
        if (
            not isinstance(state, dict)
            or state.get("instance_id") != record.get("instance_id")
            or state.get("pid") != int(record["pid"])
            or not isinstance(state.get("root"), str)
            or not _same_directory(Path(state["root"]), root)
            or state.get("generation") != runtime.name
            or type(state.get("port")) is not int or not 0 < state["port"] < 65536
            or not isinstance(state.get("nonce"), str)
            or not 32 <= len(state["nonce"]) <= 256 or not state["nonce"].isascii()
            or not isinstance(state.get("birth"), str)
            or process_start_time(state["pid"]) != state["birth"]
        ):
            raise ValueError("Driver service endpoint identity is unconfirmed")
        found.append(ServiceEndpoint(
            state["instance_id"], state["generation"], state["pid"], state["birth"],
            state["port"], state["nonce"],
        ))
    return found


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def healthy(endpoint: ServiceEndpoint, *, timeout: float) -> bool:
    if process_start_time(endpoint.pid) != endpoint.birth:
        return False
    prefix = "Bear" + "er "
    request = urllib.request.Request(
        f"http://127.0.0.1:{endpoint.port}/health",
        headers={"Authorization": prefix + endpoint.nonce},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(16385)
    if len(raw) > 16384:
        return False
    state = json.loads(raw)
    return (
        isinstance(state, dict) and state.get("contract") == "copilot-driver.service"
        and type(state.get("version")) is int and state["version"] == 1
        and state.get("ready") is True
        and state.get("instance_id") == endpoint.instance_id
        and state.get("generation") == endpoint.generation
        and process_start_time(endpoint.pid) == endpoint.birth
    )


def _ready(root: Path, generation: str, deadline: float) -> ServiceEndpoint | None:
    candidates = endpoints(root, generation=generation)
    if len(candidates) > 1:
        raise RuntimeError("Driver service has multiple owners for the selected generation")
    for endpoint in candidates:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Driver service readiness deadline expired")
        if healthy(endpoint, timeout=min(3, remaining)):
            return endpoint
        raise RuntimeError("Driver service original owner is not ready")
    return None


class StartupUncertain(RuntimeError):
    def __init__(self, process: Any) -> None:
        self.process = process
        super().__init__("Driver service startup outcome requires its original instance")


def start(binding: Any, *, deadline: float) -> ServiceEndpoint:
    """One idempotent ensure; an uncertain launch is never silently repeated."""
    with OperationLock(binding.root / "driver-service-start.lock"):
        if _selected(binding) != binding.slot.name:
            raise RuntimeError("Driver application is no longer selected")
        existing = _ready(binding.root, binding.slot.name, deadline)
        if existing is not None:
            return existing
        bootstrap = binding.slot / ".runtime-bootstrap" / "runtime_launcher.py"
        _ordinary(bootstrap)
        _, engine = load_provider(binding.slot)._resources(binding.slot)
        if time.monotonic() >= deadline:
            raise TimeoutError("Driver service readiness deadline expired before launch")
        process = spawn_daemon(
            [engine._native_retained_path(binding.python), "-I", "-S", "-B", str(bootstrap),
             "-m", "agent_remote_driver_app.service_lifecycle"],
            cwd=binding.root.anchor if os.name == "nt" and len(str(binding.root)) >= 260
            else binding.root,
            log_path=binding.root / "logs" / "driver-service.log",
            env_overrides={ROOT_ENV: str(binding.root)},
            instance=DaemonInstanceSpec(SERVICE, binding.slot, Path(__file__)),
        )
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Driver service candidate exited before readiness")
                try:
                    ready = _ready(binding.root, binding.slot.name, deadline)
                except RuntimeError as error:
                    if str(error) != "Driver service startup outcome is unresolved":
                        raise
                    ready = None
                if ready is not None:
                    if ready.pid != process.pid or _selected(binding) != binding.slot.name:
                        raise RuntimeError("Driver service readiness belongs to another selection")
                    return ready
                time.sleep(min(.05, max(0, deadline - time.monotonic())))
            raise TimeoutError("Driver service readiness deadline expired")
        except Exception as error:
            raise StartupUncertain(process) from error


def stop(binding: Any, instance_id: str, *, deadline: float) -> ServiceEndpoint | None:
    """Retire only the requested service instance, never its independent Hosts."""
    if not isinstance(instance_id, str) or re.fullmatch(r"[a-f0-9]{32}", instance_id) is None:
        raise ValueError("Driver service stop requires its exact instance id")
    with OperationLock(binding.root / "driver-service-start.lock"):
        return _stop_locked(binding, instance_id, deadline=deadline)


def _stop_locked(binding: Any, instance_id: str, *, deadline: float) -> ServiceEndpoint | None:
    if time.monotonic() >= deadline:
        raise TimeoutError("Driver service stop deadline expired before retirement")
    if _selected(binding) != binding.slot.name:
        raise RuntimeError("Driver application is no longer selected")
    candidates = endpoints(binding.root, generation=binding.slot.name)
    if not candidates:
        return None
    if len(candidates) != 1 or candidates[0].instance_id != instance_id:
        raise RuntimeError("Driver service stop does not address the original owner")
    endpoint = candidates[0]
    registrations = [
        record for record in observe_instances(service=SERVICE)
        if record.get("instance_id") == instance_id
    ]
    if len(registrations) != 1:
        raise RuntimeError("Driver service original registration is unconfirmed")
    record = registrations[0]
    if (
        record.get("service") != SERVICE
        or not _same_directory(Path(record.get("runtime_root", "")), binding.slot)
        or record.get("pid") != str(endpoint.pid)
        or record.get("state") != "running"
        or record.get("process_identity_state") != "matching"
        or not isinstance(record.get("birth"), str) or not record["birth"]
        or process_start_time(endpoint.pid) != endpoint.birth
    ):
        raise RuntimeError("Driver service original registration is unconfirmed")
    with PinnedProcess(endpoint.pid, record["birth"]) as original:
        if _selected(binding) != binding.slot.name:
            raise RuntimeError("Driver application changed before service retirement")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Driver service stop deadline expired before retirement")
        original.terminate(timeout=remaining)
        if not original.exited():
            raise RuntimeError("Driver service original exit is unconfirmed")
    return endpoint


@contextmanager
def service_removal_custody(
    binding: Any, instance_id: str, *, deadline: float,
) -> Iterator[ServiceEndpoint | None]:
    """Keep start exclusion through original service settlement and caller withdrawal."""
    if not isinstance(instance_id, str) or re.fullmatch(r"[a-f0-9]{32}", instance_id) is None:
        raise ValueError("Driver service removal requires its exact instance id")
    with OperationLock(binding.root / "driver-service-start.lock"):
        yield _settle_service_removal_locked(binding, instance_id, deadline=deadline)


def _settle_service_removal_locked(
    binding: Any, instance_id: str, *, deadline: float,
) -> ServiceEndpoint | None:
    """Caller retains the same service-start lock through subsequent withdrawal."""
    if time.monotonic() >= deadline:
        raise TimeoutError("Driver service removal deadline expired before retirement")
    if _selected(binding) != binding.slot.name:
        raise RuntimeError("Driver application is no longer selected")
    records = observe_instances(service=SERVICE)
    originals = [record for record in records if record.get("instance_id") == instance_id]
    if len(originals) != 1:
        raise RuntimeError("Driver service original registration is unconfirmed")
    record = originals[0]
    if (
        record.get("service") != SERVICE
        or not _same_directory(Path(record.get("runtime_root", "")), binding.slot)
        or record.get("protected", "0") != "0"
        or record.get("process_identity_state") not in {"matching", "dead"}
        or not isinstance(record.get("birth"), str) or not record["birth"]
        or not record.get("pid", "").isdigit()
        or any(
            other.get("instance_id") != instance_id
            and _same_directory(Path(other.get("runtime_root", "")), binding.slot)
            and other.get("process_identity_state") != "dead"
            for other in records
        )
    ):
        raise RuntimeError("Driver service original registration is unconfirmed")
    endpoint = (
        _stop_locked(binding, instance_id, deadline=deadline)
        if record["process_identity_state"] == "matching" else None
    )
    result = cleanup_instance(
        DaemonInstanceReceipt(instance_id, default_registry_root()),
        terminate=False, deadline=deadline, runtime_root=Path(record["runtime_root"]),
    )
    if result.status != "settled":
        raise RuntimeError(
            "Driver service original artifact settlement is unconfirmed: "
            f"{result.status}/{result.reason}"
        )
    return endpoint


async def serve(binding: Any) -> None:
    """Keep the original Hosts alive through service replacement or exit."""
    guard = admit_daemon_instance(SERVICE, runtime_root=binding.slot, source=__file__)
    lease = SingleInstance(binding.root, service=SERVICE + "-" + binding.slot.name)
    service = None
    try:
        lease.acquire()
        if _selected(binding) != binding.slot.name:
            raise RuntimeError("Driver service candidate is no longer selected")
        nonce = secrets.token_urlsafe(32)
        service = DriverService(
            nonce, instance_id=guard.receipt.instance_id, generation=binding.slot.name,
        )
        await service.serve()
        directory = binding.slot / ".runtime-bootstrap"
        _ordinary(directory, directory=True)
        path = directory / f"driver-service-{guard.receipt.instance_id}.json"
        birth = process_start_time(os.getpid())
        if not birth:
            raise RuntimeError("Driver service native birth is unavailable")
        write_host_state(path, {
            "root": str(binding.root), "generation": binding.slot.name,
            "instance_id": guard.receipt.instance_id, "pid": os.getpid(), "birth": birth,
            "port": service.port, "nonce": nonce,
        })
        guard.add_artifact(path)
        publish_file_observation(guard.receipt, "driver_service_state", path)
        while True:
            try:
                selected = _selected(binding)
                successor = (
                    await asyncio.to_thread(
                        _ready, binding.root, selected, time.monotonic() + 3,
                    ) if selected != binding.slot.name else None
                )
            except (OSError, UnicodeError, ValueError, RuntimeError, TimeoutError):
                successor = None
            if successor is not None:
                await service.drain()
                return
            await asyncio.sleep(.5)
    finally:
        try:
            if service is not None:
                await service.close()
        finally:
            lease.release()
            guard.close()


def main(argv: list[str] | None = None) -> int:
    if list(sys.argv[1:] if argv is None else argv):
        raise ValueError("Driver service worker accepts no arguments")
    binding = selected_source(time.monotonic() + 60)
    asyncio.run(serve(binding))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
