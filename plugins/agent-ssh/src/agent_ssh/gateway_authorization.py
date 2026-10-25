"""Explicit target-local login-key convergence for a separately owned sshd."""

from __future__ import annotations

import csv
import ctypes
from functools import lru_cache
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from agent_procutil import no_window_kwargs
from dropin_registry import atomic_write_text

from . import gateway_profile, gateway_provider, ssh_profile
from .file_lock import exclusive_file_lock

_MARKER = "# agent-ssh.gateway-authorization "
_BINDING = {"target", "controller", "user"}


def projection(policy_path: Path, directory_path: Path, *, machine: str,
               environment: str, public_key_file: Path) -> dict:
    policy_type, _, decode = gateway_provider._sdk()
    try:
        from fleet_manager.gateway_ssh_provider import project_gateway_authorization
    except ImportError as exc:
        raise RuntimeError("Gateway authorization requires a current agent-ssh[fleet] installation") from exc
    policy = policy_type.from_dict(decode(gateway_provider._read(policy_path)))
    directory = decode(gateway_provider._read(directory_path))
    with public_key_file.expanduser().open("rb") as stream:
        public = stream.read(257)
    if len(public) > 256:
        raise ValueError("Gateway local public-key file exceeds its bound")
    return project_gateway_authorization(
        policy, directory, machine=machine, environment=environment,
        local_public_key=public.decode("ascii").strip(),
    )


@lru_cache(maxsize=1)
def _windows_sid() -> str:
    whoami = Path(os.environ["SystemRoot"]) / "System32" / "whoami.exe"
    account = subprocess.run(
        [str(whoami), "/user", "/fo", "csv", "/nh"], capture_output=True,
        text=True, check=True, timeout=10, **no_window_kwargs(),
    )
    rows = list(csv.reader(io.StringIO(account.stdout)))
    if len(rows) != 1 or len(rows[0]) != 2 or not re.fullmatch(r"S-1-\d+(?:-\d+)+", rows[0][1]):
        raise PermissionError("Cannot verify the local authorization account")
    return rows[0][1]


@lru_cache(maxsize=1)
def _windows_sddl_sid(sid: str) -> str:
    """Use the same native SID spelling as the descriptor reader (for example LA)."""
    from ctypes import wintypes as w

    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        w.LPCWSTR, w.DWORD, ctypes.POINTER(w.LPVOID), w.LPVOID,
    ]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
    security.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        w.LPVOID, w.DWORD, w.DWORD, ctypes.POINTER(w.LPWSTR), w.LPVOID,
    ]
    security.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = w.BOOL
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [w.LPVOID], w.LPVOID
    descriptor, text = w.LPVOID(), w.LPWSTR()
    if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        "O:" + sid, 1, ctypes.byref(descriptor), None,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not security.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, 1, 1, ctypes.byref(text), None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            value = text.value
            if not value or not re.fullmatch(r"O:(?:[A-Z]{2}|S-1(?:-\d+)+)", value):
                raise PermissionError("Cannot verify the native authorization identity")
            return value[2:]
        finally:
            kernel.LocalFree(text)
    finally:
        kernel.LocalFree(descriptor)


def _windows_create_directory(path: Path) -> None:
    from ctypes import wintypes as w

    class Attributes(ctypes.Structure):
        _fields_ = [("length", w.DWORD), ("descriptor", w.LPVOID), ("inherit", w.BOOL)]

    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        w.LPCWSTR, w.DWORD, ctypes.POINTER(w.LPVOID), w.LPVOID,
    ]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
    kernel.CreateDirectoryW.argtypes = [w.LPCWSTR, ctypes.POINTER(Attributes)]
    kernel.CreateDirectoryW.restype = w.BOOL
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [w.LPVOID], w.LPVOID
    sid = _windows_sid()
    descriptor = w.LPVOID()
    if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"O:{sid}D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)", 1, ctypes.byref(descriptor), None,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = Attributes(ctypes.sizeof(Attributes), descriptor, False)
        if not kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.LocalFree(descriptor)


