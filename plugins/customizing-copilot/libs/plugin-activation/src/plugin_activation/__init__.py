"""Effective Copilot plugin activation across global and registered-project scopes."""

from .resolver import (
    ActivationReport,
    ActivePlugin,
    ActivePluginRoot,
    normalize_remote,
    resolve_active_plugins,
)
from .state import (
    ActivationSnapshot,
    PluginStateError,
    activation_value,
    capture,
    inspect_plugin_state,
    installed_plugin_identities,
    inventory_identity,
    inventory_records,
    read_json_object,
    remove_activation_entries,
    remove_user_activation,
    restore,
    run_install_preserving_activation,
    validate_identity,
    write_json_object_atomic,
)

__all__ = [
    "ActivationReport",
    "ActivationSnapshot",
    "ActivePlugin",
    "ActivePluginRoot",
    "PluginStateError",
    "activation_value",
    "capture",
    "inspect_plugin_state",
    "installed_plugin_identities",
    "inventory_identity",
    "inventory_records",
    "normalize_remote",
    "read_json_object",
    "remove_activation_entries",
    "remove_user_activation",
    "restore",
    "resolve_active_plugins",
    "run_install_preserving_activation",
    "validate_identity",
    "write_json_object_atomic",
]
