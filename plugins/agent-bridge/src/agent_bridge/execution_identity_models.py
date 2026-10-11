"""Explicit daemon execution-space selection, independent of preference policy."""

from __future__ import annotations

from pydantic import Field, field_validator

from .preference_models import PreferencePolicyFields


class ExecutionSpaceConfigFields(PreferencePolicyFields):
    local_execution_space: str | None = Field(
        default=None,
        description="Canonical registered execution-space key for this daemon; "
        "physical hostname/grouping cannot substitute for this selector.",
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
