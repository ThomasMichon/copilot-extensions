"""Group B Picker seams over the pinned engine CLI boundary."""

from __future__ import annotations

from .. import engine_client


def _run_group_b_json_verb(
    project: str,
    args: list[str],
    *,
    verb_name: str,
    timeout: int = engine_client._DEFAULT_TIMEOUT,
) -> dict:
    try:
        return engine_client.run_json(project, args, timeout=timeout)
    except engine_client.EngineError as error:
        detail = engine_client._engine_error_detail(error).casefold()
        unsupported = (
            ("invalid choice" in detail and verb_name in detail)
            or ("unrecognized arguments" in detail and verb_name in detail)
            or ("unknown command" in detail and verb_name in detail)
        )
        if unsupported:
            raise engine_client.EngineFeatureUnavailable(
                f"installed engine does not support {verb_name} reads"
            ) from error
        raise


def picker_bootstrap(
    project: str,
    *,
    timeout: int = engine_client._DEFAULT_TIMEOUT,
) -> dict:
    return _run_group_b_json_verb(
        project,
        ["picker-bootstrap", "--json"],
        verb_name="picker-bootstrap",
        timeout=timeout,
    )


def repair_stale_anchor(
    project: str,
    *,
    timeout: int = engine_client._DEFAULT_TIMEOUT,
) -> dict:
    return _run_group_b_json_verb(
        project,
        ["repair-stale-anchor", "--json"],
        verb_name="repair-stale-anchor",
        timeout=timeout,
    )


def resolve_launch_plan(
    project: str,
    *,
    worktree_id: str | None = None,
    new: bool = False,
    bare_resume: bool = False,
    base: bool = False,
    target_machine: str | None = None,
    target_environment: str | None = None,
    target_no_mux: bool = False,
    timeout: int = engine_client._DEFAULT_TIMEOUT,
):
    """Reuse the pinned remote-launch seam for Group B machine resolution."""
    return engine_client.resolve_launch_plan(
        project,
        worktree_id=worktree_id,
        new=new,
        bare_resume=bare_resume,
        base=base,
        target_machine=target_machine,
        target_environment=target_environment,
        target_no_mux=target_no_mux,
        timeout=timeout,
    )
