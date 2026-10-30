"""Additive preference policy fields shared by API and configuration models."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

PreferenceSource = Literal["caller-settings", "target-settings"]
SessionMode = Literal["acp", "cli"]
SessionVenue = Literal["codespace", "container", "ssh", "local"]
SessionRepo = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*(/[A-Za-z0-9][A-Za-z0-9_.-]*)?$")
]


class RepoSessionModes(BaseModel):
    """Exact, case-sensitive new-session preferences for one repository."""

    model_config = {"extra": "forbid", "hide_input_in_errors": True}
    default: SessionMode | None = None
    venues: dict[SessionVenue, SessionMode] = Field(default_factory=dict)


class SessionModes(RepoSessionModes):
    repos: dict[SessionRepo, RepoSessionModes] = Field(default_factory=dict)


class PreferenceRequestFields(BaseModel):
    preference_source: PreferenceSource | None = None
    context: str | None = None


class PreferencePolicyFields(BaseModel):
    preference_source: PreferenceSource = "caller-settings"
