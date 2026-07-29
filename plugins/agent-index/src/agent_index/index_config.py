"""agent-index core configuration: environment-driven, generic defaults."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# -- Model profiles ----------------------------------------------------------


# Content types considered "code" (indexed by the code embedding model).
CODE_CONTENT_TYPES: frozenset[str] = frozenset({
    "function",
    "class",
    "module",
    "yaml-block",
    "config",
})

# Content types considered "prose". Downstream profiles may opt into these.
PROSE_CONTENT_TYPES: frozenset[str] = frozenset({
    "heading",
    "text",
    "issue",
    "pull_request",
    "comment",
    "wiki",
    "announcement",
})


@dataclass(frozen=True)
class ModelProfile:
    """Configuration for a single embedding model.

    Each profile describes one model, its engine subprocess endpoint, the vector
    table where its embeddings live, and any model-specific query preprocessing.
    """

    model_id: str
    model_name: str
    dim: int = 768
    engine_port: int = 8421
    # Host of the engine subprocess. Defaults to localhost. Set
    # AGENT_INDEX_ENGINE_HOST=host.docker.internal when the service runs in a
    # container and the embedding engine stays on the host.
    engine_host: str = field(
        default_factory=lambda: os.environ.get("AGENT_INDEX_ENGINE_HOST", "127.0.0.1")
    )
    table_name: str = "vectors_code"
    query_prefix: str = ""
    content_types: frozenset[str] = field(default_factory=frozenset)
    batch_size: int = int(os.environ.get("AGENT_INDEX_BATCH_SIZE", "16"))
    max_seq_length: int = 1024
    # Optional service unit that runs this model's engine subprocess.
    systemd_unit: str | None = None

    @property
    def engine_url(self) -> str:
        """HTTP base URL for this model's engine subprocess."""
        return f"http://{self.engine_host}:{self.engine_port}"


def _default_cluster_thresholds() -> dict[str, float]:
    """Per-bucket cosine thresholds for similarity clustering.

    Buckets not listed fall back to ``cluster_threshold_default``. Overridable
    via ``AGENT_INDEX_CLUSTER_THRESHOLDS`` (a JSON object mapping bucket to
    float), which replaces these defaults.
    """
    raw = os.environ.get("AGENT_INDEX_CLUSTER_THRESHOLDS")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): float(v) for k, v in parsed.items()}
        except (ValueError, TypeError):
            pass
    return {}


def _default_model_profiles() -> dict[str, ModelProfile]:
    """Build the default model profile registry from environment variables."""
    code_model = os.environ.get(
        "AGENT_INDEX_MODEL", "jinaai/jina-embeddings-v2-base-code"
    )
    batch_size = int(os.environ.get("AGENT_INDEX_BATCH_SIZE", "16"))
    engine_host = os.environ.get("AGENT_INDEX_ENGINE_HOST", "127.0.0.1")

    return {
        "code": ModelProfile(
            model_id="code",
            model_name=code_model,
            dim=768,
            engine_host=engine_host,
            engine_port=int(os.environ.get("AGENT_INDEX_ENGINE_PORT", "8421")),
            table_name="vectors_code",
            content_types=CODE_CONTENT_TYPES,
            batch_size=batch_size,
            max_seq_length=int(os.environ.get("AGENT_INDEX_MAX_SEQ_LENGTH", "1024")),
            systemd_unit=os.environ.get("AGENT_INDEX_ENGINE_UNIT") or None,
        ),
    }


# -- Main config -------------------------------------------------------------


