"""Create-only attachment import through the existing KeePass backend owner."""

from __future__ import annotations

import base64
import binascii
import time
from subprocess import CompletedProcess
from typing import Any, Protocol

MAX_ATTACHMENT_BYTES = 16_384


class AttachmentImportError(ValueError):
    """Closed, secret-free outcomes; uncertain mutations are never retried."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AttachmentBackend(Protocol):
    def has_password(self, kpdb: str, /) -> bool: ...

    def _identity_listing(self, kpdb: str, deadline: float, /) -> set[str]: ...

    def _identity_run(
        self,
        kpdb: str,
        args: list[str],
        deadline: float,
        /,
    ) -> CompletedProcess[str] | None: ...

    def import_attachment_create_only(
        self,
        kpdb: str,
        entry: str,
        name: str,
        data: bytes,
        /,
    ) -> tuple[bool, str]: ...


def validate_names(entry: object, key_name: object) -> tuple[str, str]:
    if (
        not isinstance(entry, str)
        or not 1 <= len(entry) <= 512
        or entry.startswith("-")
        or any(
            part in ("", ".", "..", "[empty]") or part != part.strip() for part in entry.split("/")
        )
        or "\\" in entry
        or any(ord(char) < 32 or ord(char) == 127 for char in entry)
        or not isinstance(key_name, str)
        or not 1 <= len(key_name) <= 128
        or key_name in (".", "..")
        or key_name.startswith("-")
        or any(char in key_name for char in "/\\")
        or any(char in key_name for char in '<>:"|?*')
        or key_name.endswith(".")
        or key_name != key_name.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in key_name)
    ):
        raise AttachmentImportError("invalid_import")
    try:
        entry.encode("utf-8")
        key_bytes = key_name.encode("utf-8")
    except UnicodeError:
        raise AttachmentImportError("invalid_import") from None
    if len(key_bytes) > 128:
        raise AttachmentImportError("invalid_import")
    return entry, key_name


def decode_attachment(value: object) -> bytes:
    if not isinstance(value, str) or not 1 <= len(value) <= 21_848:
        raise AttachmentImportError("invalid_import")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise AttachmentImportError("invalid_import") from None
    if (
        not 1 <= len(data) <= MAX_ATTACHMENT_BYTES
        or base64.b64encode(data).decode("ascii") != value
    ):
        raise AttachmentImportError("invalid_import")
    return data


def create_pair(
    backend: AttachmentBackend,
    kpdb: str,
    entry: str,
    key_name: str,
    private: bytes,
    public: bytes,
    deadline: float,
) -> None:
    """Called under the existing service and local database creation locks."""
    validate_names(entry, key_name)
    if any(
        not isinstance(value, bytes) or not 1 <= len(value) <= MAX_ATTACHMENT_BYTES
        for value in (private, public)
    ):
        raise AttachmentImportError("invalid_import")
    if not callable(getattr(backend, "import_attachment_create_only", None)):
        raise AttachmentImportError("backend_unsupported")
    if not backend.has_password(kpdb):
        raise AttachmentImportError("needs_unlock")
    try:
        paths = backend._identity_listing(kpdb, deadline)
    except Exception:
        raise AttachmentImportError("vault_read_failed") from None
    if entry in paths or entry + "/" in paths:
        raise AttachmentImportError("entry_exists")
    parents = entry.split("/")[:-1]
    if any("/".join(parents[:index]) + "/" not in paths for index in range(1, len(parents) + 1)):
        raise AttachmentImportError("parent_unavailable")
    if time.monotonic() >= deadline:
        raise AttachmentImportError("operation_expired")
    try:
        result = backend._identity_run(kpdb, ["add", "-q", kpdb, entry], deadline)
    except Exception:
        raise AttachmentImportError("creation_unconfirmed") from None
    if result is None or result.returncode != 0:
        raise AttachmentImportError("creation_unconfirmed")
    for name, value in ((key_name, private), (key_name + ".pub", public)):
        if time.monotonic() >= deadline:
            raise AttachmentImportError("attachment_creation_unconfirmed")
        try:
            ok, _message = backend.import_attachment_create_only(kpdb, entry, name, value)
        except Exception:
            raise AttachmentImportError("attachment_creation_unconfirmed") from None
        if ok is not True:
            raise AttachmentImportError("attachment_creation_unconfirmed")


def handle_request(
    backend: AttachmentBackend,
    kpdb: str,
    group: str | None,
    request: dict[str, Any],
) -> dict[str, Any]:
    from .config import normalize_entry
    from .identity_key_lock import database_creation_lock

    try:
        raw_entry = request.get("entry")
        if (
            not isinstance(raw_entry, str)
            or (group is not None and not isinstance(group, str))
            or request.get("allow_prompt", False) is not False
        ):
            raise AttachmentImportError("invalid_import")
        entry, key_name = validate_names(
            normalize_entry(raw_entry, group),
            request.get("key_name"),
        )
        private = decode_attachment(request.get("key_data"))
        public = decode_attachment(request.get("pub_data"))
        if not backend.has_password(kpdb):
            raise AttachmentImportError("needs_unlock")
        deadline = time.monotonic() + 60.0
        with database_creation_lock(kpdb, deadline):
            create_pair(backend, kpdb, entry, key_name, private, public, deadline)
        return {"ok": True, "status": "created"}
    except AttachmentImportError as exc:
        return {"ok": False, "code": exc.code, "error": exc.code}
    except Exception:
        return {"ok": False, "code": "import_unconfirmed", "error": "import_unconfirmed"}
