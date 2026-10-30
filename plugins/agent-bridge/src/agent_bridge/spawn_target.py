"""Serializable launch descriptions shared by transport and session lifecycle."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from typing_extensions import TypedDict

REQUEST_OVERRIDES_KEY = "_agent_bridge_request_overrides"


class StaticPluginArgs(TypedDict):
    base: list[str]
    automatic: list[str]


@dataclass
class PluginRef:
    """A transport-neutral CLI plugin reference staged by a venue provider."""

    source: str
    enable: bool = True
    allow_missing: bool = False

    def selection(self) -> dict:
        from plugin_resolve.reflection import selection_spec

        return selection_spec({
            "source": self.source, "enable": self.enable, "allow_missing": self.allow_missing,
        })


@dataclass
class SpawnTarget:
    """Where and how to spawn an agent process."""

    type: str = "local"
    cwd: str | None = None
    host: str | None = None
    execution_space_key: str | None = None
    user: str | None = None
    copilot_path: str | None = None
    copilot_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    project: str | None = None
    explicit_cwd: bool = False
    ssh_shell: str | None = None
    worktree_id: str | None = None
    caller_worktree: str | None = None
    caller_owner_ref: str | None = None
    elevated: bool = False
    spawn_command: list[str] | None = None
    codespace: dict | None = None
    container: dict | None = None
    venue: dict | None = None
    auth_hooks: list[dict] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    remote_acp: dict[str, Any] | None = None
    remote_acp_capabilities: dict[str, Any] | None = None
    native_resume_id: str | None = None
    static_plugin_args: StaticPluginArgs | None = None
    local_plugin_preparation: bool = False
    prepared_plugins: dict | None = None
    plugin_preparation_request: dict | None = None

    def to_json(self) -> str:
        """Persist preparation provenance inside prior readers' extensible metadata."""
        data = {
            key: value for key, value in asdict(self).items()
            if key not in {
                "native_resume_id", "static_plugin_args", "local_plugin_preparation",
                "prepared_plugins", "plugin_preparation_request",
            }
            and (
                key not in {"execution_space_key", "remote_acp", "remote_acp_capabilities"}
                or value is not None
            )
        }
        venue = data["venue"] or {}
        overrides = venue.get(REQUEST_OVERRIDES_KEY, {})
        overrides.pop("plugin_preparation", None)
        if self.static_plugin_args is not None or self.local_plugin_preparation:
            overrides["plugin_preparation"] = {
                "static_plugin_args": self.static_plugin_args,
                "local_plugin_preparation": self.local_plugin_preparation,
            }
        if overrides or REQUEST_OVERRIDES_KEY in venue:
            venue[REQUEST_OVERRIDES_KEY] = overrides
            data["venue"] = venue
        return json.dumps(data)

    @classmethod
    def from_json(cls, raw: str) -> SpawnTarget:
        data: dict[str, Any] = json.loads(raw)
        overrides = (data.get("venue") or {}).get(REQUEST_OVERRIDES_KEY, {})
        preparation = overrides.get("plugin_preparation")
        if preparation is not None:
            if not isinstance(preparation, dict):
                raise ValueError("Invalid persisted plugin preparation metadata")
            for key in ("static_plugin_args", "local_plugin_preparation"):
                if key in preparation:
                    data.setdefault(key, preparation[key])
        return cls(**data)
