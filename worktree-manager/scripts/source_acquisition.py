"""Qualify Manager's bootstrap inputs without changing its installation layout."""
from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import shutil
import sys
from typing import Any

import installer_engine as engine
import versioned_runtime_source as runtime_source

tomllib = importlib.import_module("tomllib")


@contextmanager
def manager_payload(
    payload: Path, root: Path, *, uv: Path | None = None, prefer_release: bool = True,
) -> Iterator[tuple[Path, dict[str, Any]]]:
    from worktree_manager.self_install import manager_ref, manager_repo, payload_version

    root = root.resolve()
    ref = manager_ref(root)
    receipt = payload / runtime_source.SOURCE_FILE
    attributed = receipt.exists() or receipt.is_symlink()
    if ref == "main" and prefer_release and not attributed and shutil.which("git") is not None:
        from worktree_manager.runtime_source_release import prepared as released_inputs

        with released_inputs(manager_repo(root), root, uv=uv) as released:
            if released is not None:
                selected, options = released
                yield selected, {name.removeprefix("_"): value for name, value in options.items()}
                return
    if ref != "dev":
        yield payload, {}
        return
    if attributed:
        version = payload_version(payload)
        if version is None or runtime_source.read_source_identity(
            payload, version, plugin="worktree-manager",
        ) is None:
            raise ValueError("Manager prepared source receipt disappeared before validation")
        raise ValueError("Manager Dev bootstrap requires complete --install-source inputs for attributed source")
    if shutil.which("git") is None:
        raise ValueError("Manager merged-Dev acquisition requires git; no unqualified tarball fallback")
    for package in engine._package_roots(payload):
        project = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))
        if project["project"]["name"] == "agent-procutil":
            source = package / "src"
            sys.path.insert(0, str(source))
            try:
                module = importlib.import_module("agent_procutil")
            finally:
                sys.path.pop(0)
            origin = module.__file__
            if not isinstance(origin, str) or Path(origin).resolve() != (
                source / "agent_procutil/__init__.py"
            ):
                raise RuntimeError("Manager bootstrap imported a different process helper source")
            break
    else:
        raise ValueError("Manager bootstrap requires its declared local process helper")
    from worktree_manager.runtime_source_self_update import prepared
    from worktree_manager.source_config import absolute_local_source

    uv_options: dict[str, Any] = {"uv": uv} if uv is not None else {}
    with prepared(
        absolute_local_source(manager_repo(root)), root, **uv_options,
    ) as (selected, options):
        yield selected, {name.removeprefix("_"): value for name, value in options.items()}
