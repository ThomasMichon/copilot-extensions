"""Keep reviewer diagnostics on the foreground owner's existing stderr sink."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import logging
import threading

LOGGER = "agent_pull_requests.reviewer_reads"
_lock = threading.Lock()
_users = 0
_handler: logging.Handler | None = None
_previous: tuple[int, bool] = (logging.NOTSET, True)


@contextmanager
def reviewer_reads() -> Iterator[None]:
    """Share one handler across embeddings and restore the caller's logger state."""
    global _users, _handler, _previous
    logger = logging.getLogger(LOGGER)
    with _lock:
        if not _users:
            _previous = logger.level, logger.propagate
            _handler = logging.StreamHandler()
            _handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(_handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
        _users += 1
    try:
        yield
    finally:
        with _lock:
            _users -= 1
            if not _users:
                assert _handler is not None
                logger.removeHandler(_handler)
                _handler.close()
                _handler = None
                logger.setLevel(_previous[0])
                logger.propagate = _previous[1]
