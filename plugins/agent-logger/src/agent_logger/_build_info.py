"""Build/version provenance for agent-logger.

``__version__`` is kept in sync with ``pyproject.toml`` and
``plugin.json`` per the copilot-extensions version-triplet rule. The
installer overwrites ``COMMIT`` / ``BUILT_AT`` at deploy time; the
checked-in defaults are placeholders for source checkouts.
"""

from __future__ import annotations

__version__ = "0.1.0"

COMMIT = "unknown"
BUILT_AT = "unknown"
