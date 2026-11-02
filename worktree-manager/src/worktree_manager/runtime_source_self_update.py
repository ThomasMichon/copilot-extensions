"""Acquire Manager's independent merged-Dev source without changing its data root."""
from __future__ import annotations

from pathlib import Path

from .runtime_source import SourceAcquisitionError, acquire
from .runtime_source_config import RuntimeSource
from .runtime_source_prepare import prepare, preparer


def fetch(
    repo: str, root: Path, *, environment: dict[str, str] | None = None,
) -> Path:
    base = root / "manager-source"
    selection = RuntimeSource("dev", repo=repo, allow_dev=True)
    tool = preparer()
    try:
        with acquire(selection, root=base, environment=environment) as resolved:
            record = prepare(tool, resolved, "worktree-manager", base / "runtime-sources/prepared")
    except SourceAcquisitionError as error:
        raise ValueError(f"Manager merged-Dev acquisition failed: {error}") from error
    return Path(record["payload"])
