"""Filesystem and finite Git observations for aggregate input discovery."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import yaml
from agent_procutil import no_window_kwargs
from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import run_caller_owned


def load_yaml_mapping(
    path: Path,
    *,
    budget: OwnedExecutionBudget | None = None,
) -> dict[str, Any]:
    """Load one mapping, applying typed-sync byte limits when a budget is supplied."""
    if budget is None:
        text = path.read_text(encoding="utf-8")
    else:
        with path.open("rb") as stream:
            raw = stream.read((1 << 20) + 1)
        if len(raw) > 1 << 20:
            raise ValueError("aggregate configuration exceeds the typed sync byte budget")
        text = raw.decode("utf-8")
    return load_yaml_text_mapping(text, str(path))


def load_yaml_text_mapping(text: str, location: str) -> dict[str, Any]:
    """Parse a declaration without accepting non-mapping configuration."""
    data = yaml.safe_load(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{location} must contain a mapping")
    return data


def _git_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_PREFIX",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        env.pop(key, None)
    return env


def observe_git(
    checkout: Path,
    *args: str,
    budget: OwnedExecutionBudget | None = None,
) -> subprocess.CompletedProcess[str]:
    """Read Git under the existing timeout or one shared caller-owned sync budget."""
    argv = ["git", "-C", str(checkout), *args]
    if budget is None:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            env=_git_environment(),
            timeout=10,
            **no_window_kwargs(),
        )
    else:
        binary = run_caller_owned(
            argv,
            binary=True,
            max_stdout_bytes=1 << 20,
            max_stderr_bytes=4096,
            env=_git_environment(),
            timeout=10,
            budget=budget,
        )
        result = subprocess.CompletedProcess(
            binary.args,
            binary.returncode,
            binary.stdout.decode("utf-8"),
            "",
        )
    if result.returncode:
        raise ValueError("authoritative checkout metadata is unavailable")
    return result
