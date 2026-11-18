"""Shared absolute Git discovery; never starts or retries a command."""

from __future__ import annotations

import os
import shutil
import stat
import sys
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path

from .environment import fresh_user_environment

_cache: dict[tuple[str, str, str, str, str], str] = {}
_lock = threading.Lock()


def _windows() -> bool:
    return os.name == "nt"


def _value(env: Mapping[str, str], name: str, default: str = "") -> str:
    if not _windows():
        return env.get(name, default)
    return next((value for key, value in env.items() if key.upper() == name), default)


def _usable(path: str) -> bool:
    try:
        mode = os.stat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return False
    return stat.S_ISREG(mode) and (_windows() or os.access(path, os.X_OK))


def _anchor(path: str, base: str | os.PathLike[str] | None = None) -> str:
    if not os.path.isabs(path):
        parent = os.getcwd() if base is None else os.fspath(base)
        if not os.path.isabs(parent):
            parent = os.path.join(os.getcwd(), parent)
        path = os.path.join(parent, path) if path else parent
    return os.path.abspath(path) if _windows() else path


def _which(selection: str, path: str) -> str | None:
    found = shutil.which(selection, path=path)
    if found:
        absolute = _anchor(found)
        if _usable(absolute):
            return absolute
    return None


def _windows_registry_candidates() -> Iterator[str]:
    if sys.platform != "win32":
        return
    import winreg

    keys = (
        (r"SOFTWARE\GitForWindows", "InstallPath", True),
        (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\git.exe", "", False),
    )
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            for name, value_name, installation in keys:
                try:
                    with winreg.OpenKey(hive, name, 0, winreg.KEY_READ | view) as key:
                        value, _ = winreg.QueryValueEx(key, value_name)
                except FileNotFoundError:
                    continue
                if isinstance(value, str) and value.strip():
                    location = Path(os.path.expandvars(value.strip()))
                    yield str(location / "cmd" / "git.exe" if installation else location)


def resolve_git(
    explicit: str | None = None, *, env: Mapping[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> str:
    """Resolve Git, honoring explicit input or AGENT_GIT_EXECUTABLE without fallback.

    POSIX automatic PATH entries use the requested child cwd; explicit
    selections remain relative to the launcher. Windows retains native lookup.
    Only positive results are cached, and their executable must still exist at
    the next pre-launch lookup. No command is executed here: missing CWD,
    launch races, timeouts and unknown subprocess outcomes cannot cause replay.
    """
    environment = os.environ if env is None else env
    selected = explicit if explicit is not None else _value(environment, "AGENT_GIT_EXECUTABLE")
    hint = _value(environment, "AGENT_GIT_DISCOVERED_EXECUTABLE")
    path = _value(environment, "PATH", os.defpath)
    try:
        caller_cwd = os.getcwd()
    except FileNotFoundError:
        caller_cwd = ""
    lookup_cwd = os.fspath(cwd) if not selected and cwd is not None else caller_cwd
    if lookup_cwd and not _windows():
        path = os.pathsep.join(
            _anchor(entry, lookup_cwd) for entry in path.split(os.pathsep)
        )
    key = (selected, hint, path, _value(environment, "PATHEXT"), caller_cwd)
    with _lock:
        cached = _cache.get(key)
        if cached and _usable(cached):
            return cached
        _cache.pop(key, None)
        if selected:
            result = _which(os.path.expanduser(selected), path)
            if result is None:
                raise FileNotFoundError(f"Explicit Git executable is unavailable: {selected}")
        else:
            name = "git.exe" if _windows() else "git"
            result = hint if hint and os.path.isabs(hint) and _usable(hint) else _which(name, path)
            if result is None and _windows():
                result = _which(name, _value(fresh_user_environment(), "PATH", os.defpath))
                if result is None:
                    result = next(
                        (os.path.abspath(candidate) for candidate in _windows_registry_candidates()
                         if os.path.isabs(candidate) and _usable(candidate)),
                        None,
                    )
            if result is None:
                raise FileNotFoundError("Git executable is unavailable; configure AGENT_GIT_EXECUTABLE")
        if len(_cache) >= 128:
            _cache.clear()
        _cache[key] = result
        return result
