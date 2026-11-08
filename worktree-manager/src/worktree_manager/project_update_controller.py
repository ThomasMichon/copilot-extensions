"""Route opted-in project updates to the engine before Manager bootstrap."""

from __future__ import annotations

import re

from . import engine_client
from .engine_progress import error_from_envelope
from .update_orchestration import extract_project


class _NoProjectResolved(engine_client.EngineError):
    """The fixed diagnostic confirms that no project was selected."""


def legacy_controller_error(project: str | None, proc) -> engine_client.EngineError | None:
    """Recognize only fixed downlevel diagnostics, never arbitrary stdout."""
    if proc.returncode != 1:
        return None
    stdout, stderr = (proc.stdout or "").strip(), (proc.stderr or "").strip()
    if stdout and stderr:
        return None
    detail = error_from_envelope(stdout) or stdout or stderr
    if len(detail) > 4096:
        return None
    detail = re.sub(r"\x1b\[[0-9;]*m", "", detail).strip()
    if detail.startswith("\u2717 "):
        detail = detail[2:].lstrip()
    if detail in {
        "Unknown key: 'update-controller'",
        "Unknown key: 'update-controller'. Use 'get keys' to list available keys.",
    }:
        return engine_client.EngineFeatureUnavailable(
            "the selected engine cannot determine the project update controller; "
            "use the configured source's supported engine setup or recovery, then retry"
        )
    if project is None and detail in {
        "Could not resolve a project for 'get'.",
        "Cannot load config: No active project could be resolved. agent-worktrees discovers "
        "its context from the current directory (like git); run from inside a managed repo or "
        "worktree, or pass --project <name>.",
    }:
        return _NoProjectResolved("No active project could be resolved")
    return None


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
        if error.install_hint:
            return None
        if project is None and isinstance(error, _NoProjectResolved):
            return None
        raise
    if not selected:
        if managed:
            raise ValueError("selected project update controller is unavailable")
        return None
    if any(
        value == "--automatic-trigger" or value.startswith("--automatic-trigger=")
        for value in forwarded
    ):
        from .automatic_updates_cli import guarded_update

        normalized: list[str] = []

        def admit(arguments: list[str], _trigger: str, _automatic_trigger: str | None) -> int:
            normalized.extend(arguments)
            return 0

        # Release admission custody before the engine can call Manager back.
        admitted = guarded_update(forwarded, admit)
        if admitted:
            return admitted
        forwarded = normalized
    return engine_client.run_engine_passthrough(
        project, ["update", *forwarded, *(observer or [])],
        **({"headless": True} if observer and "--detach" in observer else {}),
    )
