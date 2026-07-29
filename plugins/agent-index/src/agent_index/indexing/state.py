"""Index state management — track last-indexed commits and timestamps."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class SourceState:
    """Tracking state for a single data source."""

    last_commit: str | None = None
    last_indexed_at: float | None = None
    chunk_count: int = 0


@dataclass
class IndexState:
    """Persistent state for the agent-index index."""

    sources: dict[str, SourceState] = field(default_factory=dict)
    total_chunks: int = 0
    last_full_reindex: float | None = None

    @classmethod
    def load(cls, path: Path) -> IndexState:
        """Load state from disk, or return empty state if missing."""
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        sources = {name: SourceState(**src) for name, src in data.get("sources", {}).items()}
        return cls(
            sources=sources,
            total_chunks=data.get("total_chunks", 0),
            last_full_reindex=data.get("last_full_reindex"),
        )

    def save(self, path: Path) -> None:
        """Persist state to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "sources": {name: asdict(src) for name, src in self.sources.items()},
            "total_chunks": self.total_chunks,
            "last_full_reindex": self.last_full_reindex,
        }
        path.write_text(json.dumps(data, indent=2) + "\n")

    def mark_source_indexed(self, name: str, commit: str | None, chunk_count: int) -> None:
        """Update state after indexing a source."""
        self.sources[name] = SourceState(
            last_commit=commit,
            last_indexed_at=time.time(),
            chunk_count=chunk_count,
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

        dt = datetime.fromtimestamp(state.last_full_reindex, tz=UTC)
        print(f"Last full reindex: {dt.isoformat()}")
    print()
    for name, src in sorted(state.sources.items()):
        commit = src.last_commit[:8] if src.last_commit else "none"
        print(f"  {name}: {src.chunk_count} chunks (commit: {commit})")
