"""Effective Copilot plugin activation across global and registered-project scopes."""

from .resolver import (
    ActivationReport,
    ActivePlugin,
    normalize_remote,
    resolve_active_plugins,
)

__all__ = [
    "ActivationReport",
    "ActivePlugin",
    "normalize_remote",
    "resolve_active_plugins",
]
