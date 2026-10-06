"""Canonicalized "is this the local machine?" check.

The same physical machine can be spelled differently in different contexts:
a claim/codename recorded under a raw COMPUTERNAME before the
``hostname:``-field decoupling landed vs. the current canonical alias, or a
caller passing a ``machines.yaml`` registry key where another caller passes
its alias. A bare string comparison (``name == config.machine``) then
silently misses that the target IS this machine, and dispatches an
unnecessary (and sometimes self-looping, over a devtunnel/SSH mesh) network
call. Every machine-targeted SSH dispatch path should resolve "is this
local?" through :func:`is_local_machine` instead.
"""

from __future__ import annotations

import socket

from . import config as cfg


def is_local_machine(name: str, config: cfg.Config) -> bool:
    """True when ``name`` (a machine key, alias, ``hostname`` field, or
    display_name) refers to the CURRENT machine.

    Canonicalizes both sides through the registry (:func:`config.find_machine_entry`,
    the same key/alias/hostname/display_name matching ``config.detect_machine``
    itself uses) and, as a final ground-truth check, the real local OS
    hostname, before deciding.
    """
    if not name:
        return False
    if name.lower() == config.machine.lower():
        return True
    try:
        entries = cfg.load_machines_yaml(config.default_repo.anchor)
    except (FileNotFoundError, ValueError, KeyError):
        return False
    target = cfg.find_machine_entry(entries, name)
    if target is None:
        return False
    this = cfg.find_machine_entry(entries, config.machine)
    if this is not None and target is this:
        return True
    # Ground truth: does the target entry's own key/hostname field match
    # this machine's real OS hostname, regardless of what config.machine
    # itself resolved to?
    real_hostname = socket.gethostname().lower()
    if target.hostname and target.hostname.lower() == real_hostname:
        return True
    if target.key.lower() == real_hostname:
        return True
    return False
