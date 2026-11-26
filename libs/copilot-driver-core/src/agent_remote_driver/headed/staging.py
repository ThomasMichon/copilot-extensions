"""Retain the core's extension assets inside the caller's original launch scope."""

from __future__ import annotations

import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import tempfile

CONNECTOR_ENV = "COPILOT_DRIVER_NATIVE_CONNECTOR"
CONNECTOR_CONTRACT = "driver-native-v1"


def connector_selection(
    env: dict[str, str], *, session_id: str, cwd: str,
) -> dict[str, object] | None:
    """Validate an explicit receiving selection before staging or native birth."""
    raw = env.get(CONNECTOR_ENV)
    if raw is None:
        return None
    try:
        selected = json.loads(raw)
        if (
            not isinstance(selected, dict) or set(selected) != {
                "contract", "version", "sessionId", "cwd", "scopeId", "attempt",
                "bridgePluginRoot", "driverPluginRoot",
            }
            or selected["contract"] != CONNECTOR_CONTRACT
            or type(selected["version"]) is not int or selected["version"] != 1
            or selected["sessionId"] != session_id or selected["cwd"] != cwd
            or selected["sessionId"] != env.get("AGENT_BRIDGE_NATIVE_SESSION_ID")
            or selected["scopeId"] != env.get("AGENT_BRIDGE_SCOPE_ID")
            or not selected["scopeId"]
            or selected["attempt"] != env.get("AGENT_BRIDGE_PORTABLE_ATTEMPT")
            or not selected["attempt"]
            or selected["bridgePluginRoot"] != env.get("AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT")
            or selected["driverPluginRoot"] != env.get("AGENT_BRIDGE_REMOTE_DRIVER_ROOT")
            or any(
                not isinstance(selected[key], str) or "\0" in selected[key]
                or not Path(selected[key]).is_absolute()
                for key in ("bridgePluginRoot", "driverPluginRoot")
            )
        ):
            raise ValueError("invalid native connector selection")
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError("Native connector differs from its explicit receiving launch") from error
    return selected


def selected_connector_roots(
    selected: dict[str, object], argv: list[str], *, value_options: set[str],
) -> None:
    expected = {
        "agent-bridge": Path(str(selected["bridgePluginRoot"])).resolve(strict=True),
        "agent-remote-driver": Path(str(selected["driverPluginRoot"])).resolve(strict=True),
    }
    found: dict[str, Path] = {}
    seen: set[Path] = set()
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            break
        name, separator, value = argument.partition("=")
        if not separator and name in value_options | {"-C", "--resume"}:
            index += 1
            if index >= len(argv):
                raise ValueError("Missing selected native argument")
            value = argv[index]
        if name == "--plugin-dir":
            path = Path(value)
            if not path.is_absolute():
                raise ValueError("Selected native plugin directories must be absolute")
            path = path.resolve(strict=True)
            if path in seen:
                raise ValueError("Duplicate selected native plugin directory")
            seen.add(path)
            manifest = path / "plugin.json"
            if not manifest.is_file():
                manifest = path / ".github" / "plugin" / "plugin.json"
            metadata = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str):
                raise ValueError("Selected native plugin identity is unavailable")
            plugin_name = metadata["name"]
            if plugin_name in expected:
                if plugin_name in found or path != expected[plugin_name]:
                    raise ValueError("Selected native plugin root changed or duplicated")
                found[plugin_name] = path
            elif plugin_name == "copilot-driver-headed":
                raise ValueError("The native connector must be staged by its original Host")
        index += 1
    if found != expected:
        raise ValueError("Selected native plugin roots are missing from the original command")


def stage_extension(attempt_directory: Path, *, native_connector: bool = False) -> Path:
    source = files("agent_remote_driver.headed")
    assets = {
        "plugin.json": json.dumps({
            "name": "copilot-driver-headed", "version": "1.0.0",
            "description": "Launch-bound connection to the existing Copilot Driver Host",
        }, sort_keys=True).encode(),
        **{
            "extensions/copilot-driver-headed/" + name: source.joinpath(name).read_bytes()
            for name in ("extension.mjs", "connection.mjs", "acp-adapter.mjs", "host-client.mjs")
        },
        "extensions/copilot-driver-headed/sdk-compat.mjs": files(
            "agent_remote_driver.bridge_extension"
        ).joinpath("sdk-compat.mjs").read_bytes(),
    }
    if native_connector:
        from ..bridge_extension import extension_sources

        prefix = "extensions/copilot-driver-headed/"
        assets[prefix + "extension.mjs"] = source.joinpath("connector-extension.mjs").read_bytes()
        assets.update({prefix + "core/" + name: body for name, body in extension_sources()})
    digest = hashlib.sha256()
    for name, content in sorted(assets.items()):
        digest.update(name.encode() + b"\0" + content + b"\0")
    attempt_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = attempt_directory / ("headed-extension-" + digest.hexdigest()[:16])

    def verify() -> None:
        for name, content in assets.items():
            path = destination / name
            if path.is_symlink() or path.read_bytes() != content:
                raise ValueError("Retained headed extension assets changed")

    if destination.exists():
        verify()
        return destination
    with tempfile.TemporaryDirectory(prefix=".headed-stage-", dir=attempt_directory) as temporary:
        staged = Path(temporary) / "plugin"
        for name, content in assets.items():
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        try:
            os.replace(staged, destination)
        except OSError:
            if not destination.is_dir():
                raise
            verify()
    return destination
