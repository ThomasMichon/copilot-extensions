"""Local compatibility adapter. Queue, query, workers and zdd remain core-owned."""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType

from .config import HostConfig


class NativeRuntimeUnavailable(RuntimeError):
    """The optional hosted runtime or required core contract is unavailable."""


@contextmanager
def core_environment(config: HostConfig) -> Iterator[None]:
    selected = config.core_environment()
    owned = {
        key: value for key, value in os.environ.items()
        if key.startswith("AGENT_INDEX_") or key == "COPILOT_EXTENSIONS_CONTEXT"
    }
    # Isolate from ambient plugin/cell/engine selections; children inherit this
    # explicit snapshot, including legacy core workers and zdd successors.
    for key in owned:
        del os.environ[key]
    os.environ.update(selected)
    try:
        yield
    finally:
        for key in list(os.environ):
            if key.startswith("AGENT_INDEX_") or key == "COPILOT_EXTENSIONS_CONTEXT":
                del os.environ[key]
        os.environ.update(owned)


def load_core(*, native: bool = False) -> ModuleType:
    try:
        config = importlib.import_module("agent_index.config")
        if getattr(config, "SOURCE_MODE_ENV", None) != "AGENT_INDEX_SOURCE_MODE":
            raise NativeRuntimeUnavailable(
                "agent-index core lacks the explicit-source compatibility seam; "
                "provision a compatible library before serving or deploying"
            )
        if native:
            for name in (
                "fastapi", "uvicorn", "pydantic", "numpy", "pyarrow", "lancedb",
                "tree_sitter", "agent_index.indexing.task_store", "agent_index.indexing.runner",
            ):
                importlib.import_module(name)
        return importlib.import_module("agent_index.__main__")
    except ImportError as exc:
        extra = "[native]" if native else ""
        raise NativeRuntimeUnavailable(
            f"runtime dependency unavailable ({exc.name}): "
            f"provision agent-index-service{extra} with a compatible agent-index library"
        ) from exc


def require_installed_core() -> None:
    """Core deploy/worker children use -I, not a checkout's PYTHONPATH."""
    import json
    import subprocess
    from pathlib import Path

    import agent_index
    from agent_procutil import no_window_kwargs

    try:
        probe = subprocess.run(
            [sys.executable, "-I", "-c",
             "import json,agent_index; from agent_index.config import SOURCE_MODE_ENV; "
             "print(json.dumps([agent_index.__file__, SOURCE_MODE_ENV]))"],
            capture_output=True, text=True, timeout=15, check=False, **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise NativeRuntimeUnavailable("isolated core import probe timed out after 15s") from exc
    try:
        identity = json.loads(probe.stdout) if probe.returncode == 0 else None
    except ValueError:
        identity = None
    if (
        not isinstance(identity, list) or len(identity) != 2
        or not isinstance(identity[0], str)
        or identity[1] != "AGENT_INDEX_SOURCE_MODE"
        or Path(identity[0]).resolve() != Path(agent_index.__file__).resolve()
    ):
        raise NativeRuntimeUnavailable(
            "deploy needs this compatible agent-index library installed in this interpreter; "
            "PYTHONPATH-only or mismatched core imports cannot start isolated zdd successors"
        )
