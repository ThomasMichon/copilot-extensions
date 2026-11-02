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

import json
from pathlib import Path
import sys

BUILD_INFO: dict[str, str] = {
    "version": "1.0.0",
    "commit": "dev",
    "branch": "unknown",
    "build_timestamp": "unknown",
    "source": "repo",
}

_environment = Path(sys.prefix).resolve()
if _environment.name == ".venv":
    _record = _environment.parent / ".runtime-bootstrap" / "build-info.json"
    if _record.exists():
        _value = json.loads(_record.read_text(encoding="utf-8"))
        if not isinstance(_value, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in _value.items()
        ):
            raise RuntimeError("Installed runtime build provenance is malformed")
        BUILD_INFO = _value
