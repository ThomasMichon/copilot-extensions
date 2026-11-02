"""Translate the operator's persisted launch preferences without choosing a model."""

from __future__ import annotations

import os
from pathlib import Path

from snapshot_inventory import copilot_home, read_settings

PREFERENCE_KEYS = {
    "model": "model",
    "reasoning_effort": "effortLevel",
    "context": "contextTier",
}


class PreferenceError(ValueError):
    pass


def resolve_preferences(
    *, model: str | None = None, reasoning_effort: str | None = None,
    context: str | None = None, home: Path | None = None,
    environ: dict[str, str] | None = None,
    working_directory: Path | None = None,
) -> dict:
    environment = dict(os.environ) if environ is None else environ
    settings_path = copilot_home(
        home, environment, working_directory=working_directory,
    ) / "settings.json"
    explicit = {"model": model, "reasoning_effort": reasoning_effort, "context": context}
    settings = {}
    if any(value is None for value in explicit.values()):
        try:
            settings = read_settings(settings_path)
        except (OSError, ValueError) as error:
            raise PreferenceError("operator_preferences_unreadable") from error
    resolved: dict = {}
    sources: dict = {}
    for name, key in PREFERENCE_KEYS.items():
        if explicit[name] is not None:
            value, source = explicit[name], "explicit"
        elif name == "model" and "COPILOT_MODEL" in environment:
            value, source = environment["COPILOT_MODEL"], "environment"
        elif name == "model" and "COPILOT_PROVIDER_MODEL_ID" in environment:
            value, source = environment["COPILOT_PROVIDER_MODEL_ID"], "provider_environment"
        elif name == "model" and environment.get("COPILOT_PROVIDER_BASE_URL"):
            raise PreferenceError("operator_provider_model_unavailable_supply_model")
        else:
            value, source = settings.get(key), "operator_settings"
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise PreferenceError(f"operator_preference_invalid_{name}")
        resolved[name] = value.strip() if value is not None else None
        sources[name] = source if value is not None else "not_configured"
    if resolved["model"] is None:
        raise PreferenceError("operator_model_unavailable_supply_model")
    return {**resolved, "sources": sources}
