"""Explicitly finish setup after a retained New prompt interrupted creation."""

from __future__ import annotations

import json
import re
import subprocess
from contextlib import ExitStack
from pathlib import Path

import yaml

from . import config as cfg, execution_spaces, finalize as fin, git_ops
from . import installer, knowledge_plugins, launch_admission, launch_seed_state, local_cache_refresh
from . import output, permissions, repos, sessions, state_root, tracking
from . import worktree_creation
from .launch_progress import step
from .resume_seed import cold_start_error
from .worktree_naming import session_worktree_allocation


def _same_repository(anchor: str, checkout: str, *, branch: str | None = None) -> None:
    common_dirs = [
        git_ops.git(
            "rev-parse", "--path-format=absolute", "--git-common-dir",
            cwd=directory, timeout=10, kill_tree=True, isolated_repository=True,
        ).stdout.strip()
        for directory in (anchor, checkout)
    ]
    if not all(common_dirs) or Path(common_dirs[0]).resolve() != Path(common_dirs[1]).resolve():
        raise ValueError("retained checkout belongs to another Git repository")
    if branch is not None:
        actual = git_ops.git(
            "symbolic-ref", "--short", "HEAD", cwd=checkout, timeout=10,
            kill_tree=True, isolated_repository=True,
        ).stdout.strip()
        if actual != branch:
            raise ValueError("retained checkout is on another Git branch")


def _validate(
    config: cfg.Config, path: Path, seed_id: str | None, *,
    retained: tracking.WorktreeRecord | None = None,
):
    execution_spaces.require_current_execution_space(config)
    if path.is_symlink():
        raise ValueError("tracking record symlink refused")
    record = tracking.load_record(path)
    execution_spaces.require_record_mutation(record, config)
    if retained is not None and (
        any(getattr(record, field) != getattr(retained, field) for field in (
            "worktree_id", "repo", "branch", "worktree_path", "creation_nonce",
            "machine", "platform", "machine_identity", "owner_ref",
        ))
        or any(
            getattr(retained, field) is not None and getattr(record, field) != getattr(retained, field)
            for field in ("pair_id", "pair_role", "pair_ref", "pair_kind")
        )
    ):
        raise ValueError("retained creation allocation identity changed during repair")
    if (
        record.worktree_id != path.stem or record.repo != config.repo_name
        or not execution_spaces.require_owner_identity(record.execution_machine, config, canonical_ref=False)
        or record.platform != config.platform or record.platform != cfg.detect_platform()
        or record.kind != "session"
        or record.status != "active"
        or getattr(config.default_repo, "knowledge_only", False)
    ):
        raise ValueError("creation repair requires this project's active local session worktree")
    checkout = Path(record.worktree_path)
    expected_path = Path(config.default_repo.worktree_root) / record.worktree_id
    if (
        not checkout.is_dir() or checkout.is_symlink()
        or checkout.resolve() != expected_path.resolve()
        or record.branch != f"worktree/{record.worktree_id}"
    ):
        raise ValueError("retained creation checkout is missing, moved, or mismatched")
    _same_repository(config.default_repo.anchor, record.worktree_path, branch=record.branch)
    seed = launch_seed_state.peek(path)
    if seed is None or seed.kind != "new" or seed.handoff_id:
        raise ValueError("creation repair requires an unclaimed retained New intent")
    if seed_id is not None and seed.seed_id != seed_id:
        raise ValueError("retained New identity changed; inspect before retrying")
    error = cold_start_error(sessions.verify_worktree_active(record), "new")
    if error:
        raise ValueError(error)
    return record, seed


def _permission_state(worktree_path: str) -> dict:
    config_path = permissions._config_path()
    if config_path.exists():
        _, data = permissions._read_config_jsonc(config_path)
        if not isinstance(data, dict) or not isinstance(data.get("trustedFolders", []), list):
            raise ValueError("Copilot trusted-folder configuration is invalid")
        trusted = any(
            path in data.get("trustedFolders", [])
            for path in (worktree_path, str(Path(worktree_path).resolve()))
        )
    else:
        trusted = False
    approvals_path = permissions._permissions_path()
    data = json.loads(approvals_path.read_text(encoding="utf-8")) if approvals_path.exists() else {}
    if not isinstance(data, dict) or not isinstance(data.get("locations", {}), dict):
        raise ValueError("Copilot permission configuration is invalid")
    location = data.get("locations", {}).get(worktree_path, {})
    if not isinstance(location, dict) or not isinstance(location.get("tool_approvals", []), list):
        raise ValueError("Copilot worktree approvals are invalid")
    approved = {
        entry.get("extensionName")
        for entry in location.get("tool_approvals", [])
        if isinstance(entry, dict) and entry.get("kind") == "extension-permission-access"
    }
    required = set(permissions._FACILITY_EXTENSION_NAMES)
    return {"trusted": trusted, "facility_approvals": required <= approved}


