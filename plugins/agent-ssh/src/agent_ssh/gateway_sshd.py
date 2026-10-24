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

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job
from dropin_registry import atomic_write_text

from . import gateway_authorization as authorization
from . import gateway_profile, gateway_provider, ssh_profile
from .file_lock import exclusive_file_lock

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
    if os.name != "nt":
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
    if os.name != "nt":
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


def load(path: Path) -> dict:
    path = _absolute(str(path))
    authorization._private(path, directory=False)
    _, _, decode = gateway_provider._sdk()
    value = decode(gateway_provider._read(path))
    if (
        not isinstance(value, dict) or set(value) != _FIELDS
        or value["schema"] != "agent-ssh.gateway-native-ingress"
        or type(value["version"]) is not int or value["version"] != 1
    ):
        raise ValueError("Invalid native Gateway ingress configuration")
    if type(value["port"]) is not int or not 1024 <= value["port"] <= 65535:
        raise ValueError("Select an explicit nonprivileged native loopback endpoint")
    for field in _FIELDS - {"schema", "version", "machine", "environment", "port"}:
        value[field] = _absolute(value[field])
    if value["environment"] != _environment():
        raise ValueError("Native Gateway ingress environment differs from this host")
    return value


def _executable(path: Path) -> None:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or (os.name != "nt" and not os.access(path, os.X_OK)):
        raise ValueError("Selected native OpenSSH executable is unavailable")


def _host_key(config: dict, expected: str) -> None:
    authorization._private(config["host_key_file"], directory=False)
    _executable(config["ssh_keygen"])
    result = subprocess.run(
        [str(config["ssh_keygen"]), "-y", "-P", "", "-f", str(config["host_key_file"])],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=10,
        check=False, env=_process_environment(), **no_window_kwargs(),
    )
    if result.returncode != 0 or len(result.stdout) > 4096:
        raise ValueError("Selected native SSH host key is unavailable or encrypted")
    if " ".join(result.stdout.decode("ascii").split()[:2]) != expected:
        raise ValueError("Native SSH host key differs from the admitted target pin")


def _quoted(path: Path) -> str:
    return '"' + path.as_posix() + '"'


def _native_config(config: dict, user: str, authorized: Path) -> str:
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


def _check_endpoint(config: dict) -> None:
    # A refusal check, not a reservation or proof of the subsequent native bind.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as endpoint:
        if os.name == "nt":
            endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            endpoint.bind(("127.0.0.1", config["port"]))
        except OSError as exc:
            raise OSError("Selected native loopback endpoint is already bound or unavailable") from exc


def prepare(config: dict) -> Path:
    """Verify native target bindings, then converge the dedicated ingress files."""
    value = authorization.projection(
        config["policy"], config["directory"], machine=config["machine"],
        environment=config["environment"], public_key_file=config["public_key_file"],
    )
    account = _account()
    matches = account.casefold() == value["user"].casefold() if os.name == "nt" else account == value["user"]
    if not matches:
        raise PermissionError("Native Gateway ingress user differs from the admitted login user")
    principal = _principal(account)
    if not isinstance(value["host_key"], str):
        raise ValueError("Native Gateway ingress requires an admitted host pin")
    _executable(config["sshd"])
    _host_key(config, value["host_key"])
    root = _absolute(str(config["state_dir"]))
    if not root.exists():
        if os.name == "nt":
            authorization._windows_create_directory(root)
        else:
            root.mkdir(mode=0o700)
    authorization._private(root, directory=True)
    owner = {
        "schema": "agent-ssh.gateway-native-ingress", "version": 1,
        "target": value["target"], "controller": value["controller"], "user": account,
        "host_key": value["host_key"], "principal": principal,
    }
    allowed = {"owner.json", "ingress.lock", "authorization", "sshd_config"}
    if any(entry.name not in allowed for entry in root.iterdir()):
        raise ValueError("Select a dedicated native ingress directory; preserve existing service state")
    for entry in root.iterdir():
        authorization._private(entry, directory=entry.name == "authorization")
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
        authorized = root / "authorization" / "authorized_keys.gateway"
        content = _native_config(config, principal, authorized)
        if os.path.lexists(target):
            if authorization._read(target) != content:
                raise ValueError("Native sshd configuration differs from its owner; preserve it")
        else:
            atomic_write_text(target, content)
        result = subprocess.run(
            [str(config["sshd"]), "-t", "-f", str(target)],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=10,
            check=False, env=_process_environment(), **no_window_kwargs(),
        )
        if result.returncode:
            raise ValueError("Native sshd configuration validation failed: " + result.stderr[:2048].decode("utf-8", errors="replace").strip())
        # Native syntax/identity validation precedes changing the login key set.
        authorization.apply_projection(value, root / "authorization")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        config = load(args.config)
        _check_endpoint(config)
        target = prepare(config)
        argv = [str(config["sshd"]), "-D", "-e", "-f", str(target)]
        os.chdir(config["state_dir"])
        if os.name != "nt":
            os.execv(argv[0], argv)
        child, job = spawn_sync_in_kill_on_close_job(
            argv, require_job=True, allow_breakaway=False,
            stdin=subprocess.DEVNULL, env=_process_environment(), **no_window_kwargs(),
        )
        try:
            return child.wait()
        finally:
            job.terminate_and_wait(5.0)
            child.wait(timeout=5.0)
    except (OSError, RuntimeError, ValueError, RecursionError, subprocess.TimeoutExpired) as exc:
        print(f"[FAIL] Native Gateway ingress: {exc}. Preserve existing keys and service state.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
