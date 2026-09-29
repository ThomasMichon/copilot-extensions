"""Group D Picker housekeeping seams over public engine CLI verbs."""

from __future__ import annotations

from .. import engine_client


def _run_group_d_json_verb(
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


def reap_orphan_mux_sessions(
    project: str,
    *,
    worktree_ids: list[str] | None = None,
    dry_run: bool = False,
    idle_grace_secs: float | None = None,
    timeout: int = engine_client._DEFAULT_TIMEOUT,
) -> dict:
    args = ["reap-sessions", "--json", "--include-manager-owned"]
    for worktree_id in worktree_ids or []:
        args += ["--worktree-id", worktree_id]
    if dry_run:
        args.append("--dry-run")
    if idle_grace_secs is not None:
        args += ["--grace-hours", str(float(idle_grace_secs) / 3600.0)]
    return _run_group_d_json_verb(
        project,
        args,
        verb_name="reap-sessions",
        timeout=timeout,
    )


def reap_orphan_launcher_shells(
    project: str,
    *,
    timeout: int = engine_client._DEFAULT_TIMEOUT,
) -> dict:
    return _run_group_d_json_verb(
        project,
        ["reap-shells", "--json", "--yes"],
        verb_name="reap-shells",
        timeout=timeout,
    )


def sweep_managed_worktrees(
    project: str,
    *,
    timeout: int = engine_client._DEFAULT_TIMEOUT,
) -> dict:
    return _run_group_d_json_verb(
        project,
        ["sweep-managed", "--json"],
        verb_name="sweep-managed",
        timeout=timeout,
    )


def sweep_finished_session_worktrees(
    project: str,
    *,
    timeout: int = engine_client._DEFAULT_TIMEOUT,
) -> dict:
    return _run_group_d_json_verb(
        project,
        ["sweep-finished-sessions", "--json"],
        verb_name="sweep-finished-sessions",
        timeout=timeout,
    )
