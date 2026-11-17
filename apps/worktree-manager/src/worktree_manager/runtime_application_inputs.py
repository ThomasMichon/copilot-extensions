"""Installed, non-routing access to Manager's declared complete-input producer."""
from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

import agent_procutil


def _ordinary(path: Path) -> Path:
    if ".." in path.parts:
        raise ValueError("Manager input provider paths must not contain parent traversal")
    for member in (path, *path.parents):
        if member.is_symlink() or (
            member.exists() and getattr(member.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("Manager input provider paths must not be linked")
    return path


def provider_root() -> Path:
    """Resolve this installed wheel's declared provider, never a fetched checkout."""
    root = _ordinary(Path(__file__).absolute().parent / "_runtime_assets" / "scripts" / "runtime-input-provider")
    declaration = _ordinary(root / "tools" / "manager_application_provider.json")
    if not declaration.is_file():
        raise ValueError("Manager complete-input provider is unavailable; repair authentic Manager assets")
    record = json.loads(declaration.read_text(encoding="utf-8"))
    if record != {
        "schema": "copilot-extensions.manager-application-input-provider", "version": 1,
        "component": "worktree-manager", "input": "associated-first-party-snapshot",
        "entry": "prepare_manager_application.py",
    }:
        raise ValueError("Manager complete-input provider declaration is invalid")
    return root


def _load(path: Path, namespace: str):
    spec = importlib.util.spec_from_file_location(namespace, _ordinary(path))
    if spec is None or spec.loader is None:
        raise ValueError("Manager input provider process resources are unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[namespace] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(namespace, None)
        raise
    return module


def prepare_application_inputs(
    snapshot: Path, output: Path, *, identity: dict, python: Path,
    tooling_python: Path, uv: Path, deadline: float,
) -> dict:
    """Complete an independently admitted snapshot under caller-owned source authority.

    The caller supplies its explicitly prepared tooling interpreter and retains
    source/feed authority through consumption. This never selects or installs a runtime.
    """
    if (
        isinstance(deadline, bool) or not isinstance(deadline, (float, int))
        or not math.isfinite(deadline) or deadline <= time.monotonic()
    ):
        raise ValueError("Manager input provider requires a live finite caller deadline")
    for path in (snapshot, output, python, tooling_python, uv):
        if not path.is_absolute():
            raise ValueError("Manager input provider requires absolute input and executable paths")
        if ".." in path.parts:
            raise ValueError("Manager input provider paths must not contain parent traversal")
    for path in (snapshot, output):
        _ordinary(path)
    if not all(path.is_file() for path in (python, tooling_python, uv)):
        raise ValueError("Manager input provider selected executable is unavailable")
    root = provider_root()
    entry = _ordinary(root / "tools" / "prepare_manager_application.py")
    if not entry.is_file():
        raise ValueError("Manager complete-input provider entry is unavailable")
    process = _load(
        root / "libs" / "installer-engine" / "installer_engine_process.py",
        __name__ + "._process",
    )
    command = [
        str(tooling_python), "-I", "-B", "-X", "utf8", str(entry),
        "--install-source", str(snapshot), "--output", str(output),
        "--identity", json.dumps(identity, sort_keys=True),
        "--python", str(python), "--uv", str(uv), "--deadline", str(deadline),
    ]
    try:
        result = process.run_owned(
            command, cwd=root, env={
                key: value for key, value in os.environ.items()
                if key.upper() not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"}
            }, timeout=min(180, deadline - time.monotonic()), deadline=deadline,
            procutil=agent_procutil, construction_fd=None,
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            "Manager complete-input preparation failed: " + (error.stderr or "")[-65536:]
        ) from error
    if time.monotonic() >= deadline:
        raise ValueError("Manager input provider caller deadline expired")
    if len(result.stdout.encode("utf-8")) > 65536:
        raise ValueError("Manager input provider returned an oversized result")
    record = json.loads(result.stdout)
    if not isinstance(record, dict) or not isinstance(record.get("snapshot"), str):
        raise ValueError("Manager input provider returned an invalid result")
    produced = Path(record.get("snapshot", ""))
    if (
        record.get("identity") != identity or not produced.is_absolute()
        or not produced.is_relative_to(output) or not produced.is_dir()
    ):
        raise ValueError("Manager input provider returned a different source or output")
    _ordinary(produced)
    return record
