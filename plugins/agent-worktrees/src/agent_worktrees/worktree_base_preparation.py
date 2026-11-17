"""Refresh only the creation base and the committed reader's related inputs."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import config, git_ops, inrepo_config_source as source


def _fetch_worktree_base(anchor: Path, remote: str, default_branch: str) -> None:
    deadline = time.monotonic() + git_ops.DEFAULT_FETCH_TIMEOUT
    attempted: dict[tuple[str, str, str], bool] = {}
    errors: list[Exception] = []

    def _refresh(fetch_remote: str, tracking_remote: str, branch: str) -> bool:
        key = (fetch_remote, tracking_remote, branch)
        if key not in attempted:
            attempted[key] = False
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("worktree source preparation exceeded its fetch deadline")
                git_ops._fetch_branches(
                    fetch_remote, cwd=anchor, branches=(branch,), timeout=remaining,
                    tracking_remote=tracking_remote,
                )
                attempted[key] = True
            except Exception as exc:
                errors.append(exc)
        return attempted[key]

    _refresh(remote, remote, default_branch)
    policy_remote = "origin" if git_ops.has_remote("origin", cwd=anchor) else remote
    policy_branch = source._offline_remote_head_branch(anchor, "origin", deadline)
    if policy_branch and _refresh(policy_remote, "origin", policy_branch):
        bootstrap: dict[str, Any] | None = None

        def _capture_bootstrap(text: str) -> dict[str, Any]:
            nonlocal bootstrap
            data = source.parse_yaml_text_safe(text)
            if bootstrap is None:
                bootstrap = data
            return data

        candidates = (
            config.inrepo_config_path(Path()),
            config.legacy_inrepo_config_path(Path()),
            Path(config.INREPO_CONFIG_FILENAME),
        )
        with source.resolution_budget(deadline=deadline):
            # Ignore the reader's possibly stale hop; refresh its declaring input first.
            source.load_inrepo_config_from_committed_ref(
                anchor, candidates, parser=_capture_bootstrap,
            )
        declared = bootstrap.get("default_branch") if bootstrap is not None else None
        if isinstance(declared, str) and declared:
            _refresh(policy_remote, "origin", declared)
    if errors:
        raise errors[0]