def _prepare_permissions(anchor: str, worktree_path: str) -> None:
    permissions.clone_permissions(anchor, worktree_path)
    permissions.ensure_trusted_folder(worktree_path)
    permissions.ensure_extension_permission_approvals(worktree_path)
    if not all(_permission_state(worktree_path).values()):
        raise ValueError("Copilot trust or facility approvals remain incomplete")


def _allocation(record: tracking.WorktreeRecord) -> tuple[str, str, str]:
    platform = "win" if record.platform == "windows" else record.platform
    allocation = session_worktree_allocation(
        record.worktree_id, record.machine, record.platform,
        physical_machine=record.machine_identity is not None,
    )
    if allocation is None:
        raise ValueError("retained New has no verifiable native creation allocation")
    return allocation[0], allocation[1], platform


def _existing_pair(config: cfg.Config, record: tracking.WorktreeRecord, root: state_root.StateRoot):
    if not root.requires_external:
        return None
    if not root.bound or not root.path or not root.repo:
        raise ValueError(root.error or "creation repair requires a usable knowledge binding")
    if any((record.pair_id, record.pair_ref, record.pair_role, record.pair_kind)):
        pair = state_root.resolve_pair(config, cwd=record.worktree_path)
        if pair.error or not pair.paired or pair.current is None or pair.current.role != "harness":
            raise ValueError(pair.error or "retained creation has a conflicting pair")
        if pair.sibling is None or pair.sibling.repo != root.repo:
            raise ValueError("retained pair no longer matches the knowledge binding")
        return pair
    return None


def _retained_sibling(
    config: cfg.Config, record: tracking.WorktreeRecord, root: state_root.StateRoot,
    timestamp: str, suffix: str, platform: str,
):
    knowledge_id = f"{record.worktree_id}-k"
    harness_ref = tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id)
    projects = installer.read_projects_registry(strict=True).get("projects", {})
    if not isinstance(projects, dict):
        raise ValueError("project registry cannot verify retained paired allocations")
    names = set(projects) | {root.repo}
    if any(not isinstance(name, str) or not cfg._PROJECT_NAME_RE.fullmatch(name) for name in names):
        raise ValueError("project registry contains an invalid project identity")
    found = None
    for name in sorted(names):
        path = cfg.project_dir(name) / "worktrees" / f"{knowledge_id}.yaml"
        if not path.exists():
            continue
        if path.is_symlink():
            raise ValueError("retained paired record symlink refused")
        child = tracking.load_record(path)
        if child.pair_ref != harness_ref:
            if name == root.repo:
                raise ValueError("paired allocation is already owned by another worktree")
            continue
        if name != root.repo or found is not None:
            raise ValueError("retained paired allocation conflicts with the current binding")
        expected = {
            "worktree_id": knowledge_id, "repo": root.repo, "machine": record.machine,
            "machine_identity": record.machine_identity,
            "platform": record.platform, "kind": "session", "branch": f"worktree/{knowledge_id}",
            "pair_id": f"{timestamp}-{suffix}", "pair_role": "knowledge",
            "pair_kind": "worktree",
        }
        if any(getattr(child, field) != value for field, value in expected.items()):
            raise ValueError("retained paired allocation identity does not match")
        expected_path = Path(cfg.derive_worktree_root(root.path)) / knowledge_id
        if (
            Path(child.worktree_path).resolve() != expected_path.resolve()
            or not expected_path.is_dir() or expected_path.is_symlink()
            or child.status != "active"
        ):
            raise ValueError("retained paired checkout is missing, moved, or no longer active")
        _same_repository(root.path, child.worktree_path, branch=child.branch)
        found = child
    return found


