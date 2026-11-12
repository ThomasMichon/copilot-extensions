"""Chat role policy over unchanged, installed canonical construction resources.

This is an internal setup seam, not installation ownership or release selection.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib
import importlib.util
import os
from pathlib import Path
import sys
import threading
from types import ModuleType

from .catalog import _resource, installed_catalog
from .client import ClientError

_ENGINE: ModuleType | None = None
_IMPORT_LOCK = threading.Lock()
_CLIENT_PACKAGES = ("agent-endpoint-rendezvous", "agent-procutil")
_SERVICE_PACKAGES = (*_CLIENT_PACKAGES, "agent-single-instance-lease")
_BOOTSTRAP = {
    "_versioned_runtime.py": "versioned_runtime.py",
    "versioned_runtime_leases.py": "versioned_runtime_leases.py",
    "versioned_runtime_construction.py": "versioned_runtime_construction.py",
    "versioned_runtime_publication.py": "versioned_runtime_publication.py",
    "versioned_runtime_source.py": "versioned_runtime_source.py",
    "runtime_launcher.py": "runtime_launcher.py",
}


def _engine() -> ModuleType:
    global _ENGINE
    if sys.version_info < (3, 11):
        raise ClientError(
            "construction_python_unavailable", "Chat setup requires Python 3.11 or newer.",
        )
    installed_catalog()
    with _IMPORT_LOCK:
        if _ENGINE is not None:
            return _ENGINE
        root = Path(__file__).parent
        aliases = {}
        loaded = []
        names = (
            "versioned_runtime", "versioned_runtime_source",
            "installer_engine_process", "installer_dependency_policy",
            "installer_wheel_inputs", "installer_wheel_snapshot",
            "installer_source_wheel_cache", "installer_source_build", "installer_engine_health",
        )
        if any(name in sys.modules for name in names):
            raise ClientError(
                "construction_import_conflict",
                "Chat setup requires a process without another canonical installer loaded.",
            )
        try:
            aliases["versioned_runtime"] = importlib.import_module(
                "agent_chat_service._versioned_runtime",
            )
            for name in (*names[1:], "installer_engine"):
                qualified = "agent_chat_service." + name
                if qualified in sys.modules:
                    raise ClientError(
                        "construction_import_conflict",
                        "Chat setup requires its own installed construction resources.",
                    )
                spec = importlib.util.spec_from_file_location(qualified, root / (name + ".py"))
                if spec is None or spec.loader is None:
                    raise ClientError(
                        "construction_unavailable", "Installed Chat construction is unavailable.",
                    )
                module = importlib.util.module_from_spec(spec)
                loaded.append(qualified)
                sys.modules[qualified] = module
                for alias, resource in aliases.items():
                    sys.modules[alias] = resource
                spec.loader.exec_module(module)
                aliases[name] = module
            _ENGINE = aliases["installer_engine"]
        except BaseException:
            for name in loaded:
                sys.modules.pop(name, None)
            raise
        finally:
            for name in names:
                sys.modules.pop(name, None)
        return _ENGINE


def _client_slot() -> Path:
    slot = Path(sys.prefix)
    if slot.parent.name != "versions" or slot.name == "dev":
        raise ClientError(
            "construction_slot_invalid", "Chat construction requires an immutable numbered slot.",
        )
    if not Path(__file__).is_relative_to(slot):
        raise ClientError("construction_slot_invalid", "Chat construction is outside its slot.")
    return slot


def _prepare_client(data: dict) -> None:
    """Called by canonical construction only, before immutable completion."""
    if set(data) != {"slot", "frozen_plugin"} or data["slot"] != str(_client_slot()):
        raise ClientError("construction_slot_invalid", "Chat construction has inconsistent inputs.")
    catalog = installed_catalog()
    slot = _client_slot()
    runtime = importlib.import_module("agent_chat_service._versioned_runtime")
    if os.path.lexists(runtime.marker_path(slot.parent.parent, slot.name)):
        raise ClientError("construction_slot_complete", "A completed Chat slot cannot be prepared.")
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    for name, target in _BOOTSTRAP.items():
        contents = _resource(Path(__file__).with_name(name))
        if hashlib.sha256(contents).hexdigest() != catalog["resource_sha256s"][name]:
            raise ClientError("construction_resource_changed", "Chat construction input changed.")
        with (bootstrap / target).open("xb") as stream:
            stream.write(contents)


def validate_client() -> None:
    """Installed health includes the exact slot-local retention bootstrap."""
    catalog = installed_catalog()
    slot = _client_slot()
    bootstrap = slot / ".runtime-bootstrap"
    expected = set(_BOOTSTRAP.values())
    runtime = importlib.import_module("agent_chat_service._versioned_runtime")
    if runtime.is_complete(slot.parent.parent, slot.name):
        expected.add("runtime-generation")
        try:
            launcher = importlib.import_module("agent_chat_service.runtime_launcher")
            launcher.runtime_identity(slot)
        except (OSError, ValueError):
            raise ClientError(
                "construction_bootstrap_invalid", "Chat retained runtime identity is invalid.",
            ) from None
    if {path.name for path in bootstrap.iterdir()} != expected:
        raise ClientError("construction_bootstrap_invalid", "Chat retention bootstrap is incomplete.")
    for name, target in _BOOTSTRAP.items():
        if hashlib.sha256(_resource(bootstrap / target)).hexdigest() != (
            catalog["resource_sha256s"][name]
        ):
            raise ClientError("construction_bootstrap_invalid", "Chat retention bootstrap changed.")


def validate_service() -> None:
    """Validate installed imports and retention, not serving or storage readiness."""
    validate_client()
    slot = _client_slot().resolve()
    for name in (
        "agent_chat_service.hosting", "agent_procutil.daemon_instances",
        "single_instance_lease", "endpoint_rendezvous",
    ):
        try:
            module = importlib.import_module(name)
        except ImportError:
            raise ClientError(
                "construction_service_unavailable", "Installed Chat hosting imports are unavailable.",
            ) from None
        origin = getattr(module, "__file__", None)
        if (
            not origin or not Path(origin).is_file()
            or not Path(origin).resolve().is_relative_to(slot)
        ):
            raise ClientError(
                "construction_service_unavailable",
                "Chat hosting imports must belong to the immutable candidate.",
            )


@contextmanager
def client_candidate(
    root: Path, version: str, *, project: Path, uv: Path, python: Path,
    wait_timeout: float = 180, child_timeout: float = 180,
):
    """Retain a completed client candidate without publishing or selecting it.

    ``project`` supplies complete independent source-distribution build inputs
    and the unchanged declared local library layout, not a live runtime.
    """
    engine = _engine()
    with engine.numbered_candidate(
        root, version, plugin=project, uv=uv, base_python=python,
        extras=("client",), local_packages=_CLIENT_PACKAGES,
        health_module="agent_chat_service.construction", health_callable="validate_client",
        construction_callable="agent_chat_service.construction:_prepare_client",
        construction_data={"slot": str(root.resolve() / "versions" / version)},
        wait_timeout=wait_timeout, child_timeout=child_timeout,
    ) as candidate:
        yield candidate


@contextmanager
def service_candidate(
    root: Path, version: str, *, project: Path, uv: Path, python: Path,
    wait_timeout: float = 180, child_timeout: float = 180,
):
    """Retain a sealed service-role candidate without selecting or starting it.

    Validation imports the installed host and its dependencies; it never opens
    storage, acquires a live writer lease or publishes a serving endpoint.
    """
    engine = _engine()
    with engine.numbered_candidate(
        root, version, plugin=project, uv=uv, base_python=python,
        extras=("service",), local_packages=_SERVICE_PACKAGES,
        health_module="agent_chat_service.construction", health_callable="validate_service",
        construction_callable="agent_chat_service.construction:_prepare_client",
        construction_data={"slot": str(root.resolve() / "versions" / version)},
        wait_timeout=wait_timeout, child_timeout=child_timeout,
    ) as candidate:
        yield candidate