@dataclass(frozen=True)
class IndexConfig:
    """Immutable configuration for the agent-index core."""

    # Paths
    data_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("AGENT_INDEX_DATA_DIR", "~/.agent-index/data")
        ).expanduser()
    )

    # Primary embedding model (single-model compatibility; use model_profiles
    # for multi-model indexing).
    model_name: str = os.environ.get(
        "AGENT_INDEX_MODEL", "jinaai/jina-embeddings-v2-base-code"
    )
    batch_size: int = int(os.environ.get("AGENT_INDEX_BATCH_SIZE", "16"))
    device: str = os.environ.get("AGENT_INDEX_DEVICE", "cuda")
    max_seq_length: int = int(os.environ.get("AGENT_INDEX_MAX_SEQ_LENGTH", "1024"))

    # Query-time embedding. The search/read path embeds queries in-process on
    # CPU by default so search stays responsive and never waits on a cold GPU
    # engine. Set AGENT_INDEX_SEARCH_IN_PROCESS=0 to embed queries through the
    # engine subprocess.
    search_in_process: bool = os.environ.get(
        "AGENT_INDEX_SEARCH_IN_PROCESS", "1"
    ).lower() not in ("0", "false", "no")
    query_device: str = os.environ.get("AGENT_INDEX_QUERY_DEVICE", "cpu")

    # BM25/full-text indexing. Set AGENT_INDEX_FTS_ENABLED=0 for vector-only
    # operation when full-text indexing is unavailable.
    fts_enabled: bool = os.environ.get("AGENT_INDEX_FTS_ENABLED", "1").lower() not in (
        "0",
        "false",
        "no",
    )

    # Search-path concurrency control.
    search_embed_concurrency: int = int(
        os.environ.get("AGENT_INDEX_SEARCH_EMBED_CONCURRENCY", "0")
    )
    search_max_queue: int = int(os.environ.get("AGENT_INDEX_SEARCH_MAX_QUEUE", "8"))
    search_timeout_s: float = float(os.environ.get("AGENT_INDEX_SEARCH_TIMEOUT_S", "25"))

    # Model profiles for multi-model support.
    model_profiles: dict[str, ModelProfile] = field(default_factory=_default_model_profiles)

    # Content table name (stores text + metadata, shared across all models).
    content_table: str = "chunks"

    # Similarity clustering.
    cluster_enabled: bool = os.environ.get(
        "AGENT_INDEX_CLUSTER_ENABLED", "1"
    ).lower() not in ("0", "false", "no")
    cluster_threshold_default: float = float(
        os.environ.get("AGENT_INDEX_CLUSTER_THRESHOLD", "0.92")
    )
    cluster_thresholds: dict[str, float] = field(default_factory=_default_cluster_thresholds)
    cluster_min_size: int = int(os.environ.get("AGENT_INDEX_CLUSTER_MIN_SIZE", "2"))

    # Engine subprocess defaults.
    host: str = os.environ.get("AGENT_INDEX_HOST", "127.0.0.1")
    port: int = int(os.environ.get("AGENT_INDEX_PORT", "8420"))

    # Optional backup target for fast recovery snapshots.
    backup_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("AGENT_INDEX_BACKUP_DIR", "~/.agent-index/backups")
        ).expanduser()
    )

    @property
    def lance_dir(self) -> Path:
        """Vector storage directory."""
        return self.data_dir / "lance"

    @property
    def state_file(self) -> Path:
        """Index state file (last-indexed commits, timestamps)."""
        return self.data_dir / "state.json"

    @property
    def clusters_db(self) -> Path:
        """SQLite file holding the similarity-cluster artifact."""
        return self.data_dir / "clusters.db"

    def cluster_threshold_for(self, bucket: str) -> float:
        """Cosine threshold for a source bucket, with the default fallback."""
        return self.cluster_thresholds.get(bucket, self.cluster_threshold_default)

    @property
    def backup_snapshots_dir(self) -> Path:
        """Snapshot directory for backups."""
        return self.backup_dir / "snapshots"

    @property
    def backup_mount_root(self) -> Path:
        """Mount point or root directory the backup target lives under."""
        override = os.environ.get("AGENT_INDEX_BACKUP_MOUNT_ROOT")
        return Path(override).expanduser() if override else self.backup_dir.parent

    @property
    def backup_state_dir(self) -> Path:
        """Backup metadata directory."""
        return self.backup_dir / "state"

    def get_profile(self, model_id: str) -> ModelProfile:
        """Look up a model profile by ID, raising KeyError if missing."""
        return self.model_profiles[model_id]

    def ensure_dirs(self) -> None:
        """Create local data directories if they do not exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lance_dir.mkdir(parents=True, exist_ok=True)
