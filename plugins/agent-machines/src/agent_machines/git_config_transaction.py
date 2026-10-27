"""Preview-bound, file-scoped Git configuration edits and exact rollback."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs

MAX_BYTES = 1024 * 1024
_KEY = re.compile(r"([A-Za-z][A-Za-z0-9-]*)\.(?:(.+)\.)?([A-Za-z][A-Za-z0-9-]*)")
_SHA = re.compile(r"[0-9a-f]{64}")


class TransactionError(RuntimeError):
    def __init__(self, message: str, *, changed: bool = False, receipt: Path | None = None):
        super().__init__(message)
        self.changed = changed
        self.receipt = receipt


def validate_entries(entries: Any) -> dict[str, list[str] | None]:
    if not isinstance(entries, list) or not 1 <= len(entries) <= 128:
        raise ValueError("git-config entries must be a nonempty list of at most 128 keys")
    desired: dict[str, list[str] | None] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {"key", "state", "values"}:
            raise ValueError("git-config entries accept only key, state and values")
        key = entry.get("key")
        match = _KEY.fullmatch(key) if isinstance(key, str) else None
        if not match or any(char in key for char in "\0\r\n"):
            raise ValueError("git-config requires a valid section[.subsection].variable key")
        section, subsection, variable = match.groups()
        canonical = ".".join(
            [section.lower(), *([subsection] if subsection is not None else []), variable.lower()]
        )
        if canonical in desired:
            raise ValueError("git-config entries must not repeat a normalized key")
        if variable.lower() in {"password", "token", "secret", "extraheader"}:
            raise ValueError("git-config does not accept credential-bearing keys")
        state = entry.get("state", "present")
        values = entry.get("values")
        if state == "absent" and "values" not in entry:
            desired[canonical] = None
        elif (
            state == "present"
            and isinstance(values, list)
            and 1 <= len(values) <= 64
            and all(isinstance(value, str) and "\0" not in value for value in values)
        ):
            desired[canonical] = list(values)
        else:
            raise ValueError(
                "git-config present entries need ordered string values; absent entries omit values"
            )
    if len(json.dumps(desired).encode("utf-8")) > MAX_BYTES:
        raise ValueError("git-config declarations exceed the size limit")
    return desired


def _sha(data: bytes | None) -> str:
    return hashlib.sha256(b"" if data is None else data).hexdigest()


def _check_directory_chain(path: Path) -> None:
    for directory in (path, *path.parents):
        try:
            info = directory.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise TransactionError("configuration and backup directories must not be links")


def _snapshot(path: Path) -> tuple[bytes | None, tuple[int, ...]]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None, ()
    if (
        not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
        or info.st_size > MAX_BYTES
    ):
        raise TransactionError("configuration must be a bounded regular file, not a link")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        data = stream.read(MAX_BYTES + 1)
        finished = os.fstat(stream.fileno())
    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)
    actual = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_mode)
    final = (
        finished.st_dev,
        finished.st_ino,
        finished.st_size,
        finished.st_mtime_ns,
        finished.st_mode,
    )
    if actual != identity or final != identity or len(data) > MAX_BYTES:
        raise TransactionError("configuration changed while reading")
    return data, identity


def _git(path: Path, *args: str, statuses: tuple[int, ...] = (0,), deadline: float) -> bytes:
    timeout = min(5.0, deadline - time.monotonic())
    if timeout <= 0:
        raise TransactionError("Git configuration planning deadline exceeded")
    executable = shutil.which("git")
    if executable is None:
        raise TransactionError("Git is unavailable")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    try:
        result = subprocess.run(
            [executable, "config", "--file", str(path), "--no-includes", *args],
            cwd=path.parent,
            env=env,
            capture_output=True,
            timeout=timeout,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TransactionError("Git configuration operation is unavailable") from exc
    if result.returncode not in statuses or len(result.stdout) > MAX_BYTES * 2:
        raise TransactionError("Git rejected the configuration operation")
    return result.stdout


def _config_values(raw: bytes) -> dict[bytes, list[bytes | None]]:
    entries: dict[bytes, list[bytes | None]] = {}
    for entry in raw.split(b"\0"):
        if entry:
            key, separator, value = entry.partition(b"\n")
            entries.setdefault(key, []).append(value if separator else None)
    return entries


def _value_changes(key: str, before: list[bytes | None], after: list[bytes]) -> list[str]:
    changes = []
    for prefix, selected in (("-", before), ("+", after)):
        for value in selected:
            rendered = (
                "<valueless>" if value is None
                else json.dumps(value.decode("utf-8", errors="backslashreplace"))
            )
            changes.append(f"{prefix} {key} = {rendered}\n")
    return changes


@dataclass(frozen=True)
class Plan:
    target: Path
    before: bytes | None
    after: bytes | None
    identity: tuple[int, ...]
    intent: str
    changes: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return self.before != self.after

    @property
    def before_sha256(self) -> str:
        return _sha(self.before)

    @property
    def after_sha256(self) -> str:
        return _sha(self.after)

    @property
    def plan_sha256(self) -> str:
        payload = {
            "schema": "agent-machines.git-config-plan/v1",
            "target": str(self.target),
            "before_exists": self.before is not None,
            "before_sha256": self.before_sha256,
            "identity": self.identity,
            "after_exists": self.after is not None,
            "after_sha256": self.after_sha256,
            "intent": self.intent,
        }
        return _sha(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    @property
    def diff(self) -> str:
        if not self.changed:
            return ""
        if self.intent.startswith("rollback:"):
            return f"- sha256:{self.before_sha256}\n+ sha256:{self.after_sha256}\n"
        return "--- declared before\n+++ declared after\n" + "".join(self.changes)

    def summary(self) -> dict[str, Any]:
        return {
            "schema": "agent-machines.git-config-plan/v1",
            "target": str(self.target),
            "changed": self.changed,
            "before_exists": self.before is not None,
            "after_exists": self.after is not None,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "plan_sha256": self.plan_sha256,
            "diff": self.diff,
        }


def preview(target: Path, desired: dict[str, list[str] | None]) -> Plan:
    target = Path(os.path.abspath(target))
    _check_directory_chain(target.parent)
    normalized = validate_entries(
        [
            {"key": key, "state": "absent"} if values is None else {"key": key, "values": values}
            for key, values in desired.items()
        ]
    )
    before, identity = _snapshot(target)
    deadline = time.monotonic() + 5
    with tempfile.TemporaryDirectory(prefix="git-config-plan-") as directory:
        candidate = Path(directory) / "candidate.gitconfig"
        candidate.write_bytes(before or b"")
        existing = _config_values(_git(candidate, "--null", "--list", deadline=deadline))
        changed = False
        changes = []
        for key, values in sorted(normalized.items()):
            current = existing.get(key.encode("utf-8"), [])
            expected = [value.encode("utf-8") for value in values] if values is not None else []
            if current == expected:
                continue
            changed = True
            changes.extend(_value_changes(key, current, expected))
            _git(candidate, "--unset-all", key, statuses=(0, 5), deadline=deadline)
            for value in values or []:
                _git(candidate, "--add", key, value, deadline=deadline)
        after = candidate.read_bytes() if changed else before
        if after is not None and len(after) > MAX_BYTES:
            raise TransactionError("proposed configuration exceeds the size limit")
        _git(candidate, "--null", "--list", deadline=deadline)
    intent = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return Plan(target, before, after, identity, intent, tuple(changes))


def _write_private(path: Path, data: bytes) -> None:
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600),
        "wb",
    ) as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _owns_lock(path: Path, identity: tuple[int, int]) -> bool:
    try:
        current = path.lstat()
    except FileNotFoundError:
        return False
    return (
        stat.S_ISREG(current.st_mode)
        and not getattr(current, "st_file_attributes", 0) & 0x400
        and (current.st_dev, current.st_ino) == identity
    )


def _backup(plan: Plan, root: Path) -> Path:
    _check_directory_chain(root)
    created = []
    for parent in (root, *root.parents):
        if parent.exists():
            break
        created.append(parent)
    root.mkdir(parents=True, exist_ok=True)
    _check_directory_chain(root)
    directory = root / uuid.uuid4().hex
    directory.mkdir(mode=0o700)
    if plan.before is not None:
        _write_private(directory / "before", plan.before)
        if (directory / "before").read_bytes() != plan.before:
            raise TransactionError("backup verification failed")
    receipt = directory / "receipt.json"
    payload = {
        "schema": "agent-machines.git-config-receipt/v1",
        "target": str(plan.target),
        "before_exists": plan.before is not None,
        "before_sha256": plan.before_sha256,
        "after_exists": plan.after is not None,
        "after_sha256": plan.after_sha256,
        "plan_sha256": plan.plan_sha256,
    }
    _write_private(receipt, json.dumps(payload, sort_keys=True).encode("utf-8"))
    _sync_directory(directory)
    _sync_directory(root)
    for parent in created:
        _sync_directory(parent.parent)
    return receipt


def apply(plan: Plan, *, expected_plan_sha256: str, backup_root: Path) -> Path | None:
    if expected_plan_sha256 != plan.plan_sha256:
        raise TransactionError("exact plan consent is required")
    _check_directory_chain(plan.target.parent)
    if _snapshot(plan.target) != (plan.before, plan.identity):
        raise TransactionError("configuration changed since preview")
    if not plan.changed:
        return None
    if plan.identity and not plan.identity[-1] & stat.S_IWUSR:
        raise TransactionError("configuration is read-only")
    lock = plan.target.with_name(plan.target.name + ".lock")
    try:
        fd = os.open(
            lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
        )
    except FileExistsError as exc:
        raise TransactionError("configuration is locked by another writer") from exc
    lock_identity = None
    receipt = None
    committed = False
    try:
        with os.fdopen(fd, "wb") as stream:
            try:
                info = os.fstat(stream.fileno())
            except OSError as error:
                raise TransactionError(
                    "configuration lock identity is unavailable; unconfirmed lock retained"
                ) from error
            lock_identity = (info.st_dev, info.st_ino)
            stream.write(plan.after or b"")
            stream.flush()
            os.fsync(stream.fileno())
        if _snapshot(plan.target) != (plan.before, plan.identity):
            raise TransactionError("configuration changed before commit")
        receipt = _backup(plan, Path(os.path.abspath(backup_root)))
        if _snapshot(plan.target) != (plan.before, plan.identity):
            raise TransactionError("configuration changed while backing up")
        if not _owns_lock(lock, lock_identity):
            raise TransactionError("configuration lock ownership changed")
        if plan.after is None:
            plan.target.unlink()
        else:
            if plan.identity:
                os.chmod(lock, stat.S_IMODE(plan.identity[-1]))
            os.replace(lock, plan.target)
            lock_identity = None
        committed = True
        try:
            _sync_directory(plan.target.parent)
            verified = _snapshot(plan.target)[0] == plan.after
        except (OSError, TransactionError):
            verified = False
        if not verified:
            raise TransactionError("post-write verification failed", changed=True, receipt=receipt)
        return receipt
    finally:
        try:
            if lock_identity is not None and _owns_lock(lock, lock_identity):
                lock.unlink()
        except OSError as error:
            raise TransactionError(
                "transaction lock cleanup could not be confirmed",
                changed=committed,
                receipt=receipt,
            ) from error


def rollback_preview(target: Path, receipt: Path) -> Plan:
    target = Path(os.path.abspath(target))
    _check_directory_chain(receipt.parent)
    _check_directory_chain(target.parent)

    def unique_object(pairs):
        payload = dict(pairs)
        if len(payload) != len(pairs):
            raise ValueError("duplicate receipt fields")
        return payload

    try:
        raw, _ = _snapshot(receipt)
        if raw is None or receipt.name != "receipt.json":
            raise ValueError("missing receipt")
        payload = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(payload, dict):
            raise ValueError("receipt must be an object")
        expected = {
            "schema",
            "target",
            "before_exists",
            "before_sha256",
            "after_exists",
            "after_sha256",
            "plan_sha256",
        }
        if (
            set(payload) != expected
            or payload["schema"] != "agent-machines.git-config-receipt/v1"
            or payload["target"] != str(target)
            or type(payload["before_exists"]) is not bool
            or type(payload["after_exists"]) is not bool
            or not all(
                isinstance(payload[key], str) and _SHA.fullmatch(payload[key])
                for key in ("before_sha256", "after_sha256", "plan_sha256")
            )
        ):
            raise ValueError("invalid receipt")
        original, _ = _snapshot(receipt.parent / "before")
        if (original is not None) != payload["before_exists"] or _sha(original) != payload[
            "before_sha256"
        ]:
            raise ValueError("invalid backup")
    except (OSError, ValueError, TypeError) as exc:
        raise TransactionError("invalid rollback receipt or backup") from exc
    current, identity = _snapshot(target)
    if (current is not None) != payload["after_exists"] or _sha(current) != payload[
        "after_sha256"
    ]:
        raise TransactionError("configuration changed since the recorded transaction")
    return Plan(target, current, original, identity, "rollback:" + _sha(raw))
