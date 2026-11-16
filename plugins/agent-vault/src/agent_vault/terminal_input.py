"""Controlling-terminal input, separate from credential and service ownership."""

from __future__ import annotations

import os
import sys


def _read_password_from_tty(prompt: str) -> str | None:
    """Read a password from the controlling terminal with echo disabled."""
    if sys.platform == "win32" or os.name == "nt":
        import getpass

        try:
            return getpass.getpass(prompt)
        except (EOFError, KeyboardInterrupt):
            return None

    try:
        import termios

        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return None

    try:
        old = termios.tcgetattr(fd)
        new = list(old)
        new[3] &= ~termios.ECHO
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, new)
            os.write(fd, prompt.encode("utf-8", "replace"))
            data = os.read(fd, 4096)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            os.write(fd, b"\n")
        return data.decode("utf-8", "replace").rstrip("\r\n")
    except (OSError, termios.error):
        return None
    finally:
        os.close(fd)


def _has_controlling_tty() -> bool:
    """Whether an interactive controlling terminal is available for a prompt.

    On POSIX this checks that ``/dev/tty`` can be opened -- it works even when
    stdin is piped, as long as the process has a controlling terminal (e.g. an
    interactive SSH session). On Windows it falls back to ``sys.stdin.isatty()``.
    Used to decide whether the inline-terminal unlock fallback can reach the
    operator, so a headless/agent caller fails fast instead of printing a
    "no terminal" error.
    """
    if sys.platform == "win32" or os.name == "nt":
        return sys.stdin.isatty()
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return False
    os.close(fd)
    return True
