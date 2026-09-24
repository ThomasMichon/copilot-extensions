"""The bounded public error of a remote Bridge operation (split from
``remote_operations`` for the module-size cap; re-exported there)."""

from __future__ import annotations

from typing import Any

from ssh_manager import CarrierRemoteError


class RemoteBridgeError(RuntimeError):
    """A bounded public failure from a remote Bridge operation."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        reconnectable: bool = False,
    ) -> None:
        self.status = status
        self.code = code
        self.details = dict(details or {})
        self.reconnectable = reconnectable
        super().__init__(message)

    @classmethod
    def from_carrier(cls, error: CarrierRemoteError) -> RemoteBridgeError:
        payload = error.payload
        default_status = {
            "unsupported_operation": 501,
            "unsupported_version": 426,
            "invalid_request": 400,
            "session_not_found": 404,
            "cursor_invalidated": 409,
            "cursor_mismatch": 409,
            "replay_gap": 409,
        }.get(error.code, 502)
        try:
            status = int(payload.get("status") or default_status)
        except (TypeError, ValueError):
            status = 502
        details = payload.get("details")
        return cls(
            status,
            error.code,
            str(error),
            details=details if isinstance(details, dict) else None,
            reconnectable=error.reconnectable,
        )

    def public_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            **self.details,
            "reconnectable": self.reconnectable,
        }
