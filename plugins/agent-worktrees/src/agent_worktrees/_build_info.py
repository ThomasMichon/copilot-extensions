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
_record = _environment.parent / ".runtime-bootstrap" / "build-info.json"
if _environment.name == ".venv" and _record.exists():
    _value = json.loads(_record.read_text(encoding="utf-8"))
    if not isinstance(_value, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in _value.items()
    ):
        raise RuntimeError("Installed runtime build provenance is malformed")
    BUILD_INFO = _value
else:
    import base64
    import hashlib
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        _distribution = distribution("agent-worktrees")
        _module = Path(_distribution.locate_file("agent_worktrees/_build_info.py")).resolve()
        _files = [
            item for item in (_distribution.files or ())
            if str(item).replace("\\", "/") == "agent_worktrees/_build_info.py"
        ]
        _direct_url = _distribution.read_text("direct_url.json")
        _origin = json.loads(_direct_url) if _direct_url is not None else {}
        _version = _distribution.version
        if (
            _module == Path(__file__).resolve()
            and _distribution.read_text("WHEEL")
            and len(_files) == 1 and _files[0].hash is not None
            and _files[0].hash.mode == "sha256"
            and _files[0].hash.value == base64.urlsafe_b64encode(
                hashlib.sha256(Path(__file__).read_bytes()).digest(),
            ).decode("ascii").rstrip("=")
            and isinstance(_origin, dict) and "dir_info" not in _origin and "vcs_info" not in _origin
            and isinstance(_version, str) and _version and _version == _version.strip()
        ):
            BUILD_INFO = {
                **BUILD_INFO, "version": _version, "commit": "unknown", "source": "distribution",
            }
    except (PackageNotFoundError, OSError, UnicodeError, TypeError, ValueError):
        pass