def _pair_plan(config: cfg.Config, record: tracking.WorktreeRecord):
    root = state_root.resolve_state_root(config)
    entry = repos.find_repo(root.repo) if root.requires_external and root.bound else None
    if root.requires_external and root.bound and entry is None:
        raise ValueError("knowledge repository registration is missing")
    existing = _existing_pair(config, record, root)
    allocation = None
    child = None
    if root.requires_external:
        allocation = _allocation(record)
        child = _retained_sibling(config, record, root, *allocation)
        if existing is not None and existing.pair_kind == "worktree":
            if (
                child is None or existing.sibling is None
                or existing.sibling.worktree_id != child.worktree_id
                or Path(existing.sibling.path).resolve() != Path(child.worktree_path).resolve()
            ):
                raise ValueError("retained pair does not match the original creation allocation")
        if child is not None and repos.normalize_class(entry.repo_class) not in {"worktree", "knowledge"}:
            raise ValueError("knowledge management class no longer permits the retained worktree")
        if child is not None:
            error = cold_start_error(sessions.verify_worktree_active(child), "new")
            if error:
                raise ValueError(f"retained companion is not stopped: {error}")
        elif existing is None and repos.normalize_class(entry.repo_class) in {"worktree", "knowledge"}:
            target = Path(cfg.derive_worktree_root(root.path)) / f"{record.worktree_id}-k"
            if target.exists():
                raise ValueError("untracked paired checkout exists; do not allocate another")
    return root, existing, allocation, child


def _require_composition(result: dict) -> None:
    error = result.get("pair_error")
    if error is not None or result.get("action") == "error":
        raise ValueError(error or result.get("error") or "knowledge composition failed")


def _prepare_pair(
    config: cfg.Config, record, path: Path, seed_id: str, plan, scope: ExitStack,
) -> None:
    root, existing, allocation, child = plan
    config = cfg.load_config()
    record, _ = _validate(config, path, seed_id, retained=record)
    current_plan = _pair_plan(config, record)
    if (current_plan[0].repo, current_plan[0].path) != (root.repo, root.path):
        raise ValueError("knowledge binding changed during creation recovery")
    root, existing, allocation, child = current_plan
    if not root.requires_external:
        return
    if existing is None:
        timestamp, suffix, platform = allocation
        worktree_creation._paired_knowledge_allocation_preflight(config)
        if child is None:
            entry = repos.find_repo(root.repo)
            if entry is None:
                raise ValueError("knowledge repository registration is missing")
            knowledge_id = f"{record.worktree_id}-k"
            if repos.normalize_class(entry.repo_class) in {"worktree", "knowledge"}:
                target = Path(cfg.derive_worktree_root(root.path)) / knowledge_id
                if target.exists():
                    raise ValueError("untracked paired checkout exists; do not allocate another")
            stamp = worktree_creation._carve_paired_knowledge(
                config, harness_id=record.worktree_id, timestamp=timestamp, suffix=suffix,
                plat=record.platform, plat_short=platform,
                machine_identity=record.machine_identity,
                legacy_machine=record.machine if record.machine_identity is None else None,
            )
            if stamp is None:
                raise ValueError("paired creation did not establish its required companion")
    knowledge_config = cfg.load_project_config(root.repo)
    entry = repos.find_repo(root.repo)
    if entry is None:
        raise ValueError("knowledge repository registration is missing")
    requires_child = repos.normalize_class(entry.repo_class) in {
        "worktree", "knowledge",
    }
    scope.enter_context(fin.FinalizeLock(
        Path(knowledge_config.default_repo.worktree_root) / ".finalize.lock",
        timeout=300, stale_after=3600,
    ))
    fresh = cfg.load_config()
    current, _ = _validate(fresh, path, seed_id, retained=record)
    current_root, existing, _, child = _pair_plan(fresh, current)
    if (current_root.repo, current_root.path) != (root.repo, root.path):
        raise ValueError("knowledge binding changed during creation recovery")
    if requires_child and child is None:
        raise ValueError("original companion disappeared before locked creation recovery")
    if child is not None:
        _prepare_permissions(root.path, child.worktree_path)
        stamp = {
            "pair_id": child.pair_id, "pair_role": "harness",
            "pair_ref": tracking.format_claim_ref(child.execution_machine, child.repo, child.worktree_id),
            "pair_kind": "worktree",
        }
    if existing is not None:
        result = knowledge_plugins.compose_from_pair(cwd=current.worktree_path, config=fresh)
        _require_composition(result)
        return
    fresh = cfg.load_config()
    current, _ = _validate(fresh, path, seed_id, retained=record)
    current_root = state_root.resolve_state_root(fresh)
    if (current_root.repo, current_root.path) != (root.repo, root.path):
        raise ValueError("knowledge binding changed during creation recovery")
    if any((current.pair_id, current.pair_ref, current.pair_role, current.pair_kind)):
        raise ValueError("pair metadata changed during creation recovery")
    result = worktree_creation._stamp_and_compose_paired_knowledge(
        fresh, current, current.worktree_path, stamp,
    )
    if result is None:
        result = knowledge_plugins.compose_from_pair(cwd=current.worktree_path, config=fresh)
    _require_composition(result)


