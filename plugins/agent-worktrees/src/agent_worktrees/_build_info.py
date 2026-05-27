"""Build provenance -- overwritten at deploy time.

The repo source has safe defaults.  When ``deploy_package()`` (Python),
``Deploy-Package`` (PowerShell), or ``deploy_package`` (bash) copies
the package into the runtime directory, this file is regenerated with
the actual commit hash, timestamp, and source path.

Query at runtime::

    from agent_worktrees._build_info import BUILD_INFO
    print(BUILD_INFO["version"])
"""

from __future__ import annotations

BUILD_INFO: dict[str, str] = {
    "version": "1.0.0",
    "commit": "dev",
    "branch": "unknown",
    "build_timestamp": "unknown",
    "source": "repo",
}
