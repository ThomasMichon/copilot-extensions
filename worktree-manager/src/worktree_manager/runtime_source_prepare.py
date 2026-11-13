"""Prepare and verify source payloads with the running Manager's trusted tools."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from typing import TYPE_CHECKING

import agent_procutil

from .runtime_application_inputs import prepare_application_inputs as prepare_application_inputs

if TYPE_CHECKING:
    from .runtime_source import ResolvedSource


def _runtime_installer():
    from .self_install import _runtime_installer as selected
    return selected()


def _runtime_scripts():
    from .self_install import _runtime_scripts as selected
    return selected()


def running_payload_dir():
    from .self_install import running_payload_dir as selected
    return selected()


def preparer() -> Path:
    directory = _runtime_scripts() / "runtime-source-tools"
    for relative in ("tools/prepare_runtime_source.py", "prepare_runtime_source.py"):
        packaged = directory / relative
        if packaged.is_file():
            return packaged
    # Source development uses this checkout's canonical tools, never fetched code.
    payload = running_payload_dir()
    if (payload.parent / ".git").exists():
        canonical = payload.parent / "tools/prepare_runtime_source.py"
        if canonical.is_file():
            return canonical
    raise ValueError("Manager source preparation assets are unavailable; repair the Manager payload")


def prepare(tool: Path, resolved: ResolvedSource, name: str, output: Path) -> dict:
    from agent_procutil import no_window_kwargs
    from .engine_environment import _engine_environment

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
        not payload.is_absolute() or not output.is_absolute() or ".." in payload.parts
        or identity.get("plugin") != name
        or not isinstance(identity.get("package_version"), str)
        or identity.get("source_commit") != resolved.commit
        or identity.get("source_tree") != resolved.tree
    ):
        raise ValueError(f"Runtime source preparer returned a different selection for {name}")
    installer = _runtime_installer()
    native = installer.engine._native_retained_path
    if Path(native(payload.parent)) != Path(native(output / name)):
        raise ValueError(f"Runtime source preparer returned a different selection for {name}")
    observed = installer.runtime_source.read_source_identity(
        Path(native(payload)), identity.get("package_version"), plugin=name,
    )
    if observed is None or observed.document() != identity or payload.name != observed.runtime_version:
        raise ValueError(f"Runtime source preparer returned conflicting bytes for {name}")
    return {
        "payload": str(payload),
        "identity": {"runtime_source": identity, "runtime_version": observed.runtime_version},
    }


def prepare_applications(
    tool: Path, resolved: ResolvedSource, names: tuple[str, ...], output: Path, *,
    deadline: float, environment: dict[str, str] | None = None,
    extras: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Path]:
    """Run the packaged canonical portfolio provider under existing process custody."""
    from .engine_environment import _engine_environment

    if (
        isinstance(deadline, bool) or not isinstance(deadline, (int, float))
        or not math.isfinite(deadline) or deadline <= time.monotonic()
    ):
        raise ValueError("Application preparation requires a live finite deadline")
    if (
        not names or len(names) > 64
        or any(not isinstance(name, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None
               for name in names)
        or len(set(names)) != len(names)
        or not output.is_absolute()
        or resolved.main_head is None
    ):
        raise ValueError("Application preparation requires a bounded portfolio, absolute output and Main seed")
    if extras is not None and (
        not isinstance(extras, dict) or set(extras) - set(names)
        or any(
            not isinstance(values, tuple) or len(values) > 64
            or any(not isinstance(value, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) is None
                   for value in values)
            or len(set(values)) != len(values)
            for values in extras.values()
        )
    ):
        raise ValueError("Application extras require bounded unique names for selected components")
    provider = tool.parent / "local_install_source.py"
    if not provider.is_file():
        raise ValueError("Manager complete-application preparation assets are unavailable; update Manager")
    installer = _runtime_installer()
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("uv is required for complete-application preparation")
    uv = installer.engine._select_uv_executable(uv)
    command = [
        sys.executable, "-I", "-B", "-X", "utf8", str(provider),
        "--source-checkout", str(resolved.checkout), "--source-commit", resolved.commit,
        "--main-head", resolved.main_head, "--branch", resolved.selection.branch,
        "--output", str(output), "--prepare-applications", "--uv", uv,
        "--deadline", str(deadline),
    ]
    for name in names:
        command.extend(["--project", name])
    if extras is not None:
        command.extend(["--application-extras", json.dumps(extras, sort_keys=True)])
    try:
        result = installer.engine.run_owned(
            command, cwd=provider.parent, env=_engine_environment(environment),
            timeout=deadline - time.monotonic(), deadline=deadline,
            procutil=agent_procutil, construction_fd=None,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise ValueError(f"Complete-application preparation failed: {error}") from error
    if (
        not isinstance(result.stdout, str) or len(result.stdout.encode("utf-8")) > 4 << 20
        or time.monotonic() >= deadline
    ):
        raise ValueError("Application preparation returned an over-budget receipt")
    record = json.loads(result.stdout)
    if not isinstance(record, dict) or set(record) != set(names):
        raise ValueError("Application preparer returned a different portfolio")
    snapshots = {}
    preparation_roots = set()
    native = installer.engine._native_retained_path
    for name, value in record.items():
        if not isinstance(value, str) or not Path(value).is_absolute() or ".." in Path(value).parts:
            raise ValueError("Application preparer returned an invalid snapshot")
        snapshot = Path(native(Path(value)))
        if len(snapshot.parents) < 3:
            raise ValueError("Application preparer returned a different snapshot")
        preparation_root = snapshot.parents[2]
        if (
            preparation_root.parent != Path(native(output)).parent
            or snapshot != preparation_root / "profiles" / name / "snapshot"
        ):
            raise ValueError("Application preparer returned a different snapshot")
        preparation_roots.add(preparation_root)
        snapshots[name] = snapshot
    if len(preparation_roots) != 1:
        raise ValueError("Application preparer returned snapshots from different preparations")
    return snapshots
