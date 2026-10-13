"""Shared coordinator errors, independent of client composition."""

import json


class DispatchError(RuntimeError):
    """A non-2xx response from the coordinator (carries status + detail)."""

    def __init__(self, status_code: int, detail: object):
        rendered = json.dumps(detail, sort_keys=True) if isinstance(detail, dict) else str(detail)
        super().__init__(f"HTTP {status_code}: {rendered}")
        self.status_code = status_code
        self.detail = detail

    def as_dict(self) -> dict[str, object]:
        if isinstance(self.detail, dict):
            return self.detail
        return {
            "code": "dispatch_http_error",
            "message": str(self.detail),
            "status": self.status_code,
        }


class DispatchUpgradeRequired(DispatchError):
    """The coordinator lacks the required protocol."""

    def __init__(self, detail: str):
        super().__init__(426, detail)
