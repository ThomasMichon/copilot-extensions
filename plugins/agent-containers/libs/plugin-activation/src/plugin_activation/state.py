"""Strict Copilot plugin inventory and activation state operations."""

from __future__ import annotations

import json
import io
import logging
import os
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Any, BinaryIO, TextIO

RESTORE_RETRY_DELAY_S = 1.0


class PluginStateError(ValueError):
    """Raised when Copilot plugin state cannot be handled safely."""


@dataclass(frozen=True)
class ActivationSnapshot:
    """Activation fields that an inventory bootstrap may promote."""

    identity: str
    settings_existed: bool
    enabled_plugins_existed: bool
    user_value_existed: bool
    user_value: bool | None
    inventory_enabled: bool | None
    inventory_record_existed: bool = False


@dataclass
class _StateDocuments:
    home: Path
    config_path: Path
    config_header: str
    config: dict[str, Any]
    settings_path: Path
    settings: dict[str, Any]
    record: dict[str, Any] | None
    user_activation: bool | None


def copilot_home() -> Path:
    """Return the current user's Copilot state directory."""
    return Path.home() / ".copilot"


def validate_identity(identity: str) -> None:
    """Require a source-qualified ``name@marketplace`` plugin identity."""
    if (
        not isinstance(identity, str)
        or identity.count("@") != 1
        or identity.startswith("@")
        or identity.endswith("@")
    ):
        raise PluginStateError("plugin identity must be <name>@<marketplace>")


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PluginStateError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _split_jsonc_header(raw: str) -> tuple[str, str]:
    lines = raw.splitlines(keepends=True)
    body_start = 0
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("//") or not stripped.strip():
            body_start = index + 1
            continue
        break
    return "".join(lines[:body_start]), "".join(lines[body_start:])


