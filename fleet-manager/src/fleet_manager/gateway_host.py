"""Explicit installed Gateway selection; the ordinary client remains inert."""

from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
import uuid

from .planning import MAX_BYTES, PlanError, canonical, decode, digest, identifier, record
from .private_state import create_private_file, private_directory, read_private_file

CONFIG = "gateway-host.json"
STATE = "gateway-host-state"
RUNTIME = "runtime.json"


class RuntimeEvidenceError(PlanError):
    """Unusable content does not authorize predecessor cleanup."""


def read_runtime_file(path: Path) -> bytes:
    from .artifacts import _content_stamp, _file_stat, _identity, _reader
    from .private_state import validate_private_file, windows_private

    validate_private_file(path)
    with _reader(path) as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise PlanError("Gateway runtime evidence is not an ordinary file")
        if os.name == "nt":
            windows_private(path, descriptor=stream.fileno())
        elif opened.st_uid != os.getuid() or opened.st_mode & 0o077:
            raise PlanError("Gateway runtime evidence is not private")
        content = stream.read(MAX_BYTES + 1)
        after, named = os.fstat(stream.fileno()), _file_stat(path)
        if (
            _identity(named) != _identity(opened)
            or _content_stamp(after) != _content_stamp(opened)
            or _content_stamp(named) != _content_stamp(opened)
        ):
            raise PlanError("Gateway runtime evidence changed during admission")
        if not 1 <= opened.st_size <= MAX_BYTES:
            raise RuntimeEvidenceError("Gateway runtime evidence has invalid size")
        if len(content) != opened.st_size:
            raise PlanError("Gateway runtime evidence could not be read completely")
        return content


def installation_root() -> Path:
    configured = os.environ.get("FLEET_MANAGER_HOME")
    if configured:
        root = Path(configured)
    else:
        prefix = Path(sys.prefix)
        root = prefix.parent.parent.parent if prefix.name == ".venv" else Path.home() / ".fleet-manager"
    if not root.is_absolute() or ".." in root.parts:
        raise PlanError("Gateway hosting requires an absolute installation root")
    return root


def selected_version(root: Path) -> str:
    value = (root / "current-version").read_text(encoding="utf-8-sig").strip()
    if not value or len(value) > 256 or Path(value).name != value or value in {".", ".."}:
        raise PlanError("Gateway hosting selection is invalid")
    return value


