"""Normal-round reuse fingerprints, including metadata and embedding policy."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_index.index_config import ModelProfile
    from agent_index.sources.base import FileEntry


def file_fingerprint(
    entry: FileEntry,
    profiles: dict[str, ModelProfile] | None,
    *,
    chunker_revision: str = "1",
) -> str:
    """Identify a completely stored file under the current indexing policy.

    Unlike the legacy content-only resume hash, this cannot skip metadata or
    profile changes. Old checkpoints deliberately miss once. Chunker changes
    that alter output must advance the explicit policy revision.
    """
    models = {
        key: {
            "id": profile.model_id,
            "name": profile.model_name,
            "revision": profile.revision,
            "dim": profile.dim,
            "table": profile.table_name,
            "prefix": profile.query_prefix,
            "length": profile.max_seq_length,
            "types": sorted(profile.content_types),
        }
        for key, profile in (profiles or {}).items()
    }
    payload = {
        "source": entry.source,
        "path": entry.path,
        "content": entry.content,
        "language": getattr(entry, "language", ""),
        "metadata": getattr(entry, "metadata", {}),
        "chunker_revision": chunker_revision,
        "models": models,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    # v2 also qualifies canonical ID coalescing and its unique-chunk accounting.
    return "file-v2:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
