"""Serializable launch descriptions shared by transport and session lifecycle."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PluginRef:
    """A transport-neutral CLI plugin reference staged by a venue provider."""

    source: str
    enable: bool = True


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

    def to_json(self) -> str:
        """Keep runtime-only native admission out of persisted target metadata.

        The resume lifecycle rederives it from the persisted ACP identity, so
        old target readers never encounter a new serialized launch field.
        """
        return json.dumps({
            key: value for key, value in asdict(self).items()
            if key != "native_resume_id"
            and (
                key not in {"execution_space_key", "remote_acp", "remote_acp_capabilities"}
                or value is not None
            )
        })

    @classmethod
    def from_json(cls, raw: str) -> SpawnTarget:
        data: dict[str, Any] = json.loads(raw)
        return cls(**data)
