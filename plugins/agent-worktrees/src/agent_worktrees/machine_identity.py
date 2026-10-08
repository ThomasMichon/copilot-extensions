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

This is a thin, agent-worktrees-flavored wrapper over the shared
``machine_transport.is_local_machine`` (vendored, not duplicated -- see its
own module docstring for the full canonicalization algorithm): it supplies
this plugin's own ``Config``/``load_machines_yaml`` shapes so every existing
call site keeps its original ``(name, config)`` signature.
"""

from __future__ import annotations

from machine_transport import is_local_machine as _mt_is_local_machine

from . import config as cfg


def is_local_machine(name: str, config: cfg.Config) -> bool:
    """True when ``name`` (a machine key, alias, ``hostname`` field, or
    display_name) refers to the CURRENT machine.

    Canonicalizes both sides through the registry (the same key/alias/
    hostname/display_name matching ``config.detect_machine`` itself uses)
    and, as a final ground-truth check, the real local OS hostname, before
    deciding.
    """
    return _mt_is_local_machine(
        name,
        config_machine=config.machine,
        load_entries=lambda: cfg.load_machines_yaml(config.default_repo.anchor),
    )
