"""agent-index-engine — the heavy embedding-engine server for agent-index.

A separate, independently-installable program (own venv, own dependency
footprint) that owns the torch/sentence-transformers stack. Never installed
alongside the light agent-index service or on a search client -- only on a
host role. See the package README for the full picture.
"""

from __future__ import annotations
