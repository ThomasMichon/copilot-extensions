"""Repair stored provider launchers whose payload or version slot was retired."""

from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("agent-bridge")


_RETIRED_PAYLOAD_PATH = re.compile(
    r"(?P<root>.*?)[\\/]"
    r"(?:\.install-stage[\\/][^\\/]+[\\/][^\\/]+|snapshots[\\/][^\\/]+)"
    r"(?P<tail>[\\/].+)"
)


def _reresolve_retired_payload_path(exe: str) -> str | None:
    """Map a missing staged or snapshot payload path onto ``payload-dir``.

    Installers publish each runtime's stable payload through
    ``<runtime>/payload-dir``. A session recorded while the provider resolved
    a throwaway ``<runtime>/.install-stage/<stamp>/<payload>/`` copy, or a
    since-pruned ``<runtime>/snapshots/<ver>/`` slot, keeps that path after the
    copy is removed. Repoint the same tail onto the current published payload.
    """
    match = _RETIRED_PAYLOAD_PATH.match(exe)
    if match is None:
        return None
    try:
        with open(
            os.path.join(match.group("root"), "payload-dir"), encoding="utf-8",
        ) as handle:
            payload = handle.read().strip()
    except (OSError, UnicodeError):
        return None
    if (
        not payload or not os.path.isabs(payload)
        or re.search(r"[\\/]\.install-stage(?:[\\/]|$)", payload)
    ):
        return None
    tail = [part for part in re.split(r"[\\/]", match.group("tail")) if part]
    candidate = os.path.join(payload, *tail)
    if not os.path.isfile(candidate):
        return None
    log.warning(
        "Repointed stale provider command %r -> %r (payload copy removed)",
        exe, candidate,
    )
    return candidate


def _reresolve_stale_interpreter(args: list[str]) -> list[str]:
    """Repoint a stored command whose versioned interpreter was pruned.

    A ``command`` target persists its full ``spawn_command`` -- including the
    absolute path to a provider's versioned interpreter, e.g.
    ``~/.agent-codespaces/versions/0.4.0-dev39/Scripts/python.exe``. When the
    provider (agent-codespaces, agent-bridge, ...) upgrades and prunes the old
    ``versions/<ver>/`` slot, resuming that session would spawn a now-missing
    executable and fail with ``FileNotFoundError`` (WinError 2 on Windows) --
    before the resume recovery ladder can react.

    When ``args[0]`` is an absolute path that no longer exists but sits under a
    ``.../versions/<ver>/`` root, remap ``<ver>`` to the provider's current
    version (its ``current-version`` marker) or, failing that, the newest
    existing sibling by natural (numeric) order. A missing path under a
    removed install stage or pruned payload snapshot is remapped onto the
    runtime's stable ``payload-dir`` pointer with the same tail. No-op when
    the path still exists or nothing resolves the same tail.
    """
    if not args:
        return args
    exe = args[0]
    if os.path.exists(exe):
        return args
    match = re.match(
        r"(?P<base>.*[\\/]versions[\\/])(?P<ver>[^\\/]+)(?P<tail>[\\/].*)", exe
    )
    if match is None:
        payload = _reresolve_retired_payload_path(exe)
        return args if payload is None else [payload, *args[1:]]
    base, tail = match.group("base"), match.group("tail")

    # Prefer the provider's authoritative current-version marker (a sibling of
    # the versions/ dir); fall back to the newest existing sibling by *natural*
    # (numeric) order so a dev62 slot is never shadowed by a lexicographically
    # larger dev9.
    def _natural_key(name: str) -> list[str]:
        return [
            f"{int(part):020d}" if part.isdigit() else part
            for part in re.split(r"(\d+)", name)
        ]

    ordered: list[str] = []
    marker = os.path.join(os.path.dirname(base.rstrip("\\/")), "current-version")
    try:
        with open(marker, encoding="utf-8") as handle:
            ordered.append(handle.read().strip())
    except OSError:
        pass
    try:
        ordered.extend(sorted(os.listdir(base), key=_natural_key, reverse=True))
    except OSError:
        pass

    for ver in ordered:
        if not ver:
            continue
        candidate = f"{base}{ver}{tail}"
        if os.path.exists(candidate):
            log.warning(
                "Repointed stale interpreter %r -> %r (version slot pruned)",
                exe, candidate,
            )
            return [candidate, *args[1:]]
    return args
