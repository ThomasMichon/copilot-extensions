"""Low-frequency Group A Picker reads over the pinned engine CLI boundary."""

from __future__ import annotations

from .. import engine_client


def picker_paths(project: str, *, timeout: int = engine_client._DEFAULT_TIMEOUT) -> dict:
    return engine_client.run_json(
        project,
        ["picker-paths", "--json"],
        timeout=timeout,
    )


def state_root_resolution(
    project: str,
    *,
    timeout: int = engine_client._DEFAULT_TIMEOUT,
) -> dict:
    return engine_client.run_json(
        project,
        ["state-root", "--json"],
        timeout=timeout,
        allow_nonzero=True,
    )


def update_stage_indicator_state(
    project: str,
    *,
    timeout: int = engine_client._DEFAULT_TIMEOUT,
) -> str:
    try:
        payload = engine_client.run_json(
            project,
            ["stage-update", "--indicator-state", "--json"],
            timeout=timeout,
        )
    except engine_client.EngineError as error:
        detail = engine_client._engine_error_detail(error).casefold()
        unsupported = (
            ("unrecognized arguments" in detail and "--indicator-state" in detail)
            or ("invalid choice" in detail and "stage-update" in detail)
            or ("unknown command" in detail and "stage-update" in detail)
        )
        if unsupported:
            raise engine_client.EngineFeatureUnavailable(
                "installed engine does not support update-stage indicator reads"
            ) from error
        raise
    state = payload.get("indicator_state")
    return str(state) if state else "idle"
