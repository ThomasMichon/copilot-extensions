"""Read-only SSH evidence guard for the canonical finite removal owner."""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import stat
import sys
from pathlib import Path
from types import ModuleType


def _ordinary_directory(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0) & 0x400
    ):
        raise RuntimeError(f"SSH transport evidence requires an ordinary directory: {path}")


def _transport_home() -> Path:
    override = os.environ.get("AGENT_CONTAINERS_HOME", "").strip()
    raw = override or str(Path.home() / ".agent-containers")
    if ".." in raw.replace("\\", "/").split("/"):
        raise RuntimeError("SSH transport home must not contain raw .. components")
    home = Path(os.path.abspath(os.path.expanduser(raw)))
    for path in (home, *home.parents):
        if os.path.lexists(path):
            _ordinary_directory(path)
    return home


def _runtime_probe(payload_root: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_containers_uninstall_process_probe",
        payload_root / "scripts" / "versioned_runtime.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Canonical runtime process evidence is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _broker_evidence(ssh: Path, root: Path, payload_root: Path) -> str:
    probe = _runtime_probe(payload_root)
    stale = False
    for index, path in enumerate(ssh.glob("*.proxy.json")):
        if index >= 16:
            return "unproven-proxy-artifacts (bounded record budget exceeded)"
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & 0x400
            or metadata.st_nlink != 1
            or metadata.st_size > 65536
        ):
            return "unproven-proxy-artifacts (nonordinary or oversized record)"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return "unproven-proxy-artifacts (unreadable record)"
        if not isinstance(record, dict) or type(record.get("pid")) is not int:
            return "unproven-proxy-artifacts (unattributed record)"
        pid = record["pid"]
        if pid <= 0:
            return "unproven-proxy-artifacts (invalid process identity)"
        if not probe._pid_alive(pid):
            stale = True
            continue
        runtime_path = record.get("runtime")
        port = record.get("control_port")
        if (
            record.get("schema_version") != 1
            or not isinstance(runtime_path, str)
            or not Path(runtime_path).is_absolute()
            or not (
                Path(runtime_path).is_relative_to(root)
                or Path(runtime_path).is_relative_to(payload_root)
            )
            or type(port) is not int
            or not 0 < port < 65536
        ):
            return "live-or-unattributed-proxy-artifacts (endpoint ownership unproved)"
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2) as connection:
                connection.sendall(b"ping\n")
                response = bytearray()
                while len(response) < 16 and not response.endswith(b"\n"):
                    part = connection.recv(16 - len(response))
                    if not part:
                        break
                    response.extend(part)
                if response == b"pong\n":
                    return "responsive-broker-endpoint (active/reconnect idleness unproved)"
        except OSError:
            pass
        return "live-or-unattributed-proxy-artifacts (unresponsive is not idle proof)"
    if stale:
        return "stale-proxy-artifacts (not busy evidence; complete idle proof unavailable)"
    return "reconnect-or-preparation-artifacts (complete idle proof unavailable)"


def check_under_custody(root: Path, payload_root: Path, plugin_id: str) -> None:
    """Refuse unproved closure; observations never grant removal authority."""
    if plugin_id != "agent-containers":
        raise RuntimeError("Containers transport guard refuses a foreign plugin identity")
    for home in {root, _transport_home()}:
        ssh = home / "ssh"
        if os.path.lexists(ssh):
            _ordinary_directory(ssh)
            if next(ssh.iterdir(), None) is not None:
                evidence = _broker_evidence(ssh, root, payload_root)
                raise RuntimeError(
                    f"SSH active/reconnect dependency closure is unproven: {evidence}; "
                    "legacy uninstall refused without stopping any transport"
                )