def _windows_private(path: Path, *, directory: bool) -> None:
    from ctypes import wintypes as w

    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security.GetFileSecurityW.argtypes = [
        w.LPCWSTR, w.DWORD, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD),
    ]
    security.GetFileSecurityW.restype = w.BOOL
    security.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        w.LPVOID, w.DWORD, w.DWORD, ctypes.POINTER(w.LPWSTR), w.LPVOID,
    ]
    security.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = w.BOOL
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [w.LPVOID], w.LPVOID
    sid = _windows_sid()
    size = w.DWORD()
    security.GetFileSecurityW(str(path), 5, None, 0, ctypes.byref(size))
    if ctypes.get_last_error() != 122 or not size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(size.value)
    if not security.GetFileSecurityW(str(path), 5, descriptor, size, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    text = w.LPWSTR()
    if not security.ConvertSecurityDescriptorToStringSecurityDescriptorW(
        descriptor, 1, 5, ctypes.byref(text), None,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        value = text.value
    finally:
        kernel.LocalFree(text)
    _validate_windows_descriptor(value, _windows_sddl_sid(sid), directory=directory)


def _validate_windows_descriptor(value: str, sid: str, *, directory: bool) -> None:
    # Elevated tokens may select the OS's privileged default owner for new files.
    owners = re.escape(sid) if directory else (
        "(?:" + re.escape(sid) + r"|BA|SY|S-1-5-32-544|S-1-5-18)"
    )
    match = re.fullmatch(r"O:" + owners + r"D:([PAI]*)(.+)", value)
    if not match or (directory and "P" not in match[1]):
        shape = re.sub(
            r"S-\d+(?:-\d+)+",
            lambda found: "CURRENT" if found[0] == sid else "OTHER", value,
        )
        kind = "directory" if directory else "file"
        raise PermissionError(
            f"Authorization location must be owner-private ({kind} descriptor={shape[:256]})",
        )
    allowed = re.compile(
        r"\(A;[OICID]*;FA;;;(?:" + re.escape(sid) + r"|SY)\)"
    )
    entries = allowed.findall(match[2])
    if not entries or "".join(entries) != match[2]:
        raise PermissionError("Authorization location has an unexpected access grant")


def _private(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not expected(info.st_mode) or ssh_profile._is_reparse(info)
        or (not directory and info.st_nlink != 1)
    ):
        raise PermissionError("Authorization location must be ordinary and unlinked")
    if os.name == "nt":
        _windows_private(path, directory=directory)
    elif info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
        raise PermissionError("Authorization location must be owner-private")


def _root(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        raise ValueError("Select an absolute dedicated authorization state directory")
    for ancestor in (path, *path.parents):
        if os.path.lexists(ancestor) and ssh_profile._is_reparse(ancestor.lstat()):
            raise PermissionError("Authorization ancestors must not be links or reparse points")
        if ancestor.is_symlink():
            raise PermissionError("Authorization ancestors must not be links")
    if not path.exists():
        if os.name == "nt":
            _windows_create_directory(path)
        else:
            path.mkdir(mode=0o700)
    _private(path, directory=True)
    allowed = re.compile(r"(?:owner\.json|authorization\.lock|authorized_keys\.gateway|gateway-provider-[0-9a-f]{64}\.json)")
    entries = tuple(path.iterdir())
    if any(not allowed.fullmatch(entry.name) for entry in entries):
        raise ValueError("Select a dedicated authorization directory, not an existing user/service directory")
    for entry in entries:
        _private(entry, directory=False)
    return path


def _read(path: Path) -> str:
    _private(path, directory=False)
    return gateway_profile._read_owned_file(path)


def apply_projection(value: dict, state_dir: Path) -> tuple[Path, bool]:
    """Replace only this target's dedicated file; never the user's key file."""
    root = _root(state_dir)
    target, lock, binding = root / "authorized_keys.gateway", root / "authorization.lock", root / "owner.json"
    owner = {name: value[name] for name in _BINDING}
    if os.path.lexists(lock):
        _private(lock, directory=False)
    with exclusive_file_lock(lock):
        if os.name != "nt":
            os.chmod(lock, 0o600)
        if os.path.lexists(binding):
            if json.loads(_read(binding)) != owner:
                raise ValueError("Authorization state belongs to another target/controller")
        elif any(path != lock for path in root.iterdir()):
            raise ValueError("Existing authorization has no owner binding; preserve it")
        else:
            atomic_write_text(binding, gateway_profile._json(owner))
        previous = _read(target) if os.path.lexists(target) else None
        if previous is not None:
            line, separator, body = previous.partition("\n")
            if not separator or not line.startswith(_MARKER):
                raise ValueError("Authorization file is not managed by this owner")
            receipt = json.loads(line[len(_MARKER):])
            if (
                not isinstance(receipt, dict) or set(receipt) != {*_BINDING, "body_sha256"}
                or {name: receipt[name] for name in _BINDING} != owner
                or receipt["body_sha256"] != hashlib.sha256(body.encode()).hexdigest()
            ):
                raise ValueError("Authorization file changed outside its owner; preserve it")
        body = "".join("restrict,pty " + row["public_key"] + "\n" for row in value["peers"])
        data = _MARKER + json.dumps({
            **owner, "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        }, sort_keys=True) + "\n" + body
        # Input acceptance survives an interrupted write; exact retries are safe.
        gateway_provider.accept_directory(value, root)
        if previous == data:
            return target, False
        if (os.path.lexists(target) and _read(target) != previous) or (
            not os.path.lexists(target) and previous is not None
        ):
            raise ValueError("Authorization changed during publication; preserve it")
        atomic_write_text(target, data)
        _private(target, directory=False)
        return target, True


def command(args) -> int:
    try:
        value = projection(
            args.policy, args.directory, machine=args.machine,
            environment=args.environment, public_key_file=args.public_key_file,
        )
        path, changed = apply_projection(value, args.state_dir) if args.apply else (None, False)
        print(json.dumps({
            "version": 1, "state": value["state"], "target": value["target"],
            "user": value["user"],
            "controller": value["controller"], "directory_revision": value["directory_revision"],
            "authorized_peers": [
                {"machine": row["machine"], "environment": row["environment"]}
                for row in value["peers"]
            ],
            "authorization_file": str(path) if path else None,
            "applied": args.apply, "changed": changed,
            "service_started": False, "connection_attempted": False,
            "live_readiness_verified": False,
        }, sort_keys=True))
        return 0
    except (OSError, RuntimeError, UnicodeError, ValueError, RecursionError, subprocess.SubprocessError) as exc:
        import sys

        print(f"[FAIL] Gateway authorization: {exc}. Preserve keys and accepted revision state.", file=sys.stderr)
        return 2


def add_parser(subparsers):
    parser = subparsers.add_parser(
        "gateway-authorization", help="Converge a selected target-local sshd's dedicated login-key file",
    )
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--machine", required=True)
    parser.add_argument("--environment", required=True, choices=("windows", "wsl", "linux"))
    parser.add_argument("--public-key-file", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=command)
