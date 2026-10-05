"""Local binstub resolution for ``routes/worktrees.py``.

Extracted into its own module (``worktrees.py`` is at its grandfathered
module-size ceiling) rather than inlined there, mirroring this plugin's own
precedent (``worktree_holders.py``, ``worktree_probe.py``).
"""

from __future__ import annotations

import os


def resolve_local_binstub(project: str) -> str:
    """Resolve *project*'s binstub to a directly-executable path.

    ``asyncio.create_subprocess_exec`` never consults Windows' ``PATHEXT``
    the way a shell does, so an extensionless name can't resolve to the
    installed ``.cmd``/``.ps1`` shim (``FileNotFoundError: [WinError 2]``).

    ``shutil.which`` only applies its own PATHEXT-aware suffix search when
    given a **bare** command name; a candidate that already contains a
    directory component (like the explicit ``~/.local/bin/<project>`` path
    below) is checked for an *exact* match only, with no suffix search at
    all -- so it silently misses the installed ``.cmd``/``.ps1`` shim sitting
    right next to it. Try the exact name first -- accepted only when it is
    genuinely launchable (POSIX: executable via ``os.access(X_OK)``;
    Windows: restricted to an extension ``CreateProcess`` can launch
    *directly*, with no interpreter -- ``PATHEXT`` itself is broader than
    that (it also lists interpreter-dependent extensions like ``.PS1``/
    ``.PY``/``.JS``, present on this machine purely so an interactive shell
    knows to look them up, not because they're directly spawnable the way
    ``create_subprocess_exec`` needs), so picking an arbitrary ``PATHEXT``
    match by position risks selecting a ``.ps1`` ahead of an equally-present
    ``.cmd`` and failing to launch at all -- a plain extensionless file is
    *never* launchable on Windows this way, so it must fall through exactly
    like a non-executable POSIX file does) -- then each directly-launchable
    suffix against that same directory on Windows. The final **PATH**
    fallback applies the identical restriction on Windows (a bare
    ``shutil.which(project)`` could itself resolve to a ``.ps1``/``.py``/...
    match ahead of an equally-present ``.cmd`` on PATH, the same failure
    mode as the explicit-path case) by searching PATH directly rather than
    trusting ``shutil.which``'s own, broader PATHEXT order; POSIX keeps the
    plain ``shutil.which`` fallback, since its own ``os.access(X_OK)`` check
    already guarantees direct launchability there.
    """
    import shutil
    from pathlib import Path

    # Extensions CreateProcess can exec with no interpreter in front of it.
    # Deliberately narrower than PATHEXT (which also lists .PS1/.PY/.JS/...
    # for an interactive shell's own lookup, not direct process creation).
    direct_launch_exts = {".COM", ".EXE", ".BAT", ".CMD"}
    # shutil.which's own hardcoded fallback when PATHEXT is unset/empty in
    # the environment (cmd.exe itself falls back to the same built-in list)
    # -- without it, a missing/blank PATHEXT silently zeroes every suffix
    # candidate below and every lookup falls through to the unresolved name.
    _win_default_pathext = ".COM;.EXE;.BAT;.CMD;.VBS;.JS;.WS;.MSC"

    explicit = Path.home() / ".local" / "bin" / project
    if os.name == "nt":
        pathext = []
        for raw in (os.environ.get("PATHEXT") or _win_default_pathext).split(
            os.pathsep
        ):
            # Entries can carry stray whitespace, and (rarely) omit the
            # leading dot -- normalize both before matching/building a
            # candidate name, or a well-formed extension like " .CMD" would
            # silently never match and a dot-less one would build a wrong
            # (unseparated) candidate filename.
            ext = raw.strip()
            if not ext:
                continue
            if not ext.startswith("."):
                ext = "." + ext
            pathext.append(ext)
        if explicit.suffix.upper() in direct_launch_exts and explicit.is_file():
            return str(explicit)
        for ext in pathext:
            if ext.upper() not in direct_launch_exts:
                continue
            candidate = explicit.with_name(explicit.name + ext)
            if candidate.is_file():
                return str(candidate)
        # PATH fallback, restricted the same way: don't trust a bare
        # shutil.which(project) match that might be an interpreter-
        # dependent script ranked ahead of a directly-launchable one.
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if not directory:
                continue
            base = Path(directory) / project
            for ext in pathext:
                if ext.upper() not in direct_launch_exts:
                    continue
                candidate = base.with_name(base.name + ext)
                if candidate.is_file():
                    return str(candidate)
        return project
    if explicit.is_file() and os.access(explicit, os.X_OK):
        return str(explicit)
    return shutil.which(project) or project
