"""Bounded, nonsecret reasons for a stopped guarded resolver."""

from __future__ import annotations


class GuardedSelectionError(ValueError):
    """A guarded native selection could not establish its input boundaries."""


_GENERIC = "guarded feed input was refused"
_SAFE_REASONS = {
    "wheel feed request failed or timed out": "guarded feed request failed or timed out",
    "wheel feed body read failed or timed out": "guarded feed response read failed or timed out",
    "wheel feed request failed": "guarded feed request failed",
    "wheel acquisition request budget exceeded": "guarded feed request budget exceeded",
    "wheel acquisition redirect budget exceeded": "guarded feed redirect budget exceeded",
    "wheel acquisition byte budget exceeded": "guarded feed byte budget exceeded",
    "wheel acquisition response exceeds its byte budget": "guarded feed response exceeds its byte budget",
    "wheel acquisition response length is inconsistent": "guarded feed response length is inconsistent",
    "wheel acquisition response headers are ambiguous": "guarded feed response headers are ambiguous",
    "wheel acquisition refuses encoded response bodies": "guarded feed response encoding was refused",
    "wheel index is malformed": "guarded feed index is malformed",
    "unsupported wheel index document": "guarded feed index is unsupported",
    "unsupported wheel index content type": "guarded feed index is unsupported",
    "selected exact wheel does not satisfy seasoning": "guarded feed selection did not satisfy seasoning",
    "selected exact wheel is refused by its feed": "guarded feed selection was unavailable",
    "guarded loopback request budget exceeded": "guarded loopback request budget exceeded",
    "guarded selection package budget exceeded": "guarded selection package budget exceeded",
    "guarded candidate count exceeds its budget": "guarded candidate count exceeds its budget",
    "guarded index exceeds its byte budget": "guarded index exceeds its byte budget",
    "guarded index contains invalid admission evidence": "guarded index contains invalid admission evidence",
    "guarded candidate changed after acquisition": "guarded candidate changed after acquisition",
    "guarded candidate has invalid dependencies": "guarded candidate has invalid dependencies",
    "guarded candidate has a direct dependency": "guarded candidate has a direct dependency",
}


def guarded_stop_reason(failure, *, deadline_expired: bool) -> str:
    if failure is not None:
        try:
            return _SAFE_REASONS.get(str(failure), _GENERIC)
        except Exception:  # noqa: BLE001
            return _GENERIC
    return "guarded native resolver deadline exceeded" if deadline_expired else _GENERIC
