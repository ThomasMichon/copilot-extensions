"""Exact, non-authoritative launch references using the packaged installer helper."""

from __future__ import annotations

import importlib.util
import os
import stat
import sys
from collections.abc import Mapping
from pathlib import Path

from . import output, registry_paths


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
            with output.stdout_to_stderr():
                output.info(f"Installer diagnostics (UNATTRIBUTED): {self.reference['directory']}")
        except (OSError, ValueError, ImportError, TypeError, AttributeError):
            self._warn()

    def _warn(self):
        if self.warned:
            return
        self.warned = True
        try:
            with output.stdout_to_stderr():
                output.warn("Installer diagnostics unavailable; continuing with missing diagnostics.")
        except (OSError, ValueError):
            # Missing presentation must not replace a native installer outcome.
            pass

    def finish(self, outcome: str = "interrupted", *, native_exit: int | None = None) -> dict:
        if self.helper is not None:
            try:
                self.reference = self.helper.complete_launch(
                    self.environment, outcome, native_exit,
                )
            except (OSError, ValueError, TypeError):
                self.reference = {**self.reference, "status": "unknown",
                                  "installer_entry": "unknown", "missing_diagnostics": True}
        if self.reference["missing_diagnostics"]:
            self._warn()
        return self.reference
