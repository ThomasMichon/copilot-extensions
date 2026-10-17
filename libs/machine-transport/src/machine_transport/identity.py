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

from .execution_identity import detect_platform, qualify_guest
from .machine_environment import split_machine_environment
from .registry import MachineIdentity, find_machine_entry, resolve_machine_selection

__all__ = ["is_local_machine", "local_execution_platform", "is_observed_local_name"]


def local_execution_platform() -> str:
    """Detect the current logical platform without probing or launching WSL."""
    return detect_platform()


def is_observed_local_name(
    name: str, *, config_machine: str, entries: Mapping[str, MachineIdentity],
    environment: str, real_hostname: str | None = None,
) -> bool:
    """Bind an otherwise unregistered observed hostname to this local context only."""
    if not name or not config_machine:
        return False
    try:
        target = resolve_machine_selection(entries, name, environment)
        if target.entry is not None:
            return False  # Registry keys, profiles and physical associations win.
        current = resolve_machine_selection(entries, config_machine, environment)
        if current.target.environment != target.target.environment:
            return False
        hostname = real_hostname or socket.gethostname()
        observed = {
            split_machine_environment(value, environment).machine.casefold()
            for value in (hostname, hostname.split(".")[0])
        }
        return target.target.machine.casefold() in observed
    except ValueError:
        return False


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
    ``Config.machine``) -- for native callers, checked BEFORE ``load_entries``,
    so a configured local alias still resolves local during a registry
    outage or malformed file (mirroring this function's own historical
    ordering; preserved deliberately, not merely for performance). Every
    comparison against ``config_machine`` is gated on it being non-empty: an
    unset ``config_machine`` (legitimately empty for some consumers) must
    never blanket-match an empty/unset registry field.

    ``load_entries`` is called at most once, when the native fast path did not
    settle locality (guest callers always need the authority check), and only its
    ``FileNotFoundError``/``ValueError``/``KeyError`` are treated as "no
    registry available" (degrades to ``False`` for an unresolved name rather
    than raising); any other exception propagates, since that is not this
    function's documented degrade-safe case.

    ``real_hostname`` defaults to the real local OS hostname
    (``socket.gethostname()``) -- the final ground-truth check, independent
    of what ``config_machine`` itself resolved to. Override only for tests.

    ``guest=True`` validates configured guest authority before accepting even
    an exact spelling. Registered WSL keys need not have a suffix; without a
    guest entry, only qualified guest self names retain the unscoped/absent
    registry fallback. It marks the CALLER's own execution as a WSL guest: a guest
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
    if not guest and config_machine and name_lower == config_machine.lower():
        return True
    try:
        entries = load_entries()
    except FileNotFoundError:
        return bool(
            guest and config_machine and name_lower == config_machine.lower()
            and split_machine_environment(config_machine).environment == "wsl"
        )
    except (ValueError, KeyError):
        return False
    if guest and config_machine:
        try:
            configured = find_machine_entry(entries, config_machine)
        except ValueError:
            return False
        if configured is not None:
            declared = getattr(configured, "execution_platform", "")
            guest_identity = (
                declared == "wsl" if declared
                else split_machine_environment(configured.key).environment == "wsl"
            )
            if not guest_identity:
                return False
        elif name_lower == config_machine.lower():
            return (
                split_machine_environment(config_machine).environment == "wsl"
                and not any(
                    getattr(entry, "execution_platform", "") and not getattr(entry, "legacy_key", "")
                    for entry in entries.values()
                )
            )
    if not entries and config_machine:
        if is_observed_local_name(
            name, config_machine=config_machine, entries=entries,
            environment="wsl" if guest else local_execution_platform(), real_hostname=real_hostname,
        ):
            return True
        target = resolve_machine_selection(entries, name).target
        current = resolve_machine_selection(
            entries, config_machine, "wsl" if guest else local_execution_platform(),
        ).target
        return (
            bool(target.environment) and target.environment == current.environment
            and target.machine.casefold() == current.machine.casefold()
        )
    target = find_machine_entry(entries, name)
    if target is None:
        return is_observed_local_name(
            name, config_machine=config_machine, entries=entries,
            environment="wsl" if guest else local_execution_platform(),
            real_hostname=real_hostname,
        )
    if config_machine:
        this = find_machine_entry(entries, config_machine)
        # Entry-OBJECT identity (not a string comparison against
        # ``config_machine``) deliberately: this is what makes an
        # alias-vs-key spelling mismatch for the SAME registry entry safe,
        # without also risking an empty-string false-positive the way
        # comparing two possibly-empty alias strings directly would.
        if this is not None:
            return target is this
    if getattr(target, "execution_platform", ""):
        return False
    hostname = (real_hostname or socket.gethostname()).lower()
    target_hostname = target.hostname
    if guest:
        hostname = qualify_guest(hostname)
        if split_machine_environment(target.key).environment == "wsl" and target_hostname:
            target_hostname = qualify_guest(target_hostname)
    if target_hostname and target_hostname.lower() == hostname:
        return True
    if target.key.lower() == hostname:
        return True
    return False
