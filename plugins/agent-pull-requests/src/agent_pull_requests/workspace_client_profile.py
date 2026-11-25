"""Read adopted connection data without acquiring credentials or adopting owners."""

from __future__ import annotations

import os
from pathlib import Path
import re
from collections.abc import Callable
from typing import Any, Protocol, cast

from .watch_replay_binding import ReplayBinding
from .watch_replay_store import json_object

SCHEMA = "agent-pull-requests.workspace-client-profiles"
PROFILE_FILE = "workspace-client-profiles.json"
PROFILE_BYTES = 32768
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


class _ConfigurationApi(Protocol):
    def validate_context_receipt(
        self, receipt_path: str, durable_home: Path, *, expected_plugin_id: str,
        expected_payload_root: str, expected_cell_root: Path,
    ) -> dict[str, Any]: ...

    def resolve_installation_mode(
        self, *, legacy_root: Path, context: str, plugin_id: str, payload_root: str,
        durable_home: Path, expected_cell_root: Path,
    ) -> dict[str, Any]: ...

    def probe_legacy_entrypoint(
        self, *, legacy_root: Path, plugin_id: str, payload_root: str,
    ) -> dict[str, Any]: ...

    def paths_equal(self, left: str | Path, right: str | Path) -> bool: ...


def configuration_owner() -> tuple[Path, dict[str, str]]:
    from . import _installation_context
    from .watch_storage import state_dir

    api = cast(_ConfigurationApi, _installation_context)
    root = state_dir()
    if not root.is_absolute():
        raise ValueError("absolute application configuration root required")
    context = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "")
    payload = os.environ.get("COPILOT_PLUGIN_ROOT", "")
    runtime = Path(os.environ.get("AGENT_RT_ROOT", ""))
    owner = {"mode": "application", "plugin": "agent-pull-requests"}
    if not context and (
        runtime.parent.name == "plugins"
        and runtime.parent.parent.parent.name == "marketplaces"
    ):
        raise PermissionError("workspace configuration owner unavailable")
    if not context and not payload:
        return root, owner
    if not payload or not Path(payload).is_absolute():
        raise PermissionError("workspace configuration owner unavailable")
    if context:
        if (
            not runtime.is_absolute() or runtime.name != "agent-pull-requests"
            or runtime.parent.name != "plugins"
            or runtime.parent.parent.parent.name != "marketplaces"
        ):
            raise PermissionError("workspace configuration owner unavailable")
        durable = runtime.parent.parent.parent.parent
        selected = api.validate_context_receipt(
            context, durable, expected_plugin_id="agent-pull-requests",
            expected_payload_root=payload, expected_cell_root=runtime.parent.parent,
        )
        mode = api.resolve_installation_mode(
            legacy_root=Path.home() / ".agent-pull-requests",
            context=context, plugin_id="agent-pull-requests", payload_root=payload,
            durable_home=durable, expected_cell_root=runtime.parent.parent,
        )
        if (
            (mode["status"], mode["reason"], mode["actualMode"]) !=
            ("ready", "namespaced-active", "namespaced")
            or not api.paths_equal(mode["runtimeRoot"], runtime)
            or not api.paths_equal(selected["pluginRoot"], runtime)
            or not api.paths_equal(mode["context"], selected["installReceipt"])
            or mode["installGeneration"] != selected["generation"]
            or selected["state"] != "active"
        ):
            raise PermissionError("workspace configuration owner unavailable")
        root = Path(selected["stateRoot"])
        override = os.environ.get("AGENT_PULL_REQUESTS_HOME", "")
        if override and not api.paths_equal(override, root):
            raise PermissionError("workspace configuration owner differs")
        return root, {**owner, "mode": "namespaced", "marketplace": selected["marketplaceId"]}
    mode = api.probe_legacy_entrypoint(
        legacy_root=root, plugin_id="agent-pull-requests", payload_root=payload,
    )
    if not (mode["allowMutation"] is True and mode["probeReason"] == "legacy-active"):
        raise PermissionError("workspace configuration owner unavailable")
    return root, owner


def read_profile(
    name: str, remaining: Callable[[], float],
) -> tuple[dict[str, Any], ReplayBinding, dict[str, Any]]:
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise ValueError("invalid workspace client profile selector")
    remaining()
    root, owner = configuration_owner()
    remaining()
    with (root / PROFILE_FILE).open("rb") as stream:
        raw = stream.read(PROFILE_BYTES + 1)
    if len(raw) > PROFILE_BYTES:
        raise ValueError("workspace profile limit")
    document = json_object(raw)
    if (
        set(document) != {"schema", "version", "configuration_owner", "profiles"}
        or document["schema"] != SCHEMA or type(document["version"]) is not int
        or document["version"] != 1 or document["configuration_owner"] != owner
        or type(document["profiles"]) is not dict
        or any(not isinstance(key, str) or _NAME.fullmatch(key) is None
               for key in document["profiles"])
    ):
        raise ValueError("invalid workspace client profiles")
    profile = document["profiles"].get(name)
    if type(profile) is not dict or set(profile) != {
        "gateway_origin", "route_prefix", "proof_key_file", "access_token_file",
        "gateway_ca", "binding", "scope",
    }:
        raise ValueError("workspace client profile unavailable")
    from fleet_manager.gateway_service_channel import origin, target
    from .review_workspace import _scope
    from .watch_replay_client import _identity

    origin(profile["gateway_origin"])
    binding = ReplayBinding.from_document(profile["binding"])
    prefix = profile["route_prefix"]
    if not isinstance(prefix, str) or prefix.endswith("/"):
        raise ValueError("invalid replay route prefix")
    route = prefix + "/v2/watch/" + binding.target
    canonical, query = target(route)
    if canonical != route or query:
        raise ValueError("invalid replay route prefix")
    _identity(binding.repo)
    if type(profile["scope"]) is not dict:
        raise ValueError("invalid workspace client scope")
    scope = _scope({**profile["scope"], "number": 1})
    scope.pop("number")
    if set(scope) != set(profile["scope"]):
        raise ValueError("invalid workspace client scope")
    for field in ("proof_key_file", "access_token_file", "gateway_ca"):
        value = profile[field]
        if field == "gateway_ca" and value is None:
            continue
        if (
            not isinstance(value, str) or not value or len(value) > 2048
            or any(ord(char) < 32 for char in value) or not Path(value).is_absolute()
        ):
            raise ValueError("invalid workspace credential reference")
    remaining()
    return profile, binding, scope
