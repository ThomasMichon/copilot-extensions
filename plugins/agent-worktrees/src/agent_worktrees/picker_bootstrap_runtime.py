"""Read-only resident bootstrap for an explicitly selected project."""

from __future__ import annotations

from pathlib import Path

from . import config as cfg, git_ops, tracking
from . import control_plane_providers as providers
from .execution_spaces import require_current_execution_space

ACTION = "picker-bootstrap"
CAPABILITY = "picker-bootstrap-v1"
_FIELDS = frozenset({"contract_version", "project", "cwd", "default_live", "provider", "providers_dir"})


def _invocation(cwd: Path, project: str, anchor: Path) -> tuple[bool, str | None]:
    if cwd == anchor:
        return True, None
    result = git_ops.git(
        "rev-parse", "--absolute-git-dir", "--git-common-dir",
        cwd=cwd, capture=True, check=False, timeout=2, isolated_repository=True,
    )
    lines = (result.stdout or "").strip().splitlines()
    if result.returncode != 0 or len(lines) != 2:
        return False, None
    admin, common = (
        (value if value.is_absolute() else cwd / value).resolve()
        for value in map(Path, lines)
    )
    if common not in {anchor, anchor / ".git"}:
        return False, None
    if admin.parent.name != "worktrees":
        return True, None
    git_file = Path((admin / "gitdir").read_text(encoding="utf-8").strip())
    if not git_file.is_absolute() or git_file.name != ".git":
        raise ValueError("linked-worktree metadata has no absolute worktree root")
    root = git_file.parent.resolve()
    record = tracking.load_record_by_id(
        admin.name, tracking_path=cfg.project_dir(project) / "worktrees",
    )
    if record is not None and (
        record.repo == project and record.worktree_path
        and Path(record.worktree_path).resolve() == root
        and cwd.is_relative_to(root)
    ):
        return True, record.worktree_path
    return True, None


def build(payload: dict) -> dict:
    if (
        not isinstance(payload, dict) or set(payload) - _FIELDS
        or type(payload.get("contract_version")) is not int
        or payload["contract_version"] != 1
    ):
        raise ValueError("invalid resident Picker bootstrap request")
    project, cwd = payload.get("project"), payload.get("cwd")
    if not isinstance(project, str) or cfg._PROJECT_NAME_RE.fullmatch(project) is None:
        raise ValueError("invalid bootstrap project")
    if not isinstance(cwd, str) or len(cwd) > 32768 or not Path(cwd).is_absolute():
        raise ValueError("bootstrap cwd must be an absolute path")
    live = payload.get("default_live", True)
    selected = payload.get("provider", "")
    directory = payload.get("providers_dir")
    if (
        type(live) is not bool or not isinstance(selected, str)
        or directory is not None and (
            not isinstance(directory, str) or not Path(directory).is_absolute()
        )
    ):
        raise ValueError("invalid bootstrap launch context")
    config_dir = cfg.project_dir(project)
    config = cfg.load_config(
        config_dir / "config.yaml", project=project, include_control_plane_related_pr=False,
    )
    require_current_execution_space(config)
    anchor = Path(config.default_repo.anchor).resolve()
    inside, workspace = _invocation(Path(cwd).resolve(), project, anchor)
    bootstrap = {
        "version": 1, "project": project, "should_switch_cwd": not inside,
        "cwd": None if inside else str(anchor), "workspace": workspace,
        "default_live": live, "headless": config.headless,
        "project_config": {
            "config_dir": str(config_dir), "repo_dir": str(anchor), "machine": config.machine,
        },
    }
    if config.headless:
        return {"bootstrap": bootstrap, "manager_command": None}
    manifests = providers._discover_control_plane_provider_manifests(
        **({"directory": Path(directory)} if directory is not None else {}),
    )
    manifest = manifests.get(selected) if selected else (
        next(iter(manifests.values())) if len(manifests) == 1 else None
    )
    if manifest is None or not manifest.resident_picker_command:
        raise ValueError("selected provider does not advertise resident Picker bootstrap")
    result = {"bootstrap": bootstrap, "manager_command": list(manifest.resident_picker_command)}
    session_root = getattr(manifest, "resident_picker_session_root", "")
    if session_root:
        result["manager_session"] = {"version": 1, "root": session_root}
    return result
