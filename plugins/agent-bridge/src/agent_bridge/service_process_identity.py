"""Recognize daemon entrypoints, not module names buried in other arguments."""

from __future__ import annotations

import ctypes
import ntpath
import posixpath
import re
import sys


def _basename(value: str) -> str:
    return ntpath.basename(value).lower() if sys.platform == "win32" else posixpath.basename(value)


def _starts_daemon(arguments: list[str]) -> bool:
    for argument in arguments:
        if argument != "--json":
            return argument == "start"
    return False


def bridge_daemon_argv(arguments: list[str]) -> bool:
    """Match the supported Python-module and installed console daemon launches."""
    if not arguments:
        return False
    scripts = {"agent-bridge", "agent-bridge.exe", "agent-bridge-script.py"}
    if _basename(arguments[0]) in scripts:
        return _starts_daemon(arguments[1:])
    if not re.fullmatch(r"pythonw?(?:\d+(?:\.\d+)*)?(?:\.exe)?", _basename(arguments[0])):
        return False
    index = 1
    while index < len(arguments):
        option = arguments[index]
        if option == "-m":
            return (
                arguments[index + 1:index + 2] == ["agent_bridge"]
                and _starts_daemon(arguments[index + 2:])
            )
        if option == "--":
            index += 1
            break
        if option in ("-W", "-X", "--check-hash-based-pycs"):
            index += 2
        elif re.fullmatch(r"-[bBdEiIOPqsSuvx]+", option) or option.startswith(("-W", "-X")):
            index += 1
        elif option.startswith("-"):
            return False
        else:
            break
    return (
        index + 1 < len(arguments)
        and _basename(arguments[index]) in scripts
        and _starts_daemon(arguments[index + 1:])
    )


def split_windows_command(command: str) -> list[str]:
    """Split an OS-reported Windows command line with native quoting rules."""
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    arguments = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not arguments:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [arguments[index] for index in range(count.value)]
    finally:
        kernel.LocalFree(arguments)
