#!/usr/bin/env python3
"""Installation-cell provision, cutover, and local service ownership."""

from __future__ import annotations

import argparse
import base64
import copy
import ctypes
import errno
import hashlib
import importlib.util
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
import uuid
import venv
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

PLUGIN_ID = "agent-index"
REPO_ID = "copilot-extensions"
MANIFEST_SCHEMA = 4
LOCK_TIMEOUT_SECONDS = 120
SERVICE_HEALTH_TIMEOUT_SECONDS = 30
LOCK_TOKEN_ENV = "AGENT_INDEX_CELL_LOCK_TOKEN"
LOCK_ROOT_ENV = "AGENT_INDEX_CELL_LOCK_ROOT"
TRANSACTION_PATH_ENV = "AGENT_INDEX_CELL_TRANSACTION"
TRANSACTION_TOKEN_ENV = "AGENT_INDEX_CELL_TRANSACTION_TOKEN"
TRANSACTION_ID_ENV = "AGENT_INDEX_CELL_TRANSACTION_ID"
TRANSACTION_SCHEMA = "copilot-extensions.agent-index.selection-transaction"
TRANSACTION_FILE = "selection-transaction.json"
TRANSACTION_RECEIPT_FILE = "selection-receipt.json"
RUNTIME_PROFILE_SCHEMA = "copilot-extensions.agent-index.runtime-profile"
RUNTIME_PROFILE_FILE = ".agent-index-runtime-profile.json"
RUNTIME_VERSION_ENV = "AGENT_INDEX_RUNTIME_VERSION"
INSTANCE_SCHEMA = "copilot-extensions.agent-index.service-instance"
ENSURE_WORKER_SCHEMA = "copilot-extensions.agent-index.service-ensure-worker"
ENSURE_WORKER_COMPLETION_SCHEMA = (
    "copilot-extensions.agent-index.service-ensure-completion"
)
CELL_START_TOKEN_ENV = "AGENT_INDEX_CELL_START_TOKEN"
RESERVED_CRASH_EXIT_CODES = frozenset({86, 87, 88, 89})
SERVICE_MUTATING_COMMANDS = {
    "start",
    "serve",
    "__cell-start",
    "deploy",
    "setup",
}


class CellError(RuntimeError):
    """A fail-closed installation-cell transaction error."""


class CellProcessExit(CellError):
    """A child lifecycle command whose exact exit status must cross the coordinator."""

    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class CellProfileMismatch(CellError):
    """A valid immutable slot was built for another dependency profile."""


class CellGovernanceBlocked(CellError):
    """Governance changed before a service cutover could commit."""

    def __init__(self, message: str, governance: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.governance = governance or {}


def _absolute_path(value: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(value))))


def _path_key(path: Path) -> str:
    return os.path.normcase(os.fspath(_absolute_path(path)))


def _paths_equal(left: Path | str, right: Path | str) -> bool:
    return _path_key(Path(left)) == _path_key(Path(right))


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _is_reparse(path: Path) -> bool:
    try:
        return bool(path.lstat().st_file_attributes & 0x400)
    except (AttributeError, OSError):
        return path.is_symlink()


def _assert_not_reparse(path: Path, label: str) -> None:
    if _lexists(path) and _is_reparse(path):
        raise CellError(f"{label} may not be a link or reparse point")


def _assert_regular_file(path: Path, label: str) -> None:
    _assert_not_reparse(path, label)
    if not path.is_file():
        raise CellError(f"{label} must be an ordinary file")


def _assert_directory(path: Path, label: str) -> None:
    _assert_not_reparse(path, label)
    if not path.is_dir():
        raise CellError(f"{label} must be an ordinary directory")


def _assert_path_chain(path: Path, boundary: Path, label: str) -> None:
    candidate = _absolute_path(path)
    root = _absolute_path(boundary)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise CellError(f"{label} escapes its installation") from exc
    _assert_directory(root, "installation root")
    current = root
    for part in relative.parts:
        current = current / part
        _assert_not_reparse(current, label)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _plugin_version(payload_root: Path) -> str:
    _assert_regular_file(payload_root / "pyproject.toml", "plugin pyproject")
    match = re.search(
        r'(?m)^\s*version\s*=\s*"([^"]+)"\s*$',
        (payload_root / "pyproject.toml").read_text(encoding="utf-8"),
    )
    if not match:
        raise CellError("cannot determine plugin version from pyproject.toml")
    return match.group(1)


