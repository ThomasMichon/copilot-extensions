"""Declarative installer/readiness discovery -- public re-export surface.

The actual implementation is split by responsibility:
:mod:`installer_readiness.manifest` (one installation's JSON-contract
parsing), :mod:`installer_readiness.graph` (cross-installation discovery and
dependency-graph validation), and :mod:`installer_readiness.settings`
(resolving enabled-plugin settings into installation-cell receipts). This
module re-exports the stable public surface so existing imports of
``installer_readiness.discovery`` keep working unchanged.
"""

from __future__ import annotations

from .graph import discover_modules
from .manifest import (
    CONTRACT_SCHEMA,
    CONTRACT_VERSION,
    READINESS_SCHEMA,
    READINESS_VERSION,
)
from .settings import (
    SettingsGroup,
    SettingsLayer,
    discover_from_settings,
    installations_from_settings,
)

__all__ = [
    "CONTRACT_SCHEMA",
    "CONTRACT_VERSION",
    "READINESS_SCHEMA",
    "READINESS_VERSION",
    "SettingsGroup",
    "SettingsLayer",
    "discover_from_settings",
    "discover_modules",
    "installations_from_settings",
]
