"""Opt-in POSIX native account-file renewal; no execution or scheduler ownership."""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path

from .source_lease_http import HttpSourceLease, HttpSourceLeaseClient, _invalid_constant, _pairs, _text

_MAX_CONFIG_BYTES = 1024 * 1024


class CopilotFileAuthError(RuntimeError):
    """A static refusal, safe to report without exposing native config contents."""


class CopilotAccountFile:
    """Update one existing, explicitly approved plaintext account slot.

    The caller must own exclusive config-writer custody, including native
    writers which do not cooperate with this adapter's lock. This does not
    enroll an account or establish plaintext-storage consent.
    """

    def __init__(self, path: Path, *, account: str) -> None:
        if os.name != "posix":
            raise CopilotFileAuthError("copilot-account-file-platform-unsupported")
        if not path.is_absolute() or path.name != "config.json" or not _text(account):
            raise CopilotFileAuthError("copilot-account-file-binding-invalid")
        self._path, self._key = path, "https://github.com:" + account
        self._account = account
        self._token: str | None = None
        self._withdrawn = False

    async def renew(self, client: HttpSourceLeaseClient) -> HttpSourceLease:
        """Acquire fresh source authorization before changing the native file."""
        lease = await client.acquire()
        self.install(lease)
        return lease

    def install(self, lease: HttpSourceLease) -> None:
        """Populate only an existing native slot while the source lease is live."""
        if lease.remaining_seconds() <= 0 or not _text(lease.token, 16384):
            raise CopilotFileAuthError("copilot-account-source-expired")
        if lease.account != self._account or lease.host != "github.com":
            raise CopilotFileAuthError("copilot-account-source-mismatch")
        self._change(lease.token, lease)

    def withdraw(self) -> None:
        """Remove only the exact token last installed by this in-memory owner."""
        if self._token is None:
            raise CopilotFileAuthError("copilot-account-file-unowned")
        self._change(None)

    def _change(self, token: str | None, lease: HttpSourceLease | None = None) -> None:
        if os.name != "posix":
            raise CopilotFileAuthError("copilot-account-file-platform-unsupported")
        import fcntl

        directory, lock, temporary = -1, -1, ""
        try:
            parent = self._path.parent
            if parent.resolve() != parent:
                raise ValueError
            directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            metadata = os.fstat(directory)
            if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                raise ValueError
            lock = os.open(
                ".bridge-account-auth.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600, dir_fd=directory,
            )
            self._private(os.fstat(lock))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            descriptor = os.open(
                self._path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory,
            )
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                self._private(before)
                raw = stream.read(_MAX_CONFIG_BYTES + 1)
            if len(raw) > _MAX_CONFIG_BYTES:
                raise ValueError
            text = raw.decode("utf-8-sig")
            header = "\ufeff" if raw.startswith(b"\xef\xbb\xbf") else ""
            body_start = 0
            for line in text.splitlines(keepends=True):
                if not line.strip() or line.lstrip().startswith("//"):
                    header += line
                    body_start += len(line)
                else:
                    break
            value = json.loads(
                text[body_start:], object_pairs_hook=_pairs, parse_constant=_invalid_constant,
            )
            if not isinstance(value, dict) or not isinstance(value.get("authTokens"), dict):
                raise ValueError
            slot = value["authTokens"].get(self._key)
            if not isinstance(slot, dict):
                raise ValueError
            if self._withdrawn:
                if "token" in slot:
                    raise CopilotFileAuthError("copilot-account-file-owner-changed")
            elif not _text(slot.get("token"), 16384):
                raise ValueError
            if self._token is not None and slot.get("token") != self._token:
                raise CopilotFileAuthError("copilot-account-file-owner-changed")
            if token is None:
                del slot["token"]
            else:
                slot["token"] = token
            candidate = ".bridge-account-auth-" + secrets.token_hex(16)
            descriptor = os.open(
                candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=directory,
            )
            temporary = candidate
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(header)
                json.dump(value, stream, ensure_ascii=True, allow_nan=False)
                stream.write("\n")
                stream.flush()
                if os.fstat(stream.fileno()).st_size > _MAX_CONFIG_BYTES:
                    raise CopilotFileAuthError("copilot-account-file-too-large")
                os.fsync(stream.fileno())
            current = os.stat(self._path.name, dir_fd=directory, follow_symlinks=False)
            if (
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns)
            ):
                raise CopilotFileAuthError("copilot-account-file-owner-changed")
            if lease is not None and lease.remaining_seconds() <= 0:
                raise CopilotFileAuthError("copilot-account-source-expired")
            os.replace(temporary, self._path.name, src_dir_fd=directory, dst_dir_fd=directory)
            temporary = ""
            self._token, self._withdrawn = token, token is None
            try:
                os.fsync(directory)
            except OSError:
                raise CopilotFileAuthError("copilot-account-file-durability-unknown") from None
        except CopilotFileAuthError:
            raise
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            raise CopilotFileAuthError("copilot-account-file-unavailable") from None
        finally:
            try:
                if temporary:
                    os.unlink(temporary, dir_fd=directory)
            finally:
                if lock >= 0:
                    os.close(lock)
                if directory >= 0:
                    os.close(directory)

    @staticmethod
    def _private(metadata: os.stat_result) -> None:
        if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077 or metadata.st_nlink != 1
        ):
            raise ValueError
