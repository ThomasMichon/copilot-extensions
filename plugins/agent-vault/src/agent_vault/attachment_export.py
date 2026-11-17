"""Non-activating, bounded attachment reads through the existing Vault owner."""

from __future__ import annotations

import base64
from typing import Any, Protocol

from .attachment_import import AttachmentImportError, MAX_ATTACHMENT_BYTES, validate_names


class AttachmentExportBackend(Protocol):
    def has_password(self, kpdb: str, /) -> bool: ...

    def export_attachment_bounded(
        self, kpdb: str, entry: str, name: str, /,
    ) -> tuple[bytes | None, str]: ...


def handle_request(
    backend: AttachmentExportBackend,
    kpdb: str,
    group: str | None,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Return two opaque attachments only when both bounded reads settle."""
    from .config import normalize_entry

    try:
        raw_entry = request.get("entry")
        if (
            not isinstance(raw_entry, str)
            or (group is not None and not isinstance(group, str))
            or request.get("allow_prompt", False) is not False
        ):
            raise AttachmentImportError("invalid_import")
        entry, key_name = validate_names(
            normalize_entry(raw_entry, group or ""), request.get("key_name"),
        )
    except (AttachmentImportError, TypeError, ValueError):
        return {"ok": False, "code": "invalid_export", "error": "invalid_export"}
    try:
        if not callable(getattr(backend, "export_attachment_bounded", None)):
            return {"ok": False, "code": "backend_unsupported", "error": "backend_unsupported"}
        if not backend.has_password(kpdb):
            return {"ok": False, "code": "needs_unlock", "error": "needs_unlock"}
        values: list[str] = []
        for name in (key_name, key_name + ".pub"):
            data, _message = backend.export_attachment_bounded(kpdb, entry, name)
            if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_ATTACHMENT_BYTES:
                return {"ok": False, "code": "export_failed", "error": "export_failed"}
            values.append(base64.b64encode(data).decode("ascii"))
        return {"ok": True, "key_data": values[0], "pub_data": values[1]}
    except Exception:
        return {"ok": False, "code": "export_failed", "error": "export_failed"}
