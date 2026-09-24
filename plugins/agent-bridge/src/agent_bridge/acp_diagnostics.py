"""Correlate the ``acp`` library's own malformed-JSON-RPC log records with the
generic ``ConnectionError``/"Connection closed" they produce.

The third-party ``acp`` (``agent-client-protocol``) package's own
``NdjsonTransport.receive()`` swallows a JSON parse failure with a bare
``logging.exception("Error parsing JSON-RPC message")`` (landing on the ROOT
logger, since it calls the module-level function rather than a named logger)
and just keeps looping -- the actual signal (a truncated/malformed message)
never reaches the caller. By the time the connection then dies, all we see is
an opaque ``ConnectionError("Connection closed")``, indistinguishable from a
transient network drop.

This was the exact signature of a real incident (2026-09-23): GitHub Copilot
CLI 1.0.89-1 wrote exactly one pipe-buffer's worth (64 KiB) of a
``session/new`` response and exited(0) mid-message -- every symptom pointed at
agent-bridge/agent-dispatch until the "Error parsing JSON-RPC message" log
line (already being emitted, just unlinked from the failure) was manually
correlated by timestamp with the "Connection closed" a moment later.

We cannot patch the third-party ``acp`` package's source, so instead we
temporarily capture its own diagnostic log record during a connect attempt and
fold it into whatever exception the attempt raises -- turning a multi-hour
manual log correlation into an immediately actionable error message.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

# The exact message acp's NdjsonTransport.receive() logs on a JSON parse
# failure (see the module docstring above). Matching on it is inherently a
# little fragile -- an upstream wording change would silently stop matching --
# but it's the only signal available without vendoring/patching a third-party
# dependency, and a missed match just falls back to the original generic
# error, never produces a wrong diagnosis.
ACP_PARSE_ERROR_MESSAGE = "Error parsing JSON-RPC message"


@dataclass(frozen=True)
class CapturedParseError:
    """One captured "Error parsing JSON-RPC message" log record."""

    exc_type: str
    exc_message: str


class _ParseErrorCaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.records: list[CapturedParseError] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() != ACP_PARSE_ERROR_MESSAGE:
            return
        exc_type = ""
        exc_message = ""
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            exc_type = type(exc).__name__
            exc_message = str(exc)
        self.records.append(CapturedParseError(exc_type=exc_type, exc_message=exc_message))


@contextmanager
def capture_acp_parse_errors() -> Iterator[list[CapturedParseError]]:
    """Capture ``acp``'s own malformed-JSON-RPC log records for the ``with`` block.

    Installs a handler on the root logger (matching where the third-party
    library's bare ``logging.exception(...)`` call lands) for the duration of
    the block, removing it again on exit regardless of outcome. Yields the
    (initially empty, filled in place as records arrive) capture list so the
    caller can inspect it after a failure inside the block -- the list object
    itself stays valid and readable after the ``with`` exits.
    """
    handler = _ParseErrorCaptureHandler()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield handler.records
    finally:
        root.removeHandler(handler)


def describe_truncated_child(records: list[CapturedParseError]) -> str:
    """Render a clear, actionable summary for a captured parse-failure batch.

    Returns ``""`` when ``records`` is empty (nothing to describe) -- callers
    should treat that as "no enrichment available, use the original error".
    """
    if not records:
        return ""
    last = records[-1]
    detail = f" ({last.exc_type}: {last.exc_message})" if last.exc_message else ""
    plural = "s" if len(records) > 1 else ""
    return (
        f"child emitted {len(records)} malformed/truncated JSON-RPC "
        f"message{plural} before the connection closed{detail} -- this is the "
        "signature of the child process writing partial output and exiting "
        "cleanly, not a real transport drop; check for an upstream copilot "
        "CLI regression before assuming a transient network/process issue"
    )
