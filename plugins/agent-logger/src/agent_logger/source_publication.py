"""Required producer identity and canonical publication-key validation."""

from __future__ import annotations

from pathlib import Path

from agent_logger.source_roots import (
    SourceIdentity,
    SourceLayoutError,
    read_source_metadata,
    validate_source_key,
)


def load_source_identity_file(path: Path) -> SourceIdentity:
    """Load required producer identity without inferring it from a physical alias."""
    identity, _, _ = read_source_metadata(path)
    return identity


def validate_publication_key(key: str, identity: SourceIdentity) -> str:
    """New publication may use only the identity's canonical namespace."""
    validate_source_key(key)
    if key != identity.namespace:
        raise SourceLayoutError(
            f"publication key does not match canonical source identity: {key!r}"
        )
    return key