def _checks(record, plan) -> dict:
    checks = _permission_state(record.worktree_path)
    checks["paired"] = not plan[0].requires_external or plan[1] is not None
    if plan[3] is not None:
        checks["companion_permissions"] = all(_permission_state(plan[3].worktree_path).values())
    return checks


def targeted_doctor(args) -> int:
    report = {
        "version": 1, "scope": "creation", "worktree_id": args.repair_creation,
        "mode": "fix" if getattr(args, "fix", False) else "report",
        "repairable": False, "repaired": False, "error": None,
        "projection_status": "not_checked",
    }
    try:
        identity = args.repair_creation
        expected = getattr(args, "seed_id", None)
        apply = getattr(args, "fix", False)
        if (
            not isinstance(identity, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identity)
            or ".." in identity
        ):
            raise ValueError("creation repair requires an exact worktree ID, not a path")
        if any(getattr(args, field, False) for field in (
            "repair_record", "bound_agent_end_field", "gc_sessions",
            "prune_pivots", "apply_daemon_health",
        )):
            raise ValueError("creation repair cannot be combined with another repair scope")
        if apply and not expected:
            raise ValueError("creation repair --fix requires the inspected --seed-id")
        config = cfg.load_config()
        if not config.repo_name:
            raise ValueError("creation repair requires an explicitly selected project")
        path = cfg.tracking_dir() / f"{identity}.yaml"
        launch_seed_state.state_path(path)
        record, seed = _validate(config, path, expected)
        plan = _pair_plan(config, record)
        checks = _checks(record, plan)
        report.update(seed_id=seed.seed_id, seed_kind=seed.kind, checks=checks)
        report["repairable"] = True
        setup_incomplete = not all(checks.values())
        if apply:
            with fin.FinalizeLock(
                Path(config.default_repo.worktree_root) / ".finalize.lock",
                timeout=300, stale_after=3600,
            ), launch_admission.guard(path), ExitStack() as admission:
                config = cfg.load_config()
                record, seed = _validate(config, path, expected, retained=record)
                plan = _pair_plan(config, record)
                root, _, allocation, _ = plan
                if root.requires_external:
                    entry = repos.find_repo(root.repo)
                    if entry is None:
                        raise ValueError("knowledge repository registration is missing")
                    if repos.normalize_class(entry.repo_class) in {"worktree", "knowledge"}:
                        child_id = f"{record.worktree_id}-k"
                        child_path = cfg.tracking_dir(root.repo) / f"{child_id}.yaml"
                        admission.enter_context(launch_admission.guard(child_path))
                        plan = _pair_plan(config, record)
                with step("Restoring creation permissions", worktree_id=identity):
                    _prepare_permissions(config.default_repo.anchor, record.worktree_path)
                with step("Restoring creation pair", worktree_id=identity):
                    _prepare_pair(config, record, path, seed.seed_id, plan, admission)
                with step("Restoring creation guidance", worktree_id=identity):
                    refreshed = local_cache_refresh.prepare_for_launch(record.worktree_path)
                    if refreshed.status != "ready":
                        raise ValueError(refreshed.diagnostic)
                config = cfg.load_config()
                current, retained_seed = _validate(config, path, seed.seed_id, retained=record)
                if retained_seed != seed:
                    raise ValueError("retained New changed during creation recovery")
                current_plan = _pair_plan(config, current)
                checks = _checks(current, current_plan)
                if not all(checks.values()):
                    raise ValueError("creation setup remains incomplete")
                report.update(
                    checks=checks, prepared=True,
                    repaired=setup_incomplete or bool(refreshed.changed),
                    projection_status=refreshed.status,
                )
    except (
        OSError, ValueError, RuntimeError, TypeError, yaml.YAMLError, subprocess.TimeoutExpired,
        git_ops.GitError,
    ) as exc:
        report.update(error=str(exc), error_type=type(exc).__name__)
    if getattr(args, "json", False):
        output._json_output(report)
    elif report["error"]:
        output.err(report["error"])
    else:
        output.ok(f"Creation {args.repair_creation}: " + (
            "prepared" if report.get("prepared") else "repairable" if report["repairable"]
            else "permission/pair checks complete; guidance not checked"
        ))
    return 1 if report["error"] else 0
