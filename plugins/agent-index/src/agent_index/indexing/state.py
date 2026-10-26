"""Index state management — track last-indexed commits and timestamps."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class SourceState:
    """A source cursor/totals and the accepted acquisition's nonsecret scope hash."""

    last_commit: str | None = None
    last_indexed_at: float | None = None
    chunk_count: int = 0
    acquisition_scope: str | None = None


@dataclass
class IndexState:
    """Persistent state for the agent-index index."""

    sources: dict[str, SourceState] = field(default_factory=dict)
    total_chunks: int = 0
    last_full_reindex: float | None = None
    stored_families: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> IndexState:
        """Load state from disk, or return empty state if missing."""
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        families = data.get("stored_families", {})
        if not isinstance(families, dict) or any(
            not isinstance(name, str) or not name or not isinstance(family, str) or not family
            for name, family in families.items()
        ):
            raise ValueError("invalid persisted canonical source identities")
        sources = {name: SourceState(**src) for name, src in data.get("sources", {}).items()}
        if any(src.acquisition_scope is not None and (
            not isinstance(src.acquisition_scope, str) or not src.acquisition_scope
        ) for src in sources.values()):
            raise ValueError("invalid persisted source acquisition scope")
        return cls(
            sources=sources,
            total_chunks=data.get("total_chunks", 0),
            last_full_reindex=data.get("last_full_reindex"),
            stored_families=families,
        )

    def save(self, path: Path) -> None:
        """Persist state to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "sources": {name: asdict(src) for name, src in self.sources.items()},
            "total_chunks": self.total_chunks,
            "last_full_reindex": self.last_full_reindex,
        }
        if self.stored_families:
            data["stored_families"] = self.stored_families
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(data, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def require_acquisition_scope(self, name: str, scope: str, *, full: bool) -> None:
        """Refuse an unproven incremental cursor before acquiring or applying data."""
        if not isinstance(scope, str) or not scope:
            raise ValueError("invalid source acquisition scope")
        checkpoint = self.sources.get(name)
        if not full and checkpoint is not None and checkpoint.acquisition_scope != scope:
            raise ValueError(
                f"Source checkpoint {name!r} has a missing or different acquisition identity; "
                "explicit full reindex required",
            )

    def mark_source_indexed(self, name: str, commit: str | None, chunk_count: int) -> None:
        """Update state after indexing a source."""
        self.sources[name] = SourceState(
            last_commit=commit,
            last_indexed_at=time.time(),
            chunk_count=chunk_count,
            acquisition_scope=self.sources[name].acquisition_scope if name in self.sources else None,
        )
        self.total_chunks = sum(s.chunk_count for s in self.sources.values())


def print_status() -> None:
    """Print index status to stdout."""
    from agent_index.index_config import IndexConfig

    config = IndexConfig()
    state = IndexState.load(config.state_file)

    print(f"Data directory: {config.data_dir}")
    print(f"Total chunks:   {state.total_chunks}")
    print(f"Sources:        {len(state.sources)}")
    if state.last_full_reindex:
        from datetime import datetime

        dt = datetime.fromtimestamp(state.last_full_reindex, tz=timezone.utc)
        print(f"Last full reindex: {dt.isoformat()}")
    print()
    for name, src in sorted(state.sources.items()):
        commit = src.last_commit[:8] if src.last_commit else "none"
        print(f"  {name}: {src.chunk_count} chunks (commit: {commit})")
