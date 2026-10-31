"""Native filesystem spelling for absolute, caller-owned test paths."""

from __future__ import annotations

import inspect
import os
import tempfile
from pathlib import Path


def native_test_path(path: Path) -> Path:
    if os.name != "nt" or str(path).startswith("\\\\?\\"):
        return path
    value = str(path)
    return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)


class NativeTemporaryDirectory(tempfile.TemporaryDirectory):
    @classmethod
    def _rmtree(cls, name, ignore_errors=False, repeated=False):
        cleanup = super()._rmtree
        options = {"ignore_errors": ignore_errors}
        if "repeated" in inspect.signature(cleanup).parameters:
            options["repeated"] = repeated
        return cleanup(str(native_test_path(Path(name).absolute())), **options)
