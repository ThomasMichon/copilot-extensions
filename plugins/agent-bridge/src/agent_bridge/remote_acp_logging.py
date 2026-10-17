"""Remote-scoped SDK diagnostic redaction through public logging APIs."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from threading import Lock
from collections.abc import Iterator
from typing import Any

import acp

_REMOTE_SCOPE: ContextVar[bool] = ContextVar("remote_acp_sdk_logging", default=False)
_LOCK = Lock()
_INSTALLED = False
_SDK_ROOT = os.path.normcase(str(Path(acp.__file__).resolve().parent)) + os.sep


def _install() -> None:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return
        previous = logging.getLogRecordFactory()

        def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = previous(*args, **kwargs)
            sdk_record = False
            if _REMOTE_SCOPE.get() and isinstance(record.pathname, str):
                try:
                    sdk_record = os.path.normcase(
                        str(Path(record.pathname).resolve()),
                    ).startswith(_SDK_ROOT)
                except (OSError, RuntimeError):
                    pass
            if sdk_record:
                record.msg = "Remote ACP SDK diagnostic (details redacted)"
                record.args = ()
                record.exc_info = None
                record.exc_text = None
                record.stack_info = None
                if hasattr(record, "message"):
                    record.message = record.msg
            return record

        logging.setLogRecordFactory(factory)
        _INSTALLED = True


@contextmanager
def remote_sdk_log_scope() -> Iterator[None]:
    _install()
    token = _REMOTE_SCOPE.set(True)
    try:
        yield
    finally:
        _REMOTE_SCOPE.reset(token)
