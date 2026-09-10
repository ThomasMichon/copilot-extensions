"""Regression guard for #2338: a launched worktree's plan must carry the
project that actually owns it (``config.repo_name``, resolved from the
worktree that was just created/resumed), never the mutable process-global
``cfg.active_project()``.

``launch-session.ps1``/``.sh`` treat the resolved plan's ``project`` field as
authoritative for every downstream out-of-process call (notably
``session-backend status``). Before this fix, ``_create_worktree_core`` filled
that field from ``cfg.active_project()`` -- a single, process-wide value that
can legitimately differ from the project actually being acted on (e.g. an
automated flow whose launcher started against one project but resolves a
worktree in another) -- and ``_resolve_resume`` never attached a ``project``
field at all. Either gap silently drops project scoping downstream, causing a
launcher to query the wrong project's tracking directory and report
"Worktree not found" for a worktree that was just created/resumed correctly.
"""

from __future__ import annotations

from pathlib import Path

_MAIN = (
    Path(__file__).resolve().parents[1] / "src" / "agent_worktrees" / "__main__.py"
)


def _source() -> str:
    return _MAIN.read_text(encoding="utf-8")


def test_create_worktree_core_scopes_plan_project_to_repo_name():
    """The create-path plan (used by both `_resolve_new` and `cmd_create`)
    must key its `project` off `config.repo_name` -- the project that was
    actually resolved for this specific creation -- not the ambient,
    process-wide `cfg.active_project()`."""
    src = _source()
    assert '"project": config.repo_name,' in src
    # The old, ambient-global-sourced assignment must be gone from the
    # launch-plan construction.
    assert 'project = cfg.active_project()\n    if project:\n        result["launch"]["project"] = project' not in src


def test_resolve_resume_attaches_project_to_plan():
    """The resume-path plan must carry `project` too, so a resumed worktree's
    launcher can scope its downstream calls just like a newly-created one."""
    src = _source()
    resume_start = src.index("def _resolve_resume(")
    resume_end = src.index("\ndef _resolve_new(")
    resume_body = src[resume_start:resume_end]
    assert '"project": config.repo_name,' in resume_body


def test_launchers_prefer_resolved_plan_project_over_ambient(monkeypatch):
    """Once the plan carries `project`, the launcher scripts must let it win
    over whatever ambient/starting project the launcher itself had -- never
    only filling in an empty value (the #2338 bug)."""
    ps1 = (
        Path(__file__).resolve().parents[1] / "bin" / "launch-session.ps1"
    ).read_text(encoding="utf-8")
    sh = (
        Path(__file__).resolve().parents[1] / "bin" / "launch-session.sh"
    ).read_text(encoding="utf-8")

    # The stale guard ("only if empty") must be gone from both launchers.
    assert (
        "if (-not $script:LaunchProject -and $plan.PSObject.Properties.Name "
        "-contains 'project') {" not in ps1
    )
    assert 'if [[ -z "$LAUNCH_PROJECT" ]]; then\n    LAUNCH_PROJECT=$(printf' not in sh

    # The plan's project must now be preferred unconditionally when present.
    assert (
        "if ($plan.PSObject.Properties.Name -contains 'project' "
        "-and $plan.project) {" in ps1
    )
    assert "$script:LaunchProject = [string]$plan.project" in ps1
    assert '_PLAN_PROJECT=$(printf' in sh
    assert 'if [[ -n "$_PLAN_PROJECT" ]]; then\n    LAUNCH_PROJECT="$_PLAN_PROJECT"' in sh
