"""Windows filename compatibility for Vault's optional native crypto backend."""

from __future__ import annotations

import ntpath
import sys
from collections.abc import Callable, Sequence
from importlib import import_module


def _native_filename(filename: str) -> str:
    if not ntpath.isabs(filename) or not ntpath.splitdrive(filename)[0]:
        return filename
    extended = filename.startswith("\\\\?\\")
    path_units = len(filename.encode("utf-16-le", errors="surrogatepass")) // 2
    if not extended and (path_units < 260 or filename.startswith("\\\\.\\")):
        return filename
    normalized = ntpath.normpath(filename)
    if extended:
        return normalized
    if normalized.startswith("\\\\"):
        return "\\\\?\\UNC\\" + normalized[2:]
    return "\\\\?\\" + normalized


class _WindowsFilename:
    def __init__(self, original: Callable[[Sequence[str], str], str]) -> None:
        self.original = original

    def __call__(self, components: Sequence[str], filename: str) -> str:
        return _native_filename(self.original(components, filename))


def prepare_native_crypto() -> None:
    if sys.platform != "win32":
        return
    # Patch only the in-memory filename seam, before consumers load primitives.
    # Extended Win32 paths cannot contain the backend's usual Util\.. component.
    raw_api = import_module("Cryptodome.Util._raw_api")
    if not isinstance(raw_api.pycryptodome_filename, _WindowsFilename):
        setattr(raw_api, "pycryptodome_filename", _WindowsFilename(raw_api.pycryptodome_filename))
