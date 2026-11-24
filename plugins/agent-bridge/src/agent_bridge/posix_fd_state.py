"""Drop Win32-OpenSSH's private stdio descriptor carrier from inherited env.

Win32-OpenSSH ``sshd`` hands its direct child a descriptor-state carrier,
``C28FC6F98A2C44ABBBD89D6A3037D0D9_POSIX_FD_STATE``, meant for that child
only. Environment inheritance passes it on to every descendant: an SSH-hosted
bridge carrier, daemon, Session Host and the Copilot sessions they launch. A
grandchild ``ssh.exe`` then decodes the stale carrier in its
``fd_table_initialize`` (``contrib/win32/win32compat/w32fd.c``) and
mis-types its redirected stdout/stderr pipes: the first stdio write never
completes and ``ssh`` never exits after the remote command ends. A headless
worker's ``ssh -n <origin> ...`` therefore hangs until its caller's timeout.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping

POSIX_FD_STATE_SUFFIX = "_POSIX_FD_STATE"
POWERSHELL_SCRUB = (
    "Get-ChildItem Env: | Where-Object { $_.Name -like '*_POSIX_FD_STATE' } | "
    "ForEach-Object { Remove-Item -LiteralPath ('Env:' + $_.Name) "
    "-ErrorAction SilentlyContinue }; "
)


def scrub_posix_fd_state(env: MutableMapping[str, str] | None = None) -> list[str]:
    """Remove every ``*_POSIX_FD_STATE`` key (case-insensitive) in place."""
    target = os.environ if env is None else env
    names = [name for name in list(target) if name.upper().endswith(POSIX_FD_STATE_SUFFIX)]
    for name in names:
        target.pop(name, None)
    return names
