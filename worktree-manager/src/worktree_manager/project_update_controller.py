"""Route opted-in project updates to the engine before Manager bootstrap."""

from __future__ import annotations

from . import engine_client
from .update_orchestration import extract_project


def handoff(arguments: list[str], *, observer: list[str] | None = None) -> int | None:
    if any(
        value in {"--no-project-controller", "--repair-engine", "--target"}
        or value.startswith(("--repair-engine=", "--target="))
        for value in arguments
    ):
        return None
    project, forwarded = extract_project(arguments)
    try:
        selected = engine_client.get_value(project, "update-controller")
    except engine_client.EngineError as error:
        detail = str(error)
        if error.install_hint or "Unknown key: 'update-controller'" in detail:
            return None
        if project is None and (
            "No active project could be resolved" in detail
            or "Could not resolve a project for 'get'." in detail
        ):
            return None
        raise
    if not selected:
        return None
    return engine_client.run_engine_passthrough(
        project, ["update", *forwarded, *(observer or [])],
        **({"headless": True} if observer and "--detach" in observer else {}),
    )
