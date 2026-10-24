"""Shared invocation context without importing the command dispatcher."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _override(name, local):
    for module_name in ("agent_worktrees.__main__", "agent_worktrees.front_door_cli"):
        module = sys.modules.get(module_name)
        candidate = vars(module).get(name) if module is not None else None
        if callable(candidate) and candidate is not local:
            return candidate
    return local


def resolve_invocation_project(args_list, explicit, resolve_project, contains_cwd, is_no_project):
    if args_list[:1] == ["doctor"] and any(
        option.startswith("--rec") and "--recovery-report".startswith(option)
        for option in (arg.partition("=")[0] for arg in args_list[1:])
    ):
        return False
    from . import config as cfg

    if args_list and args_list[0] == "record":
        from .record_config import metadata_project

        cfg.set_active_project(metadata_project(explicit))
        return bool(cfg.active_project())
    needs_project = not is_no_project(args_list)
    optional_project = (
        bool(args_list) and args_list[0] in {"config-root", "launch-update"}
        and is_no_project(args_list)
    )
    if explicit:
        project, assumed = resolve_project(explicit)
    elif needs_project or optional_project:
        project, assumed = resolve_project(None)
    else:
        project, assumed = None, None
    if project:
        cfg.set_active_project(project)
        if explicit and assumed is not None and not contains_cwd(assumed):
            try:
                os.chdir(assumed)
            except OSError:
                pass
    return bool(cfg.active_project())


def _extract_project_flag(args_list):
    out, project, i = [], None, 0
    while i < len(args_list):
        arg = args_list[i]
        if project is None and arg in ("--project", "-p"):
            if i + 1 < len(args_list):
                project = args_list[i + 1]
                i += 2
                continue
            i += 1
            continue
        if project is None and arg.startswith("--project="):
            project = arg.split("=", 1)[1]
            i += 1
            continue
        out.append(arg)
        i += 1
    return out, (project.strip() if project else None)


def _safe_cwd():
    override = _override("_safe_cwd", _safe_cwd)
    if override is not _safe_cwd:
        return override()
    try:
        return Path.cwd()
    except OSError:
        return None


def _git_toplevel(path):
    override = _override("_git_toplevel", _git_toplevel)
    if override is not _git_toplevel:
        return override(path)
    if path is None:
        return None
    import subprocess
    from agent_procutil import no_window_kwargs
    from . import git_ops

    def rev_parse(*args):
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", *args],
            capture_output=True, text=True, timeout=5,
            env=git_ops.repository_identity_env(), stdin=subprocess.DEVNULL,
            **_override("no_window_kwargs", no_window_kwargs)(),
        )

    try:
        result = rev_parse("--show-toplevel")
        if result.returncode == 0 and result.stdout.strip():
            return git_ops.resolve_to_anchor(Path(result.stdout.strip()).resolve())
    except Exception:
        pass
    try:
        result = rev_parse("--is-bare-repository", "--git-dir")
        lines = result.stdout.strip().splitlines()
        if result.returncode == 0 and len(lines) == 2 and lines[0].strip() == "true":
            git_dir = Path(lines[1].strip())
            if not git_dir.is_absolute():
                git_dir = (path / git_dir).resolve()
            return git_ops.resolve_to_anchor(git_dir.parent if git_dir.name == ".git" else git_dir)
    except Exception:
        pass
    return None


def _anchor_for_project(name):
    override = _override("_anchor_for_project", _anchor_for_project)
    if override is not _anchor_for_project:
        return override(name)
    from . import config as cfg, installer as inst

    try:
        entry = inst.read_projects_registry().get("projects", {}).get(name)
        if isinstance(entry, dict) and entry.get("anchor"):
            path = Path(entry["anchor"])
            if path.is_dir():
                return path.resolve()
    except Exception:
        pass
    try:
        anchor = cfg._resolve_anchor_from_registry(name, cfg.detect_platform())
        if anchor and Path(anchor).is_dir():
            return Path(anchor).resolve()
    except Exception:
        pass
    return None


def _cwd_is_inside_project(anchor):
    override = _override("_cwd_is_inside_project", _cwd_is_inside_project)
    if override is not _cwd_is_inside_project:
        return override(anchor)
    from . import git_ops

    top = _git_toplevel(_safe_cwd())
    return top is not None and (
        git_ops._normalize_wt_path(str(top)) == git_ops._normalize_wt_path(str(anchor))
    )


def _resolve_active_project(project_override):
    override = _override("_resolve_active_project", _resolve_active_project)
    if override is not _resolve_active_project:
        return override(project_override)
    if project_override:
        return project_override, _anchor_for_project(project_override)
    from .front_door_cli import _reverse_lookup_project

    anchor = _git_toplevel(_safe_cwd())
    if anchor is not None:
        name = _reverse_lookup_project(anchor)
        if name:
            return name, None
    return None, None
