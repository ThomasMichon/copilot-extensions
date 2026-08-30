"""SSH-compatible stdio transport for restricted provider targets.

The OpenSSH client speaks its normal wire protocol to this host-side process
over ``ProxyCommand`` stdio. The process opens no listener and projects no key
or SSH daemon into the container; accepted session requests are translated into
the provider's existing, live-validated ``docker exec`` boundary.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

from agent_procutil import no_window_flags

from .config import RESTRICTED_PROFILE, RUNTIME_DIR, STATE_DIR
from .lease import (
    ProviderAdmissionError,
    get_lease,
    provider_lease_guard,
    session_admission,
)
from .private_state import (
    atomic_write_json,
    enforce_mode,
    ensure_private_dir,
    fsync_directory,
)
from .resolver import (
    ContainerResolver,
    LiveExecTarget,
    build_restricted_spawn_command,
    resolve_live_exec_target,
)

log = logging.getLogger("agent-containers")
_REQUEST_TIMEOUT = 30.0
_CHANNEL_POLL_SECONDS = 0.05
_FORCED_EXIT_SECONDS = 2.0
_CHANNEL_CLOSE_GRACE_SECONDS = 0.2
_PROFILE_LOCK_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class SessionRequest:
    """One accepted SSH session-channel request."""

    command: str | None
    term: str | None
    width: int
    height: int

    @property
    def pty(self) -> bool:
        return self.term is not None


def provider_module_path() -> Path:
    """Return the provider-owned agent-ssh transport module path."""
    runtime_root = Path(os.environ.get("AGENT_RT_ROOT", "~/.agent-containers")).expanduser()
    marker = runtime_root / "payload-dir"
    try:
        payload_root = Path(marker.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError):
        payload_root = Path(__file__).resolve().parents[2]
    path = payload_root / "transports" / "provider-exec" / "module.yaml"
    if not path.is_file():
        raise RuntimeError(f"Provider-exec transport module is missing: {path}")
    return path.resolve()


def provider_command_argv() -> list[str]:
    """Return an isolated command prefix that follows the active runtime."""
    source = Path(__file__).resolve().with_name("provider_launcher.py")
    if not source.is_file():
        raise RuntimeError(f"Provider launcher source is missing: {source}")
    runtime_root = Path(
        os.environ.get("AGENT_RT_ROOT", str(RUNTIME_DIR))
    ).expanduser()
    launcher = runtime_root / "provider-launcher.py"
    payload = source.read_bytes()
    if not launcher.is_file() or launcher.read_bytes() != payload:
        ensure_private_dir(launcher.parent)
        tmp = launcher.with_name(
            f".{launcher.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        )
        fd = -1
        try:
            fd = os.open(
                str(tmp),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            with os.fdopen(fd, "wb") as stream:
                fd = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            enforce_mode(tmp, 0o600)
            os.replace(tmp, launcher)
            enforce_mode(launcher, 0o600)
            fsync_directory(launcher.parent)
        finally:
            if fd >= 0:
                os.close(fd)
            tmp.unlink(missing_ok=True)
    base_executable = Path(
        getattr(sys, "_base_executable", None) or sys.executable
    ).resolve()
    if not base_executable.is_file():
        raise RuntimeError(
            f"Provider base Python interpreter is missing: {base_executable}"
        )
    return [str(base_executable), "-I", str(launcher)]


def _null_known_hosts() -> str:
    return "NUL" if os.name == "nt" else "/dev/null"


def _validate_profile_venue(
    name: str,
    venue: object,
    *,
    container_id: str,
) -> dict:
    if not isinstance(venue, dict):
        raise RuntimeError(
            f"Container '{name}' returned no provider venue metadata; refusing SSH profile"
        )
    expected = {
        "schema_version": 1,
        "provider": "agent-containers",
        "kind": "container",
        "target_id": f"container:{name}",
        "instance_id": container_id,
        "security_profile": RESTRICTED_PROFILE,
        "configured_security_profile": RESTRICTED_PROFILE,
        "observed_security_profile": RESTRICTED_PROFILE,
        "effective_security_profile": RESTRICTED_PROFILE,
        "state": "running",
        "ready": True,
        "transport": "provider-exec",
    }
    mismatches = [
        f"{key}={venue.get(key)!r}" for key, value in expected.items() if venue.get(key) != value
    ]
    hold = venue.get("lifecycle_hold")
    if not isinstance(hold, dict) or hold.get("state") != "none":
        mismatches.append(f"lifecycle_hold={hold!r}")
    capabilities = venue.get("capabilities")
    if (
        not isinstance(capabilities, dict)
        or capabilities.get("host_credentials") is not False
        or capabilities.get("credential_relay") is not False
        or capabilities.get("session_host") is not False
        or capabilities.get("ssh_profile") is not True
    ):
        mismatches.append(f"capabilities={capabilities!r}")
    if mismatches:
        raise RuntimeError(
            f"Container '{name}' is not a ready restricted provider-exec target: "
            + "; ".join(mismatches)
        )
    return venue


def ssh_profile_spec(
    name: str,
    alias: str | None = None,
    *,
    project: str | None = None,
    label: str | None = None,
) -> dict:
    """Return the agent-ssh module + normalized registry for one live target."""
    target = resolve_live_exec_target(name)
    if target.actual_profile != RESTRICTED_PROFILE:
        raise RuntimeError(f"Container '{name}' is not restricted; refusing provider-exec profile")
    spec = asyncio.run(ContainerResolver().resolve_spec(name))
    venue = _validate_profile_venue(
        name,
        spec.get("venue"),
        container_id=target.container_id,
    )
    profile_alias = alias or name
    registry = {
        "transport": "provider-exec",
        "machines": [
            {
                "name": profile_alias,
                "hostname": name,
                # Presentation only. ssh-stdio always uses the fleet-owned user.
                "user": target.user,
                "options": {
                    "BatchMode": "yes",
                    "ClearAllForwardings": "yes",
                    "ControlMaster": "no",
                    "ControlPath": "none",
                    "ControlPersist": "no",
                    "GSSAPIAuthentication": "no",
                    "IdentitiesOnly": "yes",
                    "KbdInteractiveAuthentication": "no",
                    "LogLevel": "ERROR",
                    "PasswordAuthentication": "no",
                    "PubkeyAuthentication": "no",
                    "ForwardAgent": "no",
                    "StrictHostKeyChecking": "no",
                    "UserKnownHostsFile": _null_known_hosts(),
                },
            }
        ],
    }
    provider_binary = shutil.which("agent-containers")
    if provider_binary:
        registry["proxy_command_binary"] = provider_binary
    result = {
        "schema_version": 1,
        "module": str(provider_module_path()),
        "registry": registry,
        "venue": {**venue, "posture_verified": True},
    }
    if project is not None:
        project = project.strip()
        if not project:
            raise ValueError("project must be a non-empty name")
        lease = get_lease(name)
        if lease is None:
            raise RuntimeError(
                f"Container '{name}' must have an active lease before it can "
                "be registered as a Picker source"
            )
        source_label = (label or profile_alias).strip()
        if not source_label:
            raise ValueError("label must be non-empty")
        if len(source_label) > 80 or any(
            ord(character) < 32 or ord(character) == 127
            for character in source_label
        ):
            raise ValueError(
                "label must be at most 80 characters without control characters"
            )
        assignment = {
            "kind": "lease",
            "effort": lease.effort,
            "acquired_at": lease.acquired_at,
        }
        result["venue"] = {**result["venue"], "assignment": assignment}
        result["worktree_source"] = {
            "kind": "provider-exec",
            "project": project,
            "target_id": venue["target_id"],
            "instance_id": venue["instance_id"],
            "label": source_label,
            "alias": profile_alias,
            "shell": "bash",
            "resolve": [
                *provider_command_argv(),
                "ssh-profile",
                name,
                "--alias",
                profile_alias,
                "--project",
                project,
                "--label",
                source_label,
                "--json",
            ],
            "connect": [
                *provider_command_argv(),
                "ssh-stdio",
                name,
            ],
            "venue": result["venue"],
            "capabilities": {
                "list": True,
                "messages": True,
                "sessions": True,
                "refresh": True,
                "open": False,
                "resume": False,
                "stop": False,
                "cleanup": False,
                "sync": False,
                "finalize": False,
                "reclaim": False,
                "repair": False,
                "create": False,
            },
        }
    return result


def _profile_registry_path() -> Path:
    return STATE_DIR / "ssh-profiles" / "provider-exec.json"


def _source_registry_path() -> Path:
    root = Path(
        os.environ.get("AGENT_WORKTREES_SOURCES_DIR", "~/.agent-worktrees/sources")
    ).expanduser()
    return root / "agent-containers.json"


def _publish_worktree_source(source: dict) -> None:
    path = _source_registry_path()
    previous: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Picker source registry is unreadable: {path}"
            ) from exc
        if (
            not isinstance(loaded, dict)
            or loaded.get("schema_version") != 1
            or loaded.get("provider") != "agent-containers"
            or not isinstance(loaded.get("sources"), list)
        ):
            raise RuntimeError(f"Picker source registry is malformed: {path}")
        previous = loaded
    if any(
        not isinstance(entry, dict)
        or not isinstance(entry.get("project"), str)
        or not isinstance(entry.get("target_id"), str)
        for entry in previous.get("sources", [])
    ):
        raise RuntimeError(f"Picker source registry has invalid sources: {path}")
    sources = [
        entry
        for entry in previous.get("sources", [])
        if isinstance(entry, dict)
        and not (
            entry.get("project", "").casefold() == source["project"].casefold()
            and entry.get("target_id") == source["target_id"]
        )
    ]
    sources.append(source)
    sources.sort(key=lambda entry: (entry["project"].casefold(), entry["target_id"]))
    atomic_write_json(
        path,
        {
            "schema_version": 1,
            "provider": "agent-containers",
            "sources": sources,
        },
        indent=2,
    )


def remove_worktree_source(name: str, project: str) -> bool:
    """Remove one project-scoped Picker source without resolving the target."""
    normalized_project = project.strip()
    if not normalized_project:
        raise ValueError("project must be a non-empty name")
    target_id = f"container:{name}"
    path = _source_registry_path()
    with _profile_registry_lock():
        if not path.is_file():
            return False
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Picker source registry is unreadable: {path}") from exc
        if (
            not isinstance(loaded, dict)
            or loaded.get("schema_version") != 1
            or loaded.get("provider") != "agent-containers"
            or not isinstance(loaded.get("sources"), list)
        ):
            raise RuntimeError(f"Picker source registry is malformed: {path}")
        sources = loaded["sources"]
        if any(
            not isinstance(entry, dict)
            or not isinstance(entry.get("project"), str)
            or not isinstance(entry.get("target_id"), str)
            for entry in sources
        ):
            raise RuntimeError(f"Picker source registry has invalid sources: {path}")
        kept = [
            entry
            for entry in sources
            if not (
                entry["project"].casefold() == normalized_project.casefold()
                and entry["target_id"] == target_id
            )
        ]
        if len(kept) == len(sources):
            return False
        atomic_write_json(
            path,
            {
                "schema_version": 1,
                "provider": "agent-containers",
                "sources": kept,
            },
            indent=2,
        )
        return True


def remove_stale_worktree_sources(target: str) -> int:
    """Remove released Picker registrations by container or effort name."""
    target_id = f"container:{target}"
    path = _source_registry_path()
    with _profile_registry_lock():
        with provider_lease_guard() as leases:
            active_assignments = {
                f"container:{lease.container}": {
                    "kind": "lease",
                    "effort": lease.effort,
                    "acquired_at": lease.acquired_at,
                }
                for lease in leases
            }
            if not path.is_file():
                return 0
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Picker source registry is unreadable: {path}"
                ) from exc
            if (
                not isinstance(loaded, dict)
                or loaded.get("schema_version") != 1
                or loaded.get("provider") != "agent-containers"
                or not isinstance(loaded.get("sources"), list)
            ):
                raise RuntimeError(f"Picker source registry is malformed: {path}")
            sources = loaded["sources"]
            if any(
                not isinstance(entry, dict)
                or not isinstance(entry.get("target_id"), str)
                for entry in sources
            ):
                raise RuntimeError(
                    f"Picker source registry has invalid sources: {path}"
                )

            def stale_match(entry: dict) -> bool:
                venue = entry.get("venue")
                assignment = (
                    venue.get("assignment") if isinstance(venue, dict) else None
                )
                matches_target = entry["target_id"] == target_id
                matches_effort = (
                    isinstance(assignment, dict)
                    and assignment.get("effort") == target
                )
                if not (matches_target or matches_effort):
                    return False
                return active_assignments.get(entry["target_id"]) != assignment

            kept = [entry for entry in sources if not stale_match(entry)]
            removed = len(sources) - len(kept)
            if removed:
                atomic_write_json(path, {**loaded, "sources": kept}, indent=2)
            return removed


@contextmanager
def _profile_registry_lock():
    lock_path = _profile_registry_path().with_suffix(".lock")
    ensure_private_dir(lock_path.parent)
    deadline = time.monotonic() + _PROFILE_LOCK_TIMEOUT_SECONDS
    token = secrets.token_hex(16)
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(
                str(lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.write(fd, token.encode("ascii"))
            os.fsync(fd)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Could not acquire the provider-exec profile registry lock; "
                    f"confirm no emitter is active before removing {lock_path}"
                ) from None
            time.sleep(_CHANNEL_POLL_SECONDS)
    try:
        yield
    finally:
        os.close(fd)
        try:
            if lock_path.read_text(encoding="ascii") == token:
                lock_path.unlink()
        except OSError:
            pass


def emit_ssh_profile(
    name: str,
    alias: str | None = None,
    *,
    print_only: bool = False,
    project: str | None = None,
    label: str | None = None,
) -> int:
    """Persist provider metadata and ask agent-ssh to publish the named alias."""
    agent_ssh = shutil.which("agent-ssh")
    if not agent_ssh:
        raise RuntimeError(
            "agent-ssh is required to emit provider-exec profiles but is not on PATH"
        )
    spec = (
        ssh_profile_spec(name, alias, project=project, label=label)
        if project is not None or label is not None
        else ssh_profile_spec(name, alias)
    )
    provider_binary = spec["registry"].get("proxy_command_binary")
    if not provider_binary:
        raise RuntimeError(
            "agent-containers must be on PATH before publishing an SSH profile"
        )
    machine = spec["registry"]["machines"][0]
    registry_path = _profile_registry_path()
    with _profile_registry_lock():
        previous: dict | None = None
        if registry_path.is_file():
            try:
                loaded = json.loads(registry_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Provider-exec profile registry is unreadable: {registry_path}"
                ) from exc
            if not isinstance(loaded, dict) or not isinstance(
                loaded.get("machines"), list
            ):
                raise RuntimeError(
                    f"Provider-exec profile registry is malformed: {registry_path}"
                )
            if loaded.get("transport") != "provider-exec":
                raise RuntimeError(
                    f"Provider-exec profile registry has wrong transport: {registry_path}"
                )
            if any(
                not isinstance(existing, dict)
                or not isinstance(existing.get("name"), str)
                for existing in loaded["machines"]
            ):
                raise RuntimeError(
                    f"Provider-exec profile registry has invalid machines: {registry_path}"
                )
            previous = loaded
        merged = {
            "transport": "provider-exec",
            "proxy_command_binary": provider_binary,
            "machines": [
                existing
                for existing in (previous or {}).get("machines", [])
                if isinstance(existing, dict)
                and existing.get("name") != machine["name"]
            ],
        }
        merged["machines"].append(machine)
        merged["machines"].sort(key=lambda item: item["name"])
        active_registry = registry_path
        if print_only:
            active_registry = registry_path.with_name(
                f".provider-exec.preview.{os.getpid()}.{secrets.token_hex(8)}.json"
            )
        atomic_write_json(active_registry, merged, indent=2)
        command = [
            agent_ssh,
            "emit-profile",
            str(active_registry),
            "--module",
            spec["module"],
        ]
        if print_only:
            command.append("--print")
        try:
            result = subprocess.run(
                command,
                check=False,
                creationflags=no_window_flags(),
            )
        except OSError:
            if not print_only and previous is not None:
                atomic_write_json(registry_path, previous, indent=2)
            elif not print_only:
                registry_path.unlink(missing_ok=True)
            raise
        finally:
            if print_only:
                active_registry.unlink(missing_ok=True)
        if not print_only and result.returncode != 0 and previous is not None:
            atomic_write_json(registry_path, previous, indent=2)
        elif not print_only and result.returncode != 0:
            registry_path.unlink(missing_ok=True)
        elif not print_only and "worktree_source" in spec:
            source = spec["worktree_source"]
            assignment = source.get("venue", {}).get("assignment")
            with provider_lease_guard() as leases:
                lease = next(
                    (lease for lease in leases if lease.container == name),
                    None,
                )
                current_assignment = (
                    {
                        "kind": "lease",
                        "effort": lease.effort,
                        "acquired_at": lease.acquired_at,
                    }
                    if lease is not None
                    else None
                )
                if assignment != current_assignment:
                    raise RuntimeError(
                        f"Container '{name}' lease assignment changed before "
                        "Picker source publication"
                    )
                _publish_worktree_source(source)
        return result.returncode


def _command_for_request(
    target: LiveExecTarget,
    request: SessionRequest,
    *,
    session_nonce: str | None = None,
) -> list[str]:
    workspace = shlex.quote(target.workspace_folder)
    if request.command is None:
        payload = f"cd {workspace} && exec bash -l"
    else:
        payload = f"cd {workspace} && exec bash -lc {shlex.quote(request.command)}"
    if request.pty:
        term = shlex.quote(request.term or "xterm-256color")
        rows = max(1, min(request.height or 24, 1000))
        cols = max(1, min(request.width or 80, 1000))
        inner = f"stty rows {rows} cols {cols}; {payload}"
        payload = (
            "script -qefc true /dev/null >/dev/null 2>&1 || "
            "{ echo 'provider-exec: target lacks the util-linux script PTY helper' >&2; "
            "exit 127; }; "
            f"TERM={term} exec script -qefc {shlex.quote(inner)} /dev/null"
        )
    if session_nonce is not None:
        payload = (
            "setsid --wait true >/dev/null 2>&1 || "
            "{ echo 'provider-exec: target lacks the util-linux setsid session helper' >&2; "
            "exit 127; }; "
            f"exec env AGENT_CONTAINERS_SESSION_NONCE={session_nonce} "
            f"setsid --wait bash -lc {shlex.quote(payload)}"
        )
    return build_restricted_spawn_command(
        target.container_id,
        target.user,
        payload,
        login=False,
    )


class _ProviderServer:
    """Paramiko ServerInterface implementation for one session channel."""

    def __init__(self, paramiko_module) -> None:
        self._paramiko = paramiko_module
        self._defaults = paramiko_module.ServerInterface()
        self.request_event = threading.Event()
        self.command: str | None = None
        self.term: str | None = None
        self.width = 80
        self.height = 24
        self._channel_reserved = False

    def __getattr__(self, name: str):
        return getattr(self._defaults, name)

    def check_auth_none(self, _username: str) -> int:
        return self._paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, _username: str) -> str:
        return ""

    def check_channel_request(self, kind: str, _chanid: int) -> int:
        if kind != "session" or self._channel_reserved:
            return self._paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        self._channel_reserved = True
        return self._paramiko.OPEN_SUCCEEDED

    def check_channel_pty_request(
        self,
        _channel,
        term: bytes,
        width: int,
        height: int,
        _pixelwidth: int,
        _pixelheight: int,
        _modes: bytes,
    ) -> bool:
        try:
            self.term = term.decode("ascii", errors="strict")
        except UnicodeDecodeError:
            return False
        if not self.term or any(char in self.term for char in "\r\n\0"):
            return False
        self.width = width
        self.height = height
        return True

    def check_channel_exec_request(self, _channel, command: bytes) -> bool:
        try:
            decoded = command.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return False
        if "\0" in decoded:
            return False
        self.command = decoded
        self.request_event.set()
        return True

    def check_channel_shell_request(self, _channel) -> bool:
        self.command = None
        self.request_event.set()
        return True

    def check_channel_env_request(self, _channel, _name: bytes, _value: bytes) -> bool:
        return False

    def check_channel_subsystem_request(self, _channel, _name: str) -> bool:
        return False

    def check_channel_window_change_request(
        self,
        _channel,
        width: int,
        height: int,
        _pixelwidth: int,
        _pixelheight: int,
    ) -> bool:
        # The target-side `script` helper receives the initial dimensions. A
        # later resize is declined rather than pretending it was applied.
        self.width = width
        self.height = height
        return False


def _pump_stream_to_socket(source: BinaryIO, sock: socket.socket) -> None:
    try:
        while data := _read_stream(source):
            sock.sendall(data)
    except (OSError, ValueError):
        pass
    finally:
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _pump_socket_to_stream(sock: socket.socket, destination: BinaryIO) -> None:
    try:
        while data := sock.recv(65536):
            _write_stream(destination, data)
    except (OSError, ValueError):
        pass


def _pump_channel_to_process(channel, proc: subprocess.Popen[bytes]) -> None:
    try:
        while data := channel.recv(65536):
            if proc.stdin is None:
                return
            _write_stream(proc.stdin, data)
    except (OSError, ValueError):
        pass
    finally:
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass


def _pump_process_to_channel(source: BinaryIO, channel, *, stderr: bool) -> None:
    try:
        while data := _read_stream(source):
            if stderr:
                channel.sendall_stderr(data)
            else:
                channel.sendall(data)
    except (OSError, ValueError):
        pass


def _read_stream(source: BinaryIO) -> bytes:
    try:
        return os.read(source.fileno(), 65536)
    except (AttributeError, OSError):
        read1 = getattr(source, "read1", None)
        if read1 is not None:
            return read1(65536)
        return source.read(65536)


def _write_stream(destination: BinaryIO, data: bytes) -> None:
    try:
        fileno = destination.fileno()
    except (AttributeError, OSError):
        destination.write(data)
        destination.flush()
        return
    view = memoryview(data)
    while view:
        written = os.write(fileno, view)
        view = view[written:]


def _cleanup_target_session(target: LiveExecTarget, session_nonce: str) -> None:
    marker = f"AGENT_CONTAINERS_SESSION_NONCE={session_nonce}"
    payload = (
        "has_marker() { local entry=; "
        "while IFS= read -r -d '' entry; do "
        f'[ "$entry" = {shlex.quote(marker)} ] && return 0; '
        'done < "$1" 2>/dev/null; return 1; }; '
        "kill_marked() { local sig=$1 envfile pid stat rest; "
        "for envfile in /proc/[0-9]*/environ; do "
        'if has_marker "$envfile"; then '
        "pid=${envfile#/proc/}; pid=${pid%/environ}; "
        'IFS= read -r stat < "/proc/$pid/stat" 2>/dev/null || continue; '
        "rest=${stat##*) }; set -- $rest; "
        '[ "$3" = "$pid" ] || continue; '
        'kill "-$sig" -- "-$pid" 2>/dev/null || true; fi; done; }; '
        "kill_marked TERM; sleep 1; kill_marked KILL"
    )
    result = subprocess.run(
        build_restricted_spawn_command(
            target.container_id,
            target.user,
            payload,
            login=False,
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
        creationflags=no_window_flags(),
    )
    if result.returncode != 0:
        log.warning(
            "provider-exec cleanup failed target=%s rc=%s",
            target.name,
            result.returncode,
        )


def _channel_disconnected(channel) -> bool:
    if channel.closed:
        return True
    transport = channel.get_transport()
    return transport is None or not transport.is_active()


def _run_channel(target: LiveExecTarget, request: SessionRequest, channel) -> int:
    session_nonce = secrets.token_hex(16)
    command = _command_for_request(
        target,
        request,
        session_nonce=session_nonce,
    )
    log.info(
        "exec transport=provider-exec target=%s pty=%s",
        target.name,
        request.pty,
    )
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=no_window_flags(),
    )
    stdout = cast(BinaryIO, proc.stdout)
    stderr = cast(BinaryIO, proc.stderr)
    threads = [
        threading.Thread(
            target=_pump_channel_to_process,
            args=(channel, proc),
            daemon=True,
        ),
        threading.Thread(
            target=_pump_process_to_channel,
            args=(stdout, channel),
            kwargs={"stderr": False},
            daemon=True,
        ),
        threading.Thread(
            target=_pump_process_to_channel,
            args=(stderr, channel),
            kwargs={"stderr": not request.pty},
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    termination_requested = threading.Event()
    previous_handlers: dict[int, object] = {}
    if threading.current_thread() is threading.main_thread():
        handled = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGHUP"):
            handled.append(signal.SIGHUP)
        for signum in handled:
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, lambda _signum, _frame: termination_requested.set())

    def terminate_target() -> None:
        try:
            _cleanup_target_session(target, session_nonce)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning(
                "provider-exec cleanup unavailable target=%s error=%s",
                target.name,
                exc,
            )
        if proc.poll() is None:
            proc.terminate()

    disconnected = False
    disconnected_at: float | None = None
    try:
        while proc.poll() is None:
            if termination_requested.is_set():
                disconnected = True
                terminate_target()
                break
            if _channel_disconnected(channel):
                disconnected = True
                disconnected_at = disconnected_at or time.monotonic()
                if time.monotonic() - disconnected_at >= _FORCED_EXIT_SECONDS:
                    terminate_target()
                    break
            time.sleep(_CHANNEL_POLL_SECONDS)
        try:
            rc = proc.wait(timeout=_FORCED_EXIT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = proc.wait()
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)
    for thread in threads[1:]:
        thread.join(timeout=2 if disconnected else None)
    try:
        channel.send_exit_status(rc)
    except OSError:
        pass
    return rc


def _serve_ssh(
    target: LiveExecTarget,
    *,
    stdin: BinaryIO,
    stdout: BinaryIO,
) -> int:
    try:
        import paramiko
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Provider-exec SSH support is unavailable because Paramiko is not installed"
        ) from exc

    proxy_socket, server_socket = socket.socketpair()
    input_thread = threading.Thread(
        target=_pump_stream_to_socket,
        args=(stdin, proxy_socket),
        daemon=True,
    )
    output_thread = threading.Thread(
        target=_pump_socket_to_stream,
        args=(proxy_socket, stdout),
        daemon=True,
    )
    input_thread.start()
    output_thread.start()

    transport = paramiko.Transport(server_socket)
    transport.add_server_key(paramiko.ECDSAKey.generate())
    server = _ProviderServer(paramiko)
    try:
        transport.start_server(server=server)
        channel = transport.accept(_REQUEST_TIMEOUT)
        if channel is None:
            raise RuntimeError("OpenSSH did not open a session channel")
        if not server.request_event.wait(_REQUEST_TIMEOUT):
            channel.close()
            raise RuntimeError("OpenSSH did not request a shell or command")
        request = SessionRequest(
            command=server.command,
            term=server.term,
            width=server.width,
            height=server.height,
        )
        rc = _run_channel(target, request, channel)
        channel.shutdown_write()
        deadline = time.monotonic() + _CHANNEL_CLOSE_GRACE_SECONDS
        while transport.is_active() and not channel.closed and time.monotonic() < deadline:
            time.sleep(_CHANNEL_POLL_SECONDS)
        channel.close()
        return rc
    finally:
        transport.close()
        proxy_socket.close()
        server_socket.close()
        output_thread.join(timeout=2)


def run_ssh_stdio(
    name: str,
    *,
    expected_target_id: str | None = None,
    expected_instance_id: str | None = None,
    expected_assignment: dict | None = None,
) -> int:
    """Serve one restricted SSH connection over this process's stdio."""
    try:
        if expected_target_id is not None and expected_target_id != f"container:{name}":
            raise ProviderAdmissionError(
                f"Container '{name}' target identity changed"
            )
        with session_admission(name, expected_assignment=expected_assignment):
            target = resolve_live_exec_target(name)
            if target.actual_profile != RESTRICTED_PROFILE:
                raise RuntimeError(
                    f"Container '{name}' is not restricted; refusing provider-exec transport"
                )
            if (
                expected_instance_id is not None
                and target.container_id != expected_instance_id
            ):
                raise ProviderAdmissionError(
                    f"Container '{name}' instance identity changed"
                )
            return _serve_ssh(
                target,
                stdin=sys.stdin.buffer,
                stdout=sys.stdout.buffer,
            )
    except ProviderAdmissionError as exc:
        print(str(exc), file=sys.stderr)
        return 75
