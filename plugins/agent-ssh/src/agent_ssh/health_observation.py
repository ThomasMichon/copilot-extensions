"""Explicit, noninteractive observations of one direct SSH profile route."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import (
    OutputLimitExceeded,
    OwnedCleanupError,
    run_caller_owned,
)

from .ssh_profile import is_valid_alias

SCHEMA = "agent-ssh.health.observation"
MAX_PROFILE_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 16 * 1024
MAX_TIMEOUT_MS = 5000
_OPTIONS = {"hostname", "port", "user", "identityfile", "identitiesonly", "stricthostkeychecking"}


class ObservationError(ValueError):
    """A closed failure code without profile, credential or command content."""


@dataclass(frozen=True)
class DirectRoute:
    """A selected route with no user-controlled executable or helper."""

    host: str
    port: int
    user: str
    identity: Path


def _plain_file(path: Path) -> None:
    if any(character in str(path) for character in '\r\n\0"%$'):
        raise ObservationError("invalid-input")
    if not path.is_absolute():
        raise ObservationError("invalid-input")
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode) or path.is_symlink()
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise ObservationError("invalid-input")


def read_route(profile: Path, alias: str) -> DirectRoute:
    """Read one closed direct Host block without evaluating OpenSSH config."""
    if not isinstance(alias, str) or not is_valid_alias(alias) or len(alias) > 128:
        raise ObservationError("invalid-input")
    _plain_file(profile)
    descriptor = os.open(
        profile, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
    )
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        current = profile.lstat()
        if (
            not stat.S_ISREG(opened.st_mode) or stat.S_ISLNK(current.st_mode)
            or getattr(current, "st_file_attributes", 0) & 0x400
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ObservationError("invalid-input")
        raw = stream.read(MAX_PROFILE_BYTES + 1)
    if len(raw) > MAX_PROFILE_BYTES:
        raise ObservationError("invalid-input")
    selected = False
    seen = False
    in_host = False
    options: dict[str, str] = {}
    for line in raw.decode("utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        if len(parts) != 2:
            raise ObservationError("unsupported-profile")
        key, value = parts[0].lower(), parts[1].strip()
        if key in {"include", "match"}:
            raise ObservationError("unsupported-profile")
        if key == "host":
            # A closed fragment has exact Host names, never wildcard/global rules.
            if not is_valid_alias(value):
                raise ObservationError("unsupported-profile")
            selected = value.casefold() == alias.casefold()
            in_host = True
            if selected:
                if seen:
                    raise ObservationError("unsupported-profile")
                seen = True
            continue
        if not in_host:
            raise ObservationError("unsupported-profile")
        if selected:
            if key not in _OPTIONS or key in options:
                raise ObservationError("unsupported-profile")
            options[key] = value
    if not seen:
        raise ObservationError("route-unavailable")
    if not {"hostname", "user", "identityfile"} <= options.keys():
        raise ObservationError("unsupported-profile")
    host, user = options["hostname"], options["user"]
    if (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,253}", host) is None
        or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", user) is None
        or options.get("identitiesonly", "yes").lower() != "yes"
        or options.get("stricthostkeychecking", "yes").lower() not in {"yes", "true"}
    ):
        raise ObservationError("unsupported-profile")
    try:
        port = int(options.get("port", "22"))
    except ValueError:
        raise ObservationError("invalid-input") from None
    if not 1 <= port <= 65535:
        raise ObservationError("invalid-input")
    identity_text = options["identityfile"]
    if identity_text.startswith('"') and identity_text.endswith('"'):
        identity_text = identity_text[1:-1]
    identity = Path(identity_text)
    _plain_file(identity)
    return DirectRoute(host, port, user, identity)


def _path_option(path: Path) -> str:
    return '"' + path.as_posix().replace("\\", "\\\\") + '"'


def command(executable: str, route: DirectRoute, known_hosts: Path) -> list[str]:
    """Build a config-independent fixed-action invocation with no helper paths."""
    _plain_file(known_hosts)
    options = [
        "BatchMode=yes", "StrictHostKeyChecking=yes", "UpdateHostKeys=no",
        "GlobalKnownHostsFile=none", "UserKnownHostsFile=" + _path_option(known_hosts),
        "IdentitiesOnly=yes", "IdentityAgent=none", "CertificateFile=none", "PKCS11Provider=none",
        "SecurityKeyProvider=none", "PreferredAuthentications=publickey",
        "PasswordAuthentication=no", "KbdInteractiveAuthentication=no",
        "GSSAPIAuthentication=no", "HostbasedAuthentication=no",
        "ClearAllForwardings=yes", "ForwardAgent=no", "ForwardX11=no",
        "ControlMaster=no", "ControlPath=none", "ControlPersist=no",
        "PermitLocalCommand=no", "ProxyCommand=none", "ProxyJump=none",
        "CanonicalizeHostname=no", "VerifyHostKeyDNS=no", "CheckHostIP=no",
        "ConnectionAttempts=1", "NumberOfPasswordPrompts=0",
    ]
    argv = [executable, "-F", "none", "-n", "-T"]
    for option in options:
        argv.extend(("-o", option))
    argv.extend((
        "-o", "IdentityFile=" + _path_option(route.identity),
        "-o", "HostName=" + route.host, "-p", str(route.port),
        "-l", route.user, "--", route.host, "exit 0",
    ))
    return argv


def _result(state: str, reason: str, stamp: int | None = None) -> dict[str, object]:
    return {
        "schema": SCHEMA, "version": 1, "direction": "outbound",
        "state": state, "reason": reason, "observed_at_ms": stamp,
    }


def observe(
    profile: Path, alias: str, known_hosts: Path, *, timeout_ms: int = MAX_TIMEOUT_MS,
) -> dict[str, object]:
    """Observe one admitted route; unconfirmed cleanup retains its native owner."""
    if type(timeout_ms) is not int or not 100 <= timeout_ms <= MAX_TIMEOUT_MS:
        raise ObservationError("invalid-timeout")
    started = time.monotonic()
    budget = OwnedExecutionBudget(
        deadline=started + timeout_ms / 1000,
        cleanup_reserve=min(1.0, timeout_ms / 4000),
    )
    source_start = time.time_ns() // 1_000_000
    try:
        route = read_route(profile, alias)
        executable = shutil.which("ssh")
        if executable is None:
            return _result("unsupported", "client-unavailable")
        argv = command(executable, route, known_hosts)
        if time.monotonic() >= budget.execution_deadline:
            return _result("unknown", "timeout")
        # Windows OpenSSH cannot reuse another process's POSIX descriptor table.
        result = run_caller_owned(
            argv, timeout=None, budget=budget,
            max_stdout_bytes=MAX_OUTPUT_BYTES, max_stderr_bytes=MAX_OUTPUT_BYTES,
            env={
                name: value for name, value in os.environ.items()
                if os.name != "nt" or name.casefold()
                != "c28fc6f98a2c44abbbd89d6a3037d0d9_posix_fd_state"
            },
        )
    except OwnedCleanupError:
        raise
    except (subprocess.TimeoutExpired, TimeoutError):
        return _result("unknown", "timeout")
    except OutputLimitExceeded:
        return _result("unknown", "output-limit")
    except ObservationError as error:
        return _result("unsupported", str(error))
    except (OSError, UnicodeError):
        return _result("unknown", "unavailable")
    except RuntimeError:
        return _result("unsupported", "native-unavailable")
    stamp = time.time_ns() // 1_000_000
    elapsed_ms = (time.monotonic() - started) * 1000
    if elapsed_ms >= timeout_ms:
        return _result("unknown", "timeout")
    if stamp < source_start or abs(stamp - source_start - elapsed_ms) > 1000:
        return _result("unknown", "clock-skew")
    if result.returncode != 0:
        return _result("unknown", "route-failed")
    return _result("ok", "none", stamp)


def _run(args: argparse.Namespace) -> int:
    try:
        result = observe(args.profile, args.alias, args.known_hosts, timeout_ms=args.timeout_ms)
    except ObservationError:
        result = _result("unsupported", "invalid-timeout")
    except OwnedCleanupError:
        print(json.dumps(_result("unknown", "cleanup-unconfirmed")))
        return 3
    print(json.dumps(result))
    return 0 if result["state"] == "ok" else 1


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the optional provider; registration performs no observation."""
    parser = subparsers.add_parser("observe", help="Bounded direct-route health observation.")
    parser.add_argument("--alias", required=True, help="One exact Host in the selected fragment.")
    parser.add_argument("--profile", required=True, type=Path, help="Absolute closed SSH fragment.")
    parser.add_argument("--known-hosts", required=True, type=Path, help="Existing host-key file.")
    parser.add_argument("--timeout-ms", type=int, default=MAX_TIMEOUT_MS)
    parser.add_argument("--json", action="store_true", required=True)
    parser.set_defaults(func=_run)
