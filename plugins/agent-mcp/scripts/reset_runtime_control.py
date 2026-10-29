"""Finite fresh-install control reset under the existing MCP lifecycle locks."""

from __future__ import annotations

import errno
import json
import os
import re
import socket
import stat
import time
from collections.abc import Callable
from pathlib import Path

import installer_engine_footprint as footprint
import installer_engine_uninstall as inspection
from single_instance_lease import SingleInstance
from zdd import CutoverLock, routing

_CONTROLS = {
    "serve.sock": r"serve\.sock",
    "serve.sock.endpoint": r"serve\.sock\.endpoint",
    "serve-g{generation}.sock": r"serve-g[0-9]+\.sock",
    "serve-g{generation}.sock.endpoint": r"serve-g[0-9]+\.sock\.endpoint",
    "serve-control-{pid}.token": r"serve-control-[1-9][0-9]*\.token",
    "active.json": r"active\.json",
}


def _scope(home: Path) -> None:
    if home == Path(home.anchor) or home in (Path.home(), *Path.home().parents):
        raise RuntimeError("Control reset refuses filesystem and home ancestors")
    inspection._plain(home)
    for name in ("zdd-cutover.lock", "agent-mcp-serve.lock", "active.lock"):
        inspection._plain(home / name, file=True)


def _patterns() -> list[str]:
    path = Path(__file__).with_name("uninstall-profile.json")
    manifest = footprint.parse(footprint.decode(footprint._marker_text(path)), "agent-mcp")
    effect = manifest.effects.get("runtime-control", {})
    if (
        (effect.get("kind"), effect.get("scope"), effect.get("disposition"))
        != (
            "runtime-state",
            "runtime-root",
            "reset",
        )
        or not effect.get("entries")
        or set(effect["entries"]) - _CONTROLS.keys()
    ):
        raise RuntimeError("The maintained MCP control-reset footprint is unsupported")
    return [_CONTROLS[name] for name in effect["entries"]]


def _controls(home: Path, patterns: list[str]) -> list[Path]:
    paths = [
        path
        for path in home.iterdir()
        if any(re.fullmatch(pattern, path.name) for pattern in patterns)
    ]
    if len(paths) > 1024:
        raise RuntimeError("MCP control inventory exceeds the reset inspection budget")
    for path in paths:
        value = path.lstat()
        if stat.S_ISLNK(value.st_mode):
            target = Path(os.readlink(path))
            target = Path(os.path.abspath(path.parent / target))
            if (
                not path.name.endswith(".sock")
                or target.parent != home
                or not re.fullmatch(
                    _CONTROLS["serve-g{generation}.sock"],
                    target.name,
                )
            ):
                raise RuntimeError("MCP control link points outside its declared socket scope")
            continue
        if (
            getattr(value, "st_file_attributes", 0) & 0x400
            or value.st_nlink != 1
            or not (
                stat.S_ISREG(value.st_mode)
                or (path.name.endswith(".sock") and stat.S_ISSOCK(value.st_mode))
            )
        ):
            raise RuntimeError(f"Unsafe MCP control artifact: {path.name}")
    return sorted(paths)


def _listening(address: str | tuple[str, int]) -> bool:
    family = (
        socket.AF_UNIX
        if isinstance(address, str)
        else socket.AF_INET6
        if address[0] == "::1"
        else socket.AF_INET
    )
    with socket.socket(family, socket.SOCK_STREAM) as stream:
        stream.settimeout(0.25)
        try:
            stream.connect(address)
        except OSError as error:
            if os.name == "nt" and isinstance(address, tuple) and isinstance(error, TimeoutError):
                # Windows can time out on a closed loopback port. Exclusive
                # bind proves absence without treating that timeout as death.
                with socket.socket(family, socket.SOCK_STREAM) as probe:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    probe.bind(address)
                return False
            if isinstance(error, (ConnectionRefusedError, FileNotFoundError)) or error.errno in {
                errno.ECONNREFUSED,
                errno.ENOENT,
                errno.ENOTSOCK,
            }:
                return False
            raise
        return True