def _durable_home(context: Path, explicit: str | None) -> Path:
    if explicit:
        return _absolute_path(explicit)
    try:
        return _absolute_path(context).parents[4]
    except IndexError as exc:
        raise CellError("context path is not inside an installation cell") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CellError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _read_json(path: Path) -> dict[str, Any]:
    _assert_regular_file(path, f"JSON document {path}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise CellError(f"malformed JSON at {path}") from exc
    if not isinstance(value, dict):
        raise CellError(f"JSON document is not an object: {path}")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_directory(path.parent, f"parent directory for {path.name}")
    _assert_not_reparse(path, path.name)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if os.name != "nt":
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
    os.replace(temporary, path)


def _atomic_bytes(path: Path, value: bytes, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_directory(path.parent, f"parent directory for {path.name}")
    _assert_not_reparse(path, path.name)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(value)
    if mode is not None and os.name != "nt":
        temporary.chmod(mode)
    os.replace(temporary, path)


def _isolated_environment(
    environment: dict[str, str] | None = None,
) -> dict[str, str]:
    value = dict(os.environ if environment is None else environment)
    value.pop("PYTHONPATH", None)
    value.pop("PYTHONHOME", None)
    value["PYTHONUTF8"] = "1"
    value["PYTHONDONTWRITEBYTECODE"] = "1"
    return value


def _context_runner(payload_root: Path) -> Path:
    payload_root = _absolute_path(payload_root)
    _assert_directory(payload_root, "management payload root")
    runner = (
        payload_root
        / "scripts"
        / "installation-context"
        / "installation_context.py"
    )
    _assert_path_chain(runner, payload_root, "installation-context runner")
    _assert_regular_file(runner, "installation-context runner")
    return runner


def _run_context(
    payload_root: Path,
    action: str,
    *arguments: str,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-I",
        "-X",
        "utf8",
        str(_context_runner(payload_root)),
        action,
        *arguments,
    ]
    result = subprocess.run(
        command,
        cwd=payload_root,
        env=_isolated_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise CellError(
            f"installation-context {action} failed"
            + (f": {detail}" if detail else "")
        )
    try:
        value = json.loads(result.stdout, object_pairs_hook=_strict_object)
    except ValueError as exc:
        raise CellError(
            f"installation-context {action} returned malformed JSON"
        ) from exc
    if not isinstance(value, dict):
        raise CellError(
            f"installation-context {action} returned a non-object result"
        )
    return value


def _context_arguments(
    context: Path,
    marketplace_id: str,
    payload_root: Path,
    version: str,
    durable_home: Path,
    *,
    snapshot_id: str | None = None,
    runtime_version: str | None = None,
) -> list[str]:
    snapshot_id = snapshot_id or version
    runtime_version = runtime_version or version
    return [
        "--context",
        str(context),
        "--expected-marketplace-id",
        marketplace_id,
        "--expected-plugin-id",
        PLUGIN_ID,
        "--expected-payload-root",
        str(payload_root),
        "--expected-payload-version",
        version,
        "--snapshot-id",
        snapshot_id,
        "--runtime-version",
        runtime_version,
        "--durable-home",
        str(durable_home),
    ]


def _validate_context(
    payload_root: Path,
    context: Path,
    marketplace_id: str,
    durable_home: Path,
    *,
    expected_payload_root: Path | None = None,
) -> dict[str, Any]:
    arguments = [
        "--context",
        str(context),
        "--expected-marketplace-id",
        marketplace_id,
        "--expected-plugin-id",
        PLUGIN_ID,
        "--durable-home",
        str(durable_home),
    ]
    if expected_payload_root is not None:
        arguments.extend(["--expected-payload-root", str(expected_payload_root)])
    value = _run_context(payload_root, "validate", *arguments)
    required = (
        "pluginRoot",
        "versionsRoot",
        "snapshotsRoot",
        "stateRoot",
        "runRoot",
        "logsRoot",
        "cacheRoot",
        "namespaceGeneration",
        "generation",
    )
    if any(not value.get(key) for key in required):
        raise CellError("installation context is missing required roots or generations")
    plugin_root = _absolute_path(str(value["pluginRoot"]))
    _assert_directory(plugin_root, "plugin installation root")
    for key in (
        "versionsRoot",
        "snapshotsRoot",
        "stateRoot",
        "runRoot",
        "logsRoot",
        "cacheRoot",
    ):
        root = _absolute_path(str(value[key]))
        _assert_path_chain(root, plugin_root, f"{key} path")
        _assert_not_reparse(root, f"{key} path")
    return value


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return ctypes.get_last_error() == 5
        exit_code = ctypes.c_uint32()
        try:
            if not ctypes.windll.kernel32.GetExitCodeProcess(
                handle,
                ctypes.byref(exit_code),
            ):
                return True
            return int(exit_code.value) == 259
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        tail = raw.rsplit(")", 1)[1].split()
        if tail and tail[0] == "Z":
            return False
    except (IndexError, OSError, UnicodeError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_birth_identity(pid: int) -> str | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        class FileTime(ctypes.Structure):
            _fields_ = [
                ("low", ctypes.c_uint32),
                ("high", ctypes.c_uint32),
            ]

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return None
        creation = FileTime()
        exit_time = FileTime()
        kernel = FileTime()
        user = FileTime()
        try:
            if not ctypes.windll.kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            value = (int(creation.high) << 32) | int(creation.low)
            return f"windows-filetime:{value}"
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        raw = proc_stat.read_text(encoding="ascii")
        tail = raw.rsplit(")", 1)[1].split()
        if tail and tail[0] == "Z":
            return None
        return f"proc-start:{tail[19]}"
    except (IndexError, OSError, UnicodeError):
        pass
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    started = result.stdout.strip()
    return f"ps-start:{started}" if result.returncode == 0 and started else None


def _lock_owner_observation(
    owner: Path,
) -> tuple[int | None, str | None, bytes | None]:
    try:
        raw = owner.read_bytes()
    except OSError:
        return None, None, None
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeError, ValueError):
        return None, None, raw
    if not isinstance(value, dict):
        return None, None, raw
    pid = value.get("pid")
    token = value.get("token")
    if type(pid) is not int or not isinstance(token, str) or not token:
        return None, None, raw
    return pid, token, raw


def _live_lock_owner_matches(plugin_root: Path, token: str) -> bool:
    owner = plugin_root / ".payload-provision.lock.d" / "owner.json"
    try:
        incumbent = _read_json(owner)
    except CellError:
        return False
    pid = incumbent.get("pid")
    return (
        incumbent.get("schema")
        == "copilot-extensions.agent-index.cell-lock"
        and incumbent.get("version") == 1
        and incumbent.get("token") == token
        and type(pid) is int
        and _pid_alive(pid)
    )


def _restore_moved_lock(lock: Path, tombstone: Path) -> bool:
    try:
        _rename_directory_no_replace(tombstone, lock)
    except OSError:
        return False
    return True


def _rename_directory_no_replace(source: Path, destination: Path) -> None:
    source = _absolute_path(source)
    destination = _absolute_path(destination)
    if os.name == "nt":
        os.rename(source, destination)
        return

    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    result: int
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        renameat2 = libc.renameat2
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            -100,
            source_bytes,
            -100,
            destination_bytes,
            1,
        )
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        renamex_np = libc.renamex_np
        renamex_np.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(source_bytes, destination_bytes, 0x00000004)
    else:
        raise OSError(
            errno.ENOTSUP,
            "atomic no-replace directory rename is unavailable",
            os.fspath(destination),
        )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(
            error,
            os.strerror(error),
            os.fspath(destination),
        )


def _publish_owned_lock(lock: Path, receipt: dict[str, Any]) -> None:
    stage = lock.with_name(
        f".lock-stage-{os.getpid()}-{uuid.uuid4().hex[:8]}.d"
    )
    stage.mkdir(mode=0o700)
    try:
        _atomic_json(stage / "owner.json", receipt)
        _rename_directory_no_replace(stage, lock)
    except BaseException:
        if _lexists(stage):
            shutil.rmtree(stage, ignore_errors=True)
        raise


@contextmanager
def _installation_lock(
    plugin_root: Path,
    *,
    reentry_token: str | None = None,
) -> Iterator[str]:
    plugin_root = _absolute_path(plugin_root)
    _assert_directory(plugin_root, "plugin installation root")
    lock = plugin_root / ".payload-provision.lock.d"
    owner = lock / "owner.json"
    if reentry_token is not None:
        if not _live_lock_owner_matches(plugin_root, reentry_token):
            raise CellError("installation lock reentry owner is not live")
        yield reentry_token
        return
    token = uuid.uuid4().hex
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        _assert_not_reparse(lock, "installation lock")
        try:
            _publish_owned_lock(
                lock,
                {
                    "schema": "copilot-extensions.agent-index.cell-lock",
                    "version": 1,
                    "pid": os.getpid(),
                    "token": token,
                    "createdAt": _utc_now(),
                },
            )
            break
        except FileExistsError:
            incumbent_pid, incumbent_token, observed_raw = (
                _lock_owner_observation(owner)
            )
            owner_valid = (
                type(incumbent_pid) is int
                and isinstance(incumbent_token, str)
                and bool(incumbent_token)
                and observed_raw is not None
            )
            if (
                owner_valid
                and _pid_alive(incumbent_pid)
            ):
                if time.monotonic() >= deadline:
                    raise CellError("timed out waiting for the installation lock")
                time.sleep(0.2)
                continue
            if not owner_valid:
                if time.monotonic() >= deadline:
                    raise CellError(
                        "installation lock owner cannot be proven stale"
                    )
                time.sleep(0.2)
                continue
            tombstone = plugin_root / (
                f".payload-provision.lock.stale.{os.getpid()}.{uuid.uuid4().hex}.d"
            )
            try:
                os.rename(lock, tombstone)
            except FileNotFoundError:
                continue
            except FileExistsError:
                continue
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise CellError("stale installation lock cannot be reclaimed") from exc
                time.sleep(0.2)
                continue
            moved_pid, moved_token, moved_raw = _lock_owner_observation(
                tombstone / "owner.json"
            )
            same_owner = (
                moved_pid == incumbent_pid
                and moved_token == incumbent_token
                and moved_raw == observed_raw
            )
            moved_owner_live = (
                type(moved_pid) is int
                and isinstance(moved_token, str)
                and bool(moved_token)
                and _pid_alive(moved_pid)
            )
            if not same_owner or moved_owner_live:
                restored = _restore_moved_lock(lock, tombstone)
                if not restored and not _lexists(lock):
                    if time.monotonic() >= deadline:
                        raise CellError(
                            "installation lock owner changed during stale reclamation"
                        )
                    time.sleep(0.2)
                continue
            try:
                shutil.rmtree(tombstone)
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise CellError("stale installation lock cannot be reclaimed") from exc
                time.sleep(0.2)
    try:
        yield token
    finally:
        for attempt in range(20):
            try:
                incumbent = _read_json(owner)
                if (
                    incumbent.get("pid") != os.getpid()
                    or incumbent.get("token") != token
                ):
                    break
                tombstone = plugin_root / (
                    f".payload-provision.lock.release.{os.getpid()}."
                    f"{uuid.uuid4().hex}.d"
                )
                os.rename(lock, tombstone)
                moved_pid, moved_token, _moved_raw = _lock_owner_observation(
                    tombstone / "owner.json"
                )
                if moved_pid != os.getpid() or moved_token != token:
                    _restore_moved_lock(lock, tombstone)
                    break
                shutil.rmtree(tombstone)
                break
            except (CellError, OSError):
                if not _lexists(lock):
                    break
                if attempt == 19:
                    raise CellError("installation lock could not be released")
                time.sleep(0.05)


def _copy_payload(source: Path, destination: Path) -> None:
    for root, directories, files in os.walk(source):
        root_path = Path(root)
        relative = root_path.relative_to(source)
        for name in directories + files:
            candidate = root_path / name
            if candidate.is_symlink() or _is_reparse(candidate):
                raise CellError(
                    f"payload snapshot contains a link or reparse point: "
                    f"{(relative / name).as_posix()}"
                )
    shutil.copytree(source, destination, dirs_exist_ok=True)


def _snapshot_owner(marketplace_id: str, version: str) -> str:
    return "\n".join(
        (
            "copilot-extensions.agent-index.snapshot-publish:v1",
            f"marketplaceId={marketplace_id}",
            f"pluginId={PLUGIN_ID}",
            f"snapshotId={version}",
            "",
        )
    )


def _ensure_snapshot(
    payload_root: Path,
    context: Path,
    marketplace_id: str,
    version: str,
    durable_home: Path,
    validated: dict[str, Any],
) -> Path:
    snapshots_root = Path(str(validated["snapshotsRoot"]))
    snapshot_root = snapshots_root / version
    provenance = snapshot_root / "snapshot-provenance.json"
    owner_name = ".agent-index-snapshot-publish-owner"
    owner = snapshot_root / owner_name
    expected_owner = _snapshot_owner(marketplace_id, version)
    if snapshot_root.exists() or snapshot_root.is_symlink():
        owned = (
            snapshot_root.is_dir()
            and not _is_reparse(snapshot_root)
            and owner.is_file()
            and not _is_reparse(owner)
            and owner.read_text(encoding="utf-8").replace("\r\n", "\n")
            == expected_owner
        )
        if owned and not provenance.exists():
            shutil.rmtree(snapshot_root)
        else:
            _run_context(
                payload_root,
                "snapshot-validate",
                "--context",
                str(context),
                "--expected-marketplace-id",
                marketplace_id,
                "--expected-plugin-id",
                PLUGIN_ID,
                "--snapshot-id",
                version,
                "--durable-home",
                str(durable_home),
            )
            if owned:
                owner.unlink()
            return snapshot_root

    if (payload_root / owner_name).exists():
        raise CellError("payload uses the reserved snapshot ownership marker")
    snapshots_root.mkdir(parents=True, exist_ok=True)
    stage = snapshots_root / (
        f".agent-index-snapshot-{version}-{os.getpid()}-{uuid.uuid4().hex}"
    )
    stage.mkdir()
    (stage / owner_name).write_text(expected_owner, encoding="utf-8", newline="\n")
    try:
        _copy_payload(payload_root, stage)
        if snapshot_root.exists() or snapshot_root.is_symlink():
            raise CellError("cell snapshot appeared during publication")
        os.rename(stage, snapshot_root)
        _run_context(
            payload_root,
            "snapshot-stamp",
            "--context",
            str(context),
            "--expected-marketplace-id",
            marketplace_id,
            "--expected-plugin-id",
            PLUGIN_ID,
            "--expected-namespace-generation",
            str(validated["namespaceGeneration"]),
            "--expected-install-generation",
            str(validated["generation"]),
            "--snapshot-id",
            version,
            "--durable-home",
            str(durable_home),
        )
        _run_context(
            payload_root,
            "snapshot-validate",
            "--context",
            str(context),
            "--expected-marketplace-id",
            marketplace_id,
            "--expected-plugin-id",
            PLUGIN_ID,
            "--snapshot-id",
            version,
            "--durable-home",
            str(durable_home),
        )
        owner.unlink()
        return snapshot_root
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        if snapshot_root.is_dir() and owner.is_file() and not provenance.exists():
            try:
                if owner.read_text(encoding="utf-8").replace("\r\n", "\n") == expected_owner:
                    shutil.rmtree(snapshot_root)
            except OSError:
                pass
        raise


def _venv_python(slot: Path) -> Path:
    if os.name == "nt":
        return slot / "Scripts" / "python.exe"
    return slot / "bin" / "python"


def _runtime_profile(role: str | None) -> tuple[str, list[str]]:
    profile = role if role in {"host", "client"} else "unconfigured"
    return profile, (["store"] if profile == "host" else [])


def _profile_runtime_version(payload_version: str, role: str | None) -> str:
    profile, _extras = _runtime_profile(role)
    return f"{payload_version}+{profile}"


def _runtime_profile_value(
    slot: Path,
    marketplace_id: str,
    runtime_version: str,
    role: str | None,
) -> dict[str, Any]:
    profile, extras = _runtime_profile(role)
    return {
        "schema": RUNTIME_PROFILE_SCHEMA,
        "version": 1,
        "marketplaceId": marketplace_id,
        "pluginId": PLUGIN_ID,
        "runtime": {
            "version": runtime_version,
            "root": _normalized_path(slot),
        },
        "profile": {
            "role": profile,
            "extras": extras,
        },
    }


def _write_runtime_profile(
    slot: Path,
    marketplace_id: str,
    runtime_version: str,
    role: str | None,
) -> dict[str, Any]:
    path = slot / RUNTIME_PROFILE_FILE
    expected = _runtime_profile_value(
        slot,
        marketplace_id,
        runtime_version,
        role,
    )
    if _lexists(path):
        actual = _read_json(path)
        if actual != expected:
            raise CellError(
                "runtime dependency profile conflicts with the immutable slot"
            )
        return actual
    _atomic_json(path, expected)
    return expected


def _validate_runtime_profile(
    slot: Path,
    marketplace_id: str,
    runtime_version: str,
    role: str | None,
    *,
    enforce_role: bool = True,
) -> dict[str, Any]:
    path = slot / RUNTIME_PROFILE_FILE
    actual = _read_json(path)
    runtime = actual.get("runtime")
    profile = actual.get("profile")
    valid = (
        set(actual)
        == {
            "schema",
            "version",
            "marketplaceId",
            "pluginId",
            "runtime",
            "profile",
        }
        and actual.get("schema") == RUNTIME_PROFILE_SCHEMA
        and actual.get("version") == 1
        and actual.get("marketplaceId") == marketplace_id
        and actual.get("pluginId") == PLUGIN_ID
        and isinstance(runtime, dict)
        and set(runtime) == {"version", "root"}
        and runtime.get("version") == runtime_version
        and isinstance(runtime.get("root"), str)
        and _paths_equal(str(runtime.get("root")), slot)
        and isinstance(profile, dict)
        and set(profile) == {"role", "extras"}
        and profile.get("role") in {"host", "client", "unconfigured"}
        and runtime_version.endswith(f"+{profile.get('role')}")
        and isinstance(profile.get("extras"), list)
        and all(isinstance(item, str) for item in profile["extras"])
        and profile["extras"]
        == (["store"] if profile.get("role") == "host" else [])
    )
    if not valid:
        raise CellError("runtime dependency profile receipt is invalid")
    expected_role, expected_extras = _runtime_profile(role)
    if enforce_role and (
        profile["role"] != expected_role or profile["extras"] != expected_extras
    ):
        raise CellProfileMismatch(
            "completed runtime slot dependency profile does not match "
            "the configured host role"
        )
    return actual


def _validate_slot_ownership_identity(
    slot: Path,
    marketplace_id: str,
    runtime_version: str,
) -> None:
    ownership = _read_json(slot / ".runtime-slot-ownership.json")
    runtime = ownership.get("runtime")
    if (
        ownership.get("schema")
        != "copilot-extensions.runtime-slot-ownership"
        or ownership.get("marketplaceId") != marketplace_id
        or ownership.get("pluginId") != PLUGIN_ID
        or not isinstance(runtime, dict)
        or runtime.get("version") != runtime_version
        or not isinstance(runtime.get("root"), str)
        or not _paths_equal(str(runtime.get("root")), slot)
    ):
        raise CellError("runtime slot ownership does not match its interpreter")


def _validate_runtime_interpreter_layout(
    interpreter: Path,
    slot: Path,
    marketplace_id: str,
    runtime_version: str,
    *,
    label: str,
) -> Path:
    expected = _venv_python(slot)
    if not _paths_equal(interpreter, expected):
        raise CellError(f"{label} is not the standard venv interpreter")
    _assert_path_chain(slot, slot.parent.parent, "runtime slot")
    _assert_directory(slot, "runtime slot")
    _validate_slot_ownership_identity(slot, marketplace_id, runtime_version)
    _assert_regular_file(slot / "pyvenv.cfg", "runtime pyvenv.cfg")
    _assert_path_chain(interpreter.parent, slot, f"{label} parent")
    _assert_directory(interpreter.parent, f"{label} parent")
    if _is_reparse(interpreter):
        if os.name == "nt" or not interpreter.is_symlink():
            raise CellError(
                f"{label} may only be the standard POSIX venv interpreter symlink"
            )
        try:
            resolved = interpreter.resolve(strict=True)
        except OSError as exc:
            raise CellError(f"{label} symlink target is unavailable") from exc
        if not stat.S_ISREG(resolved.stat().st_mode):
            raise CellError(f"{label} symlink target is not an executable file")
    else:
        _assert_regular_file(interpreter, label)
        resolved = interpreter.resolve(strict=True)
    if os.name != "nt" and not os.access(interpreter, os.X_OK):
        raise CellError(f"{label} is not executable")
    return resolved


def _run_checked(
    command: list[str],
    *,
    environment: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> None:
    result = subprocess.run(
        command,
        env=environment,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise CellError(
            f"command failed with exit {result.returncode}: "
            + " ".join(shlex.quote(part) for part in command)
        )


def _runtime_module_path(
    interpreter: Path,
    slot: Path,
    *,
    environment: dict[str, str] | None = None,
    label: str,
) -> Path:
    try:
        result = subprocess.run(
            [
                str(interpreter),
                "-I",
                "-X",
                "utf8",
                "-c",
                (
                    "from pathlib import Path; import agent_index; "
                    "print(Path(agent_index.__file__).resolve())"
                ),
            ],
            cwd=slot,
            env=_isolated_environment(environment),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise CellError(
            f"{label} is unusable; ownership-checked repair is required"
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise CellError(
            f"{label} is unusable; ownership-checked repair is required"
            + (f": {detail}" if detail else "")
        )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise CellError(
            f"{label} did not report its package origin; "
            "ownership-checked repair is required"
        )
    module_path = _absolute_path(lines[-1])
    try:
        module_path.relative_to(_absolute_path(slot))
    except ValueError as exc:
        raise CellError(
            f"{label} imported agent_index outside the selected runtime slot"
        ) from exc
    _assert_path_chain(module_path, slot, f"{label} package origin")
    _assert_regular_file(module_path, f"{label} package origin")
    return module_path


def _runtime_install_target(snapshot_root: Path, role: str | None) -> str:
    if role == "host":
        raise CellError(
            "host dependencies are dispatch-managed; namespaced host "
            "provisioning is unavailable"
        )
    return str(snapshot_root)


def _build_runtime(
    snapshot_root: Path,
    slot: Path,
    *,
    marketplace_id: str,
    runtime_version: str,
    role: str | None,
) -> Path:
    # Reject before even a smoke build can create an environment.
    _runtime_install_target(snapshot_root, role)
    _assert_directory(snapshot_root, "payload snapshot")
    _assert_path_chain(slot, slot.parent.parent, "runtime slot")
    _assert_not_reparse(slot, "runtime slot")
    if slot.exists():
        _assert_directory(slot, "runtime slot")
    profile_path = slot / RUNTIME_PROFILE_FILE
    if _lexists(profile_path):
        _validate_runtime_profile(
            slot,
            marketplace_id,
            runtime_version,
            role,
        )
    interpreter = _venv_python(slot)
    if _lexists(interpreter):
        _validate_runtime_interpreter_layout(
            interpreter,
            slot,
            marketplace_id,
            runtime_version,
            label="existing runtime interpreter",
        )
    if os.environ.get("AGENT_INDEX_CELL_BUILD_SMOKE") == "1":
        if not interpreter.exists():
            venv.EnvBuilder(
                with_pip=False,
                system_site_packages=True,
            ).create(slot)
        _validate_runtime_interpreter_layout(
            interpreter,
            slot,
            marketplace_id,
            runtime_version,
            label="smoke runtime interpreter",
        )
        site_probe = subprocess.run(
            [
                str(interpreter),
                "-I",
                "-X",
                "utf8",
                "-c",
                "import site; print(next(p for p in site.getsitepackages() "
                "if p.endswith(('site-packages','dist-packages'))))",
            ],
            cwd=slot,
            env=_isolated_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if site_probe.returncode != 0:
            raise CellError("smoke runtime could not resolve site-packages")
        site_packages = Path(site_probe.stdout.strip())
        for package, source in (
            ("agent_index", snapshot_root / "src" / "agent_index"),
            ("zdd", snapshot_root / "libs" / "zdd" / "src" / "zdd"),
            (
                "agent_procutil",
                snapshot_root
                / "libs"
                / "agent-procutil"
                / "src"
                / "agent_procutil",
            ),
        ):
            _assert_directory(source, f"smoke runtime {package} source")
            destination = site_packages / package
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(source, destination)
    elif not interpreter.exists():
        uv = shutil.which("uv")
        if uv:
            _run_checked(
                [uv, "venv", str(slot), "--allow-existing"],
                environment=_isolated_environment(),
                cwd=snapshot_root,
            )
        else:
            venv.EnvBuilder(with_pip=True).create(slot)

    if os.environ.get("AGENT_INDEX_CELL_BUILD_SMOKE") != "1":
        _validate_runtime_interpreter_layout(
            interpreter,
            slot,
            marketplace_id,
            runtime_version,
            label="runtime interpreter",
        )
        uv = shutil.which("uv")
        sources = (
            snapshot_root / "libs" / "zdd",
            snapshot_root / "libs" / "agent-procutil",
            _runtime_install_target(snapshot_root, role),
        )
        for source in sources:
            if uv:
                _run_checked(
                    [
                        uv,
                        "pip",
                        "install",
                        "--python",
                        str(interpreter),
                        str(source),
                        "--quiet",
                    ],
                    environment=_isolated_environment(),
                    cwd=snapshot_root,
                )
            else:
                _run_checked(
                    [
                        str(interpreter),
                        "-I",
                        "-X",
                        "utf8",
                        "-m",
                        "pip",
                        "install",
                        "--quiet",
                        str(source),
                    ],
                    environment=_isolated_environment(),
                    cwd=snapshot_root,
                )
    _validate_runtime_interpreter_layout(
        interpreter,
        slot,
        marketplace_id,
        runtime_version,
        label="runtime interpreter",
    )
    _runtime_module_path(
        interpreter,
        slot,
        label="new runtime slot",
    )
    return interpreter


def _load_installation_context(payload_root: Path):
    path = _context_runner(payload_root)
    module_name = (
        "agent_index_installation_context_"
        + hashlib.sha256(os.fsencode(path)).hexdigest()[:16]
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise CellError("installation-context Python module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    prior = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if prior is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = prior
        raise
    return module


def _write_build_receipt(
    payload_root: Path,
    snapshot_root: Path,
    slot: Path,
    runtime_version: str,
) -> None:
    module = _load_installation_context(payload_root)
    payload_hash = module._snapshot_content_sha256(snapshot_root)
    _atomic_json(
        slot / ".install-complete.json",
        {
            "version": runtime_version,
            "completed_at": _utc_now(),
            "pid": os.getpid(),
            "payload_hash": payload_hash,
        },
    )


def _source_kind(path: Path) -> str:
    normalized = path.as_posix()
    return "marketplace" if "/.copilot/installed-plugins/" in normalized else "local"


def _git_source(path: Path) -> tuple[str | None, str | None, bool]:
    if _source_kind(path) != "local":
        return None, None, False
    repo = path.parent.parent
    try:
        commit = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            ).stdout
        )
        return commit, branch, dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown", "unknown", False


def _source_provenance(payload_root: Path, version: str) -> dict[str, Any]:
    commit, branch, dirty = _git_source(payload_root)
    return {
        "kind": _source_kind(payload_root),
        "path": _normalized_path(payload_root),
        "repo": REPO_ID,
        "plugin": PLUGIN_ID,
        "version": version,
        "commit": commit,
        "branch": branch,
        "dirty": dirty,
    }


def _normalized_path(path: Path) -> str:
    return _absolute_path(path).as_posix()


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _load_manifest(
    manifest_path: Path,
    plugin_root: Path,
    context: Path,
    marketplace_id: str,
) -> dict[str, Any] | None:
    if not _lexists(manifest_path):
        return None
    _assert_path_chain(manifest_path, plugin_root, "cell deploy manifest")
    _assert_regular_file(manifest_path, "cell deploy manifest")
    manifest = _read_json(manifest_path)
    source = manifest.get("source")
    runtime = manifest.get("runtime")
    selected = runtime.get("selectedBy") if isinstance(runtime, dict) else None
    installation = manifest.get("installation")
    valid = (
        type(manifest.get("schema_version")) is int
        and manifest["schema_version"] == MANIFEST_SCHEMA
        and manifest.get("service") == PLUGIN_ID
        and isinstance(source, dict)
        and source.get("repo") == REPO_ID
        and source.get("plugin") == PLUGIN_ID
        and all(
            isinstance(source.get(key), str) and source[key]
            for key in ("kind", "path", "version")
        )
        and os.path.isabs(source["path"])
        and type(source.get("dirty")) is bool
        and isinstance(runtime, dict)
        and runtime.get("kind") == "python"
        and all(
            isinstance(runtime.get(key), str) and runtime[key]
            for key in ("version", "path", "interpreter")
        )
        and isinstance(selected, dict)
        and all(
            isinstance(selected.get(key), str) and selected[key]
            for key in ("kind", "path", "version", "snapshotId")
        )
        and os.path.isabs(selected["path"])
        and isinstance(installation, dict)
        and installation.get("marketplaceId") == marketplace_id
        and installation.get("pluginId") == PLUGIN_ID
        and installation.get("installationId")
        == f"{marketplace_id}/{PLUGIN_ID}"
        and isinstance(installation.get("context"), str)
        and _paths_equal(installation.get("context", ""), context)
    )
    if not valid:
        raise CellError("cell deploy manifest identity or provenance is invalid")
    runtime_root = _absolute_path(plugin_root / "versions" / runtime["version"])
    interpreter = _absolute_path(_venv_python(runtime_root))
    if not _paths_equal(runtime["path"], runtime_root):
        raise CellError("cell deploy manifest runtime path escapes its installation")
    if not _paths_equal(runtime["interpreter"], interpreter):
        raise CellError(
            "cell deploy manifest interpreter escapes its installation"
        )
    _assert_path_chain(runtime_root, plugin_root, "runtime slot")
    _assert_directory(runtime_root, "runtime slot")
    _assert_path_chain(interpreter.parent, runtime_root, "runtime interpreter parent")
    _assert_directory(interpreter.parent, "runtime interpreter parent")
    for label, path_value in (
        ("reconciled payload", source["path"]),
        ("selected payload", selected["path"]),
    ):
        candidate = _absolute_path(path_value)
        if _lexists(candidate):
            _assert_not_reparse(candidate, label)
    return manifest


def _build_manifest(
    plugin_root: Path,
    context: Path,
    marketplace_id: str,
    source_payload: Path,
    source_version: str,
    selected_payload: Path,
    selected_payload_version: str,
    selected_snapshot_id: str,
    runtime_version: str,
    *,
    preserve_source: bool,
) -> dict[str, Any]:
    manifest_path = plugin_root / "deploy-manifest.json"
    existing = (
        _load_manifest(manifest_path, plugin_root, context, marketplace_id)
        if preserve_source
        else None
    )
    if existing is None:
        source = _source_provenance(source_payload, source_version)
    else:
        source = dict(existing["source"])
    runtime_root = plugin_root / "versions" / runtime_version
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "service": PLUGIN_ID,
        "deployed_at": _utc_now(),
        "deployed_by": f"{os.environ.get('COMPUTERNAME') or platform.node()}-"
        f"{'windows' if os.name == 'nt' else 'posix'}",
        "source": source,
        "runtime": {
            "kind": "python",
            "version": runtime_version,
            "path": _normalized_path(runtime_root),
            "interpreter": _normalized_path(_venv_python(runtime_root)),
            "selectedBy": {
                "kind": _source_kind(selected_payload),
                "path": _normalized_path(selected_payload),
                "version": selected_payload_version,
                "snapshotId": selected_snapshot_id,
            },
        },
        "installation": {
            "marketplaceId": marketplace_id,
            "pluginId": PLUGIN_ID,
            "installationId": f"{marketplace_id}/{PLUGIN_ID}",
            "context": _normalized_path(context),
        },
    }
    return manifest


def _write_manifest(
    plugin_root: Path,
    context: Path,
    marketplace_id: str,
    source_payload: Path,
    source_version: str,
    selected_payload: Path,
    selected_payload_version: str,
    selected_snapshot_id: str,
    runtime_version: str,
    *,
    preserve_source: bool,
) -> dict[str, Any]:
    manifest_path = plugin_root / "deploy-manifest.json"
    manifest = _build_manifest(
        plugin_root,
        context,
        marketplace_id,
        source_payload,
        source_version,
        selected_payload,
        selected_payload_version,
        selected_snapshot_id,
        runtime_version,
        preserve_source=preserve_source,
    )
    marker = plugin_root / "current-version"
    if (
        not marker.is_file()
        or _is_reparse(marker)
        or marker.read_text(encoding="utf-8").strip() != runtime_version
    ):
        raise CellError("current-version changed before manifest publication")
    _atomic_json(manifest_path, manifest)
    return manifest


def _marker_version(plugin_root: Path) -> str | None:
    marker = plugin_root / "current-version"
    _assert_not_reparse(marker, "current-version marker")
    if not marker.is_file():
        return None
    value = marker.read_text(encoding="utf-8").strip()
    if not value:
        raise CellError("current-version marker is empty")
    return value


def _selection_transaction_path(plugin_root: Path) -> Path:
    return plugin_root / TRANSACTION_FILE


def _valid_transaction_instance(
    value: Any,
    installation_id: str,
) -> bool:
    return (
        isinstance(value, dict)
        and value.get("schema") == INSTANCE_SCHEMA
        and value.get("version") == 1
        and value.get("installationId") == installation_id
        and isinstance(value.get("runtimeVersion"), str)
        and bool(value["runtimeVersion"])
        and type(value.get("pid")) is int
        and isinstance(value.get("instanceToken"), str)
        and bool(value["instanceToken"])
        and isinstance(value.get("host"), str)
        and bool(value["host"])
        and type(value.get("port")) is int
    )


def _load_selection_transaction(
    plugin_root: Path,
    context: Path,
    marketplace_id: str,
) -> dict[str, Any] | None:
    path = _selection_transaction_path(plugin_root)
    if not _lexists(path):
        return None
    _assert_path_chain(path, plugin_root, "selection transaction")
    transaction = _read_json(path)
    management = transaction.get("management")
    prior = transaction.get("prior")
    target = transaction.get("target")
    target_manifest = target.get("manifest") if isinstance(target, dict) else None
    target_runtime = (
        target_manifest.get("runtime")
        if isinstance(target_manifest, dict)
        else None
    )
    target_selected = (
        target_runtime.get("selectedBy")
        if isinstance(target_runtime, dict)
        else None
    )
    target_installation = (
        target_manifest.get("installation")
        if isinstance(target_manifest, dict)
        else None
    )
    prior_manifest = prior.get("manifest") if isinstance(prior, dict) else None
    prior_instances = prior.get("instances") if isinstance(prior, dict) else None
    prior_active = prior.get("activeService") if isinstance(prior, dict) else None
    installation_id = f"{marketplace_id}/{PLUGIN_ID}"
    if (
        transaction.get("schema") != TRANSACTION_SCHEMA
        or transaction.get("version") != 1
        or transaction.get("marketplaceId") != marketplace_id
        or transaction.get("pluginId") != PLUGIN_ID
        or transaction.get("installationId") != installation_id
        or not isinstance(transaction.get("id"), str)
        or not transaction["id"]
        or not isinstance(transaction.get("token"), str)
        or not transaction["token"]
        or transaction.get("state")
        not in {"prepared", "marker-published", "manifest-published", "reconciling"}
        or not isinstance(transaction.get("namespaceGeneration"), str)
        or not isinstance(transaction.get("installGeneration"), str)
        or not isinstance(transaction.get("context"), str)
        or not _paths_equal(transaction["context"], context)
        or not isinstance(management, dict)
        or not isinstance(management.get("path"), str)
        or not isinstance(management.get("version"), str)
        or not isinstance(prior, dict)
        or (
            prior.get("runtimeVersion") is not None
            and not isinstance(prior.get("runtimeVersion"), str)
        )
        or (
            prior.get("manifest") is not None
            and not isinstance(prior.get("manifest"), dict)
        )
        or not isinstance(prior.get("artifacts"), list)
        or not isinstance(prior_instances, list)
        or any(
            not _valid_transaction_instance(record, installation_id)
            for record in prior_instances
        )
        or (
            prior_active is not None
            and (
                not _valid_transaction_instance(prior_active, installation_id)
                or type(prior_active.get("draining")) is not bool
                or not any(
                    record.get("pid") == prior_active.get("pid")
                    and record.get("instanceToken")
                    == prior_active.get("instanceToken")
                    for record in prior_instances
                )
            )
        )
        or not isinstance(target, dict)
        or not all(
            isinstance(target.get(key), str) and target[key]
            for key in (
                "payloadRoot",
                "payloadVersion",
                "snapshotId",
                "runtimeVersion",
            )
        )
        or not isinstance(target.get("manifest"), dict)
        or not isinstance(target_runtime, dict)
        or target_runtime.get("version") != target.get("runtimeVersion")
        or not isinstance(target_selected, dict)
        or target_selected.get("path") != target.get("payloadRoot")
        or target_selected.get("version") != target.get("payloadVersion")
        or target_selected.get("snapshotId") != target.get("snapshotId")
        or not isinstance(target_installation, dict)
        or target_installation.get("marketplaceId") != marketplace_id
        or target_installation.get("pluginId") != PLUGIN_ID
        or target_installation.get("installationId")
        != f"{marketplace_id}/{PLUGIN_ID}"
        or not _paths_equal(target_installation.get("context", ""), context)
        or (
            isinstance(prior_manifest, dict)
            and (
                not isinstance(prior_manifest.get("runtime"), dict)
                or prior_manifest["runtime"].get("version")
                != prior.get("runtimeVersion")
                or not isinstance(prior_manifest.get("installation"), dict)
                or prior_manifest["installation"].get("marketplaceId")
                != marketplace_id
                or prior_manifest["installation"].get("pluginId") != PLUGIN_ID
                or not _paths_equal(
                    prior_manifest["installation"].get("context", ""),
                    context,
                )
            )
        )
    ):
        raise CellError("selection transaction identity or shape is invalid")
    return transaction


def _write_selection_transaction(
    plugin_root: Path,
    transaction: dict[str, Any],
    *,
    state: str | None = None,
) -> dict[str, Any]:
    updated = dict(transaction)
    if state is not None:
        updated["state"] = state
    updated["updatedAt"] = _utc_now()
    _atomic_json(_selection_transaction_path(plugin_root), updated)
    return updated


def _transaction_artifact_paths(plugin_root: Path) -> tuple[Path, ...]:
    launcher_root = plugin_root / "launchers"
    if os.name == "nt":
        service_launcher = launcher_root / "agent-index-service.ps1"
        command_launcher = launcher_root / "agent-index.ps1"
    else:
        service_launcher = launcher_root / "agent-index-service"
        command_launcher = launcher_root / "agent-index"
    return (
        service_launcher,
        command_launcher,
        plugin_root / "run" / "service-identity.json",
        plugin_root / "run" / "endpoint.json",
        plugin_root / "running-version.json",
    )


def _capture_transaction_artifacts(plugin_root: Path) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    for path in _transaction_artifact_paths(plugin_root):
        _assert_path_chain(path, plugin_root, "selection artifact")
        relative = path.relative_to(plugin_root).as_posix()
        if not _lexists(path):
            captured.append({"path": relative, "present": False})
            continue
        _assert_regular_file(path, "selection artifact")
        captured.append(
            {
                "path": relative,
                "present": True,
                "content": base64.b64encode(path.read_bytes()).decode("ascii"),
                "mode": path.stat().st_mode & 0o777,
            }
        )
    return captured


def _restore_transaction_artifacts(
    plugin_root: Path,
    captured: Any,
) -> None:
    expected = {
        path.relative_to(plugin_root).as_posix(): path
        for path in _transaction_artifact_paths(plugin_root)
    }
    if not isinstance(captured, list):
        raise CellError("selection transaction has no prior launcher evidence")
    observed: set[str] = set()
    for item in captured:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or item["path"] not in expected
            or item["path"] in observed
            or type(item.get("present")) is not bool
        ):
            raise CellError("selection transaction launcher evidence is malformed")
        observed.add(item["path"])
        path = expected[item["path"]]
        if item["present"]:
            content = item.get("content")
            mode = item.get("mode")
            if not isinstance(content, str) or type(mode) is not int:
                raise CellError(
                    "selection transaction launcher content is malformed"
                )
            try:
                raw = base64.b64decode(content, validate=True)
            except ValueError as exc:
                raise CellError(
                    "selection transaction launcher content is malformed"
                ) from exc
            _atomic_bytes(path, raw, mode=mode)
        elif _lexists(path):
            _assert_regular_file(path, "selection artifact")
            path.unlink()
    if observed != set(expected):
        raise CellError("selection transaction launcher evidence is incomplete")


def _capture_prior_instances(
    validated: dict[str, Any],
    installation_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    instances = [
        copy.deepcopy(record)
        for _path, record in _instance_records(validated)
        if record["installationId"] == installation_id
    ]
    active = _active_service(validated, installation_id)
    active_record = None
    if active is not None:
        for record in instances:
            if (
                int(record["pid"]) == int(active["pid"])
                and record["runtimeVersion"] == active["version"]
                and int(record["port"]) == int(active["port"])
                and record["instanceToken"] == active["instanceToken"]
            ):
                active_record = copy.deepcopy(record)
                active_record["draining"] = bool(active["draining"])
                break
        if active_record is None:
            raise CellError(
                "active service has no exact instance receipt before selection"
            )
    return instances, active_record


def _prepare_selection_transaction(
    plugin_root: Path,
    context: Path,
    marketplace_id: str,
    management_payload_root: Path,
    management_version: str,
    target_payload_root: Path,
    target_payload_version: str,
    target_snapshot_id: str,
    target_runtime_version: str,
    namespace_generation: str,
    install_generation: str,
    validated: dict[str, Any],
    *,
    preserve_source: bool,
) -> dict[str, Any]:
    if _load_selection_transaction(plugin_root, context, marketplace_id) is not None:
        raise CellError("an installation selection transaction is already pending")
    prior_manifest = _load_manifest(
        plugin_root / "deploy-manifest.json",
        plugin_root,
        context,
        marketplace_id,
    )
    prior_version = _marker_version(plugin_root)
    if prior_manifest is None and prior_version is not None:
        raise CellError("current-version exists without a deploy manifest")
    if (
        prior_manifest is not None
        and prior_manifest["runtime"]["version"] != prior_version
    ):
        raise CellError("deploy manifest does not match current-version")
    target_manifest = _build_manifest(
        plugin_root,
        context,
        marketplace_id,
        management_payload_root,
        management_version,
        target_payload_root,
        target_payload_version,
        target_snapshot_id,
        target_runtime_version,
        preserve_source=preserve_source,
    )
    prior_instances, prior_active = _capture_prior_instances(
        validated,
        f"{marketplace_id}/{PLUGIN_ID}",
    )
    transaction = {
        "schema": TRANSACTION_SCHEMA,
        "version": 1,
        "id": uuid.uuid4().hex,
        "token": uuid.uuid4().hex + uuid.uuid4().hex,
        "state": "prepared",
        "marketplaceId": marketplace_id,
        "pluginId": PLUGIN_ID,
        "installationId": f"{marketplace_id}/{PLUGIN_ID}",
        "context": _normalized_path(context),
        "namespaceGeneration": str(namespace_generation),
        "installGeneration": str(install_generation),
        "management": {
            "path": _normalized_path(management_payload_root),
            "version": management_version,
        },
        "prior": {
            "runtimeVersion": prior_version,
            "manifest": prior_manifest,
            "artifacts": _capture_transaction_artifacts(plugin_root),
            "instances": prior_instances,
            "activeService": prior_active,
        },
        "target": {
            "payloadRoot": _normalized_path(target_payload_root),
            "payloadVersion": target_payload_version,
            "snapshotId": target_snapshot_id,
            "runtimeVersion": target_runtime_version,
            "manifest": target_manifest,
        },
        "createdAt": _utc_now(),
        "updatedAt": _utc_now(),
    }
    _atomic_json(_selection_transaction_path(plugin_root), transaction)
    return transaction


def _transaction_environment(
    environment: dict[str, str],
    plugin_root: Path,
    transaction: dict[str, Any],
) -> dict[str, str]:
    value = dict(environment)
    value[TRANSACTION_PATH_ENV] = str(_selection_transaction_path(plugin_root))
    value[TRANSACTION_TOKEN_ENV] = str(transaction["token"])
    value[TRANSACTION_ID_ENV] = str(transaction["id"])
    return value


def _validated_transaction_reentry(
    payload_root: Path,
    plugin_root: Path,
    context: Path,
    marketplace_id: str,
) -> dict[str, Any]:
    transaction_path = os.environ.get(TRANSACTION_PATH_ENV, "")
    transaction_token = os.environ.get(TRANSACTION_TOKEN_ENV, "")
    transaction_id = os.environ.get(TRANSACTION_ID_ENV, "")
    expected_path = _selection_transaction_path(plugin_root)
    if (
        not transaction_path
        or not transaction_token
        or not transaction_id
        or re.fullmatch(r"[0-9a-f]{64}", transaction_token) is None
        or re.fullmatch(r"[0-9a-f]{32}", transaction_id) is None
    ):
        raise CellError("installation transaction reentry credentials are invalid")
    if not _paths_equal(transaction_path, expected_path):
        raise CellError(
            "installation transaction reentry receipt belongs to another cell"
        )
    transaction = _load_selection_transaction(
        plugin_root,
        context,
        marketplace_id,
    )
    management = transaction.get("management") if transaction is not None else None
    if (
        transaction is None
        or transaction.get("id") != transaction_id
        or transaction.get("token") != transaction_token
    ):
        raise CellError("installation transaction reentry ownership does not match")
    if transaction.get("state") != "reconciling":
        raise CellError("installation transaction is not reconciling")
    if not isinstance(management, dict):
        raise CellError(
            "installation transaction reentry management identity does not match"
        )
    if not _paths_equal(management.get("path", ""), payload_root):
        raise CellError(
            "installation transaction reentry management path does not match"
        )
    if management.get("version") != _plugin_version(payload_root):
        raise CellError(
            "installation transaction reentry management version does not match"
        )
    return transaction


def _internal_lock_reentry(
    payload_root: Path,
    plugin_root: Path,
    context: Path,
    marketplace_id: str,
    command: str,
) -> tuple[str | None, str | None, str | None]:
    if command not in {"deploy", "__cell-start"}:
        return None, None, None
    token = os.environ.get(LOCK_TOKEN_ENV, "")
    lock_root = os.environ.get(LOCK_ROOT_ENV, "")
    selected_context = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "")
    installation_id = os.environ.get("AGENT_INDEX_INSTALLATION_ID", "")
    expected_context = plugin_root / "install.json"
    if (
        re.fullmatch(r"[0-9a-f]{32}", token) is None
        or not lock_root
        or not _paths_equal(lock_root, plugin_root)
        or not _paths_equal(context, expected_context)
        or not selected_context
        or not _paths_equal(selected_context, context)
        or installation_id != f"{marketplace_id}/{PLUGIN_ID}"
        or not _live_lock_owner_matches(plugin_root, token)
    ):
        raise CellError("installation lock does not authorize internal reentry")
    if command == "deploy":
        transaction = _validated_transaction_reentry(
            payload_root,
            plugin_root,
            context,
            marketplace_id,
        )
        return token, "transaction", str(transaction["id"])
    if os.environ.get(CELL_START_TOKEN_ENV, "") != token:
        raise CellError("installation start token does not authorize lock reentry")
    transaction_values = (
        os.environ.get(TRANSACTION_PATH_ENV, ""),
        os.environ.get(TRANSACTION_TOKEN_ENV, ""),
        os.environ.get(TRANSACTION_ID_ENV, ""),
    )
    if any(transaction_values):
        transaction = _validated_transaction_reentry(
            payload_root,
            plugin_root,
            context,
            marketplace_id,
        )
        return token, "start", str(transaction["id"])
    return token, "start", None


def _inject_selection_failure(phase: str) -> None:
    requested = os.environ.get(
        "AGENT_INDEX_TEST_SELECTION_FAILURE", ""
    ).strip().lower()
    if requested == phase:
        raise CellError(f"injected selection failure at {phase}")
    if requested == f"crash-{phase}":
        os._exit(87)


def _validate_transaction_target(
    management_payload_root: Path,
    validated: dict[str, Any],
    context: Path,
    marketplace_id: str,
    durable_home: Path,
    transaction: dict[str, Any],
) -> Path:
    return _validate_recorded_selection(
        management_payload_root,
        validated,
        context,
        marketplace_id,
        durable_home,
        transaction["target"]["manifest"],
        label="target",
    )


def _publish_transaction_manifest(
    plugin_root: Path,
    context: Path,
    marketplace_id: str,
    transaction: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    target = transaction["target"]
    if _marker_version(plugin_root) != target["runtimeVersion"]:
        raise CellError("current-version changed before manifest publication")
    _atomic_json(plugin_root / "deploy-manifest.json", manifest)
    loaded = _load_manifest(
        plugin_root / "deploy-manifest.json",
        plugin_root,
        context,
        marketplace_id,
    )
    if loaded != manifest:
        raise CellError("published deploy manifest did not validate")


def _restore_prior_selection(
    management_payload_root: Path,
    plugin_root: Path,
    context: Path,
    marketplace_id: str,
    durable_home: Path,
    transaction: dict[str, Any],
    validated: dict[str, Any],
    prior_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    prior = transaction["prior"]
    target = transaction["target"]
    prior_version = prior.get("runtimeVersion")
    current = _marker_version(plugin_root)
    if prior_manifest is not None and prior_version is not None:
        _validate_recorded_selection(
            management_payload_root,
            validated,
            context,
            marketplace_id,
            durable_home,
            prior_manifest,
            label="prior",
            enforce_configured_profile=False,
        )
        selected = prior_manifest["runtime"]["selectedBy"]
        if current != prior_version:
            transaction, _result = _transaction_slot_cutover(
                management_payload_root,
                _absolute_path(selected["path"]),
                context,
                marketplace_id,
                durable_home,
                str(selected["version"]),
                str(selected["snapshotId"]),
                str(prior_version),
                current,
                validated,
                plugin_root,
                transaction,
            )
        _atomic_json(plugin_root / "deploy-manifest.json", prior_manifest)
        loaded = _load_manifest(
            plugin_root / "deploy-manifest.json",
            plugin_root,
            context,
            marketplace_id,
        )
        if loaded != prior_manifest:
            raise CellError("restored deploy manifest did not validate")
        return transaction
    if current not in {None, target["runtimeVersion"]}:
        raise CellError("selection changed outside the pending transaction")
    for name in ("current-version", "last-known-good"):
        path = plugin_root / name
        if not _lexists(path):
            continue
        _assert_regular_file(path, name)
        if path.read_text(encoding="utf-8").strip() != target["runtimeVersion"]:
            raise CellError(f"{name} changed outside the pending transaction")
        path.unlink()
    manifest_path = plugin_root / "deploy-manifest.json"
    if _lexists(manifest_path):
        loaded = _load_manifest(
            manifest_path,
            plugin_root,
            context,
            marketplace_id,
        )
        if not _same_runtime_selection(loaded, target["manifest"]):
            raise CellError("deploy manifest changed outside the pending transaction")
        manifest_path.unlink()
    return transaction


def _transaction_target_instances(
    validated: dict[str, Any],
    transaction: dict[str, Any],
) -> list[tuple[Path, dict[str, Any]]]:
    prior_keys = {
        (int(record["pid"]), str(record["instanceToken"]))
        for record in transaction["prior"]["instances"]
    }
    return [
        (path, record)
        for path, record in _instance_records(validated)
        if record["installationId"] == transaction["installationId"]
        and record.get("transactionId") == transaction["id"]
        and (
            int(record["pid"]),
            str(record["instanceToken"]),
        )
        not in prior_keys
    ]


def _exact_service_snapshot(
    record: dict[str, Any],
    installation_id: str,
) -> dict[str, Any] | None:
    status = _service_status(int(record["port"]))
    if (
        status is None
        or status.get("installationId") != installation_id
        or status.get("version") != record["runtimeVersion"]
        or int(status.get("pid", 0)) != int(record["pid"])
        or status.get("instanceToken") != record["instanceToken"]
    ):
        return None
    return {
        "port": int(record["port"]),
        "pid": int(record["pid"]),
        "version": str(record["runtimeVersion"]),
        "installationId": installation_id,
        "instanceToken": str(record["instanceToken"]),
        "draining": status.get("status") == "draining",
    }


def _retire_exact_instances(
    validated: dict[str, Any],
    installation_id: str,
    records: list[tuple[Path, dict[str, Any]]],
) -> None:
    for path, record in records:
        current = _read_json(path)
        if (
            current.get("pid") != record["pid"]
            or current.get("instanceToken") != record["instanceToken"]
            or current.get("transactionId") != record.get("transactionId")
        ):
            raise CellError(
                "transaction service ownership changed before exact retirement"
            )
        pid = int(record["pid"])
        if _pid_alive(pid):
            _shutdown_owned_instance(record, installation_id)
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and _pid_alive(pid):
                time.sleep(0.1)
            if _pid_alive(pid):
                raise CellError(
                    f"transaction service pid {pid} did not exit during rollback"
                )
    _clear_owned_service_evidence(
        validated,
        [record for _path, record in records],
    )
    for path, record in records:
        try:
            current = _read_json(path)
        except CellError:
            if _lexists(path):
                raise
            continue
        if (
            current.get("pid") == record["pid"]
            and current.get("instanceToken") == record["instanceToken"]
            and current.get("transactionId") == record.get("transactionId")
        ):
            path.unlink()


def _restore_prior_service(
    validated: dict[str, Any],
    transaction: dict[str, Any],
) -> None:
    installation_id = str(transaction["installationId"])
    prior_active = transaction["prior"].get("activeService")
    target_records = _transaction_target_instances(validated, transaction)
    if isinstance(prior_active, dict):
        snapshot = _exact_service_snapshot(prior_active, installation_id)
        if snapshot is not None:
            current = _active_service(validated, installation_id)
            unchanged = (
                current is not None
                and int(current["pid"]) == int(prior_active["pid"])
                and current["version"] == prior_active["runtimeVersion"]
                and int(current["port"]) == int(prior_active["port"])
                and current["instanceToken"] == prior_active["instanceToken"]
                and bool(current["draining"])
                == bool(prior_active.get("draining"))
            )
            if not unchanged:
                routing = _payload_routing_module()
                routing.publish_active(
                    Path(str(validated["runRoot"])) / "zdd",
                    bind=str(prior_active["host"]),
                    port=int(prior_active["port"]),
                    pid=int(prior_active["pid"]),
                    version=str(prior_active["runtimeVersion"]),
                    demote_existing=True,
                )
            if snapshot["draining"] and not prior_active.get("draining"):
                snapshot = _undrain_owned_instance(snapshot, installation_id)
    _retire_exact_instances(validated, installation_id, target_records)
    survivors = [
        record
        for _path, record in _transaction_target_instances(validated, transaction)
        if _pid_alive(int(record["pid"]))
    ]
    if survivors:
        raise CellError(
            "transaction rollback left a target service instance running"
        )


def _rollback_selection_transaction(
    management_payload_root: Path,
    plugin_root: Path,
    context: Path,
    marketplace_id: str,
    durable_home: Path,
    transaction: dict[str, Any],
    validated: dict[str, Any],
    prior_manifest: dict[str, Any] | None,
    *,
    outcome: str,
) -> dict[str, Any]:
    transaction = _restore_prior_selection(
        management_payload_root,
        plugin_root,
        context,
        marketplace_id,
        durable_home,
        transaction,
        validated,
        prior_manifest,
    )
    _restore_transaction_artifacts(
        plugin_root,
        transaction["prior"]["artifacts"],
    )
    _restore_prior_service(validated, transaction)
    _finish_selection_transaction(plugin_root, transaction, outcome=outcome)
    return transaction


def _finish_selection_transaction(
    plugin_root: Path,
    transaction: dict[str, Any],
    *,
    outcome: str,
) -> None:
    path = _selection_transaction_path(plugin_root)
    current = _load_selection_transaction(
        plugin_root,
        _absolute_path(transaction["context"]),
        str(transaction["marketplaceId"]),
    )
    if current is None or current.get("id") != transaction["id"]:
        raise CellError("selection transaction ownership changed before completion")
    receipt = {
        "schema": "copilot-extensions.agent-index.selection-receipt",
        "version": 1,
        "id": transaction["id"],
        "outcome": outcome,
        "marketplaceId": transaction["marketplaceId"],
        "pluginId": transaction["pluginId"],
        "installationId": transaction["installationId"],
        "context": transaction["context"],
        "priorRuntimeVersion": transaction["prior"].get("runtimeVersion"),
        "targetRuntimeVersion": transaction["target"]["runtimeVersion"],
        "completedAt": _utc_now(),
    }
    _atomic_json(plugin_root / TRANSACTION_RECEIPT_FILE, receipt)
    path.unlink()


def _cell_environment(
    validated: dict[str, Any],
    context: Path,
    marketplace_id: str,
    runtime_version: str | None = None,
) -> dict[str, str]:
    plugin_root = Path(str(validated["pluginRoot"]))
    run_root = Path(str(validated["runRoot"]))
    cache_root = Path(str(validated["cacheRoot"]))
    environment = {
        "COPILOT_EXTENSIONS_CONTEXT": str(context),
        "AGENT_INDEX_HOME": str(plugin_root),
        "AGENT_INDEX_STATE_DIR": str(validated["stateRoot"]),
        "AGENT_INDEX_DATA_DIR": str(validated["stateRoot"]),
        "AGENT_INDEX_RUN_DIR": str(run_root),
        "AGENT_INDEX_LOG_DIR": str(validated["logsRoot"]),
        "AGENT_INDEX_CACHE_DIR": str(cache_root),
        "AGENT_INDEX_CONFIG_ROOT": str(plugin_root / "config"),
        "AGENT_INDEX_CONFIG": str(plugin_root / "config" / "config.yaml"),
        "AGENT_INDEX_ROUTING_DIR": str(run_root / "zdd"),
        "AGENT_INDEX_HOST": "127.0.0.1",
        "AGENT_INDEX_PORT": "0",
        "AGENT_INDEX_ENGINE_HOME": str(plugin_root / "engine"),
        "AGENT_INDEX_ENGINE_HOST": "127.0.0.1",
        "AGENT_INDEX_ENGINE_PORT": "0",
        "AGENT_INDEX_ENGINE_MODE": "external",
        "AGENT_INDEX_BACKUP_DIR": str(plugin_root / "backups"),
        "AGENT_INDEX_BACKUP_MOUNT_ROOT": str(plugin_root),
        "AGENT_INDEX_INSTALLATION_ID": f"{marketplace_id}/{PLUGIN_ID}",
        "XDG_CACHE_HOME": str(cache_root),
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if runtime_version:
        environment[RUNTIME_VERSION_ENV] = runtime_version
    return environment


def _write_launchers(
    validated: dict[str, Any],
    context: Path,
    marketplace_id: str,
    management_payload: Path,
    management_version: str,
    runtime_version: str,
) -> tuple[Path, Path]:
    plugin_root = Path(str(validated["pluginRoot"]))
    launcher_root = plugin_root / "launchers"
    launcher_root.mkdir(parents=True, exist_ok=True)
    _assert_path_chain(launcher_root, plugin_root, "launcher root")
    _assert_directory(launcher_root, "launcher root")
    management_payload = _absolute_path(management_payload)
    _assert_directory(management_payload, "management payload root")
    if os.name == "nt":
        service_launcher = launcher_root / "agent-index-service.ps1"
        command_launcher = launcher_root / "agent-index.ps1"
        dispatcher = management_payload / "scripts" / "runtime-gate.ps1"
        _assert_path_chain(dispatcher, management_payload, "payload dispatcher")
        _assert_regular_file(dispatcher, "payload dispatcher")
        for path in (service_launcher, command_launcher):
            _assert_not_reparse(path, "installation launcher")
        prefix = (
            "$ErrorActionPreference = 'Stop'\n"
            f"$env:COPILOT_EXTENSIONS_CONTEXT = "
            f"{_powershell_quote(str(context))}\n"
            f"$env:AGENT_INDEX_PAYLOAD_ROOT = "
            f"{_powershell_quote(str(management_payload))}\n"
            f"$env:{RUNTIME_VERSION_ENV} = "
            f"{_powershell_quote(runtime_version)}\n"
            "Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue\n"
            "Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue\n"
            f"Set-Location -LiteralPath {_powershell_quote(str(plugin_root))}\n"
            f"[IO.Directory]::SetCurrentDirectory("
            f"{_powershell_quote(str(plugin_root))})\n"
        )
        service_launcher.write_text(
            prefix
            + f"& {_powershell_quote(str(dispatcher))} __cell-start @args\n"
            + "exit $LASTEXITCODE\n",
            encoding="utf-8-sig",
        )
        command_launcher.write_text(
            prefix
            + f"& {_powershell_quote(str(dispatcher))} @args\n"
            + "exit $LASTEXITCODE\n",
            encoding="utf-8-sig",
        )
    else:
        service_launcher = launcher_root / "agent-index-service"
        command_launcher = launcher_root / "agent-index"
        dispatcher = management_payload / "scripts" / "runtime-gate.sh"
        _assert_path_chain(dispatcher, management_payload, "payload dispatcher")
        _assert_regular_file(dispatcher, "payload dispatcher")
        for path in (service_launcher, command_launcher):
            _assert_not_reparse(path, "installation launcher")
        prefix = (
            "#!/bin/sh\nset -eu\n"
            f"export COPILOT_EXTENSIONS_CONTEXT={shlex.quote(str(context))}\n"
            f"export AGENT_INDEX_PAYLOAD_ROOT="
            f"{shlex.quote(str(management_payload))}\n"
            f"export {RUNTIME_VERSION_ENV}={shlex.quote(runtime_version)}\n"
            "unset PYTHONPATH PYTHONHOME\n"
            f"cd {shlex.quote(str(plugin_root))}\n"
        )
        service_launcher.write_text(
            prefix
            + f"exec bash {shlex.quote(str(dispatcher))} __cell-start \"$@\"\n",
            encoding="utf-8",
            newline="\n",
        )
        command_launcher.write_text(
            prefix
            + f"exec bash {shlex.quote(str(dispatcher))} \"$@\"\n",
            encoding="utf-8",
            newline="\n",
        )
        service_launcher.chmod(0o700)
        command_launcher.chmod(0o700)
    _atomic_json(
        Path(str(validated["runRoot"])) / "service-identity.json",
        {
            "schema": "copilot-extensions.agent-index.service-identity",
            "version": 2,
            "marketplaceId": marketplace_id,
            "pluginId": PLUGIN_ID,
            "installationId": f"{marketplace_id}/{PLUGIN_ID}",
            "context": _normalized_path(context),
            "managementPayloadRoot": _normalized_path(management_payload),
            "managementPayloadVersion": management_version,
            "runtimeVersion": runtime_version,
            "launcher": _normalized_path(service_launcher),
            "commandLauncher": _normalized_path(command_launcher),
        },
    )
    return service_launcher, command_launcher


def _configured_role(environment: dict[str, str]) -> str | None:
    role = (
        environment.get("AGENT_INDEX_ROLE")
        or os.environ.get("AGENT_INDEX_ROLE", "")
    ).strip().lower()
    if role in {"host", "client"}:
        return role
    config = Path(environment["AGENT_INDEX_CONFIG"])
    try:
        text = config.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(
        r"(?mi)^\s*(?:role|engine)\s*:\s*[\"']?([A-Za-z]+)",
        text,
    )
    if not match:
        return None
    value = match.group(1).lower()
    if value in {"host", "engine", "server", "indexer"}:
        return "host"
    if value in {"client", "none", "consumer"}:
        return "client"
    return None


def _service_status(port: int) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=2
        ) as response:
            if response.status != 200:
                return None
            value = json.loads(response.read().decode("utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _routing_has_active(validated: dict[str, Any]) -> bool:
    path = Path(str(validated["runRoot"])) / "zdd" / "active.json"
    try:
        document = _read_json(path)
        return isinstance(document.get("active"), dict)
    except CellError:
        return False


def _active_service(
    validated: dict[str, Any],
    installation_id: str,
) -> dict[str, Any] | None:
    path = Path(str(validated["runRoot"])) / "zdd" / "active.json"
    try:
        _assert_path_chain(path, Path(str(validated["pluginRoot"])), "routing record")
        active = _read_json(path)["active"]
        port = active["port"]
        version = active["version"]
        pid = active.get("pid")
        if (
            type(port) is not int
            or not isinstance(version, str)
            or type(pid) is not int
        ):
            return None
        status = _service_status(port)
        health_state = status.get("status") if status is not None else None
        if (
            status is None
            or health_state not in {"ok", "draining"}
            or status.get("plugin") != PLUGIN_ID
            or status.get("version") != version
            or status.get("installationId") != installation_id
            or int(status.get("pid", 0)) != pid
            or status.get("promoted") is not True
            or not isinstance(status.get("instanceToken"), str)
            or not status.get("instanceToken")
        ):
            return None
        return {
            "port": port,
            "pid": pid,
            "version": version,
            "installationId": installation_id,
            "instanceToken": status.get("instanceToken"),
            "draining": health_state == "draining",
        }
    except (CellError, KeyError, TypeError, ValueError):
        return None


def _wait_for_active_service(
    validated: dict[str, Any],
    installation_id: str,
    runtime_version: str,
    *,
    prior_instances: set[tuple[int, str]] | None = None,
    timeout: float = SERVICE_HEALTH_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    excluded = prior_instances or set()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        active = _active_service(validated, installation_id)
        if (
            active is not None
            and active["version"] == runtime_version
            and not active["draining"]
            and (
                int(active["pid"]),
                str(active["instanceToken"]),
            )
            not in excluded
        ):
            return active
        time.sleep(0.2)
    raise CellError(
        "cell-local service did not answer with the expected runtime and "
        "installation identity"
    )


def _instance_records(validated: dict[str, Any]) -> list[tuple[Path, dict[str, Any]]]:
    root = Path(str(validated["runRoot"])) / "instances"
    _assert_path_chain(root, Path(str(validated["pluginRoot"])), "service instances")
    if not _lexists(root):
        return []
    _assert_directory(root, "service instances")
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(root.glob("*.json")):
        _assert_regular_file(path, "service instance receipt")
        record = _read_json(path)
        if (
            record.get("schema") != INSTANCE_SCHEMA
            or record.get("version") != 1
            or not isinstance(record.get("installationId"), str)
            or not isinstance(record.get("runtimeVersion"), str)
            or type(record.get("pid")) is not int
            or not isinstance(record.get("instanceToken"), str)
            or not record.get("instanceToken")
            or not isinstance(record.get("host"), str)
            or type(record.get("port")) is not int
        ):
            raise CellError(f"service instance receipt is malformed: {path}")
        records.append((path, record))
    return records


def _shutdown_owned_instance(
    record: dict[str, Any],
    installation_id: str,
) -> None:
    status = _service_status(int(record["port"]))
    if (
        status is None
        or status.get("installationId") != installation_id
        or status.get("version") != record["runtimeVersion"]
        or int(status.get("pid", 0)) != record["pid"]
        or status.get("instanceToken") != record["instanceToken"]
    ):
        raise CellError(
            f"cannot ownership-attest service pid {record['pid']} for shutdown"
        )
    request = urllib.request.Request(
        f"http://127.0.0.1:{record['port']}/shutdown",
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Agent-Index-Installation-Id": installation_id,
            "X-Agent-Index-Instance-Token": str(record["instanceToken"]),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            if response.status != 200:
                raise CellError(
                    f"owned service pid {record['pid']} refused shutdown"
                )
    except Exception as exc:
        raise CellError(
            f"owned service pid {record['pid']} could not be shut down"
        ) from exc


def _undrain_owned_instance(
    active: dict[str, Any],
    installation_id: str,
) -> dict[str, Any]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{active['port']}/undrain",
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Agent-Index-Installation-Id": installation_id,
            "X-Agent-Index-Instance-Token": str(active["instanceToken"]),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            if response.status != 200:
                raise CellError("owned draining service refused recovery")
    except Exception as exc:
        raise CellError("owned draining service could not be recovered") from exc
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        status = _service_status(int(active["port"]))
        if (
            status is not None
            and status.get("installationId") == installation_id
            and status.get("version") == active["version"]
            and int(status.get("pid", 0)) == int(active["pid"])
            and status.get("instanceToken") == active["instanceToken"]
            and status.get("status") == "ok"
        ):
            return {
                **active,
                "draining": False,
            }
        time.sleep(0.1)
    raise CellError("owned draining service did not reopen admission")


def _payload_routing_module():
    library = Path(__file__).resolve().parent.parent / "libs" / "zdd" / "src"
    _assert_directory(library, "payload zdd library")
    value = str(library)
    if value not in sys.path:
        sys.path.insert(0, value)
    from zdd import routing

    return routing


def _clear_owned_service_evidence(
    validated: dict[str, Any],
    records: list[dict[str, Any]],
) -> None:
    if not records:
        return
    plugin_root = Path(str(validated["pluginRoot"]))
    run_root = Path(str(validated["runRoot"]))
    routing_root = run_root / "zdd"
    routing = _payload_routing_module()
    with routing._routing_lock(routing_root):
        table = routing.read_table(routing_root) or {}
        changed = False
        for key in ("active", "previous"):
            raw = table.get(key)
            if not isinstance(raw, dict):
                continue
            for record in records:
                if (
                    raw.get("pid") == record["pid"]
                    and raw.get("version") == record["runtimeVersion"]
                    and raw.get("port") == record["port"]
                ):
                    status = _service_status(int(record["port"]))
                    if status is not None and (
                        status.get("installationId") != record["installationId"]
                        or status.get("version") != record["runtimeVersion"]
                        or int(status.get("pid", 0)) != record["pid"]
                        or status.get("instanceToken") != record["instanceToken"]
                    ):
                        continue
                    if status is None and _pid_alive(int(record["pid"])):
                        continue
                    table.pop(key, None)
                    changed = True
                    break
        if changed:
            path = routing.routing_table_path(routing_root)
            if any(key in table for key in ("active", "previous")):
                routing._atomic_write(path, table)
            else:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    endpoint = run_root / "endpoint.json"
    running = plugin_root / "running-version.json"
    for path, version_key in ((endpoint, None), (running, "version")):
        if not _lexists(path):
            continue
        value = _read_json(path)
        for record in records:
            if value.get("pid") != record["pid"]:
                continue
            if version_key is not None and value.get(version_key) != record["runtimeVersion"]:
                continue
            status = _service_status(int(record["port"]))
            if status is not None and (
                status.get("installationId") != record["installationId"]
                or status.get("version") != record["runtimeVersion"]
                or int(status.get("pid", 0)) != record["pid"]
                or status.get("instanceToken") != record["instanceToken"]
            ):
                continue
            if status is None and _pid_alive(int(record["pid"])):
                continue
            path.unlink()
            break


def _retire_owned_instances(
    validated: dict[str, Any],
    installation_id: str,
) -> int:
    owned = [
        (path, record)
        for path, record in _instance_records(validated)
        if record["installationId"] == installation_id
    ]
    active = _active_service(validated, installation_id)
    if active is not None and not any(
        record["pid"] == active["pid"]
        and record["runtimeVersion"] == active["version"]
        and record["port"] == active["port"]
        and record["instanceToken"] == active["instanceToken"]
        for _path, record in owned
    ):
        raise CellError(
            "service demotion cannot prove ownership of the active instance"
        )
    for _path, record in owned:
        pid = int(record["pid"])
        if _pid_alive(pid):
            _shutdown_owned_instance(record, installation_id)
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and _pid_alive(pid):
                time.sleep(0.1)
            if _pid_alive(pid):
                raise CellError(f"owned service pid {pid} did not exit during demotion")
    _clear_owned_service_evidence(
        validated,
        [record for _path, record in owned],
    )
    for path, record in owned:
        try:
            current = _read_json(path)
        except CellError:
            if _lexists(path):
                raise
            continue
        if (
            current.get("pid") == record["pid"]
            and current.get("instanceToken") == record["instanceToken"]
        ):
            path.unlink()
    survivors = [
        record
        for _path, record in _instance_records(validated)
        if record["installationId"] == installation_id
        and _pid_alive(int(record["pid"]))
    ]
    if survivors:
        raise CellError(
            "service demotion could not prove retirement of every owned instance"
        )
    remaining = _active_service(validated, installation_id)
    if remaining is not None:
        raise CellError(
            "service demotion left an attributable active instance running"
        )
    return len(owned)


class _WindowsOwnedProcess:
    def __init__(
        self,
        process: subprocess.Popen[Any],
        job_handle: int,
        kernel32: Any,
    ) -> None:
        self._process = process
        self._job_handle = job_handle
        self._kernel32 = kernel32

    @property
    def pid(self) -> int:
        return self._process.pid

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def terminate_owned_tree(self) -> None:
        if self._job_handle and not self._kernel32.TerminateJobObject(
            self._job_handle,
            1,
        ):
            raise CellError(
                "spawned Windows service process tree could not be retired"
            )

    def release_owner(self) -> None:
        if self._job_handle:
            self._kernel32.CloseHandle(self._job_handle)
            self._job_handle = 0

    def __del__(self) -> None:
        self.release_owner()


def _spawn_windows_owned_process(
    command: list[str],
    *,
    cwd: str,
    environment: dict[str, str],
    stdout: Any,
    stderr: Any,
) -> _WindowsOwnedProcess:
    create_suspended = 0x00000004
    flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
        | create_suspended
    )
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        creationflags=flags,
    )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.AssignProcessToJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.TerminateJobObject.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    job_handle = int(kernel32.CreateJobObjectW(None, None) or 0)
    if not job_handle:
        process.terminate()
        process.wait(timeout=5)
        raise CellError("Windows service process job could not be created")
    try:
        if not kernel32.AssignProcessToJobObject(
            job_handle,
            int(process._handle),
        ):
            error = ctypes.get_last_error()
            process.terminate()
            process.wait(timeout=5)
            raise CellError(
                "Windows service process could not be assigned to its job "
                f"(error {error})"
            )
        status = int(ntdll.NtResumeProcess(int(process._handle)))
        if status != 0:
            kernel32.TerminateJobObject(job_handle, 1)
            process.wait(timeout=5)
            raise CellError(
                "Windows service process could not be resumed after ownership "
                f"assignment (status {status})"
            )
    except Exception:
        kernel32.CloseHandle(job_handle)
        raise
    return _WindowsOwnedProcess(process, job_handle, kernel32)


def _release_spawned_process_owner(process: Any) -> None:
    release = getattr(process, "release_owner", None)
    if callable(release):
        release()


def _retire_spawned_process(
    process: Any,
    validated: dict[str, Any],
    installation_id: str,
    runtime_version: str,
    prior_instances: set[tuple[int, str]],
) -> None:
    try:
        _retire_spawned_process_owned(
            process,
            validated,
            installation_id,
            runtime_version,
            prior_instances,
        )
    finally:
        _release_spawned_process_owner(process)


def _retire_spawned_process_owned(
    process: Any,
    validated: dict[str, Any],
    installation_id: str,
    runtime_version: str,
    prior_instances: set[tuple[int, str]],
) -> None:
    spawned: list[tuple[Path, dict[str, Any]]] = []
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        spawned = [
            (path, candidate)
            for path, candidate in _instance_records(validated)
            if candidate["installationId"] == installation_id
            and candidate["runtimeVersion"] == runtime_version
            and (
                int(candidate["pid"]),
                str(candidate["instanceToken"]),
            )
            not in prior_instances
        ]
        if spawned or process.poll() is not None:
            break
        time.sleep(0.05)
    shutdown_error: CellError | None = None
    for _path, record in spawned:
        pid = int(record["pid"])
        if not _pid_alive(pid):
            continue
        try:
            _shutdown_owned_instance(record, installation_id)
        except CellError as exc:
            shutdown_error = exc
            continue
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.1)
        if _pid_alive(pid):
            shutdown_error = CellError(
                f"spawned service pid {pid} did not exit after shutdown"
            )
    terminate_tree = getattr(process, "terminate_owned_tree", None)
    if callable(terminate_tree):
        terminate_tree()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise CellError(
                f"spawned service pid {process.pid} could not be retired"
            ) from exc
    elif process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise CellError(
                f"spawned service pid {process.pid} could not be retired"
            ) from exc
    retired = [
        (path, record)
        for path, record in spawned
        if not _pid_alive(int(record["pid"]))
    ]
    _clear_owned_service_evidence(
        validated,
        [record for _path, record in retired],
    )
    for path, record in retired:
        try:
            current = _read_json(path)
        except CellError:
            current = None
        if (
            isinstance(current, dict)
            and current.get("pid") == record["pid"]
            and current.get("instanceToken") == record["instanceToken"]
        ):
            path.unlink()
    survivors = [
        record
        for _path, record in spawned
        if _pid_alive(int(record["pid"]))
    ]
    if survivors:
        raise CellError(
            "spawned service cleanup left an owned instance running"
        )
    if shutdown_error is not None:
        raise shutdown_error


def _reconcile_owned_instances(
    validated: dict[str, Any],
    active: dict[str, Any],
) -> None:
    installation_id = str(active["installationId"])
    active_pid = int(active["pid"])
    for path, record in _instance_records(validated):
        if record["installationId"] != installation_id:
            continue
        pid = int(record["pid"])
        if pid == active_pid:
            status = _service_status(int(record["port"]))
            if (
                status is None
                or status.get("installationId") != installation_id
                or int(status.get("pid", 0)) != active_pid
                or status.get("instanceToken") != active["instanceToken"]
                or status.get("promoted") is not True
            ):
                raise CellError("active service instance receipt is not attributable")
            continue
        if not _pid_alive(pid):
            try:
                path.unlink()
            except OSError:
                pass
            continue
        if _service_status(int(record["port"])) is None:
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and _pid_alive(pid):
                time.sleep(0.1)
            if not _pid_alive(pid):
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
        _shutdown_owned_instance(record, installation_id)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.1)
        if _pid_alive(pid):
            raise CellError(f"owned superseded service pid {pid} did not exit")
        try:
            path.unlink()
        except OSError:
            pass
    survivors = [
        record
        for _path, record in _instance_records(validated)
        if record["installationId"] == installation_id
        and _pid_alive(int(record["pid"]))
    ]
    if len(survivors) != 1 or int(survivors[0]["pid"]) != active_pid:
        raise CellError("service reconciliation did not converge to one owned instance")


def _cell_deploy_command(
    command_launcher: Path,
    *,
    recover: bool,
) -> list[str]:
    arguments = [
        "deploy",
        "--json",
        "--health-timeout",
        "30",
        "--drain-timeout",
        "30",
    ]
    if recover:
        arguments.append("--recover")
    if os.name == "nt":
        return [
            shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(command_launcher),
            *arguments,
        ]
    return [str(command_launcher), *arguments]


def _run_cell_deploy(
    command_launcher: Path,
    child_env: dict[str, str],
    *,
    recover: bool,
) -> None:
    result = subprocess.run(
        _cell_deploy_command(command_launcher, recover=recover),
        cwd=command_launcher.parent.parent,
        env=_isolated_environment(child_env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        if result.stdout.strip():
            try:
                payload = json.loads(
                    result.stdout,
                    object_pairs_hook=_strict_object,
                )
            except ValueError:
                payload = None
            if (
                isinstance(payload, dict)
                and payload.get("reason")
                == "governance-blocked-before-commit"
            ):
                governance = payload.get("governance")
                raise CellGovernanceBlocked(
                    "installation governance blocked service cutover "
                    "before route promotion",
                    governance if isinstance(governance, dict) else None,
                )
        detail = (result.stderr or result.stdout).strip()
        label = "recovery" if recover else "cutover"
        message = (
            f"cell-local service {label} failed"
            + (f": {detail}" if detail else "")
        )
        if result.returncode in RESERVED_CRASH_EXIT_CODES:
            raise CellProcessExit(result.returncode, message)
        raise CellError(message)


def _reconcile_service(
    validated: dict[str, Any],
    service_launcher: Path,
    command_launcher: Path,
    runtime_version: str,
    environment: dict[str, str],
    lock_token: str,
) -> dict[str, Any] | None:
    if os.environ.get("AGENT_INDEX_CELL_NO_START") == "1":
        return None
    installation_id = environment["AGENT_INDEX_INSTALLATION_ID"]
    if _configured_role(environment) != "host":
        _retire_owned_instances(validated, installation_id)
        return None
    Path(environment["AGENT_INDEX_STATE_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(environment["AGENT_INDEX_RUN_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(environment["AGENT_INDEX_LOG_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(environment["AGENT_INDEX_CACHE_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(environment["AGENT_INDEX_CONFIG_ROOT"]).mkdir(parents=True, exist_ok=True)
    active = _active_service(validated, installation_id)
    child_env = _isolated_environment()
    child_env.update(environment)
    child_env.pop("AGENT_INDEX_ENDPOINT", None)
    child_env[LOCK_TOKEN_ENV] = lock_token
    child_env[LOCK_ROOT_ENV] = str(validated["pluginRoot"])
    child_env[CELL_START_TOKEN_ENV] = lock_token
    transaction_active = bool(child_env.get(TRANSACTION_TOKEN_ENV))
    if transaction_active:
        _run_cell_deploy(command_launcher, child_env, recover=True)
        active = _active_service(validated, installation_id)
    if active is not None and active["version"] == runtime_version:
        if active["draining"]:
            if not transaction_active:
                raise CellError(
                    "matching service runtime is draining and is not ready"
                )
            active = _undrain_owned_instance(active, installation_id)
        _reconcile_owned_instances(validated, active)
        return active
    if transaction_active:
        prior_instances = {
            (int(record["pid"]), str(record["instanceToken"]))
            for _path, record in _instance_records(validated)
        }
        _run_cell_deploy(command_launcher, child_env, recover=False)
        reconciled = _wait_for_active_service(
            validated,
            installation_id,
            runtime_version,
            prior_instances=prior_instances,
        )
        _reconcile_owned_instances(validated, reconciled)
        return reconciled
    if active is not None or _routing_has_active(validated):
        raise CellError(
            "service runtime transition requires the owning installation transaction"
        )
    _retire_owned_instances(validated, installation_id)
    prior_instances = {
        (int(record["pid"]), str(record["instanceToken"]))
        for _path, record in _instance_records(validated)
    }
    log_path = Path(environment["AGENT_INDEX_LOG_DIR"]) / "service.log"
    log_stream = log_path.open("ab")
    process: subprocess.Popen[Any]
    try:
        if os.name == "nt":
            command = [
                str(
                    _venv_python(
                        Path(str(validated["versionsRoot"])) / runtime_version
                    )
                ),
                "-I",
                "-X",
                "utf8",
                "-m",
                "agent_index",
                "__cell-start",
            ]
            process = _spawn_windows_owned_process(
                command,
                cwd=str(validated["pluginRoot"]),
                environment=child_env,
                stdout=log_stream,
                stderr=log_stream,
            )
        else:
            process = subprocess.Popen(
                [str(service_launcher)],
                cwd=str(validated["pluginRoot"]),
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=log_stream,
                stderr=log_stream,
                start_new_session=True,
            )
    finally:
        log_stream.close()
    try:
        reconciled = _wait_for_active_service(
            validated,
            installation_id,
            runtime_version,
            prior_instances=prior_instances,
        )
    except Exception:
        _retire_spawned_process(
            process,
            validated,
            installation_id,
            runtime_version,
            prior_instances,
        )
        raise
    try:
        _reconcile_owned_instances(validated, reconciled)
    finally:
        _release_spawned_process_owner(process)
    return reconciled


def _installation_status(
    payload_root: Path,
    origin_payload_root: Path,
    context: Path,
    marketplace_id: str,
    durable_home: Path,
) -> None:
    return _run_context(
        payload_root,
        "status",
        "--context",
        str(context),
        "--payload-root",
        str(origin_payload_root),
        "--plugin-id",
        PLUGIN_ID,
        "--expected-marketplace-id",
        marketplace_id,
        "--expected-plugin-id",
        PLUGIN_ID,
        "--expected-payload-root",
        str(origin_payload_root),
        "--durable-home",
        str(durable_home),
        "--legacy-root",
        str(Path.home() / ".agent-index"),
    )


def _governance_mode(
    value: dict[str, Any],
    *,
    operation: str,
    allow_deactivation_existing: bool = False,
) -> str:
    if (
        value.get("status") != "ready"
        or value.get("reason") != "namespaced-active"
        or value.get("actualMode") != "namespaced"
    ):
        if (
            allow_deactivation_existing
            and value.get("status") == "deactivation-required"
            and value.get("actualMode") == "namespaced"
        ):
            return "deactivation-required"
        raise CellError(
            f"{operation} requires an active validated namespaced installation "
            f"(status={value.get('status')} reason={value.get('reason')})"
        )
    return "active"


def _slot_cutover(
    management_payload_root: Path,
    target_payload_root: Path,
    context: Path,
    marketplace_id: str,
    durable_home: Path,
    target_payload_version: str,
    target_snapshot_id: str,
    target_runtime_version: str,
    namespace_generation: str,
    install_generation: str,
    expected_current: str | None,
) -> dict[str, Any]:
    arguments = _context_arguments(
        context,
        marketplace_id,
        target_payload_root,
        target_payload_version,
        durable_home,
    )
    arguments[arguments.index("--snapshot-id") + 1] = target_snapshot_id
    arguments[arguments.index("--runtime-version") + 1] = target_runtime_version
    arguments.extend(
        [
            "--expected-namespace-generation",
            namespace_generation,
            "--expected-install-generation",
            install_generation,
        ]
    )
    if expected_current is None:
        arguments.append("--expect-current-absent")
    else:
        arguments.extend(["--expected-current-version", expected_current])
    return _run_context(management_payload_root, "slot-cutover", *arguments)


def _runtime_import_probe(
    interpreter: Path,
    validated: dict[str, Any],
    context: Path,
    marketplace_id: str,
    runtime_version: str,
    *,
    label: str,
) -> None:
    environment = _isolated_environment()
    environment.update(_cell_environment(validated, context, marketplace_id))
    slot = Path(str(validated["versionsRoot"])) / runtime_version
    _validate_runtime_interpreter_layout(
        interpreter,
        slot,
        marketplace_id,
        runtime_version,
        label=f"{label} interpreter",
    )
    _runtime_module_path(
        interpreter,
        slot,
        environment=environment,
        label=label,
    )


def _validate_recorded_selection(
    management_payload_root: Path,
    validated: dict[str, Any],
    context: Path,
    marketplace_id: str,
    durable_home: Path,
    manifest: dict[str, Any],
    *,
    label: str,
    enforce_configured_profile: bool = True,
) -> Path:
    plugin_root = Path(str(validated["pluginRoot"]))
    runtime = manifest.get("runtime")
    selected = runtime.get("selectedBy") if isinstance(runtime, dict) else None
    installation = manifest.get("installation")
    if (
        not isinstance(runtime, dict)
        or not isinstance(selected, dict)
        or not isinstance(installation, dict)
        or installation.get("marketplaceId") != marketplace_id
        or installation.get("pluginId") != PLUGIN_ID
        or installation.get("installationId") != f"{marketplace_id}/{PLUGIN_ID}"
        or not _paths_equal(installation.get("context", ""), context)
        or not all(
            isinstance(runtime.get(key), str) and runtime[key]
            for key in ("version", "path", "interpreter")
        )
        or not all(
            isinstance(selected.get(key), str) and selected[key]
            for key in ("path", "version", "snapshotId")
        )
    ):
        raise CellError(f"{label} manifest identity is invalid")
    runtime_version = str(runtime["version"])
    slot = plugin_root / "versions" / runtime_version
    interpreter = _venv_python(slot)
    if not _paths_equal(runtime["path"], slot) or not _paths_equal(
        runtime["interpreter"],
        interpreter,
    ):
        raise CellError(f"{label} runtime escapes its installation")
    arguments = _context_arguments(
        context,
        marketplace_id,
        _absolute_path(selected["path"]),
        str(selected["version"]),
        durable_home,
    )
    arguments[arguments.index("--snapshot-id") + 1] = str(selected["snapshotId"])
    arguments[arguments.index("--runtime-version") + 1] = runtime_version
    _run_context(
        management_payload_root,
        "slot-completion-validate",
        *arguments,
    )
    role = _configured_role(
        _cell_environment(validated, context, marketplace_id)
    )
    _validate_runtime_profile(
        slot,
        marketplace_id,
        runtime_version,
        role,
        enforce_role=enforce_configured_profile,
    )
    _runtime_import_probe(
        interpreter,
        validated,
        context,
        marketplace_id,
        runtime_version,
        label=f"{label} completed runtime",
    )
    return interpreter


def _manifest_with_current_source(
    manifest: dict[str, Any],
    management_payload_root: Path,
    management_version: str,
) -> dict[str, Any]:
    updated = copy.deepcopy(manifest)
    updated["source"] = _source_provenance(
        management_payload_root,
        management_version,
    )
    return updated


def _same_runtime_selection(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> bool:
    if left is None or right is None:
        return left is right
    return (
        left.get("schema_version") == right.get("schema_version")
        and left.get("service") == right.get("service")
        and left.get("runtime") == right.get("runtime")
        and left.get("installation") == right.get("installation")
    )


def _transaction_slot_cutover(
    management_payload_root: Path,
    target_payload_root: Path,
    context: Path,
    marketplace_id: str,
    durable_home: Path,
    target_payload_version: str,
    target_snapshot_id: str,
    target_runtime_version: str,
    expected_current: str | None,
    validated: dict[str, Any],
    plugin_root: Path,
    transaction: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_expectation = expected_current
    for _attempt in range(4):
        result = _slot_cutover(
            management_payload_root,
            target_payload_root,
            context,
            marketplace_id,
            durable_home,
            target_payload_version,
            target_snapshot_id,
            target_runtime_version,
            str(transaction["namespaceGeneration"]),
            str(transaction["installGeneration"]),
            current_expectation,
        )
        if result.get("status") == "ready":
            return transaction, result
        if result.get("status") != "revalidation-required":
            raise CellError("runtime-slot cutover did not complete")
        reason = result.get("reason")
        if reason == "generation-changed":
            namespace_generation = result.get("namespaceGeneration")
            install_generation = result.get("installGeneration")
            if type(namespace_generation) is not int or type(install_generation) is not int:
                raise CellError("runtime-slot revalidation returned invalid generations")
            transaction = dict(transaction)
            transaction["namespaceGeneration"] = str(namespace_generation)
            transaction["installGeneration"] = str(install_generation)
            transaction = _write_selection_transaction(plugin_root, transaction)
            continue
        if reason in {"current-version-changed", "runtime-marker-changed"}:
            changed_current = result.get("currentVersion")
            if changed_current in {current_expectation, target_runtime_version}:
                current_expectation = changed_current
                continue
        raise CellError(
            f"runtime-slot cutover requires unresolved revalidation ({reason})"
        )
    raise CellError("runtime-slot cutover revalidation did not converge")


def _complete_slot(
    payload_root: Path,
    origin_payload_root: Path,
    snapshot_root: Path,
    context: Path,
    marketplace_id: str,
    durable_home: Path,
    payload_version: str,
    runtime_version: str,
    slot: Path,
    validated: dict[str, Any],
    role: str | None,
) -> Path:
    completion = slot / ".runtime-slot-completion.json"
    if _lexists(completion):
        _assert_path_chain(slot, slot.parent.parent, "runtime slot")
        _assert_directory(slot, "runtime slot")
        _assert_regular_file(completion, "runtime slot completion")
        _validate_runtime_profile(
            slot,
            marketplace_id,
            runtime_version,
            role,
        )
        _run_context(
            payload_root,
            "slot-completion-validate",
            *_context_arguments(
                context,
                marketplace_id,
                origin_payload_root,
                payload_version,
                durable_home,
                snapshot_id=payload_version,
                runtime_version=runtime_version,
            ),
        )
        interpreter = _venv_python(slot)
        _runtime_import_probe(
            interpreter,
            validated,
            context,
            marketplace_id,
            runtime_version,
            label="completed runtime slot",
        )
        return interpreter
    interpreter = _build_runtime(
        snapshot_root,
        slot,
        marketplace_id=marketplace_id,
        runtime_version=runtime_version,
        role=role,
    )
    _write_runtime_profile(
        slot,
        marketplace_id,
        runtime_version,
        role,
    )
    _write_build_receipt(
        payload_root,
        snapshot_root,
        slot,
        runtime_version,
    )
    _run_context(
        payload_root,
        "slot-complete",
        *_context_arguments(
            context,
            marketplace_id,
            origin_payload_root,
            payload_version,
            durable_home,
            snapshot_id=payload_version,
            runtime_version=runtime_version,
        ),
    )
    _validate_runtime_profile(
        slot,
        marketplace_id,
        runtime_version,
        role,
    )
    _runtime_import_probe(
        interpreter,
        validated,
        context,
        marketplace_id,
        runtime_version,
        label="completed runtime slot",
    )
    return interpreter


def _selected_runtime(
    management_payload_root: Path,
    validated: dict[str, Any],
    context: Path,
    marketplace_id: str,
    durable_home: Path,
    *,
    reconciled_payload_root: Path | None = None,
) -> tuple[dict[str, Any] | None, Path | None]:
    plugin_root = Path(str(validated["pluginRoot"]))
    manifest = _load_manifest(
        plugin_root / "deploy-manifest.json",
        plugin_root,
        context,
        marketplace_id,
    )
    marker = plugin_root / "current-version"
    if manifest is None:
        for candidate in (
            marker,
            plugin_root / "last-known-good",
            Path(str(validated["runRoot"])) / "service-identity.json",
        ):
            _assert_not_reparse(candidate, candidate.name)
        return None, None
    reconciled_payload_root = _absolute_path(
        reconciled_payload_root or management_payload_root
    )
    management_version = _plugin_version(reconciled_payload_root)
    source = manifest["source"]
    if (
        source["version"] != management_version
        or not _paths_equal(source["path"], reconciled_payload_root)
    ):
        raise CellError(
            "deploy manifest is not reconciled to the current management payload"
        )
    runtime = manifest["runtime"]
    runtime_version = str(runtime["version"])
    _assert_regular_file(marker, "current-version marker")
    if marker.read_text(encoding="utf-8").strip() != runtime_version:
        raise CellError("deploy manifest does not match the selected runtime")
    interpreter = _validate_recorded_selection(
        management_payload_root,
        validated,
        context,
        marketplace_id,
        durable_home,
        manifest,
        label="selected",
    )
    return manifest, interpreter


def _validate_launcher_artifacts(
    validated: dict[str, Any],
    context: Path,
    marketplace_id: str,
    management_payload_root: Path,
    management_version: str,
    runtime_version: str,
) -> tuple[Path, Path]:
    plugin_root = Path(str(validated["pluginRoot"]))
    management_payload_root = _absolute_path(management_payload_root)
    dispatcher = management_payload_root / "scripts" / (
        "runtime-gate.ps1" if os.name == "nt" else "runtime-gate.sh"
    )
    _assert_path_chain(dispatcher, management_payload_root, "payload dispatcher")
    _assert_regular_file(dispatcher, "payload dispatcher")
    launcher_root = plugin_root / "launchers"
    service_launcher = launcher_root / (
        "agent-index-service.ps1" if os.name == "nt" else "agent-index-service"
    )
    command_launcher = launcher_root / (
        "agent-index.ps1" if os.name == "nt" else "agent-index"
    )
    for path in (launcher_root, service_launcher, command_launcher):
        _assert_path_chain(path, plugin_root, "installation launcher")
    _assert_directory(launcher_root, "launcher root")
    _assert_regular_file(service_launcher, "service launcher")
    _assert_regular_file(command_launcher, "command launcher")
    identity_path = Path(str(validated["runRoot"])) / "service-identity.json"
    _assert_path_chain(identity_path, plugin_root, "service identity")
    identity = _read_json(identity_path)
    expected = {
        "schema": "copilot-extensions.agent-index.service-identity",
        "version": 2,
        "marketplaceId": marketplace_id,
        "pluginId": PLUGIN_ID,
        "installationId": f"{marketplace_id}/{PLUGIN_ID}",
        "context": _normalized_path(context),
        "managementPayloadRoot": _normalized_path(management_payload_root),
        "managementPayloadVersion": management_version,
        "runtimeVersion": runtime_version,
        "launcher": _normalized_path(service_launcher),
        "commandLauncher": _normalized_path(command_launcher),
    }
    if identity != expected:
        raise CellError("service identity does not match the selected installation")
    return service_launcher, command_launcher


def _selection_governance(
    management_payload_root: Path,
    context: Path,
    marketplace_id: str,
    durable_home: Path,
) -> tuple[bool, dict[str, Any]]:
    status = _installation_status(
        management_payload_root,
        management_payload_root,
        context,
        marketplace_id,
        durable_home,
    )
    active = (
        status.get("status") == "ready"
        and status.get("reason") == "namespaced-active"
        and status.get("actualMode") == "namespaced"
    )
    return active, status


def governance_check(
    args: argparse.Namespace,
    payload_root: Path,
) -> dict[str, Any]:
    context = _absolute_path(args.context)
    durable_home = _durable_home(context, args.durable_home)
    _validate_context(
        payload_root,
        context,
        args.expected_marketplace_id,
        durable_home,
        expected_payload_root=payload_root,
    )
    active, status = _selection_governance(
        payload_root,
        context,
        args.expected_marketplace_id,
        durable_home,
    )
    return {
        "status": "ready" if active else "governance-blocked",
        "active": active,
        "governance": status,
    }


def _resume_selection_transaction(
    management_payload_root: Path,
    context: Path,
    marketplace_id: str,
    durable_home: Path,
    validated: dict[str, Any],
    lock_token: str,
    transaction: dict[str, Any],
) -> dict[str, Any]:
    plugin_root = Path(str(validated["pluginRoot"]))
    management_version = _plugin_version(management_payload_root)
    current_namespace_generation = str(validated["namespaceGeneration"])
    current_install_generation = str(validated["generation"])
    current_management = {
        "path": _normalized_path(management_payload_root),
        "version": management_version,
    }
    if (
        transaction["namespaceGeneration"] != current_namespace_generation
        or transaction["installGeneration"] != current_install_generation
        or transaction["management"] != current_management
    ):
        transaction = dict(transaction)
        transaction["namespaceGeneration"] = current_namespace_generation
        transaction["installGeneration"] = current_install_generation
        transaction["management"] = current_management
        transaction = _write_selection_transaction(plugin_root, transaction)
    target = transaction["target"]
    prior = transaction["prior"]
    recorded_prior_manifest = prior.get("manifest")
    effective_prior_manifest = (
        _manifest_with_current_source(
            recorded_prior_manifest,
            management_payload_root,
            management_version,
        )
        if isinstance(recorded_prior_manifest, dict)
        else None
    )
    effective_target_manifest = _manifest_with_current_source(
        target["manifest"],
        management_payload_root,
        management_version,
    )

    try:
        _validate_transaction_target(
            management_payload_root,
            validated,
            context,
            marketplace_id,
            durable_home,
            transaction,
        )
    except CellError:
        transaction = _rollback_selection_transaction(
            management_payload_root,
            plugin_root,
            context,
            marketplace_id,
            durable_home,
            transaction,
            validated,
            effective_prior_manifest,
            outcome="restored-invalid-target",
        )
        raise

    active, governance = _selection_governance(
        management_payload_root,
        context,
        marketplace_id,
        durable_home,
    )
    if not active:
        transaction = _rollback_selection_transaction(
            management_payload_root,
            plugin_root,
            context,
            marketplace_id,
            durable_home,
            transaction,
            validated,
            effective_prior_manifest,
            outcome="governance-blocked",
        )
        return {
            "status": "governance-blocked",
            "started": False,
            "governance": governance,
            "runtimeVersion": prior.get("runtimeVersion"),
        }

    marker = _marker_version(plugin_root)
    target_version = str(target["runtimeVersion"])
    prior_version = prior.get("runtimeVersion")
    try:
        if marker == prior_version:
            transaction, _result = _transaction_slot_cutover(
                management_payload_root,
                _absolute_path(target["payloadRoot"]),
                context,
                marketplace_id,
                durable_home,
                str(target["payloadVersion"]),
                str(target["snapshotId"]),
                target_version,
                prior_version,
                validated,
                plugin_root,
                transaction,
            )
        elif marker != target_version:
            raise CellError("current-version changed outside the pending transaction")
    except CellError:
        transaction = _rollback_selection_transaction(
            management_payload_root,
            plugin_root,
            context,
            marketplace_id,
            durable_home,
            transaction,
            validated,
            effective_prior_manifest,
            outcome="restored-revalidation-required",
        )
        raise
    transaction = _write_selection_transaction(
        plugin_root,
        transaction,
        state="marker-published",
    )
    _inject_selection_failure("after-marker")
    _inject_selection_failure("before-manifest")

    existing_manifest = _load_manifest(
        plugin_root / "deploy-manifest.json",
        plugin_root,
        context,
        marketplace_id,
    )
    if (
        existing_manifest is not None
        and not _same_runtime_selection(existing_manifest, recorded_prior_manifest)
        and not _same_runtime_selection(existing_manifest, target["manifest"])
    ):
        raise CellError("deploy manifest changed outside the pending transaction")
    if existing_manifest != effective_target_manifest:
        _publish_transaction_manifest(
            plugin_root,
            context,
            marketplace_id,
            transaction,
            effective_target_manifest,
        )
    transaction = _write_selection_transaction(
        plugin_root,
        transaction,
        state="manifest-published",
    )
    _inject_selection_failure("after-manifest")

    active, governance = _selection_governance(
        management_payload_root,
        context,
        marketplace_id,
        durable_home,
    )
    if not active:
        transaction = _rollback_selection_transaction(
            management_payload_root,
            plugin_root,
            context,
            marketplace_id,
            durable_home,
            transaction,
            validated,
            effective_prior_manifest,
            outcome="governance-blocked",
        )
        return {
            "status": "governance-blocked",
            "started": False,
            "governance": governance,
            "runtimeVersion": prior_version,
        }

    transaction = _write_selection_transaction(
        plugin_root,
        transaction,
        state="reconciling",
    )
    _selected_runtime(
        management_payload_root,
        validated,
        context,
        marketplace_id,
        durable_home,
    )
    service_launcher, command_launcher = _write_launchers(
        validated,
        context,
        marketplace_id,
        management_payload_root,
        management_version,
        target_version,
    )
    environment = _transaction_environment(
        _cell_environment(
            validated,
            context,
            marketplace_id,
            target_version,
        ),
        plugin_root,
        transaction,
    )
    active, governance = _selection_governance(
        management_payload_root,
        context,
        marketplace_id,
        durable_home,
    )
    if not active:
        transaction = _rollback_selection_transaction(
            management_payload_root,
            plugin_root,
            context,
            marketplace_id,
            durable_home,
            transaction,
            validated,
            effective_prior_manifest,
            outcome="governance-blocked",
        )
        return {
            "status": "governance-blocked",
            "started": False,
            "governance": governance,
            "runtimeVersion": prior_version,
        }
    try:
        service = _reconcile_service(
            validated,
            service_launcher,
            command_launcher,
            target_version,
            environment,
            lock_token,
        )
    except CellGovernanceBlocked as exc:
        transaction = _rollback_selection_transaction(
            management_payload_root,
            plugin_root,
            context,
            marketplace_id,
            durable_home,
            transaction,
            validated,
            effective_prior_manifest,
            outcome="governance-blocked-before-commit",
        )
        return {
            "status": "governance-blocked",
            "started": False,
            "governance": exc.governance,
            "runtimeVersion": prior_version,
        }
    except CellProcessExit:
        raise
    except Exception:
        try:
            _rollback_selection_transaction(
                management_payload_root,
                plugin_root,
                context,
                marketplace_id,
                durable_home,
                transaction,
                validated,
                effective_prior_manifest,
                outcome="restored-service-reconciliation-failure",
            )
        except Exception as rollback_exc:
            raise CellError(
                "service reconciliation failed and prior runtime rollback did "
                "not complete; dispatch remains blocked by the pending "
                "selection transaction"
            ) from rollback_exc
        raise
    _finish_selection_transaction(
        plugin_root,
        transaction,
        outcome="committed",
    )
    return {
        "status": "ready",
        "marketplaceId": marketplace_id,
        "pluginId": PLUGIN_ID,
        "runtimeVersion": target_version,
        "runtimeRoot": str(plugin_root),
        "serviceLauncher": str(service_launcher),
        "service": service,
        "engineProvisioned": False,
    }


def _provision_locked(
    management_payload_root: Path,
    origin_payload_root: Path,
    context: Path,
    marketplace_id: str,
    durable_home: Path,
    version: str,
    validated: dict[str, Any],
    lock_token: str,
) -> dict[str, Any]:
    plugin_root = Path(str(validated["pluginRoot"]))
    environment = _cell_environment(validated, context, marketplace_id)
    role = _configured_role(environment)
    if role == "host":
        raise CellError("host provisioning is dispatch-managed")
    pending = _load_selection_transaction(plugin_root, context, marketplace_id)
    if pending is not None:
        return _resume_selection_transaction(
            management_payload_root,
            context,
            marketplace_id,
            durable_home,
            validated,
            lock_token,
            pending,
        )
    snapshot_root = _ensure_snapshot(
        origin_payload_root,
        context,
        marketplace_id,
        version,
        durable_home,
        validated,
    )
    runtime_version = _profile_runtime_version(version, role)
    _run_context(
        management_payload_root,
        "slot-provision",
        *_context_arguments(
            context,
            marketplace_id,
            origin_payload_root,
            version,
            durable_home,
            snapshot_id=version,
            runtime_version=runtime_version,
        ),
    )
    slot = Path(str(validated["versionsRoot"])) / runtime_version
    _complete_slot(
        management_payload_root,
        origin_payload_root,
        snapshot_root,
        context,
        marketplace_id,
        durable_home,
        version,
        runtime_version,
        slot,
        validated,
        role,
    )
    if _configured_role(environment) != role:
        raise CellError("configured role changed during runtime provisioning")
    transaction = _prepare_selection_transaction(
        plugin_root,
        context,
        marketplace_id,
        management_payload_root,
        _plugin_version(management_payload_root),
        origin_payload_root,
        version,
        version,
        runtime_version,
        str(validated["namespaceGeneration"]),
        str(validated["generation"]),
        validated,
        preserve_source=False,
    )
    return _resume_selection_transaction(
        management_payload_root,
        context,
        marketplace_id,
        durable_home,
        validated,
        lock_token,
        transaction,
    )


def provision(args: argparse.Namespace, payload_root: Path) -> dict[str, Any]:
    context = _absolute_path(args.context)
    origin_payload_root = _absolute_path(
        getattr(args, "origin_payload_root", None) or payload_root
    )
    durable_home = _durable_home(context, args.durable_home)
    version = _plugin_version(payload_root)
    validated = _validate_context(
        payload_root,
        context,
        args.expected_marketplace_id,
        durable_home,
        expected_payload_root=origin_payload_root,
    )
    plugin_root = Path(str(validated["pluginRoot"]))
    with _installation_lock(plugin_root) as lock_token:
        status = _installation_status(
            payload_root,
            origin_payload_root,
            context,
            args.expected_marketplace_id,
            durable_home,
        )
        validated = _validate_context(
            payload_root,
            context,
            args.expected_marketplace_id,
            durable_home,
            expected_payload_root=origin_payload_root,
        )
        pending = _load_selection_transaction(
            plugin_root,
            context,
            args.expected_marketplace_id,
        )
        if pending is not None:
            return _resume_selection_transaction(
                payload_root,
                context,
                args.expected_marketplace_id,
                durable_home,
                validated,
                lock_token,
                pending,
            )
        _governance_mode(status, operation="cell-provision")
        return _provision_locked(
            payload_root,
            origin_payload_root,
            context,
            args.expected_marketplace_id,
            durable_home,
            version,
            validated,
            lock_token,
        )


def cutover(args: argparse.Namespace, payload_root: Path) -> dict[str, Any]:
    context = _absolute_path(args.context)
    durable_home = _durable_home(context, args.durable_home)
    management_version = _plugin_version(payload_root)
    target_payload_root = _absolute_path(args.target_payload_root)
    target_payload_version = args.target_payload_version
    target_snapshot_id = args.target_snapshot_id
    target_runtime_version = args.target_runtime_version
    validated = _validate_context(
        payload_root,
        context,
        args.expected_marketplace_id,
        durable_home,
        expected_payload_root=payload_root,
    )
    plugin_root = Path(str(validated["pluginRoot"]))
    with _installation_lock(plugin_root) as lock_token:
        status = _installation_status(
            payload_root,
            payload_root,
            context,
            args.expected_marketplace_id,
            durable_home,
        )
        validated = _validate_context(
            payload_root,
            context,
            args.expected_marketplace_id,
            durable_home,
            expected_payload_root=payload_root,
        )
        pending = _load_selection_transaction(
            plugin_root,
            context,
            args.expected_marketplace_id,
        )
        if pending is not None:
            return _resume_selection_transaction(
                payload_root,
                context,
                args.expected_marketplace_id,
                durable_home,
                validated,
                lock_token,
                pending,
            )
        _governance_mode(status, operation="slot-cutover")
        manifest_path = plugin_root / "deploy-manifest.json"
        current_manifest = _load_manifest(
            manifest_path,
            plugin_root,
            context,
            args.expected_marketplace_id,
        )
        if current_manifest is not None and (
            current_manifest["source"]["version"] != management_version
            or not _paths_equal(
                current_manifest["source"]["path"],
                payload_root,
            )
        ):
            raise CellError(
                "slot-cutover must run from the latest reconciled management payload"
            )
        if args.expect_current_absent:
            if current_manifest is not None:
                raise CellError(
                    "deploy manifest exists while current runtime is expected absent"
                )
            expected_current = None
        else:
            expected_current = args.expected_current_version
            if current_manifest is None:
                raise CellError("deploy manifest is missing for runtime cutover")
            marker = plugin_root / "current-version"
            if (
                not marker.is_file()
                or _is_reparse(marker)
                or marker.read_text(encoding="utf-8").strip()
                != current_manifest["runtime"]["version"]
            ):
                raise CellError(
                    "deploy manifest does not match the current runtime selection"
                )
        if _marker_version(plugin_root) != expected_current:
            raise CellError("current-version does not match the cutover expectation")
        transaction = _prepare_selection_transaction(
            plugin_root,
            context,
            args.expected_marketplace_id,
            payload_root,
            management_version,
            target_payload_root,
            target_payload_version,
            target_snapshot_id,
            target_runtime_version,
            args.expected_namespace_generation,
            args.expected_install_generation,
            validated,
            preserve_source=True,
        )
        return _resume_selection_transaction(
            payload_root,
            context,
            args.expected_marketplace_id,
            durable_home,
            validated,
            lock_token,
            transaction,
        )


def service_ensure(args: argparse.Namespace, payload_root: Path) -> dict[str, Any]:
    context = _absolute_path(args.context)
    durable_home = _durable_home(context, args.durable_home)
    validated = _validate_context(
        payload_root,
        context,
        args.expected_marketplace_id,
        durable_home,
        expected_payload_root=payload_root,
    )
    plugin_root = Path(str(validated["pluginRoot"]))
    with _installation_lock(plugin_root) as lock_token:
        status = _installation_status(
            payload_root,
            payload_root,
            context,
            args.expected_marketplace_id,
            durable_home,
        )
        validated = _validate_context(
            payload_root,
            context,
            args.expected_marketplace_id,
            durable_home,
            expected_payload_root=payload_root,
        )
        pending = _load_selection_transaction(
            plugin_root,
            context,
            args.expected_marketplace_id,
        )
        if pending is not None:
            return _resume_selection_transaction(
                payload_root,
                context,
                args.expected_marketplace_id,
                durable_home,
                validated,
                lock_token,
                pending,
            )
        mode = _governance_mode(
            status,
            operation="service-ensure",
            allow_deactivation_existing=True,
        )
        manifest, _interpreter = _selected_runtime(
            payload_root,
            validated,
            context,
            args.expected_marketplace_id,
            durable_home,
        )
        if manifest is None:
            return {"status": "absent", "started": False}
        runtime_version = str(manifest["runtime"]["version"])
        environment = _cell_environment(
            validated,
            context,
            args.expected_marketplace_id,
            runtime_version,
        )
        before = _active_service(
            validated,
            environment["AGENT_INDEX_INSTALLATION_ID"],
        )
        if mode == "deactivation-required":
            _validate_launcher_artifacts(
                validated,
                context,
                args.expected_marketplace_id,
                payload_root,
                _plugin_version(payload_root),
                runtime_version,
            )
            return {
                "status": "deactivation-required",
                "started": False,
                "runtimeVersion": runtime_version,
                "service": before,
            }
        service_launcher, command_launcher = _write_launchers(
            validated,
            context,
            args.expected_marketplace_id,
            payload_root,
            _plugin_version(payload_root),
            runtime_version,
        )
        after = _reconcile_service(
            validated,
            service_launcher,
            command_launcher,
            runtime_version,
            environment,
            lock_token,
        )
        return {
            "status": "ready",
            "started": before is None and after is not None,
            "runtimeVersion": runtime_version,
            "service": after,
        }


def bootstrap(args: argparse.Namespace, payload_root: Path) -> dict[str, Any]:
    context = _absolute_path(args.context)
    durable_home = _durable_home(context, args.durable_home)
    version = _plugin_version(payload_root)
    validated = _validate_context(
        payload_root,
        context,
        args.expected_marketplace_id,
        durable_home,
        expected_payload_root=payload_root,
    )
    environment = _cell_environment(validated, context, args.expected_marketplace_id)
    plugin_root = Path(str(validated["pluginRoot"]))
    with _installation_lock(plugin_root) as lock_token:
        status = _installation_status(
            payload_root,
            payload_root,
            context,
            args.expected_marketplace_id,
            durable_home,
        )
        validated = _validate_context(
            payload_root,
            context,
            args.expected_marketplace_id,
            durable_home,
            expected_payload_root=payload_root,
        )
        environment = _cell_environment(
            validated, context, args.expected_marketplace_id
        )
        pending = _load_selection_transaction(
            plugin_root,
            context,
            args.expected_marketplace_id,
        )
        if pending is not None:
            result = _resume_selection_transaction(
                payload_root,
                context,
                args.expected_marketplace_id,
                durable_home,
                validated,
                lock_token,
                pending,
            )
            result["provisioned"] = False
            return result
        mode = _governance_mode(
            status,
            operation="bootstrap",
            allow_deactivation_existing=True,
        )
        if _configured_role(environment) is None:
            if os.environ.get("AGENT_INDEX_CELL_NO_START") != "1":
                _retire_owned_instances(
                    validated,
                    environment["AGENT_INDEX_INSTALLATION_ID"],
                )
            return {"status": "dormant", "provisioned": False}
        manifest = _load_manifest(
            plugin_root / "deploy-manifest.json",
            plugin_root,
            context,
            args.expected_marketplace_id,
        )
        if mode == "deactivation-required":
            return {"status": "deactivation-required", "provisioned": False}
        if manifest is not None:
            source = manifest["source"]
            runtime_version = str(manifest["runtime"]["version"])
            if (
                source["version"] == version
                and _paths_equal(source["path"], payload_root)
            ):
                try:
                    _selected_runtime(
                        payload_root,
                        validated,
                        context,
                        args.expected_marketplace_id,
                        durable_home,
                    )
                except CellProfileMismatch:
                    pass
                else:
                    environment = _cell_environment(
                        validated,
                        context,
                        args.expected_marketplace_id,
                        runtime_version,
                    )
                    service_launcher, command_launcher = _write_launchers(
                        validated,
                        context,
                        args.expected_marketplace_id,
                        payload_root,
                        version,
                        runtime_version,
                    )
                    _reconcile_service(
                        validated,
                        service_launcher,
                        command_launcher,
                        runtime_version,
                        environment,
                        lock_token,
                    )
                    return {
                        "status": "ready",
                        "provisioned": False,
                        "runtimeVersion": runtime_version,
                    }
        result = _provision_locked(
            payload_root,
            payload_root,
            context,
            args.expected_marketplace_id,
            durable_home,
            version,
            validated,
            lock_token,
        )
        result["provisioned"] = True
        return result


def recover(args: argparse.Namespace, payload_root: Path) -> dict[str, Any]:
    return service_ensure(args, payload_root)


@contextmanager
def _immediate_lock(path: Path) -> Iterator[str | None]:
    path = _absolute_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_directory(path.parent, "immediate lock parent")
    owner = path / "owner.json"
    token = uuid.uuid4().hex
    acquired = False
    for _attempt in range(2):
        try:
            _publish_owned_lock(
                path,
                {
                    "schema": "copilot-extensions.agent-index.immediate-lock",
                    "version": 1,
                    "pid": os.getpid(),
                    "token": token,
                    "createdAt": _utc_now(),
                },
            )
            acquired = True
            break
        except FileExistsError:
            pid, incumbent_token, observed_raw = _lock_owner_observation(owner)
            if (
                type(pid) is not int
                or not isinstance(incumbent_token, str)
                or not incumbent_token
                or observed_raw is None
                or _pid_alive(pid)
            ):
                break
            tombstone = path.with_name(
                f".{path.name}.stale.{os.getpid()}.{uuid.uuid4().hex}"
            )
            try:
                os.rename(path, tombstone)
            except OSError:
                break
            moved_pid, moved_token, moved_raw = _lock_owner_observation(
                tombstone / "owner.json"
            )
            if (
                moved_pid != pid
                or moved_token != incumbent_token
                or moved_raw != observed_raw
                or _pid_alive(moved_pid or 0)
            ):
                _restore_moved_lock(path, tombstone)
                break
            try:
                shutil.rmtree(tombstone)
            except OSError:
                pass
    try:
        yield token if acquired else None
    finally:
        if acquired:
            try:
                incumbent = _read_json(owner)
                if (
                    incumbent.get("pid") == os.getpid()
                    and incumbent.get("token") == token
                ):
                    tombstone = path.with_name(
                        f".{path.name}.release.{os.getpid()}.{uuid.uuid4().hex}"
                    )
                    os.rename(path, tombstone)
                    moved_pid, moved_token, _raw = _lock_owner_observation(
                        tombstone / "owner.json"
                    )
                    if moved_pid == os.getpid() and moved_token == token:
                        shutil.rmtree(tombstone)
                    else:
                        _restore_moved_lock(path, tombstone)
            except (CellError, OSError):
                pass


def _live_lock_owner(plugin_root: Path) -> bool:
    owner = plugin_root / ".payload-provision.lock.d" / "owner.json"
    try:
        value = _read_json(owner)
        return type(value.get("pid")) is int and _pid_alive(value["pid"])
    except CellError:
        return False


def _ensure_worker_receipt(
    validated: dict[str, Any],
) -> Path:
    return Path(str(validated["runRoot"])) / "service-ensure-worker.json"


def _ensure_worker_completion_receipt(
    validated: dict[str, Any],
    worker_token: str,
) -> Path:
    if re.fullmatch(r"[0-9a-f]{64}", worker_token) is None:
        raise CellError("service ensure worker token is malformed")
    token_id = hashlib.sha256(worker_token.encode("ascii")).hexdigest()[:24]
    return (
        Path(str(validated["runRoot"]))
        / "service-ensure-completions"
        / f"{token_id}.json"
    )


def _live_ensure_worker(
    receipt: dict[str, Any],
    context: Path,
) -> bool:
    pid = receipt.get("pid")
    birth = receipt.get("processBirth")
    return (
        receipt.get("schema") == ENSURE_WORKER_SCHEMA
        and receipt.get("version") == 2
        and type(pid) is int
        and isinstance(birth, str)
        and bool(birth)
        and isinstance(receipt.get("workerToken"), str)
        and re.fullmatch(r"[0-9a-f]{64}", receipt["workerToken"]) is not None
        and isinstance(receipt.get("context"), str)
        and _paths_equal(receipt["context"], context)
        and _process_birth_identity(pid) == birth
    )


def _clear_ensure_worker_receipt(
    receipt_path: Path,
    *,
    pid: int,
    process_birth: str,
    worker_token: str,
) -> None:
    try:
        current = _read_json(receipt_path)
    except CellError:
        return
    if (
        current.get("schema") == ENSURE_WORKER_SCHEMA
        and current.get("version") == 2
        and current.get("pid") == pid
        and current.get("processBirth") == process_birth
        and current.get("workerToken") == worker_token
    ):
        receipt_path.unlink()


def service_ensure_worker(
    args: argparse.Namespace,
    payload_root: Path,
) -> dict[str, Any]:
    context = _absolute_path(args.context)
    durable_home = _durable_home(context, args.durable_home)
    validated = _validate_context(
        payload_root,
        context,
        args.expected_marketplace_id,
        durable_home,
        expected_payload_root=payload_root,
    )
    receipt_path = _ensure_worker_receipt(validated)
    completion_path = _ensure_worker_completion_receipt(
        validated,
        args.worker_token,
    )
    process_birth = _process_birth_identity(os.getpid())
    if process_birth is None:
        raise CellError("service ensure worker birth identity is unavailable")
    deadline = time.monotonic() + 5.0
    while True:
        try:
            receipt = _read_json(receipt_path)
        except CellError:
            receipt = {}
        if (
            _live_ensure_worker(receipt, context)
            and receipt.get("pid") == os.getpid()
            and receipt.get("processBirth") == process_birth
            and receipt.get("workerToken") == args.worker_token
        ):
            break
        if time.monotonic() >= deadline:
            raise CellError("service ensure worker receipt was not published")
        time.sleep(0.02)
    try:
        result = service_ensure(args, payload_root)
        _atomic_json(
            completion_path,
            {
                "schema": ENSURE_WORKER_COMPLETION_SCHEMA,
                "version": 1,
                "pid": os.getpid(),
                "processBirth": process_birth,
                "workerToken": args.worker_token,
                "context": _normalized_path(context),
                "outcome": "succeeded",
                "result": result,
                "completedAt": _utc_now(),
            },
        )
        return result
    except BaseException as exc:
        _atomic_json(
            completion_path,
            {
                "schema": ENSURE_WORKER_COMPLETION_SCHEMA,
                "version": 1,
                "pid": os.getpid(),
                "processBirth": process_birth,
                "workerToken": args.worker_token,
                "context": _normalized_path(context),
                "outcome": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "completedAt": _utc_now(),
            },
        )
        raise
    finally:
        _clear_ensure_worker_receipt(
            receipt_path,
            pid=os.getpid(),
            process_birth=process_birth,
            worker_token=args.worker_token,
        )


def service_ensure_kick(
    args: argparse.Namespace,
    payload_root: Path,
) -> dict[str, Any]:
    context = _absolute_path(args.context)
    durable_home = _durable_home(context, args.durable_home)
    validated = _validate_context(
        payload_root,
        context,
        args.expected_marketplace_id,
        durable_home,
        expected_payload_root=payload_root,
    )
    plugin_root = Path(str(validated["pluginRoot"]))
    run_root = Path(str(validated["runRoot"]))
    receipt = _ensure_worker_receipt(validated)
    lock = run_root / ".service-ensure-kick.lock.d"
    with _immediate_lock(lock) as token:
        if token is None:
            return {"status": "coalesced", "started": False}
        if _live_lock_owner(plugin_root):
            return {"status": "lock-busy", "started": False}
        try:
            incumbent = _read_json(receipt)
            incumbent_pid = incumbent.get("pid")
            if _live_ensure_worker(incumbent, context):
                incumbent_token = str(incumbent["workerToken"])
                return {
                    "status": "coalesced",
                    "started": False,
                    "pid": incumbent_pid,
                    "processBirth": incumbent["processBirth"],
                    "workerToken": incumbent_token,
                    "receipt": str(receipt),
                    "completionReceipt": str(
                        _ensure_worker_completion_receipt(
                            validated,
                            incumbent_token,
                        )
                    ),
                }
        except CellError:
            pass
        worker_token = uuid.uuid4().hex + uuid.uuid4().hex
        command = [
            sys.executable,
            "-I",
            "-X",
            "utf8",
            str(Path(__file__).resolve()),
            "service-ensure-worker",
            "--context",
            str(context),
            "--expected-marketplace-id",
            args.expected_marketplace_id,
            "--durable-home",
            str(durable_home),
            "--worker-token",
            worker_token,
        ]
        environment = _isolated_environment()
        environment.pop(LOCK_TOKEN_ENV, None)
        environment.pop(LOCK_ROOT_ENV, None)
        log_path = Path(str(validated["logsRoot"])) / "service-ensure.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_stream = log_path.open("ab")
        try:
            kwargs: dict[str, Any] = {
                "env": environment,
                "cwd": str(payload_root),
                "stdin": subprocess.DEVNULL,
                "stdout": log_stream,
                "stderr": log_stream,
            }
            if os.name == "nt":
                kwargs["creationflags"] = (
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "DETACHED_PROCESS", 0)
                )
            else:
                kwargs["start_new_session"] = True
            process = subprocess.Popen(command, **kwargs)
        finally:
            log_stream.close()
        process_birth = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            process_birth = _process_birth_identity(process.pid)
            if process_birth is not None:
                break
            if process.poll() is not None:
                raise CellError(
                    "service ensure worker exited before its ownership "
                    "receipt could be published"
                )
            time.sleep(0.01)
        if process_birth is None:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            raise CellError("service ensure worker birth identity could not be recorded")
        _atomic_json(
            receipt,
            {
                "schema": ENSURE_WORKER_SCHEMA,
                "version": 2,
                "pid": process.pid,
                "processBirth": process_birth,
                "workerToken": worker_token,
                "context": _normalized_path(context),
                "startedAt": _utc_now(),
            },
        )
        return {
            "status": "started",
            "started": True,
            "pid": process.pid,
            "processBirth": process_birth,
            "workerToken": worker_token,
            "receipt": str(receipt),
            "completionReceipt": str(
                _ensure_worker_completion_receipt(validated, worker_token)
            ),
        }


def launch_validate(args: argparse.Namespace, payload_root: Path) -> dict[str, Any]:
    context = _absolute_path(args.context)
    durable_home = _durable_home(context, args.durable_home)
    management_version = _plugin_version(payload_root)
    validated = _validate_context(
        payload_root,
        context,
        args.expected_marketplace_id,
        durable_home,
        expected_payload_root=payload_root,
    )
    plugin_root = Path(str(validated["pluginRoot"]))
    reentry_token, lock_reentry, transaction_id = _internal_lock_reentry(
        payload_root,
        plugin_root,
        context,
        args.expected_marketplace_id,
        args.command,
    )
    with _installation_lock(plugin_root, reentry_token=reentry_token):
        status = _installation_status(
            payload_root,
            payload_root,
            context,
            args.expected_marketplace_id,
            durable_home,
        )
        mode = _governance_mode(
            status,
            operation="runtime launch",
            allow_deactivation_existing=True,
        )
        validated = _validate_context(
            payload_root,
            context,
            args.expected_marketplace_id,
            durable_home,
            expected_payload_root=payload_root,
        )
        pending = _load_selection_transaction(
            plugin_root,
            context,
            args.expected_marketplace_id,
        )
        if pending is not None and pending.get("id") != transaction_id:
            raise CellError(
                "selection transaction is pending; runtime dispatch is blocked "
                "until service reconciliation completes"
            )
        manifest, interpreter = _selected_runtime(
            payload_root,
            validated,
            context,
            args.expected_marketplace_id,
            durable_home,
        )
        if manifest is None or interpreter is None:
            return {
                "status": "absent",
                "governance": mode,
                "interpreter": None,
                "lockReentry": lock_reentry,
            }
        runtime_version = str(manifest["runtime"]["version"])
        _validate_launcher_artifacts(
            validated,
            context,
            args.expected_marketplace_id,
            payload_root,
            management_version,
            runtime_version,
        )
        if (
            mode == "deactivation-required"
            and args.command in SERVICE_MUTATING_COMMANDS
        ):
            raise CellError(
                "deactivation-pending installation cannot configure, start, "
                "restart, or deploy a service"
            )
        return {
            "status": "ready",
            "governance": mode,
            "runtimeVersion": runtime_version,
            "interpreter": str(interpreter),
            "lockReentry": lock_reentry,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in (
        "cell-provision",
        "slot-cutover",
        "cell-recover",
        "service-ensure",
        "service-ensure-worker",
        "service-ensure-kick",
        "bootstrap",
        "launch-validate",
        "governance-check",
    ):
        child = subparsers.add_parser(action)
        child.add_argument("--context", required=True)
        child.add_argument("--expected-marketplace-id", required=True)
        child.add_argument("--durable-home")
        if action == "cell-provision":
            child.add_argument("--origin-payload-root")
        elif action == "slot-cutover":
            child.add_argument("--expected-namespace-generation", required=True)
            child.add_argument("--expected-install-generation", required=True)
            child.add_argument("--target-payload-root", required=True)
            child.add_argument("--target-payload-version", required=True)
            child.add_argument("--target-snapshot-id", required=True)
            child.add_argument("--target-runtime-version", required=True)
            current = child.add_mutually_exclusive_group(required=True)
            current.add_argument("--expected-current-version")
            current.add_argument("--expect-current-absent", action="store_true")
        elif action == "launch-validate":
            child.add_argument("--command", required=True)
        elif action == "service-ensure-worker":
            child.add_argument("--worker-token", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload_root = _absolute_path(Path(__file__).parent.parent)
    try:
        if args.action in {"bootstrap", "service-ensure-kick", "service-ensure-worker"}:
            print(json.dumps({"status": "dispatch-managed", "started": False}))
            return 0
        if args.action in {
            "cell-provision", "slot-cutover", "cell-recover", "service-ensure"
        }:
            context = _absolute_path(args.context)
            validated = _validate_context(
                payload_root,
                context,
                args.expected_marketplace_id,
                _durable_home(context, args.durable_home),
                expected_payload_root=_absolute_path(
                    getattr(args, "origin_payload_root", None) or payload_root
                ),
            )
            environment = _cell_environment(
                validated, context, args.expected_marketplace_id
            )
            if _configured_role(environment) == "host":
                raise CellError(
                    "host service lifecycle is dispatch-managed; this "
                    "namespaced installation context does not support managed hosts"
                )
        if args.action == "cell-provision":
            value = provision(args, payload_root)
        elif args.action == "slot-cutover":
            value = cutover(args, payload_root)
        elif args.action == "cell-recover":
            value = recover(args, payload_root)
        elif args.action == "service-ensure":
            value = service_ensure(args, payload_root)
        elif args.action == "service-ensure-worker":
            value = service_ensure_worker(args, payload_root)
        elif args.action == "service-ensure-kick":
            value = service_ensure_kick(args, payload_root)
        elif args.action == "launch-validate":
            value = launch_validate(args, payload_root)
        elif args.action == "governance-check":
            value = governance_check(args, payload_root)
        else:
            value = bootstrap(args, payload_root)
    except CellProcessExit as exc:
        print(f"[agent-index] {exc}", file=sys.stderr)
        return exc.exit_code
    except CellError as exc:
        print(f"[agent-index] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(value, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
