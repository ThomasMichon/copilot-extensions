"""Canonicalized "is this the local machine?" check.

The same physical machine can be spelled differently in different contexts:
a claim/codename recorded under a raw COMPUTERNAME before the
``hostname:``-field decoupling landed vs. the current canonical alias, a
caller passing a ``machines.yaml`` registry key where another caller passes
its alias, or (the self-looping-SSH bug this consolidation was born from --
``copilot-extensions`` Worktree Manager picker, 2026-10) a cloud/devtunnel-
provisioned box whose real OS hostname differs from its configured alias
entirely. A bare string comparison (``name == config_machine``) then
silently misses that the target IS this machine, and dispatches an
unnecessary (and sometimes self-looping, over a devtunnel/SSH mesh) network
call. Every machine-targeted dispatch path should resolve "is this local?"
through :func:`is_local_machine` instead.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Mapping

from .execution_identity import qualify_guest
from .registry import MachineIdentity, find_machine_entry

__all__ = ["is_local_machine"]


def is_local_machine(
    name: str,
    *,
    config_machine: str,
    load_entries: Callable[[], Mapping[str, MachineIdentity]],
    real_hostname: str | None = None,
    guest: bool = False,
) -> bool:
    """True when ``name`` (a machine key, alias, ``hostname`` field, or
    display_name) refers to the CURRENT machine.

    ``config_machine`` is this machine's own configured identity (e.g.
    ``Config.machine``) -- checked directly BEFORE calling ``load_entries``,
    so a configured local alias still resolves local during a registry
    outage or malformed file (mirroring this function's own historical
    ordering; preserved deliberately, not merely for performance). Every
    comparison against ``config_machine`` is gated on it being non-empty: an
    unset ``config_machine`` (legitimately empty for some consumers) must
    never blanket-match an empty/unset registry field.

    ``load_entries`` is called at most once, lazily -- only once the direct
    check has already failed to resolve the answer -- and only its
    ``FileNotFoundError``/``ValueError``/``KeyError`` are treated as "no
    registry available" (degrades to ``False`` for an unresolved name rather
    than raising); any other exception propagates, since that is not this
    function's documented degrade-safe case.

    ``real_hostname`` defaults to the real local OS hostname
    (``socket.gethostname()``) -- the final ground-truth check, independent
    of what ``config_machine`` itself resolved to. Override only for tests.

    ``guest=True`` marks the CALLER's own execution as a WSL guest: a guest
    process must never resolve local against its native host's registry
    entry just because ``config_machine`` fuzzy-matched that entry (a guest
    whose own topology has no dedicated ``-wsl`` entry yet) -- native-host
    collision is denied outright rather than silently granted -- and the
    real-hostname ground-truth comparison is itself guest-qualified.
    """
    if not name:
        return False
    name_lower = name.lower()
    config_machine = config_machine or ""
    if config_machine and name_lower == config_machine.lower():
        return True
    try:
        entries = load_entries()
    except (FileNotFoundError, ValueError, KeyError):
        return False
    target = find_machine_entry(entries, name)
    if target is None:
        return False
    if config_machine:
        this = find_machine_entry(entries, config_machine)
        # Entry-OBJECT identity (not a string comparison against
        # ``config_machine``) deliberately: this is what makes an
        # alias-vs-key spelling mismatch for the SAME registry entry safe,
        # without also risking an empty-string false-positive the way
        # comparing two possibly-empty alias strings directly would.
        if this is not None:
            if guest and not this.key.casefold().endswith("-wsl"):
                return False
            if target is this:
                return True
    hostname = (real_hostname or socket.gethostname()).lower()
    target_hostname = target.hostname
    if guest:
        hostname = qualify_guest(hostname)
        if target.key.casefold().endswith("-wsl") and target_hostname:
            target_hostname = qualify_guest(target_hostname)
    if target_hostname and target_hostname.lower() == hostname:
        return True
    if target.key.lower() == hostname:
        return True
    return False
