#!/usr/bin/env python3
"""Publish only session metadata/binstubs under the runtime gate's admission lock."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile


def publish(path: Path, data: bytes, *, executable: bool = False) -> None:
    for parent in (path, *path.parents):
        if parent.is_symlink() or (
            hasattr(parent, "is_junction") and parent.is_junction()
        ):
            raise ValueError(f"refusing linked stamp destination: {parent}")
    if path.is_file() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".session-stamp-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        if executable:
            temporary.chmod(0o755)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def stamp(root: Path, payload: Path, mode: str) -> None:
    manifest = json.loads((payload / "plugin.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    spec = importlib.util.spec_from_file_location(
        "stamp_versioned_runtime", payload / "scripts/versioned_runtime.py"
    )
    if spec is None or spec.loader is None:
        raise ValueError("stamp version comparator is unavailable")
    comparator = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(comparator)
    for marker in ("current-version", "stamped-version"):
        path = root / marker
        if path.is_file() and comparator._version_key(
            path.read_text(encoding="utf-8").strip()
        ) > comparator._version_key(version):
            return
    # A deployed launcher's snapshot pointer is not session metadata to replace.
    if (root / "current-version").exists():
        return
    publish(root / "payload-dir", (str(payload) + "\n").encode())
    publish(root / "stamped-version", (version + "\n").encode())
    if mode == "legacy":
        home = Path(os.environ.get("USERPROFILE") or str(Path.home())) if os.name == "nt" else Path.home()
        bin_dir = home / ".local/bin"
        if os.name == "nt":
            gate = str(payload / "scripts/runtime-gate.ps1").replace("'", "''")
            publish(bin_dir / "agent-index.ps1",
                    f"& '{gate}' @args\nexit $LASTEXITCODE\n".encode())
            publish(bin_dir / "agent-index.cmd", (
                '@echo off\n'
                'where pwsh >nul 2>&1\n'
                'if %ERRORLEVEL%==0 (pwsh -NoProfile -ExecutionPolicy Bypass -File '
                '"%~dp0agent-index.ps1" %*) else (powershell -NoProfile -ExecutionPolicy '
                'Bypass -File "%~dp0agent-index.ps1" %*)\nexit /b %ERRORLEVEL%\n'
            ).encode())
        else:
            gate = shlex.quote(str(payload / "scripts/runtime-gate.sh"))
            publish(bin_dir / "agent-index",
                    f'#!/usr/bin/env bash\nexec bash {gate} "$@"\n'.encode(),
                    executable=True)


if __name__ == "__main__":
    try:
        stamp(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    except (OSError, ValueError, KeyError) as error:
        print(f"[agent-index] session stamp failed: {error}", file=sys.stderr)
        sys.exit(1)
