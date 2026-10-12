#!/usr/bin/env python3
"""Publish only session metadata/binstubs under a no-follow admission lock."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys

sys.path.insert(0, str(Path(__file__).parent))
sys.dont_write_bytecode = True
from stamp_paths import Directory, lock  # noqa: E402
from stamp_repair import destinations, installed_launcher, repairable  # noqa: E402


def repair_known(directory: Directory, name: str, expected: tuple[bytes, ...],
                 *, executable: bool = False) -> None:
    if os.name == "nt":
        return
    try:
        fd = directory.open(name, repair=True)
    except FileNotFoundError:
        return
    with os.fdopen(fd, "rb") as stream:
        if not repairable(os.fstat(stream.fileno())):
            return
        data = stream.read()
    if data not in expected:
        raise ValueError(f"cannot repair untrusted stamp content: {name}")
    directory.publish(name, data, executable=executable, repair=True)


def publish(path: Path, data: bytes, *, executable: bool = False) -> None:
    with Directory(path.parent, repair_paths=destinations(path.parent)) as directory:
        directory.publish(path.name, data, executable=executable, repair=True)


def prepare_lock(root: Path, *, inherited: bool = False) -> None:
    with Directory(root, repair_paths=destinations(root)) as directory:
        fd = directory.open(".publication.lock", create=True, repair=True)
        try:
            if inherited:
                info, held = os.fstat(fd), os.fstat(9)
                if (info.st_dev, info.st_ino) != (held.st_dev, held.st_ino):
                    raise ValueError("installer publication lock identity changed")
            if os.name != "nt" and repairable(os.fstat(fd)):
                directory.verify()
                os.fchmod(fd, 0o600)
                print("[agent-index] repaired group-writable publication lock", file=sys.stderr)
        finally:
            os.close(fd)


def stamp(root: Path, payload: Path, mode: str) -> None:
    paths = destinations(root) if mode == "legacy" else frozenset()
    with Directory(root, repair_paths=paths) as directory:
        fd = directory.open(".publication.lock", create=True, repair=mode == "legacy")
        try:
            if lock(fd):
                if os.name != "nt" and repairable(os.fstat(fd)):
                    directory.verify()
                    os.fchmod(fd, 0o600)
                    print("[agent-index] repaired group-writable publication lock", file=sys.stderr)
                _stamp(directory, payload, mode)
        finally:
            os.close(fd)


def _stamp(root: Directory, payload: Path, mode: str) -> None:
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
    # Writable supersession state cannot be trusted or guessed during migration.
    root.read("current-version")
    if mode == "legacy":
        repair_known(root, "stamped-version", ((version + "\n").encode(),))
        repair_known(root, "payload-dir", ((str(payload) + "\n").encode(),))
    for marker in ("current-version", "stamped-version"):
        data = root.read(marker)
        if data is not None and comparator._version_key(
            data.decode("utf-8").strip()
        ) > comparator._version_key(version):
            return
    # A deployed launcher's snapshot pointer is not session metadata to replace.
    deployed = root.read("current-version") is not None
    if (mode == "legacy" and os.name != "nt"
            and (not deployed or (Path.home() / ".local/bin").exists())):
        gate = shlex.quote(str(payload / "scripts/runtime-gate.sh"))
        launcher = f'#!/usr/bin/env bash\nexec bash {gate} "$@"\n'.encode()
        home = Path.home()
        with Directory(home / ".local/bin", repair_paths=destinations(root.path)) as directory:
            repair_known(directory, "agent-index", (launcher, installed_launcher()), executable=True)
    if deployed:
        return
    root.publish("payload-dir", (str(payload) + "\n").encode())
    root.publish("stamped-version", (version + "\n").encode())
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
        if sys.argv[1] == "publish":
            publish(Path(sys.argv[2]), sys.stdin.buffer.read(), executable=sys.argv[3] == "executable")
        elif sys.argv[1] in ("prepare-lock", "verify-lock"):
            prepare_lock(Path(sys.argv[2]), inherited=sys.argv[1] == "verify-lock")
        else:
            stamp(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    except (OSError, ValueError, KeyError) as error:
        print(f"[agent-index] session stamp failed: {error}", file=sys.stderr)
        sys.exit(1)
