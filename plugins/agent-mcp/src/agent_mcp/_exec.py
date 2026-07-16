"""Cross-platform executable resolution for child-process spawns.

Python's ``asyncio.create_subprocess_exec`` (and the ``CreateProcess`` it calls
on Windows) only auto-appends ``.exe`` when locating a bare command name -- it
does *not* consult ``PATHEXT``. So a command that exists only as a
``.cmd``/``.bat`` shim raises ``FileNotFoundError`` even though it is on ``PATH``
and runs fine from a shell. This bites two common bridge inputs on Windows:

* upstream stdio servers launched via ``npx`` (ships as ``npx.cmd``), and
* ``command``-kind auth that shells out to a ``.cmd`` binstub such as a
  a ``vault``-style ``vault.cmd`` credential printer.

``shutil.which`` *is* ``PATHEXT``-aware, so resolving ``argv[0]`` to its full
path before spawning fixes ``.cmd``/``.bat`` shims uniformly. On POSIX this is a
harmless normalization (it yields the same absolute path the OS would have
found). If resolution fails, the original ``argv[0]`` is kept so the caller's
existing ``FileNotFoundError`` handling still fires with the original name.

**Windows arg fidelity (the ``cli`` transport).** A ``.cmd``/``.bat`` shim is a
batch script: ``cmd.exe`` re-parses the forwarded ``%*``, so metacharacters
(``&``, ``|``, ``%``, ``^``, quotes) and even a Unicode dash in flag position get
mangled or drop out. For a transport that binds *arbitrary tool arguments* into
an argv, that is corruption. :func:`resolve_spawn` therefore prefers a sibling
``.ps1`` (PowerShell receives its arguments as a faithful ``@args`` array) and
invokes it via ``pwsh -NoProfile -File``, falling back to the ``.cmd`` only as a
last resort. The stdio-MCP path keeps using :func:`resolve_argv` unchanged -- it
launches ``npx``-style servers and needs stdin streaming, which a ``.ps1`` shim
does not reliably provide.
"""

from __future__ import annotations

import os
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


def _find_ps1_on_path(name: str, *, path: str | None = None) -> str | None:
    """Return the full path of a ``<name>.ps1`` on ``PATH``, or ``None``.

    ``.ps1`` is not in ``PATHEXT`` (and isn't directly executable), so
    :func:`shutil.which` never returns one; we search ``PATH`` explicitly. An
    ``argv[0]`` that already ends in ``.ps1`` is honored directly.
    """
    if name.lower().endswith(".ps1"):
        return name if os.path.isfile(name) else None
    # A path-qualified command: look only next to it.
    head, tail = os.path.split(name)
    if head:
        cand = name + ".ps1"
        return cand if os.path.isfile(cand) else None
    dirs = (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep)
    for d in dirs:
        if not d:
            continue
        cand = os.path.join(d, f"{tail}.ps1")
        if os.path.isfile(cand):
            return cand
    return None


def resolve_spawn(
    argv: Sequence[str],
    *,
    is_windows: bool | None = None,
    path: str | None = None,
    pwsh: str | None = None,
) -> list[str]:
    """Resolve ``argv`` for an arg-faithful subprocess spawn (the ``cli`` path).

    On Windows, prefer a sibling ``.ps1`` (invoked via ``pwsh``/``powershell``
    ``-NoProfile -File``) so forwarded arguments are not re-parsed by ``cmd.exe``;
    fall back to :func:`resolve_argv` (which finds a ``.cmd``/``.exe`` via
    ``PATHEXT``) only when no ``.ps1`` or PowerShell host is available. On POSIX
    this is exactly :func:`resolve_argv`.

    The keyword args are injection seams for testing; in normal use they default
    to the live platform, ``PATH``, and an auto-detected PowerShell host.
    """
    out = list(argv)
    if not out:
        return out
    if is_windows is None:
        is_windows = os.name == "nt"
    if not is_windows:
        return resolve_argv(out)

    ps1 = _find_ps1_on_path(out[0], path=path)
    if ps1:
        host = pwsh or shutil.which("pwsh") or shutil.which("powershell")
        if host:
            return [host, "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", ps1, *out[1:]]
    # Last resort: a .cmd/.exe via PATHEXT (may mangle args -- see module docs).
    return resolve_argv(out)
