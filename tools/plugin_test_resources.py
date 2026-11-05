"""Source-bound retained caches and disposable runner scratch directories."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from .cache_directory_paths import ordinary_cache_root
else:
    from cache_directory_paths import ordinary_cache_root


def resource_root(repo: Path) -> Path:
    """Keep retained environments separate for every canonical source checkout."""
    source = repo.resolve(strict=True)
    configured = os.environ.get("AGENT_SCRATCH_ROOT")
    owner = (
        str(os.getuid()) if hasattr(os, "getuid")
        else hashlib.sha256(str(Path.home()).encode("utf-8")).hexdigest()[:12]
    )
    scratch = (
        Path(configured).expanduser() if configured is not None
        else Path(tempfile.gettempdir()) / f"copilot-extensions-tests-{owner}"
    )
    if not scratch.is_absolute():
        raise ValueError("AGENT_SCRATCH_ROOT must name an absolute directory")
    scratch = ordinary_cache_root(scratch)
    if scratch.is_relative_to(source):
        raise ValueError("test scratch must be outside the source checkout")
    identity = hashlib.sha256(os.path.normcase(str(source)).encode("utf-8")).hexdigest()[:20]
    scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = ordinary_cache_root(scratch / f"ce-tests-{identity}")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    record_owner(root, source, retention="retained source-bound test cache; no automatic sweep")
    return root


def record_owner(path: Path, repo: Path, *, retention: str) -> None:
    """A breadcrumb describes ownership, never grants cleanup permission."""
    try:
        with (path / "scratch-owner.json").open("x", encoding="utf-8") as output:
            json.dump({
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_checkout": str(repo.resolve()),
                "session_id": os.environ.get("COPILOT_AGENT_SESSION_ID"),
                "purpose": "copilot-extensions repository test runner",
                "retention": retention,
            }, output)
    except FileExistsError:
        pass


def venv_root(repo: Path) -> Path:
    return ordinary_cache_root(resource_root(repo) / "venvs" / sys.platform)


def runs_root(repo: Path) -> Path:
    root = ordinary_cache_root(resource_root(repo) / "runs")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def run_prefix(target: str) -> str:
    return f"ce-{target[:4]}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-"
