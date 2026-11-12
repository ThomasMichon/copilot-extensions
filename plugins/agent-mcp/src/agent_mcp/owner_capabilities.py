"""Read-only, physical-runtime support discovery for originating-owner input."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

CONTRACT = {
    "name": "agent-mcp.session-owner-handoff",
    "minimum": 1,
    "maximum": 1,
    "capabilities": ["explicit-native-owner-pair", "optional-physical-owner-entry-v1"],
}


def _platform() -> str | None:
    if os.name == "nt":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return None


def report() -> dict[str, object]:
    from . import forward, incoming
    from .custody_contract import CONTRACT as custody_contract
    from .custody_lifetime import SessionOwner
    from .owner_entry import launch_environment

    layout = incoming.require_selected()
    if layout is None:
        return {"contract": CONTRACT, "supported": False, "reason": "unmanaged-runtime"}
    platform = _platform()
    if platform is None:
        return {"contract": CONTRACT, "supported": False, "reason": "unsupported-platform"}
    from agent_procutil._daemon_instance_identity import birth_identity

    pid = os.getpid()
    with SessionOwner(pid, birth_identity(pid)):
        from .cutover import _require_selected

        _require_selected(layout)
    return {
        "contract": CONTRACT,
        "supported": True,
        "platform": platform,
        "selection": {
            "generation": layout[0], "root": str(Path(layout[2]).resolve()),
            "prefix": str(Path(sys.prefix).resolve()),
            "python": str(Path(sys.executable).resolve()),
        },
        "custody": custody_contract,
        "entry": {
            # Resolving the executable symlink would discard its venv selection.
            "python": str(Path(sys.executable).absolute()),
            "prefix": str(Path(sys.prefix).resolve()),
            "module": str(Path(__file__).with_name("owner_entry.py").resolve()),
            "environment": launch_environment(),
        },
        "invocation_allowed": forward._multiplex_enabled() and forward._ensure_serve_enabled(),
    }


def command(_args: argparse.Namespace) -> int:
    try:
        result = report()
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({
            "contract": CONTRACT, "supported": False, "error": str(error),
        }, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0
