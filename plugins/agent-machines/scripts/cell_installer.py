"""Bound cell transactions and bind ordinary release payloads before lock waits."""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from versioned_runtime import fingerprint_source  # noqa: E402
from versioned_runtime_source import payload_package_version  # noqa: E402

OWNER_MARKER = ".agent-machines-snapshot-publish-owner"
HASH_ENV = "AGENT_MACHINES_EXPECTED_PAYLOAD_HASH"


def _copied_files(payload: Path) -> list[Path]:
    files: list[Path] = []

    def require_plain(path: Path, *, directory: bool = False) -> None:
        info = path.lstat()
        ordinary = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not ordinary or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise RuntimeError("Copied payload inputs must be ordinary files and directories")

    def scan_error(error: OSError) -> None:
        raise error

    require_plain(payload, directory=True)
    for directory, directories, names in os.walk(payload, onerror=scan_error, followlinks=False):
        for name in directories:
            require_plain(Path(directory) / name, directory=True)
        for name in names:
            path = Path(directory) / name
            require_plain(path)
            if path.parent == payload and name in (OWNER_MARKER, "snapshot-provenance.json"):
                continue
            files.append(path)
    return files


def payload_identity(payload: Path, version: str) -> str:
    """Hash all copied bytes, including caches, excluding only root publication sidecars."""
    files = _copied_files(payload)
    project = (payload / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', project, re.MULTILINE)
    if (
        match is None or match[1] != version
        or payload_package_version(payload, "agent-machines") != version
    ):
        raise RuntimeError("Payload package version changed after selection; refusing publication")
    return fingerprint_source(files)


def watchdog(request: dict) -> int:
    """Own the whole installer tree through its deadline and confirmed retirement."""
    if set(request) != {"argv", "cwd", "payload", "timeout"}:
        raise ValueError("Invalid cell installer watchdog request")
    timeout = request["timeout"]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Cell installer deadline must be finite and positive")
    deadline = time.monotonic() + timeout
    argv = request["argv"]
    if not isinstance(argv, list) or not argv or any(
        not isinstance(value, str) or "\0" in value for value in argv
    ):
        raise ValueError("Cell installer command must contain NUL-free arguments")
    payload = Path(request["payload"])
    version = payload_package_version(payload, "agent-machines")
    identity = payload_identity(payload, version)
    environment = dict(os.environ)
    if environment.get(HASH_ENV) not in (None, identity):
        raise RuntimeError("Payload changed across the installation transaction")
    environment[HASH_ENV] = identity
    environment["COPILOT_EXTENSIONS_HUMAN_UTF8"] = "1"
    archive = Path(__file__).with_name("installation-context") / "daemon_instance_runtime.pyz"
    sys.path.insert(0, str(archive))
    from agent_procutil.owned_process import run_caller_owned

    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(argv, timeout)
        result = run_caller_owned(
            argv, timeout=remaining, max_stdout_bytes=16 * 1024 * 1024,
            max_stderr_bytes=16 * 1024 * 1024, cwd=request["cwd"], env=environment,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[agent-machines] WATCHDOG: cell installer exceeded {timeout:g}s; "
            "owned tree retirement confirmed.",
            file=sys.stderr,
        )
        return 124
    sys.stdout.write(result.stdout)
    sys.stdout.flush()
    sys.stderr.write(result.stderr)
    sys.stderr.flush()
    return result.returncode


def main(request: dict | None = None) -> int:
    if request is not None:
        lifetime = request.pop("lifetime")
        archive = Path(__file__).with_name("installation-context") / "daemon_instance_runtime.pyz"
        sys.path.insert(0, str(archive))
        from agent_procutil._shell_caller_owned import serve

        return serve(lambda: watchdog(request), **lifetime)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    identity = commands.add_parser("identity")
    identity.add_argument("--payload", type=Path, required=True)
    identity.add_argument("--version", required=True)
    args = parser.parse_args()
    print(payload_identity(args.payload, args.version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
