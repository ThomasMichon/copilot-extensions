"""Registration-specific receiving-side authority configuration."""

from __future__ import annotations

import subprocess
from pathlib import Path

_GIT_PROBE_TIMEOUT_S = 0.3


def _load_state_root_authority(
    name: str, anchor: str, platform_name: str, remote_name: str,
) -> dict:
    """Read only state-root gates from the registered committed config."""
    from . import config as cfg, git_ops, repos

    root = Path(anchor)
    candidates = (
        cfg.inrepo_config_path(Path()),
        cfg.legacy_inrepo_config_path(Path()),
        Path(cfg.INREPO_CONFIG_FILENAME),
    )
    entry = repos.read_registry().repos.get(name)
    branch = entry.default_branch if entry is not None else ""
    if not branch:
        branch = str(
            cfg._resolve_adoption_defaults_from_registry(
                name, platform_name,
            ).get("default_branch", "")
        )
    if not branch:
        head_ref = (
            f"refs/remotes/{remote_name}/HEAD"
            if remote_name else "HEAD"
        )
        head = git_ops.git(
            "symbolic-ref", "-q", head_ref,
            cwd=root, check=False, capture=True, timeout=_GIT_PROBE_TIMEOUT_S,
            isolated_repository=True,
        )
        prefix = (
            f"refs/remotes/{remote_name}/"
            if remote_name else "refs/heads/"
        )
        ref = head.stdout.strip() if head.returncode == 0 else ""
        branch = ref[len(prefix):] if ref.startswith(prefix) else ""
    if not branch:
        raise ValueError(f"cannot resolve committed branch for project {name!r}")
    refs = (
        [
            f"refs/remotes/{remote_name}/{branch}",
            f"refs/heads/{branch}",
        ]
        if remote_name else [f"refs/heads/{branch}"]
    )
    ref = ""
    listing = None
    for candidate_ref in refs:
        candidate_listing = git_ops.git(
            "ls-tree", "-r", "--name-only", candidate_ref, "--",
            *(candidate.as_posix() for candidate in candidates),
            cwd=root, check=False, capture=True, timeout=_GIT_PROBE_TIMEOUT_S,
            isolated_repository=True,
        )
        if candidate_listing.returncode == 0:
            ref, listing = candidate_ref, candidate_listing
            break
    if listing is None:
        raise ValueError(f"cannot read committed authority config for {name!r}")
    available = set(listing.stdout.splitlines())
    selected = next(
        (candidate for candidate in candidates if candidate.as_posix() in available),
        None,
    )
    if selected is None:
        return {}
    content = git_ops.git(
        "show", f"{ref}:{selected.as_posix()}",
        cwd=root, check=False, capture=True, timeout=_GIT_PROBE_TIMEOUT_S,
        isolated_repository=True,
    )
    if content.returncode != 0:
        raise ValueError(f"cannot read committed authority config for {name!r}")
    raw = cfg.yaml.safe_load(content.stdout)
    if not isinstance(raw, dict):
        raise ValueError(f"committed authority config for {name!r} is malformed")
    return raw


def load_project_authority_config(
    name: str, *, path: Path | None = None,
):
    """Resolve only identity, registry, and state-root facts used by fences."""
    from . import config as cfg

    path = path or cfg.project_dir(name) / "config.yaml"
    global_raw = cfg.config_migrations.migrate_loaded(
        cfg._load_yaml_safe(cfg.global_config_path()),
        cfg.config_migrations.SCHEMA_CONFIG,
    )
    machine_raw = cfg._load_yaml_safe(path)
    dropins = cfg._load_config_d(
        path.parent / "config.d",
        project_name=name,
        warn=True,
    )
    if dropins:
        machine_raw = cfg._deep_merge(dropins, machine_raw)

    platform_name = (
        machine_raw.get("platform")
        or global_raw.get("platform")
        or cfg.detect_platform()
    )
    machine = (
        machine_raw.get("machine")
        or global_raw.get("machine")
        or cfg.detect_machine()
    )
    machine_repos = machine_raw.get("repos") or {}
    machine_repo = (
        machine_repos.get(name) or {}
        if isinstance(machine_repos, dict)
        else {}
    )
    if not isinstance(machine_repo, dict):
        machine_repo = {}
    remote_name = str(machine_repo.get("remote", "origin") or "")
    anchor = machine_repo.get("anchor") or cfg._resolve_anchor_from_registry(
        name, platform_name
    )
    if not anchor:
        raise ValueError(
            f"No repo could be resolved for project {name!r} from "
            f"{path} or the repos registry"
        )

    inrepo_authority = _load_state_root_authority(
        name, str(anchor), platform_name, remote_name,
    )
    stateless = bool(
        machine_repo.get("stateless", inrepo_authority.get("stateless", False))
    )
    requires_external_state_root = bool(
        machine_repo.get(
            "requires_external_state_root",
            inrepo_authority.get("requires_external_state_root", False),
        )
        or stateless
    )
    worktree_root = machine_repo.get("worktree_root") or cfg.derive_worktree_root(
        anchor
    )
    config = cfg.Config(
        srcroot=machine_raw.get("srcroot") or global_raw.get("srcroot") or "",
        machine=machine,
        platform=platform_name,
        repo_name=name,
        knowledge_repo=(
            machine_raw.get("knowledge_repo")
            or global_raw.get("knowledge_repo")
            or ""
        ),
        repos={
            name: cfg.RepoConfig(
                anchor=str(anchor),
                worktree_root=str(worktree_root),
                remote=remote_name,
                stateless=stateless,
                requires_external_state_root=requires_external_state_root,
            ),
        },
    )
    from .execution_spaces import canonicalize_legacy_config

    return canonicalize_legacy_config(config)


def require_registration_record_mutation(record) -> None:
    """Fence one session-registration record without full project policy."""
    from . import config as cfg
    from .execution_spaces import ExecutionSpaceError, require_record_mutation

    try:
        config = load_project_authority_config(record.repo)
        if record.repo not in config.repos:
            raise ValueError(
                "requested project is not registered in receiving-side configuration"
            )
        require_record_mutation(record, config)
    except (
        OSError, ValueError, KeyError, TypeError,
        cfg.yaml.YAMLError, subprocess.TimeoutExpired,
    ) as exc:
        raise ExecutionSpaceError(
            f"cannot establish receiving-side authority for project {record.repo!r}: {exc}"
        ) from exc
