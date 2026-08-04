"""Persisted CodeSpace -> owning gh account bindings.

Resolvers normally discover the owning gh account by merging
``gh codespace list`` across mapped accounts. This non-TTL store records the
account at creation/adoption time so per-name operations can remain pinned to
the owning account even if the ambient ``gh`` account changes later.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from .config import RUNTIME_DIR, ensure_runtime_dir

log = logging.getLogger("agent-codespaces")

BINDINGS_FILE = RUNTIME_DIR / "account-bindings.json"
_LOCK_FILE = RUNTIME_DIR / "account-bindings.lock"


@dataclass
class AccountBinding:
    """The owning gh account for one CodeSpace."""

    codespace: str
    account: str
    bound_at: float
    repo: str = ""


@contextmanager
def _binding_lock(timeout: float = 10.0, poll: float = 0.05) -> Iterator[None]:
    """Cross-platform exclusive lock via O_CREAT|O_EXCL lock file.

    Mirrors ``status._status_lock`` (including stale-lock recovery) so the
    non-TTL stores behave identically under contention.
    """
    ensure_runtime_dir()
    deadline = time.monotonic() + timeout
    fd = None
    while True:
        try:
            fd = os.open(str(_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                try:
                    age = time.time() - _LOCK_FILE.stat().st_mtime
                    if age > timeout * 3:
                        _LOCK_FILE.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                raise RuntimeError(
                    "Could not acquire account binding lock (held by another process)"
                ) from None
            time.sleep(poll)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        _LOCK_FILE.unlink(missing_ok=True)


def _read() -> dict[str, AccountBinding]:
    """Read the binding file -> {codespace: AccountBinding}. {} if absent/corrupt.

    Tolerant of unknown keys (forward-compat) by filtering each record to the
    dataclass fields, so a newer writer's extra fields never drop a record.
    """
    if not BINDINGS_FILE.exists():
        return {}
    try:
        raw = json.loads(BINDINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("account-bindings.json unreadable; treating as empty")
        return {}
    known = {"codespace", "account", "bound_at", "repo"}
    out: dict[str, AccountBinding] = {}
    for name, rec in (raw or {}).items():
        if not isinstance(rec, dict):
            continue
        fields = {k: v for k, v in rec.items() if k in known}
        fields.setdefault("codespace", name)
        try:
            out[name] = AccountBinding(**fields)
        except TypeError:
            continue
    return out


def _write(records: dict[str, AccountBinding]) -> None:
    """Atomically write the binding file."""
    ensure_runtime_dir()
    tmp = BINDINGS_FILE.with_suffix(".json.tmp")
    payload = {name: asdict(rec) for name, rec in records.items()}
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, BINDINGS_FILE)


def bind(codespace: str, account: str, repo: str = "") -> AccountBinding | None:
    """Persist the owning gh ``account`` for ``codespace``."""
    if not codespace or not account:
        return None
    with _binding_lock():
        records = _read()
        rec = AccountBinding(
            codespace=codespace,
            account=account,
            repo=repo,
            bound_at=time.time(),
        )
        records[codespace] = rec
        _write(records)
        log.info("CodeSpace %s bound to gh account %s", codespace, account)
        return rec


def bound_account(codespace: str) -> str | None:
    """Return the bound gh account for ``codespace``, or None."""
    try:
        with _binding_lock():
            rec = _read().get(codespace)
        return (rec.account or None) if rec else None
    except Exception:
        return None


def unbind(codespace: str) -> bool:
    """Remove a CodeSpace account binding. Returns True if removed."""
    with _binding_lock():
        records = _read()
        if codespace not in records:
            return False
        del records[codespace]
        _write(records)
        log.info("CodeSpace %s account binding cleared", codespace)
        return True


def list_bindings() -> list[AccountBinding]:
    """Return all CodeSpace account bindings."""
    with _binding_lock():
        return list(_read().values())


def bound_accounts() -> tuple[str, ...]:
    """Return distinct non-empty bound gh logins."""
    try:
        seen: list[str] = []
        with _binding_lock():
            records = _read()
        for rec in records.values():
            if rec.account and rec.account not in seen:
                seen.append(rec.account)
        return tuple(seen)
    except Exception:
        return ()
