"""Native SSH listener entrypoint for an explicitly selected lifecycle owner.

The existing owner selects the loopback endpoint and supervises foreground sshd.
This adapter never installs a supervisor, allocates or advertises an endpoint,
selects a Gateway, or treats transport access as SSH authentication.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import re
import socket
import stat
import subprocess
import sys
import threading
import time
from typing import Any

from agent_procutil import JobHandle, no_window_kwargs, spawn_sync_in_kill_on_close_job
from dropin_registry import atomic_write_text

from . import gateway_authorization as authorization
from . import gateway_native, gateway_profile, gateway_provider, ssh_profile
from .file_lock import exclusive_file_lock

DIRECTORY_UPDATE_CAPABILITY = "gateway-native-ingress-directory-v1"
FOREGROUND_CAPABILITY = "gateway-native-ingress-foreground-v1"
NATIVE_MEMBERSHIP_CAPABILITY = "gateway-native-ingress-membership-v1"
Document = dict[str, Any]


class PreparationCancelled(RuntimeError):
    """The embedding stopped preparation before authorization publication."""


class Preparation:
    """Cancellation and retained native probe custody for one receiving run."""

    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self.process: subprocess.Popen[bytes] | None = None
        self.job: JobHandle | None = None

    def check(self) -> None:
        if self.cancelled.is_set():
            raise PreparationCancelled("Native ingress preparation was cancelled")

    def close(self) -> None:
        if self.job is not None:
            self.job.terminate_and_wait(2.0)
            self.job = None
        if self.process is not None:
            self.process.communicate(timeout=2.0)
            self.process = None

    def run(self, arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
        require_foreground_support()
        self.check()
        if self.process is not None or self.job is not None:
            raise RuntimeError("Native ingress probe cleanup is still pending")
        self.process, self.job = spawn_sync_in_kill_on_close_job(
            arguments, require_job=True, allow_breakaway=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=_process_environment(), **no_window_kwargs(),
        )
        assert self.job is not None
        deadline = time.monotonic() + 10
        try:
            while True:
                self.check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(arguments, 10)
                try:
                    stdout, stderr = self.process.communicate(timeout=min(0.05, remaining))
                except subprocess.TimeoutExpired:
                    continue
                self.check()
                assert self.process.returncode is not None
                return subprocess.CompletedProcess(
                    arguments, self.process.returncode, stdout, stderr,
                )
        finally:
            self.close()


def _probe(
    arguments: list[str], preparation: Preparation | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if preparation is not None:
        return preparation.run(arguments)
    return subprocess.run(
        arguments, stdin=subprocess.DEVNULL, capture_output=True, timeout=10,
        check=False, env=_process_environment(), **no_window_kwargs(),
    )


_FIELDS = {
    "schema", "version", "policy", "directory", "machine", "environment",
    "public_key_file", "host_key_file", "state_dir", "sshd", "ssh_keygen", "port",
}


def _process_environment() -> dict[str, str]:
    descriptor = "c28fc6f98a2c44abbbd89d6a3037d0d9_posix_fd_state"
    return {
        name: value for name, value in os.environ.items()
        if os.name != "nt" or name.casefold() != descriptor
    }


def _absolute(value: str) -> Path:
    if not isinstance(value, str) or not value or any(
        ord(char) < 32 or char in '"%' for char in value
    ):
        raise ValueError("Native SSH references must be literal absolute paths")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Native SSH references must be absolute")
    for parent in (path, *path.parents):
        if os.path.lexists(parent) and (
            parent.is_symlink() or ssh_profile._is_reparse(parent.lstat())
        ):
            raise PermissionError("Native SSH references cannot traverse links or reparse points")
    return path


def _account() -> str:
    if sys.platform != "win32":
        import pwd

        if os.geteuid() == 0:
            raise PermissionError("Native Gateway ingress runs as its non-root login user")
        return pwd.getpwuid(os.geteuid()).pw_name
    from ctypes import wintypes as w

    security = ctypes.WinDLL("advapi32", use_last_error=True)
    security.GetUserNameW.argtypes = [w.LPWSTR, ctypes.POINTER(w.DWORD)]
    security.GetUserNameW.restype = w.BOOL
    size = w.DWORD(257)
    name = ctypes.create_unicode_buffer(size.value)
    if not security.GetUserNameW(name, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return name.value


def _principal(account: str) -> str:
    if sys.platform != "win32":
        return account
    from ctypes import wintypes as w

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.IsUserAnAdmin.argtypes, shell.IsUserAnAdmin.restype = [], w.BOOL
    if shell.IsUserAnAdmin():
        raise PermissionError("Native Gateway ingress requires a non-elevated user token")
    security = ctypes.WinDLL("secur32", use_last_error=True)
    security.GetUserNameExW.argtypes = [w.DWORD, w.LPWSTR, ctypes.POINTER(w.ULONG)]
    security.GetUserNameExW.restype = w.BOOL
    size = w.ULONG(1024)
    name = ctypes.create_unicode_buffer(size.value)
    if not security.GetUserNameExW(2, name, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\\[A-Za-z0-9_.-]+", name.value):
        raise PermissionError("Native Windows login principal is not representable without patterns")
    return name.value.lower()


def _environment() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform != "linux":
        raise ValueError("Native Gateway ingress supports Windows and Linux/WSL")
    return "wsl" if "microsoft" in platform.release().lower() else "linux"


def load(path: Path) -> Document:
    path = _absolute(str(path))
    authorization._private(path, directory=False)
    value = gateway_native._decode(gateway_provider._read(path))
    native = type(value.get("version")) is int and value["version"] == 2
    fields = (_FIELDS - {"policy", "directory"}) | {"native_gateway"} if native else _FIELDS
    if (
        set(value) != fields
        or value["schema"] != "agent-ssh.gateway-native-ingress"
        or type(value["version"]) is not int or value["version"] not in (1, 2)
    ):
        raise ValueError("Invalid native Gateway ingress configuration")
    if type(value["port"]) is not int or not 1024 <= value["port"] <= 65535:
        raise ValueError("Select an explicit nonprivileged native loopback endpoint")
    if native:
        value["native_gateway"] = gateway_native._descriptor(value["native_gateway"])
        if value["native_gateway"]["machine"] != value["machine"]:
            raise ValueError("Native Gateway descriptor differs from the configured target")
    for field in fields - {"schema", "version", "machine", "environment", "port", "native_gateway"}:
        value[field] = _absolute(value[field])
    if value["environment"] != _environment():
        raise ValueError("Native Gateway ingress environment differs from this host")
    return value


def _executable(path: Path) -> None:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or (os.name != "nt" and not os.access(path, os.X_OK)):
        raise ValueError("Selected native OpenSSH executable is unavailable")


def _host_key(
    config: Document, expected: str, preparation: Preparation | None = None,
) -> None:
    authorization._private(config["host_key_file"], directory=False)
    _executable(config["ssh_keygen"])
    result = _probe(
        [str(config["ssh_keygen"]), "-y", "-P", "", "-f", str(config["host_key_file"])],
        preparation,
    )
    if result.returncode != 0 or len(result.stdout) > 4096:
        raise ValueError("Selected native SSH host key is unavailable or encrypted")
    if " ".join(result.stdout.decode("ascii").split()[:2]) != expected:
        raise ValueError("Native SSH host key differs from the admitted target pin")


def _quoted(path: Path) -> str:
    return '"' + path.as_posix() + '"'


def _native_config(config: Document, user: str, authorized: Path) -> str:
    return "\n".join((
        "# agent-ssh.gateway-native-ingress v1",
        "Port " + str(config["port"]),
        "ListenAddress 127.0.0.1",
        "AddressFamily inet",
        "PidFile none",
        "HostKey " + _quoted(config["host_key_file"]),
        "HostKeyAlgorithms ssh-ed25519",
        "AuthorizedKeysFile " + _quoted(authorized),
        "AuthorizedKeysCommand none",
        "TrustedUserCAKeys none",
        "AuthorizedPrincipalsFile none",
        "AllowUsers " + user,
        "PubkeyAuthentication yes",
        "PasswordAuthentication no",
        "KbdInteractiveAuthentication no",
        "AuthenticationMethods publickey",
        "PermitEmptyPasswords no",
        "PermitRootLogin no",
        "StrictModes yes",
        "DisableForwarding yes",
        "AllowTcpForwarding no",
        "AllowAgentForwarding no",
        "X11Forwarding no",
        "PermitTunnel no",
        "PermitUserEnvironment no",
        "PermitUserRC no",
        "UseDNS no",
        "MaxAuthTries 3",
        "LoginGraceTime 30",
        "MaxSessions 8",
        "Subsystem sftp internal-sftp",
        "LogLevel VERBOSE",
        "",
    ))


def _check_endpoint(config: Document) -> None:
    # A refusal check, not a reservation or proof of the subsequent native bind.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as endpoint:
        if sys.platform == "win32":
            endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            endpoint.bind(("127.0.0.1", config["port"]))
        except OSError as exc:
            raise OSError("Selected native loopback endpoint is already bound or unavailable") from exc


def prepare(config: Document, *, preparation: Preparation | None = None) -> Path:
    """Verify native target bindings, then converge the dedicated ingress files."""
    if config["version"] == 2:
        raise ValueError("Native ingress requires a current authenticated Gateway observation")
    value = authorization.projection(
        config["policy"], config["directory"], machine=config["machine"],
        environment=config["environment"], public_key_file=config["public_key_file"],
    )
    return _prepare(config, value, preparation)


def prepare_update(
    config: Document, update: object, *, preparation: Preparation | None = None,
) -> Path:
    """Converge authenticated inbound membership without staging outbound profiles."""
    if config["version"] == 2:
        value = authorization.projection_from_native(
            update, descriptor=config["native_gateway"], machine=config["machine"],
            environment=config["environment"], public_key_file=config["public_key_file"],
        )
    else:
        value = authorization.projection_from_update(
            update, policy_path=config["policy"], machine=config["machine"],
            environment=config["environment"], public_key_file=config["public_key_file"],
        )
    return _prepare(config, value, preparation)


def validate_bindings(
    config: Document, value: Document, preparation: Preparation | None = None,
) -> tuple[str, str]:
    """Check incoming local identity and executables without changing listener state."""
    account = _account()
    matches = account.casefold() == value["user"].casefold() if os.name == "nt" else account == value["user"]
    if not matches:
        raise PermissionError("Native Gateway ingress user differs from the admitted login user")
    principal = _principal(account)
    if not isinstance(value["host_key"], str):
        raise ValueError("Native Gateway ingress requires an admitted host pin")
    _executable(config["sshd"])
    _host_key(config, value["host_key"], preparation)
    return account, principal


def _retained_state(
    config: Document, value: Document, account: str, principal: str,
) -> tuple[Path, Document, str]:
    root = _absolute(str(config["state_dir"]))
    owner = {
        "schema": "agent-ssh.gateway-native-ingress", "version": 1,
        "target": value["target"], "controller": value["controller"], "user": account,
        "host_key": value["host_key"], "principal": principal,
    }
    content = _native_config(config, principal, root / "authorization" / "authorized_keys.gateway")
    if not os.path.lexists(root):
        return root, owner, content
    authorization._private(root, directory=True)
    allowed = {"owner.json", "ingress.lock", "authorization", "sshd_config"}
    if any(entry.name not in allowed for entry in root.iterdir()):
        raise ValueError("Select a dedicated native ingress directory; preserve existing service state")
    for entry in root.iterdir():
        authorization._private(entry, directory=entry.name == "authorization")
    binding, target = root / "owner.json", root / "sshd_config"
    if binding.exists():
        if json.loads(authorization._read(binding)) != owner:
            raise ValueError("Native ingress directory belongs to another target or host key")
    elif any(entry.name != "ingress.lock" for entry in root.iterdir()):
        raise ValueError("Native ingress directory has no owner binding; preserve it")
    if os.path.lexists(target) and authorization._read(target) != content:
        raise ValueError("Native sshd configuration differs from its owner; preserve it")
    authorization.validate_state(
        root / "authorization", target=value["target"], controller=value["controller"],
    )
    authorized_owner = root / "authorization" / "owner.json"
    if authorized_owner.exists() and json.loads(authorization._read(authorized_owner)) != {
        name: value[name] for name in authorization._BINDING
    }:
        raise ValueError("Authorization state belongs to another target/controller")
    if "native_membership" in value:
        authorization.validate_native_projection(value, root / "authorization")
    return root, owner, content


def validate_preparation(config: Document, value: Document) -> None:
    """Refuse incompatible retained state without creating files or probing the bind."""
    account, principal = validate_bindings(config, value)
    _retained_state(config, value, account, principal)


def _prepare(
    config: Document, value: Document, preparation: Preparation | None = None,
) -> Path:
    account, principal = validate_bindings(config, value, preparation)
    root, owner, content = _retained_state(config, value, account, principal)
    if not root.exists():
        if os.name == "nt":
            authorization._windows_create_directory(root)
        else:
            root.mkdir(mode=0o700)
    authorization._private(root, directory=True)
    binding, target = root / "owner.json", root / "sshd_config"
    with exclusive_file_lock(root / "ingress.lock"):
        if os.name != "nt":
            (root / "ingress.lock").chmod(0o600)
        if binding.exists():
            if json.loads(authorization._read(binding)) != owner:
                raise ValueError("Native ingress directory belongs to another target or host key")
        elif any(entry.name != "ingress.lock" for entry in root.iterdir()):
            raise ValueError("Native ingress directory has no owner binding; preserve it")
        else:
            atomic_write_text(binding, gateway_profile._json(owner))
        if os.path.lexists(target):
            if authorization._read(target) != content:
                raise ValueError("Native sshd configuration differs from its owner; preserve it")
        else:
            atomic_write_text(target, content)
        result = _probe(
            [str(config["sshd"]), "-t", "-f", str(target)],
            preparation,
        )
        if result.returncode:
            raise ValueError("Native sshd configuration validation failed: " + result.stderr[:2048].decode("utf-8", errors="replace").strip())
        # Native syntax/identity validation precedes changing the login key set.
        if preparation is not None:
            preparation.check()
        authorization.apply_projection(value, root / "authorization")
    return target


class ForegroundIngress:
    """A caller-owned listener, not a daemon or an independent lifetime owner."""

    def __init__(self, process: subprocess.Popen[bytes], job: JobHandle, port: int) -> None:
        self.process, self.job, self.port = process, job, port
        self._job_settled = False
        self._closed = False

    def ready(self) -> bool:
        """Observe this child's loopback listener and banner, not SSH authentication."""
        from ._native_listener import listener_pid, ssh_banner

        if self._job_settled or self.process.poll() is not None:
            return False
        try:
            if listener_pid(self.port) != self.process.pid or not ssh_banner(self.port):
                return False
            return self.process.poll() is None and listener_pid(self.port) == self.process.pid
        except OSError:
            return False

    def close(self) -> None:
        if not self._closed:
            if not self._job_settled:
                self.job.terminate_and_wait(5.0)
                self._job_settled = True
            self.process.wait(timeout=5.0)
            self._closed = True


