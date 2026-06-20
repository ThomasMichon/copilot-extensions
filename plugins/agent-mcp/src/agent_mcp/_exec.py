"""Cross-platform executable resolution for child-process spawns.

Python's ``asyncio.create_subprocess_exec`` (and the ``CreateProcess`` it calls
on Windows) only auto-appends ``.exe`` when locating a bare command name -- it
does *not* consult ``PATHEXT``. So a command that exists only as a
``.cmd``/``.bat`` shim raises ``FileNotFoundError`` even though it is on ``PATH``
and runs fine from a shell. This bites two common bridge inputs on Windows:

* upstream stdio servers launched via ``npx`` (ships as ``npx.cmd``), and
* ``command``-kind auth that shells out to a ``.cmd`` binstub such as a
  facility ``vault`` -> ``vault.cmd`` credential printer.

``shutil.which`` *is* ``PATHEXT``-aware, so resolving ``argv[0]`` to its full
path before spawning fixes ``.cmd``/``.bat`` shims uniformly. On POSIX this is a
harmless normalization (it yields the same absolute path the OS would have
found). If resolution fails, the original ``argv[0]`` is kept so the caller's
existing ``FileNotFoundError`` handling still fires with the original name.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence


def resolve_argv(argv: Sequence[str]) -> list[str]:
    """Return ``argv`` with ``argv[0]`` resolved to a full executable path.

    Uses :func:`shutil.which` (``PATHEXT``-aware on Windows) so ``.cmd``/``.bat``
    shims resolve. If the command cannot be found, ``argv`` is returned
    unchanged so the spawn raises its normal ``FileNotFoundError``.
    """
    out = list(argv)
    if not out:
        return out
    resolved = shutil.which(out[0])
    if resolved:
        out[0] = resolved
    return out
