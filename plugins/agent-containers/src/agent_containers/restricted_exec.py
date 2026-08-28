"""Sanitized absolute executable resolution for restricted Docker exec."""

from __future__ import annotations

import subprocess
import time
from pathlib import PurePosixPath

from agent_procutil import no_window_flags

from .config import SECURITY_HOME_LABEL

_BASH_CANDIDATES = ("/bin/bash", "/usr/bin/bash", "/usr/local/bin/bash")
_NODE_CANDIDATES = (
    "/usr/local/bin/node",
    "/usr/bin/node",
    "/bin/node",
    "/opt/node/bin/node",
)
_READLINK_CANDIDATES = ("/usr/bin/readlink", "/bin/readlink")
_CLEARED_ENV = (
    "BASH_ENV",
    "ENV",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "LD_AUDIT",
    "NODE_OPTIONS",
    "NODE_PATH",
    "NODE_EXTRA_CA_CERTS",
    "NODE_ICU_DATA",
    "NPM_CONFIG_PREFIX",
    "NPM_CONFIG_USERCONFIG",
    "NPM_CONFIG_GLOBALCONFIG",
    "PROMPT_COMMAND",
    "CDPATH",
)


class RestrictedExecError(RuntimeError):
    """A restricted helper executable could not be resolved safely."""


def _remaining(deadline: float | None, default: float) -> float:
    if deadline is None:
        return default
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RestrictedExecError("restricted helper resolution exceeded its deadline")
    return min(default, remaining)


def writable_roots(inspected: dict) -> tuple[str, ...]:
    """Return actual writable mount/tmpfs destinations from Docker inspect."""
    host = inspected.get("HostConfig") or {}
    roots = {str(path) for path in (host.get("Tmpfs") or {})}
    for mount in inspected.get("Mounts") or []:
        destination = mount.get("Destination")
        if destination:
            roots.add(str(destination))
    labels = (inspected.get("Config") or {}).get("Labels") or {}
    home = labels.get(SECURITY_HOME_LABEL)
    if home:
        roots.add(str(home))
    return tuple(sorted(root.rstrip("/") or "/" for root in roots))


def _under(path: str, root: str) -> bool:
    candidate = PurePosixPath(path)
    boundary = PurePosixPath(root)
    return candidate == boundary or boundary in candidate.parents


def sanitized_exec_prefix(
    container: str,
    user: str,
    home: str,
) -> list[str]:
    """Build docker-exec argv with startup/preload injection surfaces cleared."""
    args = ["docker", "exec", "-u", user]
    clean_environment = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": home,
        **{name: "" for name in _CLEARED_ENV},
    }
    for name, value in clean_environment.items():
        args += ["-e", f"{name}={value}"]
    args.append(container)
    return args


def resolve_executable(
    container: str,
    user: str,
    inspected: dict,
    *,
    kind: str,
    deadline: float | None,
) -> tuple[str, str]:
    """Probe fixed absolute candidates outside the actual writable surfaces."""
    if (inspected.get("HostConfig") or {}).get("ReadonlyRootfs") is not True:
        raise RestrictedExecError(
            "restricted helper execution requires a read-only root filesystem"
        )
    labels = (inspected.get("Config") or {}).get("Labels") or {}
    home = str(labels.get(SECURITY_HOME_LABEL) or "")
    if not home.startswith("/"):
        raise RestrictedExecError("restricted home is unavailable for helper execution")
    roots = writable_roots(inspected)
    if kind == "bash":
        candidates = _BASH_CANDIDATES
        probe = ["--noprofile", "--norc", "-c", "exit 0"]
    elif kind == "node":
        candidates = _NODE_CANDIDATES
        probe = ["--version"]
    else:
        raise RestrictedExecError(f"unsupported restricted helper kind: {kind}")

    prefix = sanitized_exec_prefix(container, user, home)
    for candidate in candidates:
        if any(_under(candidate, root) for root in roots):
            continue
        resolved = _resolve_target(
            prefix,
            candidate,
            roots,
            deadline=deadline,
        )
        if resolved is None:
            continue
        try:
            proc = subprocess.run(
                [*prefix, resolved, *probe],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_remaining(deadline, 10.0),
                creationflags=no_window_flags(),
            )
        except FileNotFoundError:
            raise RestrictedExecError("docker CLI not found on PATH") from None
        except subprocess.TimeoutExpired:
            continue
        if proc.returncode == 0:
            return resolved, home
    raise RestrictedExecError(
        f"restricted image has no safe absolute {kind} executable outside writable state"
    )


def _resolve_target(
    prefix: list[str],
    candidate: str,
    roots: tuple[str, ...],
    *,
    deadline: float | None,
) -> str | None:
    for readlink in _READLINK_CANDIDATES:
        if any(_under(readlink, root) for root in roots):
            continue
        try:
            proc = subprocess.run(
                [*prefix, readlink, "-f", candidate],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=_remaining(deadline, 5.0),
                creationflags=no_window_flags(),
            )
        except FileNotFoundError:
            raise RestrictedExecError("docker CLI not found on PATH") from None
        except subprocess.TimeoutExpired:
            continue
        resolved = proc.stdout.strip() if proc.returncode == 0 else ""
        if (
            not resolved.startswith("/")
            or "\n" in resolved
            or any(_under(resolved, root) for root in roots)
        ):
            continue
        return resolved
    return None
