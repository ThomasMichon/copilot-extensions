"""Read-only snapshot dispatch using the owning payload's canonical APIs.

No installer, shell dispatcher, interpreter search or persistent cache is used.
The outer session writer (or optional-peer caller) owns this process tree.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("Canonical snapshot dispatch support is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def runtime_command(payload: Path, plugin: str, environment: dict) -> tuple[list[str], dict]:
    """Delegate installation decisions and completed-slot selection, never rebuild them."""
    raw = environment.get("COPILOT_EXTENSIONS_CONTEXT") or None
    if not raw:
        primitive = load(
            payload / "scripts" / "installation-context" / "installation_context.py",
            "_snapshot_installation_context",
        )
        decision = primitive.probe_legacy_entrypoint(
            legacy_root=Path.home() / f".{plugin}",
            payload_root=payload,
            plugin_id=plugin,
            environment=environment,
        )
        if decision["probeReason"] == "legacy-active":
            root = Path(decision["legacy"]["root"])
            selected = {**environment, "AGENT_RT_ROOT": str(root)}
        else:
            raw = decision.get("context")
            if not raw:
                raise ValueError("Selected installation refuses snapshot dispatch")
    own = None
    if raw:
        # The existing owner validator enforces active receipts, namespace,
        # source and invocation governance. A refusal never selects legacy.
        peer = load(
            payload / "src" / plugin.replace("-", "_") / "_peer_launch.py",
            "_snapshot_peer_launch",
        )
        pointer = json.loads(raw)["installReceipt"] if raw.lstrip().startswith("{") else raw
        root = Path(pointer).parent
        before_environment = dict(os.environ)
        try:
            os.environ.clear()
            os.environ.update(environment)
            os.environ["COPILOT_PLUGIN_ROOT"] = str(payload)
            own = peer.validate_owner(plugin, root, raw)
        finally:
            os.environ.clear()
            os.environ.update(before_environment)
        selected = peer.peer_environment(own, environment)
        primitive = sys.modules["_owner_installation_context"]
    runtime = load(payload / "scripts" / "versioned_runtime.py", "_snapshot_runtime")
    python = runtime.resolve_python(root)
    if python is None:
        raise ValueError("Selected completed runtime is unavailable")
    if own is not None:
        _, slot, _ = primitive._runtime_slot_paths(
            own,
            python.parent.parent.name,
            require_existing=True,
        )
        if not primitive.paths_equal(slot, python.parent.parent):
            raise ValueError("Selected interpreter escaped its installation")
        # Re-read authority at execution, not a loaded-state/receipt cache.
        durable = root.parents[3]
        active = peer._active_context(
            primitive,
            Path(own["installReceipt"]),
            durable,
            plugin,
            root.parent.parent,
            selected,
        )
        if (
            any(active.get(key) != own.get(key) for key in active)
            or peer._governance(primitive, active, durable, selected)
            != own["invocationGovernance"]
        ):
            raise ValueError("Installation changed during snapshot resolution")
    selected.update(
        {
            "COPILOT_PLUGIN_ROOT": str(payload),
            f"{plugin.upper().replace('-', '_')}_PAYLOAD_ROOT": str(payload),
            f"{plugin.upper().replace('-', '_')}_HOME": str(root),
            f"{plugin.upper().replace('-', '_')}_NO_SELFPROVISION": "1",
            "PYTHONUTF8": "1",
        }
    )
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONEXECUTABLE", "VIRTUAL_ENV"):
        selected.pop(key, None)
    return [str(python), "-I", "-X", "utf8", "-m", plugin.replace("-", "_")], selected


def legacy_worktrees_command(owner: Path, environment: dict) -> tuple[list[str], dict]:
    """Resolve the optional installed owner before loading any of its code."""
    environment = {
        key: value
        for key, value in environment.items()
        if key.upper()
        not in {
            "COPILOT_EXTENSIONS_CONTEXT",
            "COPILOT_PLUGIN_ROOT",
            "AGENT_RT_ROOT",
            "AGENT_SSH_HOME",
            "AGENT_SSH_PAYLOAD_ROOT",
        }
    }
    from plugin_resolve.inventory import installed_item, inventory_paths

    home, cache = inventory_paths(Path.cwd(), environment)
    try:
        relative = owner.resolve().relative_to((home / "installed-plugins").resolve())
    except ValueError:
        relative = Path()
    if len(relative.parts) == 2 and relative.name == "agent-ssh":
        marketplace = relative.parts[0]
        current = installed_item(f"agent-ssh@{marketplace}", home, cache)
        current_record = current.get("inventory") or current.get("reference")
        registration = current_record.get("registration") or current_record.get("definition")
        if registration:
            if Path(current_record["payload"]).resolve() != owner.resolve():
                raise ValueError("Installed inventory selected another caller")
            expected_peer = owner.resolve().parent / "agent-worktrees"
            if (
                "inventory" in current
                and not expected_peer.exists()
                and not expected_peer.is_symlink()
            ):
                raise FileNotFoundError("Optional installed peer is absent")
            optional = installed_item(f"agent-worktrees@{marketplace}", home, cache)
            optional_record = optional.get("inventory") or optional.get("reference")
            other_registration = optional_record.get("registration") or optional_record.get(
                "definition"
            )
            if registration != other_registration or current["catalog"] != optional["catalog"]:
                raise ValueError("Optional installation source changed during resolution")
            if Path(optional_record["payload"]).resolve() != expected_peer:
                raise ValueError("Optional installed payload escaped its qualified installation")
            return runtime_command(
                Path(optional_record["payload"]), "agent-worktrees", environment
            )
    # Compatibility for legacy source/snapshot payloads without native inventory.
    # A rejected inventory/source is never retried against a different owner.
    primitive = load(
        owner / "scripts" / "installation-context" / "installation_context.py",
        "_snapshot_peer_provenance",
    )
    marker = Path.home() / ".agent-worktrees" / "payload-dir"  # marketplace-isolation: allow legacy source/snapshot ownership compatibility
    payload = Path(marker.read_text(encoding="utf-8").strip())
    if not payload.is_absolute():
        raise ValueError("Optional owner payload must be absolute")
    own = primitive.resolve_payload_source(
        payload_root=owner,
        plugin_id="agent-ssh",
        environment=environment,
    )
    peer = primitive.resolve_payload_source(
        payload_root=payload,
        plugin_id="agent-worktrees",
        environment=environment,
    )
    if own["sourceFingerprint"] != peer["sourceFingerprint"]:
        raise ValueError("Optional owner belongs to another source")
    return runtime_command(payload, "agent-worktrees", environment)


def worktrees_command(owner: Path, own: dict | None) -> tuple[list[str], dict]:
    """Compose existing owners in-process; the caller bounds resolution and execution."""
    if own is None:
        command, environment = legacy_worktrees_command(owner, dict(os.environ))
    else:
        peer = load(
            owner / "src" / "agent_ssh" / "_peer_launch.py",
            "_snapshot_owner_peer",
        )
        primitive = sys.modules["_owner_installation_context"]
        root = Path(own["pluginRoot"])
        durable = root.parents[3]
        active = peer._active_context(
            primitive,
            Path(own["installReceipt"]),
            durable,
            "agent-ssh",
            Path(own["cellRoot"]),
            dict(os.environ),
        )
        if (
            any(active.get(key) != own.get(key) for key in active)
            or peer._governance(primitive, active, durable, dict(os.environ))
            != own["invocationGovernance"]
        ):
            raise ValueError("Caller installation changed before optional lookup")
        receipt = Path(own["cellRoot"]) / "plugins" / "agent-worktrees" / "install.json"
        if not receipt.parent.exists():
            raise FileNotFoundError("Optional peer is absent")
        selected = peer._active_context(
            primitive,
            receipt,
            durable,
            "agent-worktrees",
            Path(own["cellRoot"]),
            {},
        )
        environment = peer.peer_environment(selected, dict(os.environ))
        command, environment = runtime_command(
            Path(selected["payloadRoot"]),
            "agent-worktrees",
            environment,
        )
    # The selected runtime imports only the existing owner resolution APIs,
    # including its normal cwd/project discovery, not the broad CLI composition.
    command[-2:] = [str(owner / "scripts" / "snapshot_worktrees.py")]
    return command, environment


def main() -> int:
    owner = Path(__file__).resolve().parents[1]
    try:
        if sys.argv[1:] == ["snapshot"]:
            command, environment = runtime_command(owner, "agent-ssh", dict(os.environ))
            command[-1] = "agent_ssh.session_snapshot"
        elif sys.argv[1:] == ["state-root", "--json"]:
            command, environment = worktrees_command(owner, None)
        else:
            raise ValueError("Unsupported read-only snapshot invocation")
        return subprocess.call(
            command,
            env=environment,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError:
        return 127
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError):
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
