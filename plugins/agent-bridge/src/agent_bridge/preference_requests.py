"""Request/reuse policy selection without changing the session lifecycle."""

from typing import Any

from fastapi import HTTPException

from .session_preferences import CONTEXT_ENV, SOURCE_ENV, SOURCES, launch_preferences


def reused_preference_source(req: Any, existing: Any) -> str:
    env = getattr(getattr(existing, "target", None), "env", {})
    source = env.get(SOURCE_ENV, "caller-settings") if isinstance(env, dict) else "caller-settings"
    requested = req.preference_source or (req.env or {}).get(SOURCE_ENV)
    if requested and requested != source:
        raise HTTPException(
            status_code=409,
            detail="reused session has another preference_source; use force_new",
        )
    if source == "target-settings":
        wanted = launch_preferences(req.copilot_args or [], req.env or {})
        if (req.env or {}).get(CONTEXT_ENV):
            wanted["context"] = req.env[CONTEXT_ENV]
        for key, value in (
            ("model", req.model), ("reasoning_effort", req.effort), ("context", req.context),
        ):
            if value:
                wanted[key] = value
        client = getattr(existing, "client", None)
        confirmed = getattr(client, "confirmed_preferences", {})
        confirmed = confirmed if isinstance(confirmed, dict) else {}
        differs = any(confirmed.get(key) != value for key, value in wanted.items())
        differs = differs or any(
            key in (req.env or {}) and (req.env or {})[key] != env.get(key)
            for key in ("COPILOT_MODEL", "COPILOT_PROVIDER_BASE_URL", "COPILOT_OFFLINE")
        )
        if differs or (wanted and not getattr(client, "_preferences_ready", False)):
            raise HTTPException(
                status_code=409,
                detail="reused session cannot apply changed/unverified preferences; use force_new",
            )
    return source


def apply_request_preferences(
    req: Any, state: Any, target: Any,
) -> tuple[dict[str, str], str]:
    config = getattr(state, "config", None)
    target_env = target.env
    request_env = dict(req.env or {})
    source = (
        req.preference_source or request_env.get(SOURCE_ENV) or target_env.get(SOURCE_ENV)
        or getattr(config, "preference_source", "caller-settings")
    )
    if source not in SOURCES:
        raise HTTPException(status_code=422, detail="unsupported preference_source")
    if req.preference_source is not None or source != "caller-settings":
        request_env[SOURCE_ENV] = source
    if req.context is not None:
        if source != "target-settings":
            raise HTTPException(status_code=422, detail="context requires target-settings mode")
        request_env[CONTEXT_ENV] = req.context
    if request_env:
        target.env = {**target_env, **request_env}
    return request_env, source
