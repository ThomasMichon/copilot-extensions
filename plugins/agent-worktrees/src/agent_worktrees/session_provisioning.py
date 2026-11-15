"""Session provisioning preview and selected-runtime child commands."""

from __future__ import annotations

from pathlib import Path


def reconciliation_argv(
    python: str, runtime_root: Path, cwd: str, status: Path,
) -> list[str]:
    """Arguments for the existing background reconciliation operation."""
    import agent_procutil
    cache_arguments = getattr(agent_procutil, "python_cache_policy_args", lambda *a, **k: ["-B"])

    return [
        python, *cache_arguments(runtime_root, runtime=python), "-m", "agent_worktrees", "reconcile-plugins",
        "--repo", cwd, "--status", str(status), "--apply",
    ]


def preview_services(cwd: str) -> str | None:
    """Return requested service names, or None when no reconciliation is needed."""
    from . import reconcile

    plan = reconcile.build_plan(Path(cwd), save=False)
    if plan.get("action") != "reconcile":
        return None
    return ", ".join(
        dict.fromkeys(
            str(item.get("service"))
            for item in plan.get("updates", [])
            if isinstance(item, dict) and item.get("service")
        )
    )
