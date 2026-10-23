"""Use the optional static-publication owner before local Copilot discovery."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .local_cache_refresh import RefreshResult, _checkout_root, _process_start_time, _report, _run_bounded_result


async def prepare_for_launch(repo_root: str | Path) -> RefreshResult:
    from . import _peer_launch
    from .session_lifecycle_cli import _agent_worktrees_launch_prefix, _agent_worktrees_environment

    try:
        prefix = _agent_worktrees_launch_prefix(allow_path_fallback=False)
    except _peer_launch.ContextRefused as exc:
        return _report(RefreshResult("unavailable", detail=f"static preparation owner refused: {exc}"))
    if not prefix:
        from .local_cache_refresh import refresh_local_cache

        return await refresh_local_cache(repo_root)
    root = _checkout_root(repo_root).resolve()
    environment = _agent_worktrees_environment()
    if sys.platform == "darwin":
        try:
            if _process_start_time(os.getpid()) is None:
                raise ValueError("current process birth identity unavailable")
        except Exception as exc:
            return _report(RefreshResult(
                "unavailable", blocking=1, detail=f"native preparation custody unavailable: {exc}",
            ))
    if os.name != "nt":
        environment["WORKTREE_MANAGER_ENGINE_OWNS_PROCESS_GROUP"] = "1"
    run = await _run_bounded_result(
        [*prefix, "knowledge", "compose-plugins", "--cwd", str(root),
         "--prepare-projections", "--json"],
        timeout=None, env=environment, retire_group=True,
    )
    if run.status != "completed":
        return _report(RefreshResult(run.status, blocking=1, counts_known=False, detail=run.detail))
    try:
        value = json.loads(run.stdout)["projections"]
        if not isinstance(value, dict) or value.get("status") not in ("ready", "partial", "failed", "timeout"):
            raise ValueError("missing static preparation outcome")
        fields = ("changed", "unchanged", "warnings", "blocking", "removed")
        if any(type(value.get(key)) is not int or value[key] < 0 for key in fields):
            raise ValueError("invalid static preparation counts")
        if not isinstance(value.get("detail"), str):
            raise ValueError("invalid static preparation diagnostic")
        if type(value.get("counts_known", True)) is not bool:
            raise ValueError("invalid static preparation count availability")
        status = value["status"]
        if status == "ready" and run.returncode != 0:
            status = "failed"
        return _report(RefreshResult(
            status, **{key: value[key] for key in fields}, detail=value["detail"],
            generation=value.get("generation", ""), reused=value.get("reused", False),
            counts_known=value.get("counts_known", True),
        ))
    except (ValueError, TypeError, KeyError) as exc:
        return _report(RefreshResult(
            "failed", blocking=1, counts_known=False, detail=f"static preparation owner: {exc}; {run.detail}",
        ))
