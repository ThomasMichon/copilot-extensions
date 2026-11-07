"""Durable move custody shared by the existing launch and record writers."""

from __future__ import annotations

import json
import os
import re
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_owners: ContextVar[frozenset[str]] = ContextVar("checkout_relocation_owners", default=frozenset())


def require_writer_generation() -> None:
    from . import locks, status_monitor_runtime

    path = status_monitor_runtime._monitor_lock_path()
    if not path.exists():
        return
    data = locks.read_lock(path)
    if (
        path.is_symlink() or not isinstance(data, dict)
        or type(data.get("checkout_relocation_writer_generation")) is not int
        or data["checkout_relocation_writer_generation"] != 1
    ):
        raise ValueError("published resident writer does not support checkout relocation; use its owned activation path")


def journal_path(ledger: Path) -> Path:
    return ledger.with_suffix(".relocation.json")


def read(ledger: Path) -> dict | None:
    path = journal_path(ledger)
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 64 * 1024:
        raise ValueError("relocation journal is redirected or oversized")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != "agent-worktrees.checkout-relocation.v1":
        raise ValueError("unsupported relocation journal")
    if (
        data.get("phase") not in {"prepared", "copying", "copied", "published", "git-repaired", "rebound", "retiring", "complete", "cancelling", "cancelled"}
        or not isinstance(data.get("identity"), dict)
        or any(not isinstance(data.get(key), str) for key in ("source", "destination", "stage", "fingerprint"))
        or not re.fullmatch(r"[a-f0-9]{64}", data["fingerprint"])
        or not isinstance(data.get("permissions"), dict)
        or set(data["permissions"]) != {"trust", "permissions"}
        or type(data["permissions"]["trust"]) not in (bool, type(None))
        or not isinstance(data["permissions"]["permissions"], (dict, type(None)))
        or not isinstance(data.get("head"), str) or re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", data["head"]) is None
        or not isinstance(data.get("ledger_sha256"), str) or re.fullmatch(r"[a-f0-9]{64}", data["ledger_sha256"]) is None
    ):
        raise ValueError("invalid relocation recovery journal")
    source, destination, stage = (Path(data[key]) for key in ("source", "destination", "stage"))
    identity = data["identity"].get("worktree_id")
    if (
        not all(path.is_absolute() and ".." not in path.parts for path in (source, destination, stage))
        or identity != ledger.stem or source.name != identity or destination.name != identity
        or stage.parent != destination.parent
        or not re.fullmatch(re.escape(destination.name) + r"\.relocating-[a-f0-9]{16}", stage.name)
    ):
        raise ValueError("relocation recovery location conflicts with its ledger")
    if data["phase"] not in {"complete", "cancelled"} and any(
        path.absolute() != path.resolve() for path in (source, destination, stage)
    ):
        raise ValueError("active relocation location is redirected")
    return data


def write(ledger: Path, data: dict) -> None:
    path = journal_path(ledger)
    if path.is_symlink():
        raise ValueError("relocation journal is redirected")
    staging = path.with_suffix(".writing-" + secrets.token_hex(8))
    try:
        with staging.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        from .checkout_durability import replace

        replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)


@contextmanager
def owner(ledger: Path):
    key = str(ledger.resolve())
    token = _owners.set(_owners.get() | {key})
    try:
        yield
    finally:
        _owners.reset(token)


def require_idle(ledger: Path) -> None:
    if str(ledger.resolve()) in _owners.get():
        return
    data = read(ledger)
    if data is not None and data.get("phase") not in {"complete", "cancelled"}:
        raise ValueError("checkout relocation is unfinished; retry the original move before launch or mutation")


def preserve(record, current, ledger: Path) -> None:
    require_idle(ledger)
    if current is None or str(ledger.resolve()) in _owners.get():
        return
    data = read(ledger)
    if data is not None and record.worktree_path != current.worktree_path:
        raise ValueError("stale checkout location; reload the relocated worktree record")


def require_previous_owner(data: dict) -> None:
    from agent_procutil._daemon_instance_identity import identity_state

    caller = data.get("caller")
    if not isinstance(caller, dict) or type(caller.get("pid")) is not int or not isinstance(caller.get("birth"), str):
        raise ValueError("relocation caller custody is unavailable")
    if caller["pid"] != os.getpid() and identity_state(caller["pid"], caller["birth"]) not in {"dead", "reused"}:
        raise ValueError("the original relocation caller is still active or unobservable")


def caller() -> dict:
    from agent_procutil._daemon_instance_identity import birth_identity

    return {"pid": os.getpid(), "birth": birth_identity(os.getpid())}


def archive_terminal(ledger: Path, expected: str) -> None:
    import hashlib

    path = journal_path(ledger)
    raw = path.read_bytes()
    data = read(ledger)
    if data is None or data["phase"] not in {"complete", "cancelled"} or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("the preceding relocation is not the inspected terminal operation")
    directory = ledger.parent / "checkout-relocations" / ledger.stem
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("relocation history is redirected")
    history = directory / f"{expected}.json"
    if history.exists():
        if history.is_symlink() or history.read_bytes() != raw:
            raise ValueError("relocation history conflicts with the original receipt")
    else:
        staging = history.with_suffix(".archiving-" + secrets.token_hex(8))
        try:
            with staging.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            from .checkout_durability import replace

            replace(staging, history, existing=False)
        finally:
            staging.unlink(missing_ok=True)
    from .checkout_durability import directory as synchronize_directory, files

    files([history])
    synchronize_directory(directory.parent)
    synchronize_directory(ledger.parent)
