"""Read-only, same-cell native child boundary, vendored beside the validator.

Executed by absolute filename under the caller's isolated Python. The caller's
list[str] prefix remains composable: no user argument enters a shell, including
on Windows PowerShell 5.1. Resolution never provisions or activates anything.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

CONTEXT_ENV = "COPILOT_EXTENSIONS_CONTEXT"
PEERS = {"agent-worktrees": "agent_worktrees", "agent-bridge": "agent_bridge"}
OWNERS = {"agent-dispatch", "agent-codespaces"}


def no_window_kwargs() -> dict[str, int]:
    """Keep the standalone bootstrap dependency-free and its children windowless."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}  # headless-guard: allow dependency-free canonical bootstrap
    return {}


class ContextRefused(RuntimeError):
    """An explicit installation boundary was rejected; never an optional miss."""


def launch_prefix(owner: str, own_root: Path, raw_context: str, peer: str) -> list[str]:
    """Build a native prefix that revalidates receipts at execution time."""
    bootstrap = Path(sys.executable)
    if bootstrap.name.lower() == "pythonw.exe":
        bootstrap = bootstrap.with_name("python.exe")
    return [
        str(bootstrap), "-I", "-X", "utf8", str(Path(__file__).resolve()),
        owner, str(own_root), raw_context, peer,
    ]


