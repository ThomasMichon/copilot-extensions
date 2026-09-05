"""Independent lifecycle identity for the durable embedding-engine runtime."""

from __future__ import annotations

import os

ENGINE_GENERATION_ENV = "AGENT_INDEX_ENGINE_GENERATION"
_DEFAULT_ENGINE_GENERATION = "engine-v1"


def current_engine_generation() -> str:
    """Return the durable engine generation, independent from the service version."""
    return (
        os.environ.get(ENGINE_GENERATION_ENV, "").strip()
        or _DEFAULT_ENGINE_GENERATION
    )
