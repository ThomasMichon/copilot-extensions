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
def _binding_lock(
    timeout: float = 10.0, poll: float = 0.05, *, stale_after: float | None = None,
) -> Iterator[None]:
    """Cross-platform exclusive lock via O_CREAT|O_EXCL lock file.

    Mirrors ``status._status_lock`` (including stale-lock recovery) so the
    non-TTL stores behave identically under contention. ``stale_after``
    (default ``3 * timeout``) is how old a lock must be to be recovered, so a
    caller with a short deadline never steals a live holder's lock.
    """
    ensure_runtime_dir()
    deadline = time.monotonic() + timeout
    stale = stale_after if stale_after is not None else timeout * 3
    fd = None
    while True:
        try:
            fd = os.open(str(_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                try:
                    age = time.time() - _LOCK_FILE.stat().st_mtime
                    if age > stale:
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


def _read(*, strict: bool = False) -> dict[str, AccountBinding]:
    """Read bindings; legacy calls return {} for absent or corrupt state.

    Unknown keys remain forward-compatible. Strict admission reads distinguish
    absent state from corruption or read failure; legacy readers stay tolerant.
    """
    try:
        raw = json.loads(BINDINGS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        if strict:
            raise RuntimeError("Account binding store is unreadable or corrupt") from exc
        if not isinstance(exc, (OSError, json.JSONDecodeError)):
            raise
        log.warning("account-bindings.json unreadable; treating as empty")
        return {}
    if strict and not isinstance(raw, dict):
        raise RuntimeError("Malformed account binding store")
    known = {"codespace", "account", "bound_at", "repo"}
    out: dict[str, AccountBinding] = {}
    for name, rec in (raw or {}).items():
        if not isinstance(rec, dict):
            if strict:
                raise RuntimeError("Malformed account binding record")
            continue
        fields = {k: v for k, v in rec.items() if k in known}
        fields.setdefault("codespace", name)
        try:
            record = AccountBinding(**fields)
            if strict and (
                not name or record.codespace != name
                or not isinstance(record.account, str) or not record.account
                or not isinstance(record.repo, str)
                or type(record.bound_at) not in (int, float)
            ):
                raise ValueError("Invalid binding fields")
            out[name] = record
        except (TypeError, ValueError) as exc:
            if strict:
                raise RuntimeError("Malformed account binding record") from exc
            continue
    return out


def _write(records: dict[str, AccountBinding]) -> None:
    """Atomically write the binding file."""
    _write_payload({name: asdict(rec) for name, rec in records.items()})


def _write_payload(payload: dict) -> None:
    ensure_runtime_dir()
    tmp = BINDINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, BINDINGS_FILE)


def bind(
    codespace: str, account: str, repo: str = "", *, strict: bool = False,
) -> AccountBinding | None:
    """Persist the owning gh ``account`` for ``codespace``."""
    if not codespace or not account:
        return None
    with _binding_lock():
        records = _read(strict=strict)
        rec = AccountBinding(
            codespace=codespace,
            account=account,
            repo=repo,
            bound_at=time.time(),
        )
        records[codespace] = rec
        if strict:
            try:
                try:
                    payload = json.loads(BINDINGS_FILE.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    payload = {}
                if not isinstance(payload, dict) or not isinstance(payload.get(codespace, {}), dict):
                    raise ValueError("Malformed account binding document")
                payload[codespace] = {**payload.get(codespace, {}), **asdict(rec)}
                _write_payload(payload)
            except (OSError, ValueError, TypeError) as error:
                raise RuntimeError("Account binding update was not confirmed") from error
        else:
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


def bound_repo(codespace: str) -> str | None:
    """Return the ``owner/repo`` recorded with ``codespace``'s binding, or None."""
    try:
        with _binding_lock():
            rec = _read().get(codespace)
        return (rec.repo or None) if rec else None
    except Exception:
        return None


def bound_account_or_raise(codespace: str, *, timeout: float = 10.0) -> str | None:
    """Like :func:`bound_account`, but propagates a lock-acquisition
    failure instead of silently degrading to "no binding" -- for a
    caller (e.g. a destructive CodeSpace reclaim) that must not treat an
    UNAVAILABLE authoritative binding the same as a confirmed absence of
    one (claim-provider-pattern effort review finding: "Fail closed when
    account binding cannot be read"). A missing/corrupt bindings FILE
    still degrades to "no binding" here too -- that is this store's own
    intentional, documented contract (see :func:`_read`'s own docstring)
    -- only lock CONTENTION (a transient, ambiguous unavailability, not a
    confirmed empty store) propagates. ``timeout`` bounds the lock wait."""
    with _binding_lock(timeout=timeout, stale_after=30.0):
        rec = _read().get(codespace)
    return (rec.account or None) if rec else None


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


def list_bindings(*, strict: bool = False) -> list[AccountBinding]:
    """Return bindings; opt-in strict admission refuses damaged or unreadable state."""
    try:
        with _binding_lock():
            return list(_read(strict=strict).values())
    except OSError as exc:
        if strict:
            raise RuntimeError("Account binding store is unavailable") from exc
        raise


def bound_accounts() -> tuple[str, ...]:
    """Return distinct non-empty bound gh logins."""
    try:
        return bound_accounts_or_raise()
    except Exception:
        return ()


def bound_accounts_or_raise() -> tuple[str, ...]:
    """Like :func:`bound_accounts`, but propagates a lock-acquisition
    failure instead of silently degrading to an empty tuple -- for a
    caller (e.g. building the fallback candidate list for a destructive
    CodeSpace reclaim) that must not treat an UNAVAILABLE binding store
    the same as a confirmed empty one (claim-provider-pattern effort
    review finding: "Propagate binding read failures instead of scanning
    accounts"). Losing visibility into which accounts hold bindings and
    then still scanning mapped/ambient accounts anyway risks selecting
    (and reclaiming!) a same-named CodeSpace under the wrong account."""
    seen: list[str] = []
    with _binding_lock():
        records = _read()
    for rec in records.values():
        if rec.account and rec.account not in seen:
            seen.append(rec.account)
    return tuple(seen)
