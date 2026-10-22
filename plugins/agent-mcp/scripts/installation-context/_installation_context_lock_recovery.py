# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator


def _lock_path(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    for component in (*reversed(path.parents), path):
        if os.path.lexists(component) and _is_link_or_junction(component):
            _fail(f"Installation lock path may not traverse links or reparse points: '{component}'.")
    return path


@contextmanager
def _lock_mutation_guard(path: Path, *, deadline: float) -> Iterator[None]:
    """Kernel exclusion for new writers and repairs; never unlink the guard."""
    path = _lock_path(path)
    guard = path.with_name(path.name + ".mutation-guard")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(guard, flags, 0o600)
    acquired = False
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_link_or_junction(guard)
            or _stat_identity(opened) != _stat_identity(guard.lstat())
            or opened.st_nlink != 1
        ):
            _fail(f"Installation lock mutation guard is not an ordinary owned file: '{guard}'.")
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(descriptor, 1 << 30, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    _fail(f"Installation lock '{path}' mutation guard remained busy.")
                time.sleep(LOCK_POLL_SECONDS)
        if _stat_identity(opened) != _stat_identity(guard.lstat()):
            _fail(f"Installation lock mutation guard changed: '{guard}'.")
        yield
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    os.lseek(descriptor, 1 << 30, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _lock_owner_state(owner: Mapping[str, Any]) -> str:
    if owner["host"] != socket.gethostname().split(".", 1)[0].casefold():
        return "foreign"
    if not _pid_is_live(owner["pid"]):
        return "stale"
    birth = owner.get("processIdentity")
    if birth is not None and (
        not isinstance(birth, str)
        or re.fullmatch(
            r"(?:windows:[1-9][0-9]*|linux:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}:[1-9][0-9]*)",
            birth,
        ) is None
    ):
        _fail("Installation lock process identity is invalid.")
    if birth is None:
        return "live-legacy"
    current = _runtime_publication_process_identity(owner["pid"])
    if current is None:
        return "unknown"
    if birth.split(":", 1)[0] != current.split(":", 1)[0]:
        return "unknown"
    return "live" if birth == current else "stale"


def _lock_snapshot(
    lock: _DirectoryLock, *, allow_incomplete: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    path = _lock_path(lock.path)
    if not os.path.lexists(path):
        return {"state": "absent", "path": str(path)}, None
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        _fail(f"Installation lock must be an ordinary directory: '{path}'.")
    entries = sorted(item.name for item in path.iterdir())
    if entries not in ([], ["owner.json"]):
        _fail(
            f"Installation lock '{path}' has unexpected contents; retain them for manual diagnosis."
        )
    if not entries:
        if not allow_incomplete:
            _fail(f"Installation lock '{path}' has no owner receipt; approved manual recovery is required.")
        return {
            "state": "manual-assessment-required", "path": str(path),
            "evidence": {"directoryIdentity": list(_stat_identity(info)), "ownerSha256": None},
        }, None
    if lock.owner_path.lstat().st_size > 65_536:
        _fail("Installation lock owner exceeds the 64 KiB recovery limit.")
    data, owner_info = _read_regular_file(
        lock.owner_path, label="Installation lock owner", require_stable_identity=True,
    )
    try:
        parsed = json.loads(data, object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, UnicodeError):
        if not allow_incomplete:
            _fail(f"Installation lock '{path}' has an unreadable owner; approved manual recovery is required.")
        parsed = None
    incomplete = not isinstance(parsed, dict) or not {
        "schema", "version", "kind", "marketplaceId", "pluginId", "token", "host", "pid",
    }.issubset(parsed) or not isinstance(parsed.get("token"), str) or not parsed["token"]
    if incomplete:
        if not allow_incomplete:
            _fail(f"Installation lock '{path}' has an incomplete owner; approved manual recovery is required.")
        if isinstance(parsed, dict):
            scope = {
                "schema": LOCK_SCHEMA, "version": LOCK_VERSION, "kind": lock.kind,
                "marketplaceId": lock.marketplace_id, "pluginId": lock.plugin_id or "",
            }
            if any(key in parsed and parsed[key] != value for key, value in scope.items()):
                _fail("An incomplete installation lock has invalid scope.")
            if parsed.get("host", lock.host) != lock.host:
                _fail("An incomplete foreign installation lock may not be bypassed.")
            if "pid" in parsed and (
                type(parsed["pid"]) is not int or _pid_is_live(parsed["pid"])
            ):
                _fail("An incomplete installation lock may still have a live owner.")
        owner = None
    else:
        owner = dict(lock._owner())
    if (owner is not None and parsed != owner) or data != _read_regular_file_bytes(
        lock.owner_path, label="Installation lock owner", require_stable_identity=True,
    ):
        raise _FileIdentityChanged(f"Installation lock '{path}' owner changed during inspection.")
    evidence = {
        "directoryIdentity": list(_stat_identity(info)),
        "ownerIdentity": list(_stat_identity(owner_info)),
        "ownerSha256": hashlib.sha256(data).hexdigest(),
    }
    if _stat_identity(path.lstat()) != _stat_identity(info):
        raise _FileIdentityChanged(f"Installation lock '{path}' directory changed during inspection.")
    return {
        "state": _lock_owner_state(owner) if owner else "manual-assessment-required", "path": str(path),
        "kind": lock.kind, "marketplaceId": lock.marketplace_id,
        "pluginId": lock.plugin_id or "", "host": owner["host"] if owner else None,
        "pid": owner["pid"] if owner else None,
        "evidence": evidence,
    }, owner


def inspect_installation_lock(
    path: str | os.PathLike[str], *, kind: str, marketplace_id: str,
    plugin_id: str | None = None, allow_incomplete: bool = False,
) -> dict[str, Any]:
    lock = _DirectoryLock(_lock_path(Path(path)), kind=kind, marketplace_id=marketplace_id,
                          plugin_id=plugin_id)
    snapshot, _owner = _lock_snapshot(lock, allow_incomplete=allow_incomplete)
    if "evidence" in snapshot:
        evidence = json.dumps(snapshot["evidence"], sort_keys=True, separators=(",", ":"))
        snapshot["expectedEvidence"] = hashlib.sha256(evidence.encode()).hexdigest()
    return {"schema": "copilot-extensions.lock-inspection", "version": 1, **snapshot}


def _sync_lock_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _quarantine_stale_lock(
    lock: _DirectoryLock, expected: Mapping[str, Any], *, inactive_owner_evidence: str = "",
) -> dict[str, Any]:
    current, _owner = _lock_snapshot(lock, allow_incomplete=bool(inactive_owner_evidence))
    allowed = {"stale", "manual-assessment-required"} if inactive_owner_evidence else {"stale"}
    if current != dict(expected) or current["state"] not in allowed:
        _fail(f"Installation lock '{lock.path}' changed or is not provably stale.")
    backup = lock.path.with_name(lock.path.name + ".recovery-" + secrets.token_hex(8))
    backup.mkdir()
    _sync_lock_directory(lock.path.parent)
    receipt = {
        "schema": "copilot-extensions.lock-recovery", "version": 1,
        "state": "prepared", "createdAt": _utc_now(),
        "originalPath": str(lock.path), "backupPath": str(backup / "lock"),
        "inspection": current,
        "inactiveOwnerEvidence": inactive_owner_evidence or None,
        "scope": "coordination-only; retry and verify the original operation",
    }
    _atomic_write_json(backup / "recovery.json", receipt)
    checked, _owner = _lock_snapshot(lock, allow_incomplete=bool(inactive_owner_evidence))
    if checked != current:
        _fail(f"Installation lock '{lock.path}' changed before quarantine; no lock was moved.")
    os.rename(lock.path, backup / "lock")
    _sync_lock_directory(backup)
    _sync_lock_directory(lock.path.parent)
    moved = _DirectoryLock(backup / "lock", kind=lock.kind, marketplace_id=lock.marketplace_id,
                           plugin_id=lock.plugin_id)
    captured, _owner = _lock_snapshot(moved, allow_incomplete=bool(inactive_owner_evidence))
    if captured.get("evidence") != current["evidence"] or captured["state"] not in allowed:
        _fail(
            f"Installation lock changed during quarantine; recovery is required at '{backup}'. "
            "Do not activate or overwrite a replacement lock."
        )
    receipt["state"] = "quarantined"
    _atomic_write_json(backup / "recovery.json", receipt)
    return receipt


def repair_installation_lock(
    path: str | os.PathLike[str], *, kind: str, marketplace_id: str,
    plugin_id: str | None = None, expected_evidence: str, inactive_owner_evidence: str = "",
) -> dict[str, Any]:
    lock = _DirectoryLock(_lock_path(Path(path)), kind=kind, marketplace_id=marketplace_id,
                          plugin_id=plugin_id)
    with _lock_mutation_guard(lock.path, deadline=time.monotonic() + 5):
        if not isinstance(inactive_owner_evidence, str) or len(inactive_owner_evidence) > 8192:
            _fail("Manual recovery evidence must be bounded text.")
        if not inactive_owner_evidence.strip():
            _fail(
                "Independent inactivity assessment is required for the owner and its descendants; "
                "supply --inactive-owner-evidence after verification."
            )
        current, _owner = _lock_snapshot(lock, allow_incomplete=bool(inactive_owner_evidence.strip()))
        evidence = json.dumps(current.get("evidence"), sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(evidence.encode()).hexdigest() != expected_evidence:
            _fail("Installation lock changed since inspection; inspect again before repair.")
        return _quarantine_stale_lock(
            lock, current, inactive_owner_evidence=inactive_owner_evidence.strip(),
        )
