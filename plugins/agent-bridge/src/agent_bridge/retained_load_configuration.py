"""Finish acknowledged-load preferences on the same surviving conversation."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

from .retained_load_acceptance import EVENT, _refuse

PREFERENCE_EVENTS = frozenset({
    "model_applied", "model_fallback", "preference_resolution", "preference_application",
    "preference_selected",
})
_FIELDS = (
    "preference_source", "model_override", "effort_override", "context_override",
    "target_preferences", "launch_preferences", "confirmed_preferences", "provider_intent",
)


def configuration_event(name: str, data: dict[str, Any]) -> bool:
    """Exclude turn output and usage accounting from configuration-only history."""
    return name in PREFERENCE_EVENTS or name == "usage_update" and set(data) == {"model"}


def intent(client: Any) -> dict[str, Any] | None:
    """Freeze caller resolution before the native RPC, not at a later retry."""
    if not hasattr(client, "preference_source"):
        return None
    value = {key: getattr(client, key) for key in _FIELDS}
    caller = None
    if client.preference_source == "caller-settings":
        try:
            caller = client._resolve_caller_preferences()
        except Exception:
            caller = {}
    value["caller_preferences"] = caller
    return cast(dict[str, Any], json.loads(json.dumps(value)))


def response(preferences: dict[str, Any] | None, result: Any) -> dict[str, Any] | None:
    """An unsupported response still retains its ACK, without configuration proof."""
    if preferences is None:
        return None
    try:
        options = getattr(result, "config_options", None)
        if options is not None:
            options = [
                option.model_dump(mode="json", by_alias=True, exclude_none=True)
                if hasattr(option, "model_dump") else option for option in options
            ]
        return cast(dict[str, Any], json.loads(json.dumps({"version": 1, **preferences, "options": options})))
    except (TypeError, ValueError):
        return None


async def finish(
    client: Any, events: list[dict[str, Any]], before_effect: Callable[[], None],
    on_event: Callable[[str, dict[str, Any]], None],
) -> None:
    """A completed legacy load needs no replay; unfinished modern loads need their response."""
    if any(item["event_type"] == "startup_progress" and item["data"].get("phase") == "native-session-load"
           and item["data"].get("state") == "finished" for item in events):
        return
    accepted = next(item["data"] for item in events if item["event_type"] == EVENT)
    value = accepted.get("configuration")
    if (
        not isinstance(value, dict) or set(value) != {"version", "options", "caller_preferences", *_FIELDS}
        or type(value["version"]) is not int or value["version"] != 1
        or value["preference_source"] not in {"caller-settings", "target-settings"}
        or value["options"] is not None and (
            not isinstance(value["options"], list) or any(not isinstance(option, dict) for option in value["options"])
        )
        or value["preference_source"] == "caller-settings" and not isinstance(value["caller_preferences"], dict)
        or not isinstance(value["confirmed_preferences"], dict)
    ):
        raise _refuse("Acknowledged load lacks its original pending configuration response")
    before_effect()
    for key in _FIELDS:
        setattr(client, key, json.loads(json.dumps(value[key])))
    if client.preference_source == "target-settings" and not client.confirmed_preferences:
        client.confirmed_preferences = client._selected_preferences(value["options"])
    client._preferences_ready = False
    previous = client._on_event
    client._on_event = on_event
    try:
        await client._apply_model_config(
            value["options"], resuming=True, before_effect=before_effect,
            caller_preferences=value["caller_preferences"], force_readback=True,
        )
        before_effect()
        if client.preference_source == "caller-settings":
            client._preferences_ready = True
        if not client._preferences_ready:
            raise _refuse("Original acknowledged-load configuration remains unverified")
    finally:
        client._on_event = previous
