"""Additive preference policy fields shared by API and configuration models."""

from typing import Literal

from pydantic import BaseModel

PreferenceSource = Literal["caller-settings", "target-settings"]


class PreferenceRequestFields(BaseModel):
    preference_source: PreferenceSource | None = None
    context: str | None = None


class PreferencePolicyFields(BaseModel):
    preference_source: PreferenceSource = "caller-settings"
