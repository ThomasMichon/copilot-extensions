"""Route opted-in project updates to the engine before Manager bootstrap."""

from __future__ import annotations

from . import engine_client
from .update_orchestration import extract_project


def handoff(
    arguments: list[str], *, observer: list[str] | None = None, authority=None,
    delegated: bool = False,
) -> int | None:
    managed = authority is not None and authority.configured and authority.mode == "project"
    if authority is not None and authority.configured and authority.mode == "standalone":
        return None
    bypass = any(
        value in {"--no-project-controller", "--repair-engine", "--target"}
        or value.startswith(("--repair-engine=", "--target="))
        for value in arguments
    )
    if bypass:
        delegated_target = any(
            value == "--target" or value.startswith("--target=") for value in arguments
        )
        if managed and not (delegated and (
            "--no-project-controller" in arguments or delegated_target
        )):
            raise ValueError("standalone or partial update is disabled by project authority")
        return None
    project, forwarded = extract_project(arguments)
    if managed:
        project = authority.project
    try:
        selected = engine_client.get_value(project, "update-controller")
    except engine_client.EngineError as error:
        if managed:
            raise ValueError("selected project update controller is unavailable") from error
        detail = str(error)
        if error.install_hint:
            return None
        if project is None and (
            "No active project could be resolved" in detail
            or "Could not resolve a project for 'get'." in detail
        ):
            return None
        raise
    if not selected:
        if managed:
            raise ValueError("selected project update controller is unavailable")
        return None
    return engine_client.run_engine_passthrough(
        project, ["update", *forwarded, *(observer or [])],
        **({"headless": True} if observer and "--detach" in observer else {}),
    )
