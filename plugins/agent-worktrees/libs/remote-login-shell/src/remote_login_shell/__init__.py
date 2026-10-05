"""Shared POSIX login-shell command wrapping for remote SSH exec.

A bare ``ssh host "command"`` is both non-interactive AND non-login for the
remote shell. On a POSIX target, neither ``~/.profile`` (login-shell-only) nor
a ``~/.bashrc``/``~/.zshrc`` entry placed after the interactive-shell guard
ever runs for it -- so anything relying on a PATH addition made there
(``uv``, ``copilot``, ``gh``, or any other tool installed to
``~/.local/bin``) is unreachable, even though it works fine from an actual
interactive/login session.

Forcing a login shell (``<shell> -lc``, using the caller's configured shell
itself -- never a hardcoded ``bash`` substituted for a different configured
one) makes the POSIX target source its own **login** startup files (e.g.
``~/.profile``) before running the command -- not the interactive-only
portion of ``~/.bashrc``, since ``-lc`` starts a non-interactive login
shell, never an interactive one. A PATH addition must live in a login-loaded
file (or in ``~/.bashrc`` content placed before its own interactive-shell
guard) to be reachable this way.

This module is the single shared source for that wrapping, vendored (shared,
not duplicated) across every plugin that execs a command over SSH -- see
this package's own ``README.md`` § Vendoring for the mechanism.
"""

from __future__ import annotations

import shlex

__all__ = ["POSIX_LOGIN_SHELLS", "is_posix_login_shell", "wrap_login_shell"]

#: Documented, supported POSIX remote-shell values
#: (``plugins/agent-bridge/docs/machine-config.md``) that accept a
#: ``-lc <command>`` login-shell invocation.
POSIX_LOGIN_SHELLS = ("bash", "sh", "zsh")


def is_posix_login_shell(shell: str) -> bool:
    """Whether ``shell`` is a documented POSIX shell supporting ``-lc``.

    Callers use this to decide whether to wrap at all -- never guess for an
    unrecognized/empty ``shell`` value (e.g. ``"pwsh"``), since wrapping a
    non-POSIX target in a POSIX login shell would break it outright.
    """
    return shell in POSIX_LOGIN_SHELLS


def wrap_login_shell(command: str, shell: str = "bash") -> str:
    """Wrap ``command`` so it runs as a login shell under ``shell``.

    Safely quotes ``command`` with :func:`shlex.quote`. Does not itself check
    :func:`is_posix_login_shell` -- callers that support both POSIX and
    non-POSIX remote shells (e.g. ``pwsh`` on Windows) must guard the call
    with that check themselves and leave a non-POSIX ``command`` untouched.
    """
    return f"{shell} -lc {shlex.quote(command)}"
