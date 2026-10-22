"""Reconstruct nonsecret same-cell routing at the callback process boundary."""

from __future__ import annotations

import os
import json
from pathlib import Path

from . import peer_launch
from .install_paths import install_dir


def validate_installation(value: object) -> dict:
    if (
        not isinstance(value, dict) or set(value) != {"root", "receipt"}
        or any(
            not isinstance(value[field], str) or not value[field]
            or len(value[field]) > 4096 or "\0" in value[field]
            for field in ("root", "receipt")
        )
    ):
        raise ValueError("invalid delegated installation")
    return dict(value)


def _validated_context(installation: dict) -> dict:
    value = validate_installation(installation)
    return peer_launch.validate_owner(
        "agent-dispatch", Path(value["root"]), value["receipt"],
    )


def capture() -> tuple[dict | None, dict[str, str]]:
    raw = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "")
    if not raw:
        return None, {}
    context = _validated_context({"root": str(install_dir()), "receipt": raw})
    return (
        {"root": context["pluginRoot"], "receipt": context["installReceipt"]},
        peer_launch.peer_environment(context, {}),
    )


def enter(installation: dict, envelope: dict) -> int | None:
    from .delegated_waiter_contract import CALLBACK_COMMAND

    value = validate_installation(installation)
    current = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "")
    if current.lstrip().startswith("{"):
        declared = json.loads(current)
        if not isinstance(declared, dict):
            raise ValueError("invalid callback installation context")
        current = declared.get("installReceipt")
    if current and Path(current).resolve() == Path(value["receipt"]).resolve():
        restore(value)
        return None
    root = Path(value["root"])
    if not root.is_absolute() or root.name != "agent-dispatch" or root.parent.name != "plugins":
        raise ValueError("invalid delegated installation root")
    owner = root.parent / "agent-pull-requests"
    return peer_launch.launch(
        "agent-pull-requests", owner, str(owner / "install.json"), "agent-dispatch",
        [CALLBACK_COMMAND, json.dumps(envelope, separators=(",", ":"))],
    )


def restore(installation: dict) -> None:
    context = _validated_context(installation)
    # The PR owner's client overrides are not the subscribing dispatch caller's.
    inherited = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith("AGENT_DISPATCH_")
    }
    environment = peer_launch.peer_environment(context, inherited)
    os.environ.clear()
    os.environ.update(environment)


def restore_legacy(root: object) -> None:
    if (
        not isinstance(root, str) or not root or len(root) > 4096
        or "\0" in root or not Path(root).is_absolute()
    ):
        raise ValueError("invalid delegated legacy root")
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith("AGENT_DISPATCH_")
        and key.upper() not in {
            "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_RT_ROOT",
        }
    }
    environment.update({
        "AGENT_RT_ROOT": root,
        "AGENT_DISPATCH_INSTALL_DIR": root,
        "AGENT_DISPATCH_ROUTING_DIR": root,
        "AGENT_DISPATCH_RUN_DIR": str(Path(root) / "run"),
    })
    os.environ.clear()
    os.environ.update(environment)
