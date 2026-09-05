"""Read-only installation admission for the attributed host companion."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any


def installation_mode() -> dict[str, Any]:
    """Resolve this payload, never an inherited sibling installation context."""
    plugin = Path(__file__).resolve().parents[1]
    path = plugin / "scripts" / "installation-context" / "installation_context.py"
    spec = importlib.util.spec_from_file_location("_index_companion_context", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("agent-index installation-context resolver is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("AGENT_INDEX_", "COPILOT_PLUGIN_"))
        and key.upper() not in {
            "COPILOT_EXTENSIONS_CONTEXT", "CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT",
        }
    }
    resolution = module.resolve_installation_mode(
        payload_root=plugin,
        plugin_id="agent-index",
        legacy_root=Path.home() / ".agent-index",
        environment=environment,
    )
    return {
        "schema_version": 1,
        "supported": (
            resolution["status"] == "ready"
            and resolution["actualMode"] == "legacy"
            and resolution["desiredMode"] == "legacy"
        ),
        "mode": resolution["actualMode"],
        "status": resolution["status"],
        "reason": resolution["reason"],
    }