def require_foreground_support() -> None:
    if sys.platform != "win32":
        raise OSError("Owned native ingress currently requires Windows; POSIX CLI remains exec-based")


def start(
    config: Document, *, update: object | None = None,
    preparation: Preparation | None = None,
) -> ForegroundIngress:
    """Give the selected Windows lifetime owner custody of its foreground sshd.

    The embedding owns reconnect policy, directory refresh and durable startup.
    No detached process, registration, outbound profile or credential is created.
    """
    require_foreground_support()
    _check_endpoint(config)
    options = {} if preparation is None else {"preparation": preparation}
    target = prepare(config, **options) if update is None else prepare_update(config, update, **options)
    if preparation is not None:
        preparation.check()
    process, job = spawn_sync_in_kill_on_close_job(
        [str(config["sshd"]), "-D", "-e", "-f", str(target)],
        require_job=True, allow_breakaway=False, cwd=config["state_dir"],
        stdin=subprocess.DEVNULL, env=_process_environment(), **no_window_kwargs(),
    )
    assert job is not None
    return ForegroundIngress(process, job, config["port"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        config = load(args.config)
        if sys.platform == "win32":
            owner = start(config)
            try:
                return owner.process.wait()
            finally:
                owner.close()
        _check_endpoint(config)
        target = prepare(config)
        argv = [str(config["sshd"]), "-D", "-e", "-f", str(target)]
        os.chdir(config["state_dir"])
        os.execv(argv[0], argv)
    except (OSError, RuntimeError, ValueError, RecursionError, subprocess.TimeoutExpired) as exc:
        print(f"[FAIL] Native Gateway ingress: {exc}. Preserve existing keys and service state.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