def installed_slot(root: Path, *, current: bool) -> Path:
    from agent_procutil._daemon_instance_identity import runtime_slot_paths

    layout = runtime_slot_paths(sys.prefix)
    if layout is None:
        raise PlanError("Gateway hosting requires its installed application")
    version, slot_name, root_name = layout
    slot = Path(slot_name)
    for path in (slot, *slot.parents):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PlanError("Gateway hosting installation must not traverse indirection")
    if (
        not root.samefile(root_name) or Path(sys.prefix).name != ".venv"
        or not Path(__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
        or current and selected_version(root) != version
    ):
        raise PlanError("Gateway hosting does not match its installed selection")
    helper = slot / ".runtime-bootstrap" / "versioned_runtime.py"
    if helper.is_symlink() or not helper.is_file():
        raise PlanError("Gateway hosting completion helper is unavailable")
    spec = importlib.util.spec_from_file_location("_fleet_host_completion", helper)
    if spec is None or spec.loader is None:
        raise PlanError("Gateway hosting completion helper is unavailable")
    runtime = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runtime
    spec.loader.exec_module(runtime)
    if not runtime.is_complete(root, version):
        raise PlanError("Gateway hosting requires a completed application")
    return slot


@contextmanager
def runtime_custody(root: Path):
    slot = installed_slot(root, current=True)
    runtime = sys.modules["_fleet_host_completion"]
    with runtime.retention_lease(root, slot.name, timeout=5):
        if selected_version(root) != slot.name:
            raise PlanError("Gateway selection changed before lifetime admission")
        yield slot


def optional_selection(root: Path) -> dict | None:
    path = root / CONFIG
    if not os.path.lexists(path):
        if os.path.lexists(root / STATE):
            raise PlanError("Previously selected Gateway hosting configuration is missing")
        return None
    value = decode(read_private_file(path, MAX_BYTES))
    record(value, "fleet-manager.gateway-host", {
        "role", "policy", "tls", "journal", "authority", "installation",
    })
    if value["role"] != "gateway":
        raise PlanError("Unsupported explicitly selected hosted role")
    for key in ("policy", "tls", "journal"):
        if not isinstance(value[key], str):
            raise PlanError("Gateway host references require absolute ordinary paths")
        path = Path(value[key])
        if not path.is_absolute() or ".." in path.parts:
            raise PlanError("Gateway host references require absolute ordinary paths")
    for key in ("authority", "installation"):
        identifier(value[key], "Gateway operation owner")
    return value


def write_private(path: Path, value: dict) -> None:
    if os.path.lexists(path):
        if path.name == RUNTIME and path.parent.name == STATE:
            try:
                read_runtime_file(path)
            except RuntimeEvidenceError:
                pass
        else:
            read_private_file(path, MAX_BYTES)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex)
    try:
        with os.fdopen(create_private_file(temporary), "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _listener(selection: dict):
    from .gateway_service_listener import Listener

    path = Path(selection["tls"])
    read_private_file(path, MAX_BYTES)
    listener = Listener.load(path)
    for value in (listener.certificate, listener.key):
        if not isinstance(value, str) or not Path(value).is_absolute() or ".." in Path(value).parts:
            raise PlanError("Gateway host TLS references require absolute ordinary paths")
    return listener


def prepare(root: Path, selection: dict) -> tuple[object, object]:
    from .gateway_enrollment import EnrollmentPolicy
    from .ledger import OperationLedger

    policy_path, journal_path = (Path(selection[key]) for key in ("policy", "journal"))
    policy = decode(read_private_file(policy_path, MAX_BYTES))
    listener = _listener(selection)
    if listener.port == 0:
        raise PlanError("Installed Gateway hosting requires a non-ephemeral listener")
    read_private_file(Path(listener.key), MAX_BYTES)
    listener.tls()
    for parent in (journal_path.parent, *journal_path.parent.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISDIR(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise PlanError("Gateway journal parent is not an ordinary directory")
    private_directory(journal_path.parent)
    if not os.path.lexists(journal_path):
        os.close(create_private_file(journal_path))
    from .private_state import validate_private_file

    validate_private_file(journal_path)
    ledger = OperationLedger(
        journal_path, authority=selection["authority"], installation=selection["installation"],
        enable_enrollment=True,
    )
    candidate = EnrollmentPolicy(policy, ledger, lambda: int(time.time()))
    if (candidate.fleet, candidate.gateway) != (listener.fleet, listener.gateway):
        raise PlanError("Gateway hosting listener and policy owners differ")
    if candidate.trust_valid_until <= int(time.time()):
        raise PlanError("Gateway hosting policy authority has expired")
    candidate.check_floor()
    return listener, ledger


def callback(action: str) -> int:
    root = installation_root()
    selection = optional_selection(root)
    if selection is None:
        raise PlanError("No Gateway hosting role is explicitly selected")
    installed_slot(root, current=action == "__source-start")
    prepare(root, selection)
    if action == "__source-start":
        from .gateway_host_start import ensure

        ensure(root, selection)
    return 0


def main(action: str) -> int:
    if action == "capabilities":
        print(json.dumps({
            "schema": "fleet-manager.gateway-host-contract", "version": 1,
            "capabilities": ["selected-application-host-v1", "native-owner-stop-v1"],
            "required_extra": "gateway-host",
        }))
        return 0
    root = installation_root()
    selection = optional_selection(root)
    if selection is None:
        if action != "status":
            raise PlanError("No Gateway hosting role is explicitly selected")
        print(json.dumps({"role": "client", "hosting": False, "mutations_performed": False}))
        return 0
    installed_slot(root, current=True)
    from .gateway_host_start import ensure, inspect, stop

    if action == "start":
        prepare(root, selection)
        result = ensure(root, selection)
    elif action == "stop":
        result = stop(root, selection)
    else:
        result = inspect(root, selection)
    print(canonical(result).decode("utf-8"))
    return 0


def selection_digest(selection: dict) -> str:
    return digest(selection)


def startup_digest(selection: dict, *, listener=None) -> str:
    from .gateway_service_listener import read_bounded

    if listener is None:
        listener = _listener(selection)
    return digest({
        "listener": asdict(listener),
        "certificate": hashlib.sha256(read_bounded(Path(listener.certificate))).hexdigest(),
        "key": hashlib.sha256(read_private_file(Path(listener.key), MAX_BYTES)).hexdigest(),
    })
