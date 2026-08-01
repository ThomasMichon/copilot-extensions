"""Tests for the legacy client-fallback port constant + the dynamic sentinel.

After dotfiles #694, ``default_port()`` is no longer the daemon's *bind* default
(a primary binds an OS-assigned ephemeral port advertised via ``active.json``).
It survives only as the client's last-resort fallback, and the WSL "+1" (9281)
is retired -- an ephemeral bind cannot collide across the Windows/WSL boundary,
so both contexts share the single 9280 fallback. ``ServiceConfig.port`` defaults
to the ``0`` sentinel meaning "unset -> bind dynamic".
"""

from __future__ import annotations

import agent_bridge.models as models
from agent_bridge.models import ServiceConfig


def test_default_port_is_single_fallback_constant():
    assert models.default_port() == 9280


def test_wsl_plus_one_is_retired():
    # _is_wsl and the platform split are gone; the fallback no longer branches.
    assert not hasattr(models, "_is_wsl")


def test_service_config_port_defaults_to_dynamic_sentinel():
    # Port 0 signals "unset -> bind an OS-assigned ephemeral port".
    assert ServiceConfig().port == 0


def test_service_config_pinned_port_round_trips():
    assert ServiceConfig(port=9280).port == 9280

