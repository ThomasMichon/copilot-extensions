"""Normalize network and local Git remotes to a comparable identity string."""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
from urllib.parse import unquote, urlsplit

_SCP_REMOTE = re.compile(r"^(?:[^@/]+@)?([^:/]+):(.+)$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_URI_DRIVE = re.compile(r"^/[A-Za-z]:/")


def _strip_git_suffix(value: str, *, case_insensitive: bool = False) -> str:
    value = value.rstrip("/")
    comparison = value.casefold() if case_insensitive else value
    return value[:-4] if comparison.endswith(".git") else value


def _normalize_windows_path(value: str) -> str:
    normalized = ntpath.normpath(value.replace("/", "\\"))
    return (
        f"file:{_strip_git_suffix(normalized, case_insensitive=True).casefold()}"
    )


def _normalize_posix_path(value: str) -> str:
    return f"file:{_strip_git_suffix(posixpath.normpath(value))}"


def normalize_remote(value: str) -> str | None:
    """Normalize network and local Git remotes without folding URL path case."""
    if not isinstance(value, str):
        return None
    raw = (value or "").strip()
    if not raw:
        return None
    if _WINDOWS_DRIVE.match(raw):
        return _normalize_windows_path(raw)
    if raw.startswith(("\\\\", "//")):
        return _normalize_windows_path(raw)
    if raw.startswith("/"):
        return _normalize_posix_path(raw)

    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.casefold()
    except ValueError:
        return None
    if scheme == "file":
        path = unquote(parsed.path)
        if parsed.netloc and parsed.netloc.casefold() != "localhost":
            return _normalize_windows_path(f"\\\\{parsed.netloc}{path}")
        if _WINDOWS_DRIVE.match(path):
            return _normalize_windows_path(path)
        if _WINDOWS_URI_DRIVE.match(path):
            return _normalize_windows_path(path[1:])
        if path.startswith(("\\\\", "//")):
            return _normalize_windows_path(path)
        return _normalize_posix_path(path)
    if scheme:
        try:
            host = (parsed.hostname or "").casefold()
            port = parsed.port
        except ValueError:
            return None
        if not host or parsed.query or parsed.fragment:
            return None
        if port is not None:
            host = f"{host}:{port}"
        path = _strip_git_suffix(unquote(parsed.path).lstrip("/"))
        return f"network:{host}/{path}" if path else None

    scp = _SCP_REMOTE.match(raw)
    if scp:
        host, path = scp.groups()
        return f"network:{host.casefold()}/{_strip_git_suffix(path.lstrip('/'))}"

    normalized = os.path.normpath(raw)
    if os.name == "nt":
        normalized = os.path.normcase(normalized)
    return f"file-relative:{_strip_git_suffix(normalized)}"
