"""Configure retained application diagnostics for ``agent-dispatch.*``.

Call :func:`configure_file_logging` once, early, from each long-running
daemon entrypoint (the coordinator's ``serve``, ``supervise serve`` and its
actual worker-lane ``supervise`` process) -- never from unrelated short-lived
CLI invocations, which would
needlessly create log files for every one-off command. Native stdout/stderr,
installer attempts and old text handlers are separate owners; ``logs`` discovers
their locations without claiming application retention covers them.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import install_paths
from .diagnostic_logs import DiagnosticLog

#: Every submodule logs under this parent name (see the many
#: ``logging.getLogger("agent-dispatch.<x>")`` call sites) -- attaching one
#: handler here, with propagation left at its default, captures all of them.
_PACKAGE_LOGGER_NAME = "agent-dispatch"

#: Marks a handler this function already installed, so a second call (e.g. a
#: self-update respawn re-running the same entrypoint in-process) is a no-op
#: rather than stacking duplicate handlers and double-logging every record.
_HANDLER_MARKER = "_agent_dispatch_file_handler"

def configure_file_logging(
    name: str, *, level: int = logging.INFO, install_dir: Path | None = None
) -> Path:
    """Attach an age-retained JSONL handler for ``agent-dispatch.*`` loggers.

    ``name`` picks ``<install_dir>/logs/diagnostics/<name>.jsonl`` -- pass a
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
    handler = DiagnosticLog(name, root)
    log_path = handler.path
    setattr(handler, _HANDLER_MARKER, True)

    package_logger.addHandler(handler)
    package_logger.setLevel(level)
    # This package's own loggers never need to climb past their own handler --
    # avoids a duplicate copy landing in some ambient root-logger handler a
    # future embedding process might configure.
    package_logger.propagate = False

    return log_path
