"""Guard against `__version__` drifting from the packaged version.

`agent_dispatch.__version__` is surfaced by `--version` and the coordinator's
`/health`. It lives in `__init__.py` -- a file the marketplace version-bump
process does not read -- so it is easy to leave stale after bumping
`pyproject.toml`. This test fails loudly when the two diverge.
"""

from __future__ import annotations

import importlib.metadata

import agent_dispatch


def test_dunder_version_matches_package_metadata():
    # importlib normalizes "0.1.0-dev36" to "0.1.0.dev36"; compare normalized.
    packaged = importlib.metadata.version("agent-dispatch")
    declared = agent_dispatch.__version__.replace("-", ".")
    assert declared == packaged, (
        f"__version__ ({agent_dispatch.__version__!r}) is out of sync with the "
        f"packaged version ({packaged!r}); bump __init__.py in the same commit"
    )
