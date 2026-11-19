"""Bounded, nonsecret reasons for a stopped guarded resolver."""

from __future__ import annotations

import re
import socket
import ssl
from types import FunctionType
from urllib.error import URLError


class GuardedSelectionError(ValueError):
    """A guarded native selection could not establish its input boundaries."""


_GENERIC = "guarded feed input was refused"
_REQUEST_FAILURE = "wheel feed request failed or timed out"
_REQUEST_CAUSES = (
    OSError, TimeoutError, ConnectionError, ConnectionAbortedError,
    ConnectionRefusedError, ConnectionResetError, BrokenPipeError, PermissionError,
    FileNotFoundError, ssl.SSLError, ssl.SSLCertVerificationError, ssl.SSLEOFError,
    ssl.SSLZeroReturnError, ssl.SSLWantReadError, ssl.SSLWantWriteError,
    socket.gaierror, socket.herror, ValueError, URLError,
)
_REQUEST_CAUSE_NAMES = tuple(cause.__name__ for cause in _REQUEST_CAUSES) + ("unknown",)
_REQUEST_REASON = re.compile(
    r"guarded request \[stage=(?:project-index|wheel),type=(?P<kind>"
    + "|".join(_REQUEST_CAUSE_NAMES)
    + r"),errno=(?P<errno>none|-?[0-9]{1,10}),"
    r"winerror=(?P<winerror>none|-?[0-9]{1,10}),tls=(?P<tls>none|-?[0-9]{1,10})\]"
)
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


def _native_code(value: object) -> int | None:
    return value if type(value) is int and -(1 << 31) <= value < (1 << 32) else None


def _known_cause(value: object) -> str:
    return next((cause.__name__ for cause in _REQUEST_CAUSES if type(value) is cause), "unknown")


def safe_guarded_request_reason(value: object) -> str | None:
    """Validate optional request diagnostics from the selected producer's refusal."""
    if type(value) is not str or len(value) > 128:
        return None
    match = _REQUEST_REASON.fullmatch(value)
    if match is None:
        return None
    for field in ("errno", "winerror", "tls"):
        encoded = match[field]
        if encoded != "none":
            code = int(encoded)
            if _native_code(code) is None or str(code) != encoded or match["kind"] == "unknown":
                return None
    return value


def _request_stage(failure: BaseException) -> str | None:
    retained = failure.__traceback__
    for _ in range(16):
        if retained is None:
            break
        frame = retained.tb_frame
        namespace = frame.f_globals
        if namespace.get("__name__") == "core_native_feed_resolution":
            project: object = namespace.get("_fetch_project")
            if isinstance(project, FunctionType) and project.__code__ is frame.f_code:
                return "project-index"
            index: object = namespace.get("_GuardedIndex")
            wheel: object = vars(index).get("wheel") if isinstance(index, type) else None
            if isinstance(wheel, FunctionType) and wheel.__code__ is frame.f_code:
                return "wheel"
        retained = retained.tb_next
    return None


def _request_reason(failure: BaseException) -> str | None:
    if (
        len(failure.args) != 1
        or type(failure.args[0]) is not str
        or failure.args[0] != _REQUEST_FAILURE
    ):
        return None
    stage = _request_stage(failure)
    if stage is None:
        return None
    native: object = failure.__context__
    if type(native) is URLError:
        native = native.reason
    kind = _known_cause(native)
    codes: tuple[int | None, int | None, int | None] = (None, None, None)
    if kind != "unknown":
        codes = (
            _native_code(getattr(native, "errno", None)),
            _native_code(getattr(native, "winerror", None)),
            _native_code(getattr(native, "verify_code", None))
            if type(native) is ssl.SSLCertVerificationError else None,
        )
    errno, winerror, tls = ("none" if value is None else str(value) for value in codes)
    return safe_guarded_request_reason(
        f"guarded request [stage={stage},type={kind},errno={errno},winerror={winerror},tls={tls}]",
    )


def guarded_stop_reason(failure: BaseException | None, *, deadline_expired: bool) -> str:
    if failure is not None:
        try:
            recorded = _request_reason(failure)
            if recorded is not None:
                return recorded
            return _SAFE_REASONS.get(str(failure), _GENERIC)
        except Exception:  # noqa: BLE001
            return _GENERIC
    return "guarded native resolver deadline exceeded" if deadline_expired else _GENERIC
