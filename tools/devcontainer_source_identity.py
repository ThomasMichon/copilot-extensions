"""Pinned version inputs in the existing credential-free source snapshot."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess


def check_snapshot_ancestors(repo: Path, paths: list[str]) -> None:
    real_repo = Path(os.path.realpath(repo))
    for relative in paths:
        path = repo / relative
        if os.path.lexists(path):
            parent = Path(os.path.realpath(path.parent))
            if parent != real_repo and real_repo not in parent.parents:
                raise SystemExit(
                    f"tracked path {relative!r} has an ancestor directory that resolves outside "
                    "the repository root (replaced with a symlink) -- refusing to archive "
                    "external content into the test-isolation container."
                )


def pin_source_inputs(
    metadata: Path, repo: Path, main: str, paths: list[str], environment: dict[str, str],
) -> None:
    result = subprocess.run(
        ["git", "-c", "core.logAllRefUpdates=false", f"--git-dir={metadata}",
         "update-ref", "refs/ce-test/pinned-main", main],
        capture_output=True, text=True, timeout=60, env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise SystemExit(f"pinned Main closure is unavailable: {result.stderr.strip()}")
    check_snapshot_ancestors(repo, paths)
    private_index = metadata / "test-source-index"
    index_environment = {**environment, "GIT_INDEX_FILE": str(private_index)}
    result = subprocess.run(
        ["git", f"--git-dir={metadata}", "read-tree", "--empty"],
        capture_output=True, text=True, timeout=60, env=index_environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise SystemExit(f"candidate source index could not be initialized: {result.stderr.strip()}")
    paths = [path for path in paths if os.path.lexists(repo / path) and not (repo / path).is_dir()]
    if not paths:
        return
    indexed = subprocess.run(
        ["git", "--literal-pathspecs", f"--git-dir={metadata}", f"--work-tree={repo}",
         "add", "--force", "--all", "--pathspec-from-file=-", "--pathspec-file-nul"],
        input=b"\0".join(os.fsencode(path) for path in paths) + b"\0",
        capture_output=True, timeout=60,
        env=index_environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if indexed.returncode:
        raise SystemExit(f"candidate source index could not be pinned: {os.fsdecode(indexed.stderr).strip()}")
