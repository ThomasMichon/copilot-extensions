"""Launch-bound target-local preference authority; no registry inference."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shlex
import socket
import sys
from pathlib import Path
from typing import Any

SHELL_MARKER = "agent-bridge:target-preferences:wrapper-v1"
LAUNCH_TOKEN = "{{target_preference_launcher}}"
FD_ENV = "AGENT_BRIDGE_PREFERENCE_FD"
NONCE_ENV = "AGENT_BRIDGE_PREFERENCE_NONCE"
DIGEST_ENV = "AGENT_BRIDGE_PREFERENCE_DIGEST"
MODE_ENV = "AGENT_BRIDGE_PREFERENCE_MODE"
TARGET_ENV = "AGENT_BRIDGE_PREFERENCE_TARGET"
MAX_FRAME = 8192
AUTHORITY_MODULES = (
    "agent_bridge",
    "agent_bridge.winjob",
    "agent_bridge.session_preferences",
    "agent_bridge.preference_attestation",
    "agent_bridge.preference_exec",
    "agent_bridge.session_host",
    "agent_bridge.session_host.protocol",
    "agent_bridge.session_host.host",
    "agent_bridge.session_host.osutil",
    "agent_bridge.session_host.launcher",
    "agent_bridge.session_host.preference_spawn",
    "agent_procutil",
)


def component_digest() -> str:
    from importlib.util import find_spec

    digest = hashlib.sha256()
    for name in AUTHORITY_MODULES:
        spec = find_spec(name)
        if spec is None or spec.loader is None:
            raise RuntimeError("target preference component is unavailable")
        source = spec.loader.get_source(name)
        if source is None:
            raise RuntimeError("target preference component has no attributable source")
        digest.update(name.encode() + b"\0")
        digest.update(source.replace("\r\n", "\n").encode("utf-8") + b"\0")
    return digest.hexdigest()


def namespace_context() -> dict[str, Any]:
    if not sys.platform.startswith("linux") or not hasattr(os, "geteuid"):
        raise RuntimeError("wrapper authority v1 requires a Linux execution space")
    parts = [Path("/proc/sys/kernel/random/boot_id").read_text().strip()]
    for name in ("mnt", "pid", "user"):
        stat = Path(f"/proc/self/ns/{name}").stat()
        parts.append(f"{name}:{stat.st_dev}:{stat.st_ino}")
    return {
        "platform": "linux",
        "uid": os.geteuid(),
        "namespace": hashlib.sha256("|".join(parts).encode()).hexdigest(),
    }


def path_digest(path: str | os.PathLike[str]) -> str:
    return hashlib.sha256(str(Path(path).resolve()).encode("utf-8")).hexdigest()


def target_digest(descriptor: dict[str, str]) -> str:
    if (
        set(descriptor) != {"user", "instance"}
        or any(not isinstance(value, str) or not value for value in descriptor.values())
    ):
        raise ValueError("target preference launch has no selected execution-space descriptor")
    return hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()


def user_uid(user: str) -> int:
    if user.isdecimal():
        return int(user)
    import pwd

    return pwd.getpwnam(user).pw_uid


def process_start(pid: int) -> str:
    # The comm field may contain spaces or ')'; fields follow its last ')'.
    tail = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return tail[19]


def shell_request(command: str, *, enabled: bool) -> list[str]:
    """Only the provider-owned template, not appended caller args, enables it."""
    if not enabled:
        return ["bash", "-lc", command]
    if command.count(LAUNCH_TOKEN) != 1:
        raise ValueError("target preference template requires exactly one launcher token")
    return [SHELL_MARKER, "bash", "-lc", command]


def prepare_launch(
    argv: list[str], env: dict[str, str] | None, cwd: str | None,
) -> tuple[list[str], dict[str, str], dict[str, Any], socket.socket, socket.socket]:
    if len(argv) != 6 or argv[0] != SHELL_MARKER or argv[3:5] != ["bash", "-lc"]:
        raise ValueError("unsupported attested target launch shape")
    if argv[1] not in {"defaults", "selection"}:
        raise ValueError("unsupported target preference authority purpose")
    context = namespace_context()
    descriptor = json.loads(argv[2])
    context["target"] = target_digest(descriptor)
    context["uid"] = user_uid(descriptor["user"])
    if not cwd:
        raise ValueError("attested launch requires an explicit target workspace")
    context["cwd"] = path_digest(cwd)
    context["digest"] = component_digest()
    context["nonce"] = secrets.token_hex(32)
    context["mode"] = argv[1]
    if argv[5].count(LAUNCH_TOKEN) != 1:
        raise ValueError("target preference template requires exactly one launcher token")
    bundle = Path(sys.argv[0]).resolve()
    if not bundle.is_file() or bundle.suffix != ".pyz":
        raise ValueError("attested launcher requires its content-addressed Session Host bundle")
    invocation = " ".join(shlex.quote(value) for value in (
        sys.executable, "-I", "-S", str(bundle), "--preference-exec", "--",
    ))
    command = argv[5].replace(LAUNCH_TOKEN, "exec " + invocation)
    reader, writer = socket.socketpair()
    child_env = dict(env or {})
    # Reserved binding values are always launcher-owned, never caller-owned.
    child_env[FD_ENV] = str(writer.fileno())
    child_env[NONCE_ENV] = context["nonce"]
    child_env[DIGEST_ENV] = context["digest"]
    child_env[MODE_ENV] = context["mode"]
    child_env[TARGET_ENV] = context["target"]
    return ["bash", "-lc", command], child_env, context, reader, writer


def read_frame(sock: socket.socket) -> dict[str, Any]:
    raw = bytearray()
    while len(raw) <= MAX_FRAME:
        data = sock.recv(min(4096, MAX_FRAME + 1 - len(raw)))
        if not data:
            break
        raw.extend(data)
        if b"\n" in data:
            break
    if len(raw) > MAX_FRAME or not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise ValueError("invalid target preference attestation frame")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("target preference attestation must be an object")
    return result


def verify_frame(
    frame: dict[str, Any], expected: dict[str, Any], child_pid: int,
) -> dict[str, Any]:
    proof = frame.get("proof")
    receipt = frame.get("receipt")
    if (
        set(frame) != {"proof", "receipt"} or not isinstance(proof, dict)
        or set(proof) != {"nonce"} or not isinstance(receipt, dict)
        or not isinstance(proof.get("nonce"), str)
    ):
        raise ValueError("target preference attestation lacks binding")
    if not hmac.compare_digest(proof["nonce"].encode(), expected["nonce"].encode()):
        raise ValueError("target preference attestation nonce mismatch")
    authority = receipt.get("authority") or {}
    if not isinstance(authority, dict):
        raise ValueError("target preference attestation execution binding mismatch")
    space = authority.get("space") or {}
    if not isinstance(space, dict):
        raise ValueError("target preference attestation execution binding mismatch")
    if (
        receipt.get("version") != 2 or receipt.get("child_pid") != child_pid
        or authority.get("kind") != "wrapper-v1"
        or authority.get("digest") != expected["digest"]
        or authority.get("mode") != expected["mode"]
        or authority.get("target") != expected["target"]
        or any(space.get(key) != expected[key] for key in ("platform", "uid", "namespace", "cwd"))
        or authority.get("start") != process_start(child_pid)
    ):
        raise ValueError("target preference attestation execution binding mismatch")
    if not valid_receipt(receipt, child_pid):
        raise ValueError("target preference receipt is invalid")
    if expected["mode"] == "defaults":
        values = receipt.get("values") or {}
        sources = receipt.get("sources") or {}
        explicit = (
            receipt.get("provider_selected") is True
            or isinstance(sources, dict) and sources.get("model") == "launch-profile"
        )
        if not explicit and receipt.get("status") != "resolved":
            raise ValueError("target preference defaults are missing or unreadable")
        if not explicit and (not values.get("model") or not values.get("reasoning_effort")):
            raise ValueError("target preference defaults lack required model/effort intent")
    return receipt


def valid_receipt(receipt: Any, child_pid: int) -> bool:
    if not isinstance(receipt, dict):
        return False
    authority = receipt.get("authority")
    values, sources = receipt.get("values"), receipt.get("sources")
    if not isinstance(authority, dict) or not isinstance(values, dict) or not isinstance(sources, dict):
        return False
    if (
        set(receipt) - {"version", "child_pid", "status", "reason", "values", "sources",
                        "provider_selected", "authority"}
        or set(authority) != {"kind", "digest", "mode", "space", "start", "target"}
        or set(sources) != set(values)
        or any(not isinstance(value, str) for value in sources.values())
        or not isinstance(receipt.get("status"), str)
        or not isinstance(authority.get("mode"), str)
    ):
        return False
    space = authority.get("space")
    if not isinstance(space, dict):
        return False
    if set(space) != {"platform", "uid", "namespace", "home", "cwd"}:
        return False
    if (
        type(receipt.get("version")) is not int or receipt["version"] != 2
        or type(receipt.get("child_pid")) is not int or receipt["child_pid"] != child_pid
        or receipt.get("status") not in {"resolved", "missing", "error"}
        or authority.get("kind") != "wrapper-v1"
        or authority.get("mode") not in {"defaults", "selection"}
        or authority.get("digest") != component_digest()
        or space.get("platform") != "linux" or not isinstance(space.get("uid"), int)
        or isinstance(space.get("uid"), bool) or space["uid"] < 0
        or not isinstance(authority.get("start"), str) or not authority["start"].isdigit()
    ):
        return False
    for key in ("namespace", "home", "cwd"):
        value = space.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            return False
    target = authority.get("target")
    if not isinstance(target, str) or len(target) != 64 or any(c not in "0123456789abcdef" for c in target):
        return False
    if any(
        key not in {"model", "reasoning_effort", "context"}
        or not isinstance(value, str) or not value.strip()
        or sources.get(key) not in {"target-settings", "launch-profile"}
        for key, value in values.items()
    ):
        return False
    try:
        size = len(json.dumps(receipt).encode("utf-8"))
    except (TypeError, ValueError):
        return False
    return (
        "proof" not in receipt and "nonce" not in receipt and size <= 4096
        and isinstance(receipt.get("provider_selected", False), bool)
    )
