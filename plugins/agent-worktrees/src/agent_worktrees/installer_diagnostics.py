"""Exact, non-authoritative launch references using the packaged installer helper."""

from __future__ import annotations

import importlib.util
import io
import os
import stat
import sys
import threading
from collections.abc import Mapping
from pathlib import Path

from . import registry_paths


_NOTIFICATION_LOCK = threading.Lock()
_NOTIFICATION = None


def _notify(message: str):
    """At most one pending raw write; a stopped reader cannot hold the caller."""
    global _NOTIFICATION
    sink = sys.stderr
    try:
        if isinstance(sink, io.StringIO) or (
            isinstance(sink, io.TextIOWrapper) and isinstance(sink.buffer, io.BytesIO)
        ):
            sink.write(message + "\n")
            return
        data = (message + "\n").encode("utf-8", errors="replace")[:4096]
        with _NOTIFICATION_LOCK:
            if _NOTIFICATION is not None and _NOTIFICATION.is_alive():
                return
            def write():
                descriptor = None
                try:
                    descriptor = os.dup(sink.fileno())
                    os.write(descriptor, data)
                except (OSError, ValueError, AttributeError):
                    pass
                finally:
                    if descriptor is not None:
                        os.close(descriptor)

            thread = threading.Thread(target=write, name="installer-diagnostics-notice", daemon=True)
            try:
                thread.start()
            except RuntimeError:
                return
            _NOTIFICATION = thread
        # Never hold a buffered stdio lock across an abandoned write: Python
        # flushes those same locks at interpreter shutdown.
        thread.join(.05)
    except (OSError, ValueError, AttributeError):
        # Presentation is optional; the private reference remains in the result.
        pass


def _helper():
    bootstrap = Path(sys.prefix) / ".runtime-bootstrap"
    path = (
        bootstrap / "installer_diagnostics.py" if bootstrap.is_dir()
        else registry_paths._payload_root() / "scripts" / "installer_diagnostics.py"
    )
    if bootstrap.is_symlink() or path.is_symlink():
        raise ValueError("installer diagnostics helper must be an ordinary file")
    for item in (path, *path.parents):
        if os.path.lexists(item):
            metadata = item.lstat()
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
                raise ValueError("installer diagnostics helper traverses a link or reparse point")
    spec = importlib.util.spec_from_file_location("_installer_launch_diagnostics", path)
    if spec is None or spec.loader is None:
        raise ImportError("installer diagnostics helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ordinary(path)
    return module


class Launch:
    """Diagnostics never replace the existing process owner or its native result."""

    def __init__(self, environment: Mapping[str, str]):
        self.environment = dict(environment)
        self.reference = {"status": "missing", "installer_entry": "unknown",
                          "missing_diagnostics": True}
        self.helper = None
        self.warned = False
        try:
            helper = _helper()
            self.environment = helper.reserve_launch(environment)
            self.helper = helper
            self.reference = helper.launch_reference(self.environment)
            _notify(f"Installer diagnostics (UNATTRIBUTED): {self.reference['directory']}")
        except Exception:
            # This boundary contains only optional diagnostics, never the installer.
            self._warn()

    def _warn(self):
        if self.warned:
            return
        self.warned = True
        _notify("WARNING: Installer diagnostics unavailable; continuing with missing diagnostics.")

    def finish(self, outcome: str = "interrupted", *, native_exit: int | None = None) -> dict:
        if self.helper is not None:
            try:
                self.reference = self.helper.complete_launch(
                    self.environment, outcome, native_exit,
                )
            except Exception:
                self.reference = {**self.reference, "status": "unknown",
                                  "installer_entry": "unknown", "missing_diagnostics": True}
        if self.reference["missing_diagnostics"]:
            self._warn()
        return self.reference
