"""Named outbound SSH routes through an explicitly selected existing Gateway."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import tempfile
from pathlib import Path
from typing import Any

from . import ssh_profile
from .file_lock import exclusive_file_lock

MAX_INPUT_BYTES = 1024 * 1024
MAX_MACHINES = 256
_SOURCE_DIR = ".agent-ssh-sources"  # marketplace-isolation: allow shared-config-source
_MODULE = {
    "module": "gateway", "kind": "transport",
    "produces": {"ssh_profile": True, "keyed_by": "machine-name"},
    "inbound_ports": "none",
    "capabilities": {
        "install-client": False, "emit-profile": True, "verify": True,
        "provision-server": False,
    },
}
_USER = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2) + "\n"


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Gateway configuration contains a duplicate field")
        result[name] = value
    return result


def load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        data = stream.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("Gateway configuration exceeds 1 MiB")
    try:
        value = json.loads(data, object_pairs_hook=_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Gateway configuration must be bounded JSON") from exc
    return validate_config(value)


def _alias(value: object) -> str:
    if not isinstance(value, str) or not ssh_profile.is_valid_alias(value):
        raise ValueError("Gateway and machine names must be exact SSH aliases")
    return value


def _public_key(value: object) -> str:
    if not isinstance(value, str) or any(not 32 <= ord(char) <= 126 for char in value):
        raise ValueError("Supply a single-line approved ed25519 public host key")
    fields = value.split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise ValueError("Gateway peer host keys must use ssh-ed25519")
    try:
        raw = base64.b64decode(fields[1], validate=True)
    except ValueError as exc:
        raise ValueError("Invalid Gateway peer public host key encoding") from exc
    prefix = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32)
    if len(raw) != 51 or raw[:19] != prefix or base64.b64encode(raw).decode() != fields[1]:
        raise ValueError("Invalid Gateway peer ed25519 public host key")
    return "ssh-ed25519 " + fields[1]


def _path(value: object) -> str:
    if (not isinstance(value, str) or not value
            or any(ord(char) < 32 or char in '"%' for char in value)):
        raise ValueError("Gateway identity path contains unsupported SSH syntax")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Gateway identity_file must be an absolute local path")
    return str(path)


def validate_config(value: object) -> dict[str, Any]:
    fields = {"schema", "version", "gateway", "identity_file", "machines"}
    if (not isinstance(value, dict) or set(value) != fields
            or value["schema"] != "agent-ssh.gateway-profile"
            or type(value["version"]) is not int or value["version"] != 1):
        raise ValueError("Invalid schema-v1 Gateway profile configuration")
    gateway = _alias(value["gateway"])
    identity = _path(value["identity_file"])
    machines = value["machines"]
    if not isinstance(machines, list) or len(machines) > MAX_MACHINES:
        raise ValueError("Gateway machines must be a list of at most 256 entries")
    aliases = {gateway.casefold()}
    ports: set[int] = set()
    rows = []
    for row in machines:
        if not isinstance(row, dict) or set(row) != {"name", "user", "reverse_port", "host_key"}:
            raise ValueError("Invalid Gateway machine fields")
        name = _alias(row["name"])
        if name.casefold() in aliases:
            raise ValueError("Gateway and machine aliases must be distinct")
        aliases.add(name.casefold())
        if not isinstance(row["user"], str) or not _USER.fullmatch(row["user"]):
            raise ValueError("Invalid Gateway peer login user")
        port = row["reverse_port"]
        if type(port) is not int or not 1 <= port <= 65535 or port in ports:
            raise ValueError("Gateway reverse ports must be distinct positive TCP ports")
        ports.add(port)
        rows.append({
            "name": name, "user": row["user"], "reverse_port": port,
            "host_key": _public_key(row["host_key"]),
        })
    return {
        "schema": value["schema"], "version": 1, "gateway": gateway,
        "identity_file": identity, "machines": sorted(rows, key=lambda row: row["name"]),
    }


def _quote(path: Path | str) -> str:
    value = str(path)
    if any(ord(char) < 32 or char in '"%' for char in value):
        raise ValueError("Gateway source path contains unsupported SSH syntax")
    return '"' + value + '"'


def profile_sources(
    config: object, source_root: Path,
) -> tuple[Path, dict[str, Any], dict[str, str]]:
    """Compile owner-supplied public bindings; never discover or authorize them."""
    if not source_root.is_absolute():
        raise ValueError("Gateway retained source root must be absolute")
    selected = validate_config(config)
    digest = hashlib.sha256(_json({"config": selected, "module": _MODULE}).encode()).hexdigest()
    snapshot = source_root / ("gateway-" + digest)
    machines = []
    pins = []
    for row in selected["machines"]:
        machines.append({
            "name": row["name"], "hostname": "127.0.0.1",
            "port": row["reverse_port"], "user": row["user"],
            "identity_file": _quote(selected["identity_file"]),
            "options": {
                "ProxyJump": selected["gateway"],
                "HostKeyAlias": row["name"],
                "HostKeyAlgorithms": "ssh-ed25519",
                "UserKnownHostsFile": _quote(snapshot / "known_hosts"),
                "GlobalKnownHostsFile": "none",
                "StrictHostKeyChecking": "yes",
                "CheckHostIP": "no",
                "VerifyHostKeyDNS": "no",
                "UpdateHostKeys": "no",
                "IdentitiesOnly": "yes",
                "BatchMode": "yes",
                "PreferredAuthentications": "publickey",
                "PasswordAuthentication": "no",
                "KbdInteractiveAuthentication": "no",
                "GSSAPIAuthentication": "no",
                "ForwardAgent": "no",
            },
        })
        pins.append(row["name"] + " " + row["host_key"] + "\n")
    registry = {"transport": "gateway", "topology": "per-machine", "machines": machines}
    ssh_profile.validate_profile_inputs(registry, _MODULE, require_transport_match=True)
    files = {
        "registry.json": _json(registry),
        "module.json": _json(_MODULE),
        "known_hosts": "".join(pins),
    }
    return snapshot, registry, files


def _read_owned_file(path: Path) -> str:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or ssh_profile._is_reparse(info)
            or info.st_nlink != 1 or info.st_size > 4 * MAX_INPUT_BYTES):
        raise ValueError("Gateway retained source is not an ordinary bounded file")
    return path.read_text(encoding="utf-8")


def _retain(snapshot: Path, files: dict[str, str]) -> None:
    if snapshot.exists() or snapshot.is_symlink():
        ssh_profile._regular_config_directory(snapshot)
        if {path.name for path in snapshot.iterdir()} != set(files):
            raise ValueError("Gateway retained source contents differ")
        for name, content in files.items():
            if _read_owned_file(snapshot / name) != content:
                raise ValueError("Gateway retained source bytes differ")
        return
    stage = Path(tempfile.mkdtemp(prefix=".gateway-", dir=snapshot.parent))
    try:
        ssh_profile._chmod(stage, 0o700)
        for name, content in files.items():
            path = stage / name
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            ssh_profile._chmod(path, 0o600)
        stage.rename(snapshot)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def emit_profile(
    config_path: Path, *, config_d: Path | None = None, ssh_config: Path | None = None,
) -> Path:
    """Retain immutable public inputs, then use the sole generic profile writer."""
    config = load_config(config_path)
    if not Path(config["identity_file"]).is_file():
        raise ValueError("Gateway identity_file is unavailable; restore the approved key first")
    managed = (config_d or ssh_profile._ssh_dir() / "config.d").expanduser().absolute()
    sources = managed.parent / _SOURCE_DIR
    identity = Path(config["identity_file"]).resolve(strict=True)
    root_config = (ssh_config or ssh_profile._ssh_dir() / "config").expanduser().resolve()
    if (identity == root_config or identity.is_relative_to(managed.resolve())
            or identity.is_relative_to(sources.resolve())):
        raise ValueError("Gateway identity_file cannot be a managed configuration/source file")
    sources.mkdir(mode=0o700, parents=True, exist_ok=True)
    sources = ssh_profile._regular_config_directory(sources)
    ssh_profile._chmod(sources, 0o700)
    snapshot, registry, files = profile_sources(config, sources)
    with exclusive_file_lock(sources / "gateway.lock"):
        _retain(snapshot, files)
        return ssh_profile.write_fragment(
            registry, _MODULE, config_d=managed, ssh_config=ssh_config,
            registry_path=snapshot / "registry.json", module_path=snapshot / "module.json",
        )


def command(args: Any) -> int:
    try:
        fragment = emit_profile(args.config, config_d=args.config_d, ssh_config=args.ssh_config)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        import sys

        print(f"[FAIL] cannot emit Gateway SSH profiles: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({
            "transport": "gateway", "state": "configured", "fragment": str(fragment),
            "connection_attempted": False, "enrollment_performed": False,
        }, sort_keys=True))
    else:
        print(f"[OK] wrote Gateway SSH profiles to {fragment}; connectivity is not verified")
    return 0


def add_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "gateway-profile", help="Emit strict named peer routes through an existing Gateway SSH alias.",
    )
    parser.add_argument("config", type=Path, help="Explicit approved Gateway profile JSON.")
    parser.add_argument("--config-d", type=Path, default=None, help="Override ~/.ssh/config.d.")
    parser.add_argument("--ssh-config", type=Path, default=None, help="Override ~/.ssh/config.")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=command)
