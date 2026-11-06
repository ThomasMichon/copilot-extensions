"""Live-use proof before a derived-output reclaim deletes anything.

Process metadata is not always readable, so an empty best-effort scan is not
proof that nothing uses a directory. The checks are layered so an unknown
answer refuses instead of passing:

* Every platform: working-directory discovery fails closed when enumeration is
  unavailable (``procs.checkout_retirement_blocker``).
* POSIX: every live process owned by this user must be fully inspectable. One
  whose working directory or executable cannot be read refuses the reclaim.
* Windows: elevated, sandboxed and protected processes never expose their
  working directory, even to their own user, so the reclaim first moves the
  directory aside (:func:`move_aside`). Windows refuses that move while any
  process works in the directory or holds a file inside it open. Executables
  run from it are found by image path.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path


def blocker(target: str | os.PathLike[str], *, proc_root: Path = Path("/proc")) -> str | None:
    """Refusal reason when a live process uses ``target``, or when that cannot
    be ruled out; ``None`` only when the platform's checks prove it unused."""
    from . import procs

    root = os.path.realpath(target)
    if found := procs.checkout_retirement_blocker(root):
        return found
    if os.name != "nt":
        return _posix_census(root, proc_root)
    try:
        images = procs.processes_with_executable_under(root)
    except OSError as exc:
        return f"executable inspection unavailable: {exc}; reclaim deferred"
    if images:
        names = ", ".join(f"{p.get('name') or '?'}({p['pid']})" for p in images)
        return f"live process(es) run an executable from it: {names}"
    return None


def _zombie(entry: Path) -> bool:
    stat_text = (entry / "stat").read_text(errors="ignore")
    fields = stat_text[stat_text.rfind(")") + 1:].split()
    return bool(fields) and fields[0] == "Z"


def _posix_census(root: str, proc_root: Path) -> str | None:
    from . import procs

    uid, me = os.getuid(), os.getpid()
    try:
        entries = [entry for entry in proc_root.iterdir() if entry.name.isdigit()]
    except OSError as exc:
        return f"process inspection unavailable: {exc}; reclaim deferred"
    users: list[str] = []
    opaque: list[str] = []
    for entry in entries:
        if int(entry.name) == me:
            continue
        try:
            if entry.stat().st_uid != uid or _zombie(entry):
                continue
            paths = [os.readlink(entry / "cwd"), os.readlink(entry / "exe")]
        except FileNotFoundError:
            continue  # exited during the census
        except OSError:
            opaque.append(entry.name)
            continue
        if any(procs._is_under(p.removesuffix(" (deleted)"), root) for p in paths):
            users.append(entry.name)
    if users:
        return f"live process(es) use it: pid {', '.join(users)}"
    if opaque:
        return (f"cannot inspect live process(es) owned by this user (pid {', '.join(opaque)}); "
                "reclaim deferred")
    return None


def move_aside(target: Path) -> tuple[Path | None, str]:
    """On Windows, rename ``target`` to a sibling before deleting it, so the
    kernel proves no process works in it or holds a file inside it open.
    Elsewhere the directory is deleted in place."""
    if os.name != "nt":
        return target, ""
    aside = target.with_name(f"{target.name}.reclaiming-{secrets.token_hex(4)}")
    try:
        os.rename(target, aside)
    except OSError as exc:
        return None, f"Windows refused to move it aside, so a process still holds it ({exc})"
    return aside, ""