def open_binary_shared(path: Path) -> BinaryIO:
    if os.name != "nt":
        return path.open("rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    try:
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def _replace_state(source: str, destination: Path) -> None:
    if os.name != "nt":
        os.replace(source, destination)
        return
    import ctypes
    from ctypes import wintypes

    class RenameInfo(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD), ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD), ("FileName", wintypes.WCHAR * 1),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    # Rename the staged name itself rather than following a substituted source link.
    handle = kernel.CreateFileW(source, 0x10000, 0x7, None, 3, 0x80 | 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        name = str(destination.absolute()).encode("utf-16-le", errors="surrogatepass")
        offset = RenameInfo.FileName.offset
        buffer = ctypes.create_string_buffer(max(ctypes.sizeof(RenameInfo), offset + len(name) + 2))
        info = RenameInfo.from_buffer(buffer)
        info.Flags = 0x1 | 0x2  # replace-if-exists plus POSIX retained-reader semantics
        info.FileNameLength = len(name)
        ctypes.memmove(ctypes.addressof(buffer) + offset, name, len(name))
        if not kernel.SetFileInformationByHandle(handle, 22, buffer, len(buffer)):
            error = ctypes.WinError(ctypes.get_last_error())
            if error.winerror in {1175, 1176, 1177}:
                error.retain_staged_state = True
            raise error
    finally:
        kernel.CloseHandle(handle)


def _open_state_text(path: Path) -> TextIO:
    return io.TextIOWrapper(open_binary_shared(path), encoding="utf-8")


def read_json_object(
    path: Path,
    *,
    jsonc_header: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Read one JSON object, rejecting malformed input and duplicate keys."""
    if not path.exists():
        return "", {}
    try:
        with _open_state_text(path) as stream:
            raw = stream.read()
        header, body = _split_jsonc_header(raw) if jsonc_header else ("", raw)
        value = json.loads(body, object_pairs_hook=_reject_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError, PluginStateError) as exc:
        raise PluginStateError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PluginStateError(f"{path} must contain a JSON object")
    return header, value


def _object_unchanged(
    path: Path,
    value: dict[str, Any],
    header: str = "",
    *,
    jsonc_header: bool = False,
) -> bool:
    """Return whether ``path`` already holds this exact object and header.

    Used to skip a write entirely when nothing would actually change. This
    file is shared, user-global state (``~/.copilot/settings.json`` /
    ``config.json``) that every open Copilot CLI session on the machine
    watches; an unconditional rewrite on every call -- even one that changes
    nothing -- fires a distinct file-change event per call and can trigger a
    cross-session extension-host reload storm.
    """
    if not path.exists():
        return False
    try:
        existing_header, existing = read_json_object(path, jsonc_header=jsonc_header)
    except PluginStateError:
        return False
    return existing_header == header and existing == value


def write_json_object_atomic(
    path: Path,
    value: dict[str, Any],
    header: str = "",
) -> None:
    """Atomically write one JSON object while preserving a leading JSONC header."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(header)
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _replace_state(temp_name, path)
    except BaseException as exc:
        if getattr(exc, "retain_staged_state", False):
            exc.staged_state_path = Path(temp_name)
        else:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
        raise


def inventory_identity(record: dict[str, Any]) -> str | None:
    """Return a source-qualified identity from one installed inventory record."""
    name = record.get("name")
    marketplace = record.get("marketplace")
    if not isinstance(name, str) or not name:
        return None
    identity = (
        name
        if "@" in name
        else f"{name}@{marketplace}"
        if isinstance(marketplace, str) and marketplace
        else None
    )
    if identity is not None:
        validate_identity(identity)
    return identity


def inventory_records(
    config: dict[str, Any],
    path: Path,
) -> list[dict[str, Any]]:
    """Validate and return installed plugin inventory records."""
    raw = config.get("installedPlugins", [])
    if not isinstance(raw, list):
        raise PluginStateError(f"{path}: installedPlugins must be an array")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise PluginStateError(
                f"{path}: installedPlugins[{index}] must be an object"
            )
        identity = inventory_identity(item)
        if identity is not None:
            if identity in seen:
                raise PluginStateError(
                    f"{path}: duplicate installed plugin identity: {identity}"
                )
            seen.add(identity)
        enabled = item.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            label = identity or str(index)
            raise PluginStateError(
                f"{path}: installed plugin {label} enabled must be boolean"
            )
        records.append(item)
    return records


def _checked_enabled_plugins(data: dict[str, Any], path: Path) -> dict[str, Any] | None:
    """``enabledPlugins`` validated for the entries this library manages, or
    ``None`` when absent.

    Only marketplace plugins (any key with an ``@``) are this library's: each
    must be a well-formed ``name@marketplace`` with a boolean value. A key
    without one is a built-in plugin's own entry (Copilot CLI's first-party
    plugins have no marketplace): it is never read, written or validated here,
    so one such entry can't make every activation read refuse."""
    enabled = data.get("enabledPlugins")
    if enabled is None and "enabledPlugins" not in data:
        return None
    if not isinstance(enabled, dict):
        raise PluginStateError(f"{path}: enabledPlugins must be an object")
    for key, value in enabled.items():
        if isinstance(key, str) and "@" not in key:
            continue
        validate_identity(key)
        if not isinstance(value, bool):
            raise PluginStateError(f"{path}: enabledPlugins.{key} must be boolean")
    return enabled


def activation_value(
    data: dict[str, Any],
    path: Path,
    identity: str,
) -> bool | None:
    """Return one activation value, distinguishing absence from ``false``."""
    validate_identity(identity)
    enabled = _checked_enabled_plugins(data, path)
    return None if enabled is None else enabled.get(identity)


def remove_activation_entries(
    settings: dict[str, Any],
    identities: Iterable[str],
    *,
    path: Path,
) -> tuple[dict[str, Any], list[str]]:
    """Remove exact user activation keys while preserving all other settings."""
    requested = sorted(set(identities))
    for identity in requested:
        validate_identity(identity)
    enabled = _checked_enabled_plugins(settings, path)
    if enabled is None:
        return dict(settings), []

    new = dict(settings)
    new_enabled = dict(enabled)
    removed = [identity for identity in requested if identity in new_enabled]
    for identity in removed:
        del new_enabled[identity]
    new["enabledPlugins"] = new_enabled
    return new, removed


def installed_plugin_identities(home: Path | None = None) -> list[str]:
    """Return source-qualified identities from persistent installed inventory."""
    home = home or copilot_home()
    config_path = home / "config.json"
    _, config = read_json_object(config_path, jsonc_header=True)
    return sorted(
        identity
        for record in inventory_records(config, config_path)
        if (identity := inventory_identity(record)) is not None
    )


def _load_state(
    identity: str,
    home: Path | None = None,
) -> _StateDocuments:
    validate_identity(identity)
    home = home or copilot_home()
    config_path = home / "config.json"
    config_header, config = read_json_object(config_path, jsonc_header=True)
    records = inventory_records(config, config_path)
    matching = [
        record for record in records if inventory_identity(record) == identity
    ]
    settings_path = home / "settings.json"
    _, settings = read_json_object(settings_path)
    user_activation = activation_value(settings, settings_path, identity)
    return _StateDocuments(
        home=home,
        config_path=config_path,
        config_header=config_header,
        config=config,
        settings_path=settings_path,
        settings=settings,
        record=matching[0] if matching else None,
        user_activation=user_activation,
    )


def inspect_plugin_state(
    identity: str,
    home: Path | None = None,
    repo: Path | None = None,
) -> dict[str, Any]:
    """Inspect inventory, user activation, repository activation, and trust."""
    documents = _load_state(identity, home)
    repository_activation: bool | None = None
    repository_trusted: bool | None = None
    if repo is not None:
        repo = repo.expanduser().resolve()
        repo_settings_path = repo / ".github" / "copilot" / "settings.json"
        _, repo_settings = read_json_object(repo_settings_path)
        repository_activation = activation_value(
            repo_settings,
            repo_settings_path,
            identity,
        )
        trusted_folders = documents.config.get("trustedFolders", [])
        if not isinstance(trusted_folders, list) or not all(
            isinstance(item, str) for item in trusted_folders
        ):
            raise PluginStateError(
                f"{documents.config_path}: trustedFolders must be an array of paths"
            )
        repo_key = os.path.normcase(os.path.normpath(str(repo)))
        repository_trusted = any(
            os.path.normcase(
                os.path.normpath(str(Path(item).expanduser().resolve()))
            )
            == repo_key
            for item in trusted_folders
        )

    record = documents.record
    return {
        "identity": identity,
        "installed": record is not None,
        "inventoryEnabled": record.get("enabled") if record else None,
        "userActivation": (
            "absent"
            if documents.user_activation is None
            else str(documents.user_activation).lower()
        ),
        "repositoryActivation": (
            None
            if repo is None
            else "absent"
            if repository_activation is None
            else str(repository_activation).lower()
        ),
        "repositoryTrusted": repository_trusted,
        "installedButNotUserEnabled": (
            record is not None and documents.user_activation is not True
        ),
    }


def remove_user_activation(
    identity: str,
    home: Path | None = None,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Preview or remove user activation without uninstalling inventory."""
    documents = _load_state(identity, home)
    if documents.record is None:
        raise PluginStateError(
            f"{identity} is not present in installed inventory; refusing mutation"
        )
    state = inspect_plugin_state(identity, documents.home)
    settings, removed = remove_activation_entries(
        documents.settings,
        [identity],
        path=documents.settings_path,
    )
    changes = [
        f"remove enabledPlugins.{removed_identity}"
        for removed_identity in removed
    ]
    inventory_changed = documents.record.get("enabled") is not False
    if inventory_changed:
        changes.append(f"set installedPlugins[{identity}].enabled=false")
        if apply:
            documents.record["enabled"] = False
    if apply and changes:
        if removed:
            write_json_object_atomic(documents.settings_path, settings)
        if inventory_changed:
            write_json_object_atomic(
                documents.config_path,
                documents.config,
                documents.config_header,
            )
    return {
        **state,
        "mode": "apply" if apply else "dry-run",
        "changes": changes,
        "changed": bool(changes),
    }


def capture(identity: str, home: Path | None = None) -> ActivationSnapshot:
    """Capture exact user activation plus the inventory activation flag."""
    documents = _load_state(identity, home)
    enabled = documents.settings.get("enabledPlugins")
    enabled_existed = "enabledPlugins" in documents.settings
    user_existed = isinstance(enabled, dict) and identity in enabled
    return ActivationSnapshot(
        identity=identity,
        settings_existed=documents.settings_path.exists(),
        enabled_plugins_existed=enabled_existed,
        user_value_existed=user_existed,
        user_value=documents.user_activation,
        inventory_enabled=(
            documents.record.get("enabled")
            if documents.record is not None
            else None
        ),
        inventory_record_existed=documents.record is not None,
    )


def restore(snapshot: ActivationSnapshot, home: Path | None = None) -> None:
    """Restore activation fields without rolling back refreshed inventory."""
    for attempt in range(2):
        try:
            _restore_once(snapshot, home)
            return
        except (OSError, PluginStateError) as error:
            cause = error.__cause__ if isinstance(error, PluginStateError) else error
            code = getattr(cause, "winerror", None)
            if attempt or code not in (32, 33):
                raise
            logging.getLogger(__name__).warning(
                "Activation restoration for %s hit Windows lock error %s; "
                "retrying the original snapshot once",
                snapshot.identity, code,
            )
            sleep(RESTORE_RETRY_DELAY_S)


def _restore_once(snapshot: ActivationSnapshot, home: Path | None) -> None:
    home = home or copilot_home()
    settings_path = home / "settings.json"
    _, settings = read_json_object(settings_path)
    activation_value(settings, settings_path, snapshot.identity)
    enabled = settings.get("enabledPlugins")
    if enabled is None and "enabledPlugins" not in settings:
        enabled = {}
    if not isinstance(enabled, dict):  # validated above; narrows the type
        raise PluginStateError(f"{settings_path}: enabledPlugins must be an object")
    if snapshot.user_value_existed:
        enabled[snapshot.identity] = snapshot.user_value
    else:
        enabled.pop(snapshot.identity, None)
    if enabled or snapshot.enabled_plugins_existed:
        settings["enabledPlugins"] = enabled
    else:
        settings.pop("enabledPlugins", None)
    if settings or snapshot.settings_existed:
        if not _object_unchanged(settings_path, settings):
            write_json_object_atomic(settings_path, settings)
    elif settings_path.exists():
        settings_path.unlink()

    config_path = home / "config.json"
    header, config = read_json_object(config_path, jsonc_header=True)
    for record in inventory_records(config, config_path):
        if inventory_identity(record) != snapshot.identity:
            continue
        new_enabled = (
            snapshot.inventory_enabled
            if snapshot.inventory_enabled is not None
            else bool(snapshot.user_value)
            if snapshot.user_value_existed
            else False
        )
        if record.get("enabled") == new_enabled:
            return
        record["enabled"] = new_enabled
        write_json_object_atomic(config_path, config, header)
        return


def run_install_preserving_activation(
    argv: Sequence[str],
    identity: str,
    *,
    home: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    **run_kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run an install command and restore activation on every exit path."""
    snapshot = capture(identity, home)
    try:
        return (runner if runner is not None else subprocess.run)(argv, **run_kwargs)
    finally:
        restore(snapshot, home)
