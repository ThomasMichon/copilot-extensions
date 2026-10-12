"""Configure persistent, rotating file logging for the ``agent-dispatch``
logger hierarchy.

Every submodule across this package already calls ``logging.getLogger(
"agent-dispatch.<name>")`` and logs meaningful operational events (body
recovery, reservation retirement, carried-session decisions, coordinator
errors) -- but **nothing in the package ever configures a logging handler**.
Python's logging module silently drops every record below WARNING with no
handler attached, and even WARNING/ERROR records only reach the "handler of
last resort" (stderr) -- which goes nowhere for a daemon launched via
``pythonw.exe`` with no attached console (the normal way both the coordinator
and the supervisor run). The result: every ``log.info``/``log.warning``/
``log.exception`` call in this package has been operationally invisible,
making exactly the kind of diagnosis this module exists to support (why did
the supervisor stop spawning anything?) require manual SQLite/process
archaeology instead of reading a log file (copilot-extensions#4978).

Call :func:`configure_file_logging` once, early, from each long-running
daemon entrypoint (the coordinator's ``serve`` and the supervisor's
``supervise serve``) -- never from a short-lived CLI invocation, which would
needlessly create log files for every one-off command.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from . import install_paths

#: Every submodule logs under this parent name (see the many
#: ``logging.getLogger("agent-dispatch.<x>")`` call sites) -- attaching one
#: handler here, with propagation left at its default, captures all of them.
_PACKAGE_LOGGER_NAME = "agent-dispatch"

#: Marks a handler this function already installed, so a second call (e.g. a
#: self-update respawn re-running the same entrypoint in-process) is a no-op
#: rather than stacking duplicate handlers and double-logging every record.
_HANDLER_MARKER = "_agent_dispatch_file_handler"

_MAX_BYTES = 5_000_000
_BACKUP_COUNT = 3


def configure_file_logging(
    name: str, *, level: int = logging.INFO, install_dir: Path | None = None
) -> Path:
    """Attach a rotating file handler for ``agent-dispatch.*`` loggers.

    ``name`` picks the log file (``<install_dir>/logs/<name>.log``) -- pass a
    distinct name per daemon (``"coordinator"``, ``"supervisor"``) so they
    don't interleave into one file. Returns the resolved log file path.

    Idempotent: calling this again (same or different ``name``) does not
    stack handlers -- only the first call in a process takes effect, since
    the package logger's level and handler are process-wide state.
    """
    package_logger = logging.getLogger(_PACKAGE_LOGGER_NAME)
    if any(getattr(h, _HANDLER_MARKER, False) for h in package_logger.handlers):
        existing = next(
            h.baseFilename
            for h in package_logger.handlers
            if getattr(h, _HANDLER_MARKER, False)
        )
        return Path(existing)

    root = install_dir if install_dir is not None else install_paths.install_dir()
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{name}.log"

    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    setattr(handler, _HANDLER_MARKER, True)

    package_logger.addHandler(handler)
    package_logger.setLevel(level)
    # This package's own loggers never need to climb past their own handler --
    # avoids a duplicate copy landing in some ambient root-logger handler a
    # future embedding process might configure.
    package_logger.propagate = False

    return log_path
