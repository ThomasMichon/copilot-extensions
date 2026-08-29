"""Persistent, encrypted on-disk credential cache for agent-vault.

The daemon caches unlocked credentials in memory with a TTL; that cache dies when
the daemon restarts and cannot serve reads while the vault is locked. The
*persistent* cache is a separate, **opt-in** tier: a Fernet-encrypted file on disk
that survives restarts and answers reads without unlocking the vault -- so a
consumer can fetch a previously-cached secret even when the master password is not
loaded (e.g. an unattended job on a locked box).

It is inert unless enabled. Storing secrets on disk -- even encrypted with a
local key -- is a deliberate tradeoff, so the cache does nothing until switched on
via the ``AGENT_VAULT_CACHE`` env var (truthy) or a configured cache directory
(``AGENT_VAULT_CACHE_DIR``). When disabled, or when the optional ``cryptography``
dependency is not installed, every operation is a safe no-op.

Security posture: the Fernet key sits beside the cache file, **wrapped at rest** --
DPAPI (per-OS-user) on Windows via the ``kek`` module, or a ``0600`` file on POSIX.
On Windows the wrapped key is only unwrappable by the same user account; on POSIX it
is hygiene, not high security, so physical-access control and full-disk encryption
remain the real barriers. Never treat the on-disk cache as a trust boundary.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

from .config import IS_WINDOWS, default_config_path

# Enablement / location env vars.
CACHE_ENABLE_ENV = "AGENT_VAULT_CACHE"
CACHE_DIR_ENV = "AGENT_VAULT_CACHE_DIR"

_TRUTHY = {"1", "true", "yes", "on"}


def _truthy(value: str | None) -> bool:
    return bool(value) and value.strip().lower() in _TRUTHY


def default_cache_dir() -> Path:
    """Return the default cache directory (alongside the config file)."""
    return default_config_path().parent / "cache"


def cache_enabled() -> bool:
    """Report whether the persistent cache is switched on for this process.

    Enabled when ``AGENT_VAULT_CACHE`` is truthy or ``AGENT_VAULT_CACHE_DIR`` is
    set. A configured directory implies intent, so it enables on its own.
    """
    return _truthy(os.environ.get(CACHE_ENABLE_ENV)) or bool(
        os.environ.get(CACHE_DIR_ENV)
    )


class PersistentCache:
    """Fernet-encrypted on-disk ``(entry, field) -> value`` cache.

    All mutating operations are no-ops when the cache is disabled or when the
    ``cryptography`` library is unavailable; reads return ``None`` in those cases.
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        if base_dir is not None:
            base = Path(base_dir)
        elif os.environ.get(CACHE_DIR_ENV):
            base = Path(os.environ[CACHE_DIR_ENV])
        else:
            base = default_cache_dir()
        self._base_dir = base
        self._cache_file = base / "credential-cache.enc"
        self._key_file = base / "credential-cache.key"
        self._lock_file = base / "credential-cache.lock"
        self._replacement_lock_file = base / "credential-replacement.lock"
        self._fernet = None  # lazy
        self._available: bool | None = None

    # -- availability ------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True when the cache is switched on *and* usable (crypto present)."""
        return cache_enabled() and self._crypto_available()

    def _crypto_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            from cryptography.fernet import Fernet as _F  # noqa: F401

            self._available = True
        except ImportError:
            self._available = False
        return self._available

    def _get_fernet(self):
        if self._fernet is not None:
            return self._fernet
        if not self._crypto_available():
            return None
        from cryptography.fernet import Fernet

        from . import kek

        self._base_dir.mkdir(parents=True, exist_ok=True)
        if self._key_file.exists():
            blob = self._key_file.read_bytes()
            if kek.is_wrapped(blob):
                key = kek.unwrap_bytes(blob)
            else:
                # Legacy plaintext key file -> migrate to a wrapped (DPAPI on
                # Windows) blob bound to the OS user, best-effort.
                key = blob.strip()
                with contextlib.suppress(Exception):
                    self._write_key(key)
        else:
            key = Fernet.generate_key()
            self._write_key(key)
        self._fernet = Fernet(key)
        return self._fernet

    def _write_key(self, key: bytes) -> None:
        """Persist the Fernet key wrapped at rest (DPAPI per-user on Windows)."""
        from . import kek

        self._key_file.write_bytes(kek.wrap_bytes(key))
        if not IS_WINDOWS:
            self._key_file.chmod(0o600)

    # -- atomic file I/O ---------------------------------------------------

    def _read_store(self) -> dict:
        f = self._get_fernet()
        if f is None or not self._cache_file.exists():
            return {"v": 1, "entries": {}}
        try:
            plaintext = f.decrypt(self._cache_file.read_bytes())
            data = json.loads(plaintext)
            if data.get("v") != 1:
                return {"v": 1, "entries": {}}
            return data
        except Exception:
            return {"v": 1, "entries": {}}

    @contextlib.contextmanager
    def _file_lock(self, lock_file: Path):
        self._base_dir.mkdir(parents=True, exist_ok=True)
        with open(lock_file, "w") as lock_fd:
            try:
                if IS_WINDOWS:
                    import msvcrt

                    msvcrt.locking(lock_fd.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
                yield
            finally:
                with contextlib.suppress(Exception):
                    if not IS_WINDOWS:
                        import fcntl

                        fcntl.flock(lock_fd, fcntl.LOCK_UN)

    @contextlib.contextmanager
    def _store_lock(self):
        with self._file_lock(self._lock_file):
            yield

    @contextlib.contextmanager
    def replacement_lock(self):
        """Serialize a caller's complete credential replacement transaction."""
        if not self.enabled:
            yield
            return
        with self._file_lock(self._replacement_lock_file):
            yield

    def _write_store_unlocked(self, store: dict) -> bool:
        f = self._get_fernet()
        if f is None:
            return False
        self._base_dir.mkdir(parents=True, exist_ok=True)

        import tempfile

        try:
            plaintext = json.dumps(store, separators=(",", ":")).encode()
            ciphertext = f.encrypt(plaintext)

            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._base_dir), prefix=".cache-", suffix=".tmp"
            )
            try:
                os.write(fd, ciphertext)
                os.fsync(fd)
                os.close(fd)
                if not IS_WINDOWS:
                    os.chmod(tmp_path, 0o600)
                os.replace(tmp_path, str(self._cache_file))
                if not IS_WINDOWS:
                    directory_fd = os.open(self._base_dir, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            except Exception:
                with contextlib.suppress(OSError):
                    os.close(fd)
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise
            return True
        except Exception:
            return False

    def _write_store(self, store: dict) -> bool:
        try:
            with self._store_lock():
                return self._write_store_unlocked(store)
        except Exception:
            return False

    # -- public API --------------------------------------------------------

    @staticmethod
    def _field_record(store: dict, entry: str, field: str):
        entries = store.get("entries")
        if not isinstance(entries, dict):
            return None
        fields = entries.get(entry)
        if not isinstance(fields, dict):
            return None
        return fields.get(field)

    @staticmethod
    def _fields_for_write(store: dict, entry: str) -> tuple[dict, dict]:
        entries = store.get("entries")
        if not isinstance(entries, dict):
            entries = {}
            store["entries"] = entries
        fields = entries.get(entry)
        if not isinstance(fields, dict):
            fields = {}
            entries[entry] = fields
        return entries, fields

    def get(
        self,
        entry: str,
        field: str = "password",
        max_age_seconds: int | None = None,
    ) -> str | None:
        """Return a cached value, or ``None`` on miss / disabled cache."""
        if not self.enabled:
            return None
        rec = self._field_record(self._read_store(), entry, field)
        if not isinstance(rec, dict) or "rotation" in rec:
            return None
        if max_age_seconds is not None:
            cached_epoch = rec.get("cached_at_epoch")
            if not isinstance(cached_epoch, (int, float)):
                return None
            if time.time() - cached_epoch > max_age_seconds:
                return None
        return rec.get("value")

    def put(
        self,
        entry: str,
        field: str,
        value: str,
        generation: int = 0,
    ) -> bool:
        """Cache a value (cache-through). No-op when disabled."""
        if not self.enabled:
            return False
        try:
            with self._store_lock():
                store = self._read_store()
                _, fields = self._fields_for_write(store, entry)
                current = fields.get(field)
                if isinstance(current, dict) and "rotation" in current:
                    return True
                if (
                    isinstance(current, dict)
                    and int(current.get("generation", 0)) > generation
                ):
                    return True
                fields[field] = {
                    "value": value,
                    "cached_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "cached_at_epoch": time.time(),
                    "generation": generation,
                }
                return self._write_store_unlocked(store)
        except Exception:
            return False

    def begin_replace(
        self,
        entry: str,
        field: str,
        value: str,
        operation: int,
    ) -> bool:
        """Journal an ambiguous credential replacement before its backend write."""
        if not self.enabled:
            return False
        try:
            with self._store_lock():
                store = self._read_store()
                _, fields = self._fields_for_write(store, entry)
                current = fields.get(field)
                if isinstance(current, dict) and "rotation" in current:
                    return False
                fields[field] = {
                    "rotation": {
                        "candidate": value,
                        "operation": operation,
                        "previous": current,
                    },
                }
                return self._write_store_unlocked(store)
        except Exception:
            return False

    def pending_replace(self, entry: str, field: str) -> dict | None:
        """Return a pending replacement journal, if one exists."""
        if not self.enabled:
            return None
        rec = self._field_record(self._read_store(), entry, field)
        if not isinstance(rec, dict):
            return None
        rotation = rec.get("rotation")
        return rotation if isinstance(rotation, dict) else None

    def rollback_replace(
        self,
        entry: str,
        field: str,
        operation: int,
    ) -> bool:
        """Restore the prior value when a journaled backend replacement fails."""
        if not self.enabled:
            return False
        try:
            with self._store_lock():
                store = self._read_store()
                entries = store.get("entries")
                current = self._field_record(store, entry, field)
                rotation = current.get("rotation") if isinstance(current, dict) else None
                if (
                    not isinstance(entries, dict)
                    or
                    not isinstance(rotation, dict)
                    or int(rotation.get("operation", -1)) != operation
                ):
                    return False
                previous = rotation.get("previous")
                if previous is None:
                    del entries[entry][field]
                    if not entries[entry]:
                        del entries[entry]
                else:
                    entries[entry][field] = previous
                return self._write_store_unlocked(store)
        except Exception:
            return False

    def complete_replace(
        self,
        entry: str,
        field: str,
        value: str,
        operation: int,
        generation: int,
    ) -> bool:
        """Finalize one owned journal without overwriting a newer value."""
        if not self.enabled:
            return False
        try:
            with self._store_lock():
                store = self._read_store()
                _, fields = self._fields_for_write(store, entry)
                current = fields.get(field)
                rotation = current.get("rotation") if isinstance(current, dict) else None
                if isinstance(rotation, dict):
                    if int(rotation.get("operation", -1)) != operation:
                        return False
                elif (
                    isinstance(current, dict)
                    and int(current.get("generation", 0)) > generation
                ):
                    return True
                fields[field] = {
                    "value": value,
                    "cached_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "cached_at_epoch": time.time(),
                    "generation": generation,
                }
                return self._write_store_unlocked(store)
        except Exception:
            return False

    def replace(
        self,
        entry: str,
        field: str,
        value: str,
        generation: int = 0,
    ) -> bool:
        """Authoritatively replace a cached value and clear any journal."""
        if not self.enabled:
            return False
        try:
            with self._store_lock():
                store = self._read_store()
                _, fields = self._fields_for_write(store, entry)
                fields[field] = {
                    "value": value,
                    "cached_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "cached_at_epoch": time.time(),
                    "generation": generation,
                }
                return self._write_store_unlocked(store)
        except Exception:
            return False

    def invalidate(self, entry: str, field: str | None = None) -> bool:
        """Drop a cached entry (or one field). No-op when disabled."""
        if not self.enabled:
            return False
        try:
            with self._store_lock():
                store = self._read_store()
                entries = store.get("entries")
                if not isinstance(entries, dict) or entry not in entries:
                    return False
                fields = entries[entry]
                if not isinstance(fields, dict):
                    del entries[entry]
                    return self._write_store_unlocked(store)
                if field is None:
                    del entries[entry]
                elif field in fields:
                    del fields[field]
                    if not fields:
                        del entries[entry]
                else:
                    return False
                return self._write_store_unlocked(store)
        except Exception:
            return False

    def clear(self) -> bool:
        """Wipe the entire cache file. Safe when the file is absent."""
        try:
            if self._cache_file.exists():
                self._cache_file.unlink()
            return True
        except OSError:
            return False

    def status(self) -> dict:
        """Return cache metadata (enabled, location, counts, staleness)."""
        store = self._read_store()
        entries = store.get("entries", {})
        if not isinstance(entries, dict):
            entries = {}
        total_fields = sum(
            len(fields)
            for fields in entries.values()
            if isinstance(fields, dict)
        )
        oldest = newest = None
        for fields in entries.values():
            if not isinstance(fields, dict):
                continue
            for rec in fields.values():
                if not isinstance(rec, dict):
                    continue
                ts = rec.get("cached_at", "")
                if ts:
                    if oldest is None or ts < oldest:
                        oldest = ts
                    if newest is None or ts > newest:
                        newest = ts
        return {
            "enabled": cache_enabled(),
            "available": self._crypto_available(),
            "cache_dir": str(self._base_dir),
            "cache_file": str(self._cache_file),
            "cache_exists": self._cache_file.exists(),
            "key_exists": self._key_file.exists(),
            "entry_count": len(entries),
            "field_count": total_fields,
            "oldest": oldest,
            "newest": newest,
        }


def get_cache(base_dir: str | Path | None = None) -> PersistentCache:
    """Return a :class:`PersistentCache` bound to the current environment."""
    return PersistentCache(base_dir)
