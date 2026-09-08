"""Live version-staleness detection for the coordinator's own self-update.

``agent-dispatch deploy`` (see ``__main__._cmd_cutover``) already performs a
zero-downtime cutover to whatever code is installed in *its own* interpreter --
but it must be invoked by something. Historically that "something" was always
external: the installer, right after writing a new version's slot, or an
operator running ``deploy`` by hand. A version bump that lands between
sessions (``copilot plugin update`` bumps the payload; a later
``install.ps1 update`` publishes a new ``versions/<version>`` slot and flips
the ``current-version`` marker) left the *running* coordinator lagging until
something external noticed and redeployed it.

This module supplies the missing piece: a fail-safe, fully-injectable
predicate -- :func:`stale_target` -- that a coordinator's own background loop
can poll to ask "is a different, fully-installed version now published, and
should I hand off to it?". It intentionally does **not** run the cutover
itself (that stays the well-tested :class:`zdd.cutover.CutoverOrchestrator`
path); it only resolves *which interpreter* a self-triggered ``deploy`` should
spawn.

The ``current-version`` marker file and the per-version slot layout
(``versions/<version>/{bin/python,Scripts/python.exe}``) are owned by
``scripts/versioned_runtime.py`` (a stdlib-only bootstrap helper kept out of
every runtime venv, so it cannot be imported from here) -- this module reuses
:mod:`agent_dispatch.procutil`'s marker/slot readers (which already mirror
that on-disk convention for resolving a sibling plugin's runtime) instead of
re-implementing them a third time.
"""

from __future__ import annotations

from pathlib import Path

from .procutil import _read_marker, _slot_python

CURRENT_VERSION_FILE = "current-version"


def read_current_version(root: Path) -> str | None:
    """The version published by the ``current-version`` marker, or ``None``.

    Missing file, empty content, or any read error is treated as "no marker"
    (fail-safe: the caller stays on its running version). Thin wrapper over
    :func:`agent_dispatch.procutil._read_marker` -- the slot-python resolver
    (:func:`agent_dispatch.procutil.resolve_runtime_python`) already reads
    this same marker convention; reusing it here keeps exactly one place that
    knows the on-disk layout.
    """
    return _read_marker(root, CURRENT_VERSION_FILE)


def slot_python(root: Path, version: str) -> Path | None:
    """The interpreter path for ``versions/<version>``, or ``None`` if absent.

    A present-but-incomplete install (e.g. an interrupted venv build) has no
    interpreter binary at this path yet, so this doubles as a lightweight
    completeness check. Thin wrapper over
    :func:`agent_dispatch.procutil._slot_python`.
    """
    return _slot_python(root, version)


def stale_target(
    root: Path,
    running_version: str,
    *,
    read_marker=read_current_version,
    resolve_slot_python=slot_python,
) -> Path | None:
    """The interpreter to hand off to, or ``None`` if we should stay put.

    Fail-safe by construction, mirroring :func:`agent_dispatch.self_retire.
    is_superseded`: returns a concrete interpreter path **only** when the
    ``current-version`` marker names a version that (a) differs from
    ``running_version`` and (b) has a fully installed interpreter on disk.
    Every ambiguous state -- no marker, a marker matching what is already
    running, or a marker naming a version whose slot is missing/incomplete --
    returns ``None`` (stay on the running version).
    """
    marker = read_marker(root)
    if not marker or marker == running_version:
        return None
    return resolve_slot_python(root, marker)