def _load_primitive(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Installation-context primitive is unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _active_context(
    primitive: ModuleType, receipt: Path, durable: Path, plugin: str,
    cell: Path, environment: dict[str, str],
) -> dict[str, Any]:
    context = primitive.validate_context_receipt(
        receipt, durable, expected_plugin_id=plugin,
        expected_cell_root=cell, environment=environment,
    )
    namespace = primitive.validate_namespace_receipt(
        context["namespaceReceipt"], durable,
    )
    if context["state"] != "active" or namespace["receipt"]["state"] != "active":
        raise ValueError(f"{plugin}: installation and namespace must be active")
    return context


def peer_environment(context: dict[str, Any], inherited: dict[str, str]) -> dict[str, str]:
    """Rebind installation routing, retaining non-routing caller/session data."""
    environment = {}
    for key, value in inherited.items():
        upper = key.upper()
        if upper in {
            CONTEXT_ENV, "COPILOT_PLUGIN_ROOT", "PYTHONPATH", "PYTHONHOME",
            "AGENT_HOME",
            "GH_TOKEN", "GITHUB_TOKEN",
        }:
            continue
        if upper.startswith((
            "AGENT_RT_", "AGENT_DISPATCH_", "AGENT_CODESPACES_",
            "AGENT_WORKTREES_", "AGENT_BRIDGE_",
        )) or (
            upper.startswith("AGENT_")
            and upper.endswith(("_ROOT", "_DIR", "_HOME", "_INSTALLATION_ID"))
        ):
            continue
        environment[key] = value
    plugin = context["pluginId"]
    prefix = plugin.upper().replace("-", "_")
    environment.update({
        CONTEXT_ENV: context["installReceipt"],
        "COPILOT_PLUGIN_ROOT": context["payloadRoot"],
        f"{prefix}_PAYLOAD_ROOT": context["payloadRoot"],
        "AGENT_RT_ROOT": context["pluginRoot"],
        "PYTHONUTF8": "1",
    })
    if plugin == "agent-bridge":
        environment.update({
            "AGENT_BRIDGE_INSTALL_DIR": context["pluginRoot"],
            "AGENT_BRIDGE_CONFIG_DIR": context["pluginRoot"],
            "AGENT_BRIDGE_INSTALLATION_ID": f"{context['marketplaceId']}/{plugin}",
            "AGENT_BRIDGE_CONNECT_LOG": str(Path(context["logsRoot"]) / "connect.log"),
        })
    return environment


def _governance(
    primitive: ModuleType, context: dict[str, Any], durable: Path,
    environment: dict[str, str],
) -> dict[str, Any]:
    plugin = context["pluginId"]
    resolution = primitive.resolve_installation_mode(
        legacy_root=Path.home() / f".{plugin}",
        payload_root=context["payloadRoot"], plugin_id=plugin,
        context=context["installReceipt"], durable_home=durable,
        expected_cell_root=context["cellRoot"],
        environment=environment,
    )
    allowed = (
        resolution["status"] == "ready"
        and resolution["reason"] == "namespaced-active"
    ) or resolution["status"] == "deactivation-required"
    if (
        not allowed
        or resolution["actualMode"] != "namespaced"
        or resolution["runtimeRoot"] != context["pluginRoot"]
        or resolution["context"] != context["installReceipt"]
        or resolution["installGeneration"] != context["generation"]
    ):
        raise ValueError(
            f"{plugin}: installation governance blocks invocation: "
            f"{resolution['status']} ({resolution['reason']})"
        )
    return resolution


def _peer_python(context: dict[str, Any], environment: dict[str, str]) -> Path:
    """Ask the peer's actual resolver, never reconstruct its slot algorithm."""
    scripts = Path(context["payloadRoot"]) / "scripts"
    if os.name == "nt":
        resolver = scripts / "resolve-runtime.ps1"
        host = (
            Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell"
            / "v1.0" / "powershell.exe"
        )
        # The command is constant. Paths travel in the rebound environment;
        # arbitrary caller argv never crosses PS5's native serialization.
        command = [
            str(host), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-Command",
            "$ErrorActionPreference='Stop'; "
            "[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false); "
            ". (Join-Path $env:COPILOT_PLUGIN_ROOT 'scripts\\resolve-runtime.ps1'); "
            "if ($AgentRtPy) { [Console]::Out.Write($AgentRtPy) }",
        ]
    else:
        resolver = scripts / "resolve-runtime.sh"
        command = [
            "/bin/sh", "-c",
            '. "$COPILOT_PLUGIN_ROOT/scripts/resolve-runtime.sh"; '
            'printf %s "${AGENT_RT_PY:-}"',
        ]
    if not resolver.is_file():
        raise ValueError(f"Peer canonical runtime resolver is missing: {resolver}")
    result = subprocess.run(
        command, env=environment, stdin=subprocess.DEVNULL,
        capture_output=True, encoding="utf-8", timeout=15, check=True,
        **no_window_kwargs(),
    )
    if not result.stdout:
        raise ValueError(f"{context['pluginId']}: no completed runtime resolved")
    python = Path(result.stdout)
    versions = Path(context["versionsRoot"])
    # Constrain the resolver's answer to this peer, but do NOT dereference the
    # interpreter leaf: ordinary POSIX venvs symlink it to their base Python.
    if (
        not python.is_absolute() or not python.is_file()
        or python.parent.name not in {"bin", "Scripts"}
        or python.name not in {"python", "python.exe"}
        or python.parent.parent.parent != versions
        or python.parent.resolve().parent.parent != versions.resolve()
    ):
        raise ValueError(f"{context['pluginId']}: resolver escaped its version slots")
    return python


def validate_owner(owner: str, own_root: Path, raw_context: str) -> dict[str, Any]:
    """Validate the caller using packaged bytes, never an unvalidated payload."""
    if owner not in OWNERS:
        raise ValueError(f"Unsupported peer-launch owner: {owner}")
    if (
        not own_root.is_absolute() or own_root.name != owner
        or own_root.parent.name != "plugins"
        or own_root.parent.parent.parent.name != "marketplaces"
    ):
        raise ValueError("Explicit context requires a canonical cell install root")
    cell = own_root.parent.parent
    durable = cell.parent.parent
    primitive = _load_primitive(
        Path(__file__).with_name("_installation_context.py"),
        "_owner_installation_context",
    )
    declared = None
    pointer = raw_context
    if raw_context.lstrip().startswith("{"):
        declared = json.loads(raw_context)
        pointer = declared.get("installReceipt")
    if not isinstance(pointer, str) or not pointer:
        raise ValueError("Explicit context must name an installation receipt")
    own = _active_context(
        primitive, Path(pointer), durable, owner, cell, dict(os.environ),
    )
    if not primitive.paths_equal(own["pluginRoot"], own_root):
        raise ValueError("Explicit context does not belong to this owner installation")
    if declared is not None and any(
        value != own.get(key) for key, value in declared.items() if key != "action"
    ):
        raise ValueError("Explicit inline context conflicts with its canonical receipt")
    own["invocationGovernance"] = _governance(
        primitive, own, durable, dict(os.environ),
    )
    return own


def launch(
    owner: str, own_root: Path, raw_context: str, plugin: str, argv: list[str],
) -> int:
    """Validate both owners, delegate peer governance/resolution, then launch."""
    if plugin not in PEERS:
        raise ValueError(f"Unsupported peer: {plugin}")
    own = validate_owner(owner, own_root, raw_context)
    cell = own_root.parent.parent
    durable = cell.parent.parent
    primitive = _load_primitive(
        Path(__file__).with_name("_installation_context.py"),
        "_owner_installation_context",
    )
    peer_receipt = Path(own["cellRoot"]) / "plugins" / plugin / "install.json"
    peer = _active_context(primitive, peer_receipt, durable, plugin, cell, {})
    environment = peer_environment(peer, dict(os.environ))
    peer_primitive = _load_primitive(
        Path(peer["payloadRoot"]) / "scripts" / "installation-context"
        / "installation_context.py", "_peer_installation_context",
    )
    # Use the peer's shipped contract too: a peer may update independently.
    validated = _active_context(
        peer_primitive, peer_receipt, durable, plugin, cell, environment,
    )
    if validated != peer:
        raise ValueError("Peer installation changed during validation")
    before = _governance(peer_primitive, peer, durable, environment)
    python = _peer_python(peer, environment)
    if (
        validate_owner(owner, own_root, raw_context) != own
        or _active_context(peer_primitive, peer_receipt, durable, plugin,
                           cell, environment) != peer
        or _governance(peer_primitive, peer, durable, environment) != before
    ):
        raise ValueError("Installation governance changed during peer resolution")
    command = [str(python), "-I", "-X", "utf8", "-m", PEERS[plugin], *argv]
    if os.name != "nt":
        os.execve(python, command, environment)
    # Keep inherited stdio and process-tree ownership, not a detached child.
    return subprocess.call(
        command, env=environment,
        stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
        **no_window_kwargs(),
    )


def main() -> int:
    try:
        if len(sys.argv) < 5:
            raise ValueError("Expected owner, owner root, explicit context, and peer")
        return launch(sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5:])
    except (OSError, ValueError, ImportError, subprocess.SubprocessError) as error:
        print(f"peer launch refused: {error}", file=sys.stderr)
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
