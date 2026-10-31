"""Prepare and verify source payloads with the running Manager's trusted tools."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from agent_procutil import no_window_kwargs

from .engine_environment import _engine_environment
from .runtime_source import ResolvedSource
from .self_install import _runtime_installer, running_payload_dir


def preparer() -> Path:
    payload = running_payload_dir()
    packaged = payload / "scripts/runtime-source-tools/prepare_runtime_source.py"
    if packaged.is_file():
        return packaged
    # Source development uses this checkout's canonical tools, never fetched code.
    if (payload.parent / ".git").exists():
        canonical = payload.parent / "tools/prepare_runtime_source.py"
        if canonical.is_file():
            return canonical
    raise ValueError("Manager source preparation assets are unavailable; repair the Manager payload")


def prepare(tool: Path, resolved: ResolvedSource, name: str, output: Path) -> dict:
    command = [
        sys.executable, "-I", "-B", "-X", "utf8", str(tool),
        "--source", str(resolved.checkout), "--commit", resolved.commit,
        "--branch", resolved.selection.branch, "--plugin", name, "--output", str(output),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, env=_engine_environment(),
            timeout=600, check=False, **no_window_kwargs(),
        )
    except subprocess.SubprocessError as error:
        raise ValueError(f"Runtime source preparation for {name} failed: {error}") from error
    if result.returncode:
        raise ValueError(f"Runtime source preparation for {name} failed: {result.stderr.strip()}")
    record = json.loads(result.stdout)
    if (
        not isinstance(record, dict) or not isinstance(record.get("payload"), str)
        or not isinstance(record.get("identity"), dict)
    ):
        raise ValueError(f"Runtime source preparer returned an invalid receipt for {name}")
    payload = Path(record["payload"])
    identity = record["identity"]
    if (
        not payload.is_absolute() or payload.parent != output / name
        or identity.get("plugin") != name
        or not isinstance(identity.get("package_version"), str)
        or identity.get("source_commit") != resolved.commit
        or identity.get("source_tree") != resolved.tree
    ):
        raise ValueError(f"Runtime source preparer returned a different selection for {name}")
    source = _runtime_installer().runtime_source
    observed = source.read_source_identity(payload, identity.get("package_version"), plugin=name)
    if observed is None or observed.document() != identity or payload.name != observed.runtime_version:
        raise ValueError(f"Runtime source preparer returned conflicting bytes for {name}")
    return {
        "payload": str(payload),
        "identity": {"runtime_source": identity, "runtime_version": observed.runtime_version},
    }
