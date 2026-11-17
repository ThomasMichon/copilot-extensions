"""Refresh only the creation base and the committed reader's related inputs."""

from __future__ import annotations

import time
from pathlib import Path

from dropin_registry import ScanAuthority

from . import config, config_acquisition, git_ops, inrepo_config_source as source
from .git_acquisition_budget import acquisition_budget


def _mapped_source(ref: str, pattern: str, replacement: str) -> str | None:
    if "*" not in pattern:
        return replacement if ref == pattern else None
    before, after = pattern.split("*", 1)
    if not ref.startswith(before) or not ref.endswith(after) or len(ref) < len(before) + len(after):
        return None
    end = len(ref) - len(after) if after else len(ref)
    return replacement.replace("*", ref[len(before):end])


def _policy_source(anchor: Path, remote: str, branch: str) -> str:
    target = f"refs/remotes/origin/{branch}"
    result = git_ops.git(
        "config", "--get-all", f"remote.{remote}.fetch",
        cwd=anchor, check=False, isolated_repository=True,
    )
    if result.returncode not in (0, 1):
        raise ValueError("committed policy fetch mapping is unavailable")
    sources: set[str] = set()
    exclusions: list[str] = []
    for line in result.stdout.splitlines():
        spec = line.removeprefix("+")
        if spec.startswith("^"):
            exclusions.append(spec[1:])
            continue
        left, separator, right = spec.partition(":")
        if not separator or not right:
            continue
        if left.count("*") != right.count("*") or left.count("*") > 1:
            raise ValueError("committed policy fetch mapping is invalid")
        matched = _mapped_source(target, right, left)
        if matched is not None:
            sources.add(matched)
    if len(sources) > 1:
        raise ValueError("committed policy fetch mapping is ambiguous")
    selected = next(iter(sources), f"refs/heads/{branch}")
    if not selected:
        raise ValueError("committed policy fetch mapping has no source")
    if any(
        _mapped_source(selected, pattern, selected) is not None for pattern in exclusions
    ):
        raise ValueError("committed policy fetch mapping excludes its source")
    return selected


def _fetch_worktree_base(anchor: Path, remote: str, default_branch: str) -> tuple[bool, str | None]:
    deadline = time.monotonic() + git_ops.DEFAULT_FETCH_TIMEOUT
    with acquisition_budget(deadline):
        if not git_ops.has_remote(remote, cwd=anchor):
            return False, None
        return _refresh_sources(anchor, remote, default_branch, deadline)


def _refresh_sources(
    anchor: Path, remote: str, default_branch: str, deadline: float,
) -> tuple[bool, str | None]:
    attempted: dict[tuple[str, str, str], bool] = {}
    errors: list[Exception] = []
    base_source = f"refs/heads/{default_branch}"
    base_target = f"refs/remotes/{remote}/{default_branch}"

    def _refresh(fetch_remote: str, fetch_source: str, target: str) -> bool:
        key = (fetch_remote, fetch_source, target)
        if key not in attempted:
            attempted[key] = False
            try:
                if target == base_target and (fetch_remote != remote or fetch_source != base_source):
                    raise ValueError("committed policy fetch would replace the configured base")
                git_ops._fetch_refspecs(
                    fetch_remote, cwd=anchor, refspec=(f"+{fetch_source}:{target}",),
                    timeout=git_ops.DEFAULT_FETCH_TIMEOUT,
                )
                attempted[key] = True
            except Exception as exc:
                errors.append(exc)
        return attempted[key]

    fetched = _refresh(remote, base_source, base_target)
    parent_observations = config_acquisition.current_observations()
    try:
        with config_acquisition.capture_config_observations() as observations:
            try:
                policy_remote = "origin" if git_ops.has_remote("origin", cwd=anchor) else remote
                policy_branch = source._offline_remote_head_branch(anchor, "origin", deadline)
                if policy_branch:
                    def _refresh_policy(branch: str) -> bool:
                        return _refresh(
                            policy_remote, _policy_source(anchor, policy_remote, branch),
                            f"refs/remotes/origin/{branch}",
                        )

                    if _refresh_policy(policy_branch):
                        candidates = (
                            config.inrepo_config_path(Path()),
                            config.legacy_inrepo_config_path(Path()),
                            Path(config.INREPO_CONFIG_FILENAME),
                        )
                        bootstrap = source._read_committed_batch(
                            anchor, "origin", policy_branch, candidates, deadline,
                        )
                        if bootstrap is not None:
                            data = next((
                                source.parse_yaml_text_safe(bootstrap[path])
                                for path in candidates if path in bootstrap
                            ), {})
                            declared = data.get("default_branch")
                            if isinstance(declared, str) and declared:
                                _refresh_policy(declared)
                if any(o.authority is ScanAuthority.INDETERMINATE for o in observations):
                    errors.append(ValueError("committed policy refresh could not verify its source"))
            except Exception as exc:
                errors.append(exc)
    finally:
        if parent_observations is not None:
            parent_observations.extend(observations)
    return fetched, str(errors[0]) if errors else None