def _integer(value: object) -> int | None:
    """Match the numeric coercion accepted by routing.Endpoint.from_dict."""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _require_idle_routes(path: Path, rows: dict[int, dict]) -> None:
    if path.stat().st_size > 65536:
        raise RuntimeError("MCP routing table exceeds its reset inspection budget")
    try:
        table = json.loads(footprint._marker_text(path))
    except (ValueError, UnicodeError):
        return
    if not isinstance(table, dict):
        return
    for role in ("active", "previous"):
        endpoint = table.get(role)
        if not isinstance(endpoint, dict):
            continue
        pid = _integer(endpoint.get("pid"))
        if pid in rows:
            raise RuntimeError("A live process still owns MCP routing state")
        port = _integer(endpoint.get("port"))
        if port is None or not 0 < port < 65536:
            continue
        bind = endpoint.get("bind")
        if bind is not None and not isinstance(bind, str):
            raise RuntimeError("An uncertain MCP routing endpoint prevents control reset")
        host = routing.Endpoint(bind=bind or "", port=port).client_host
        if host not in {"127.0.0.1", "::1"}:
            raise RuntimeError("An uncertain MCP routing endpoint prevents control reset")
        if _listening((host, port)):
            raise RuntimeError("A live MCP routing endpoint prevents control reset")


def _require_idle(home: Path, paths: list[Path], rows: dict[int, dict]) -> None:
    for row in rows.values():
        arguments = inspection._arguments(row)
        if (
            not any(
                arguments[index : index + 3] == ["-m", "agent_mcp", "serve"]
                for index in range(len(arguments) - 2)
            )
            or "--passive" not in arguments
        ):
            continue
        raw = next(
            (arg.split("=", 1)[1] for arg in arguments if arg.startswith("--socket=")), None
        )
        if "--socket" in arguments:
            index = arguments.index("--socket") + 1
            raw = arguments[index] if index < len(arguments) else None
        # Managed passives always use an absolute generation socket. An
        # uncertain passive cannot establish an idle control home.
        if raw is None or not Path(raw).is_absolute() or Path(raw).parent == home:
            raise RuntimeError("A live or unscoped passive MCP owner prevents control reset")
    for path in paths:
        token = re.fullmatch(r"serve-control-([1-9][0-9]*)\.token", path.name)
        if token and int(token[1]) in rows:
            raise RuntimeError(f"A live process still owns MCP control state: {path.name}")
        if path.name == "active.json":
            _require_idle_routes(path, rows)
        elif path.name.endswith(".sock") and path.is_socket() and hasattr(socket, "AF_UNIX"):
            if _listening(str(path)):
                raise RuntimeError("A live MCP socket prevents control reset")
        elif path.name.endswith(".endpoint"):
            value = path.lstat()
            if value.st_size > 65536:
                raise RuntimeError("MCP endpoint exceeds its reset inspection budget")
            try:
                endpoint = json.loads(path.read_text(encoding="utf-8-sig"))
            except (ValueError, UnicodeError):
                continue
            port = _integer(endpoint.get("port")) if isinstance(endpoint, dict) else None
            if port is not None and 0 < port < 65536 and _listening(("127.0.0.1", port)):
                raise RuntimeError("A live MCP endpoint prevents control reset")


def reset_control(
    home: Path,
    *,
    settle: Callable[[], list[dict]],
    inspect: Callable[[], dict[int, dict]],
) -> list[dict]:
    """Settle displaced processes, then reset only a demonstrably idle home."""
    home = Path(os.path.abspath(home))
    _scope(home)
    patterns = _patterns()
    home.mkdir(parents=True, exist_ok=True)
    with CutoverLock(home):
        reaped = settle()
        with SingleInstance(home, service="agent-mcp-serve"):
            with routing.routing_lock(home, deadline=time.monotonic() + 5):
                _scope(home)
                paths = _controls(home, patterns)
                _require_idle(home, paths, inspect())
                for path in paths:
                    path.unlink(missing_ok=True)
    return reaped
