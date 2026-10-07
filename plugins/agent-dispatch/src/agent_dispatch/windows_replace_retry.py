"""Retry transient Windows file-lock errors around directory ``os.replace``.

A managed-runtime cell publish/quarantine/unpublish rename (``os.replace`` of
a directory) can race **Windows Defender** (or another on-access scanner)
briefly holding a handle open on a file the runtime just finished writing
into that same directory -- surfacing as ``WinError 5`` (access denied) or a
sharing/lock violation (``32``/``33``), not a real, permanent failure. This
is the same class of flake already root-caused and fixed for version-dir GC
in ``libs/versioned-runtime/versioned_runtime.py`` (dotfiles #911) -- this
module applies the identical retry-then-give-up pattern to agent-dispatch's
own managed-runtime cell renames (coverage-guided-ci effort, Phase 3.5,
2026-10-06).
"""

from __future__ import annotations

import errno
import os
import time
from pathlib import Path

_RETRY_ATTEMPTS = 4
_RETRY_BACKOFF_SECONDS = 0.5  # grows linearly per attempt


def is_transient_windows_lock(exc: OSError) -> bool:
    """True when ``exc`` looks like a *transient* file lock worth retrying,
    rather than a real, permanent failure (see module docstring)."""
    if isinstance(exc, PermissionError):
        return True
    if getattr(exc, "errno", None) == errno.EACCES:
        return True
    return getattr(exc, "winerror", None) in (5, 32, 33)


def replace_with_retry(src: Path, dst: Path) -> None:
    """``os.replace(src, dst)`` with AV-tolerant retries.

    Retries a bounded number of times with a short linear backoff when the
    failure looks transient (see :func:`is_transient_windows_lock`); any
    other -- or still-unresolved after retries -- ``OSError`` propagates to
    the caller unchanged, so existing fail-closed handling at each call site
    is preserved.
    """
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            if not is_transient_windows_lock(exc) or attempt == _RETRY_ATTEMPTS - 1:
                raise
            time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
