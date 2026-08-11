"""Build provenance -- overwritten at deploy time.

The repo source ships an EMPTY ``version`` on purpose: an un-stamped checkout
must fall through to ``importlib.metadata`` (see ``_resolve_version`` in
``__init__.py``) rather than report a stale literal. When the installer copies
the package into the runtime slot, ``scripts/stamp_build_info.py`` regenerates
this file with the real version (from ``pyproject.toml`` -- the single source of
truth), commit hash, branch, timestamp, and source path.

Query at runtime::

    from agent_dispatch._build_info import BUILD_INFO
    print(BUILD_INFO["version"])
"""

from __future__ import annotations

BUILD_INFO: dict[str, str] = {
    "version": "",
    "commit": "dev",
    "branch": "unknown",
    "build_timestamp": "unknown",
    "source": "repo",
}
