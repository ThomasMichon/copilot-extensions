"""Daemon execution-space configuration and non-persistent source context."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, PrivateAttr, field_validator

from .preference_models import PreferencePolicyFields


class ExecutionSpaceConfigFields(PreferencePolicyFields):
    _source_worktree: Path = PrivateAttr(default_factory=Path.cwd)

    local_execution_space: str | None = Field(
        default=None,
        description="Optional canonical topology key for this daemon's actual platform; "
        "otherwise use unambiguous hostname and platform discovery.",
    )

    @field_validator("local_execution_space")
    @classmethod
    def validate_space_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or any(char.isspace() for char in value) or any(
            separator in value for separator in ("/", "\\", "#")
        ):
            raise ValueError("local_execution_space must be a nonempty canonical registry key")
        return value
