"""Private platform cache placement and completed-runtime namespace ownership."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from functools import lru_cache
from pathlib import Path

SCHEMA = "copilot-extensions.python-bytecode"


@lru_cache(maxsize=8)
def component(name: str):
    """Resolve a declared companion from this independently installed package."""
    if name == "python_cache_permissions":
        from . import python_cache_permissions as module
    elif name == "_daemon_instance_owner":
        from . import _daemon_instance_owner as module
    elif name == "python_cache_catalog":
        from . import python_cache_catalog as module
    else:
        raise ValueError("unknown Python cache companion")
    return module


def normalized(path: str | os.PathLike[str]) -> str:
    value = os.path.abspath(path)
    if os.name == "nt":
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
    return os.path.normcase(os.path.normpath(value))


def native(path: Path) -> Path:
    if os.name != "nt":
        return path
    text = os.path.abspath(path)
    if not text.startswith("\\\\?\\"):
        text = "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text
    return Path(text)


def _local_appdata() -> Path:
    import ctypes
    from ctypes import wintypes
    import uuid

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32")
    shell.SHGetKnownFolderPath.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(wintypes.LPWSTR),
    ]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    identifier = ctypes.create_string_buffer(
        uuid.UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le,
    )
    result = wintypes.LPWSTR()
    error = shell.SHGetKnownFolderPath(identifier, 0, None, ctypes.byref(result))
    if error:
        raise OSError("Local AppData is unavailable")
    try:
        return Path(result.value)
    finally:
        ole.CoTaskMemFree(ctypes.cast(result, ctypes.c_void_p))


def user_cache() -> Path:
    """Resolve the OS user-cache base without a current-directory fallback."""
    if os.name == "nt":
        configured = os.environ.get("LOCALAPPDATA", "")
        base = Path(configured) if configured and os.path.isabs(configured) else _local_appdata()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        configured = os.environ.get("XDG_CACHE_HOME", "")
        base = Path(configured) if configured and os.path.isabs(configured) else Path.home() / ".cache"
    if not base.is_absolute():
        raise ValueError("Python cache base must be absolute")
    return native(base)


def _directory(path: Path, permissions, *, private: bool = False) -> None:
    if not os.path.lexists(path):
        _directory(path.parent, permissions)
        permissions.mkdir(path)
    permissions.check(path, private=private)


def prepare(root: Path, target: Path, identity: str, *, read_record) -> Path:
    """Create/reuse one matching private namespace outside the runtime."""
    permissions = component("python_cache_permissions")
    base = user_cache()
    _directory(base, permissions)
    permissions.check_ancestors(base)
    location = base
    root_text, target_text = normalized(root), normalized(target)
    owner = {
        "schema": SCHEMA, "version": 1, "runtime_root": root_text,
        "runtime": target_text, "installed_identity": identity,
    }
    installation = hashlib.sha256(root_text.encode("utf-8")).hexdigest()
    key = hashlib.sha256(
        json.dumps(owner, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()
    for part in ("copilot-extensions", "python", "v1", installation, "installed-" + key):
        location /= part
        _directory(location, permissions, private=True)
    record = location / "owner.json"
    encoded = (json.dumps(owner, sort_keys=True) + "\n").encode("utf-8")
    private_owner = component("_daemon_instance_owner")
    component("python_cache_catalog").register(
        location.parent, key, encoded, private_owner=private_owner,
    )
    try:
        descriptor = private_owner.open_private(
            record, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
    except FileExistsError:
        if record.lstat().st_size > 16384:
            raise ValueError("Python cache ownership exceeds its byte budget")
        existing = read_record(record)
        if len(existing) > 16384 or existing != encoded:
            raise ValueError("Python cache ownership differs")
    else:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
    bytecode = location / "bytecode"
    _directory(bytecode, permissions, private=True)
    return bytecode
