"""Source-tree preparation contract for the explicit process-only adapter."""

from __future__ import annotations

import hashlib
import importlib
import os
from pathlib import Path, PurePosixPath
import re
import sys
import time
from typing import Any, Callable

if __package__:
    from .native_qualification_contract import Boundary, QualificationError, STREAM_BYTES, decode_object
else:
    from native_qualification_contract import Boundary, QualificationError, STREAM_BYTES, decode_object

SCHEMA = "native-qualification.process-preparation"
LAUNCHER = "native-process-qualification.ps1"
TOOLS = (
    "native_process_sources.py", "native_process_stage.py", "native_process_qualification.py",
    "native_process_artifacts.py",
    "native_qualification_receiver.py", "native_qualification_contract.py",
    "plugin_test_containment.py", "plugin_test_environment.py",
    "plugin_test_containment_posix.py", "plugin_test_containment_observation.py",
    "native_test_paths.py", LAUNCHER,
)
SDK_HELPERS = (
    "sdk_visibility.py", "sdk_visibility_sources.py", "sdk_visibility_cases.py",
    "sdk_desktop_observer.py", "sdk_native_win32.py", "sdk_desktop_win32.py",
)


def digest(path: Path, boundary: Boundary, clock: Callable[[], float]) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            boundary.require(clock)
            chunk = source.read(65536)
            if not chunk:
                break
            value.update(chunk)
    boundary.require(clock)
    return value.hexdigest()


def ordinary(path: Path) -> None:
    if not path.is_absolute() or path.resolve() != path or path.is_symlink() or not path.is_file():
        raise QualificationError("Process preparation contains a missing or redirected source.")


def load(
    launcher: Path, expected: str, boundary: Boundary,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    """Verify every declared byte before importing or launching the candidate."""
    ordinary(launcher)
    if launcher.name != LAUNCHER or launcher.parent.name != "tools":
        raise QualificationError("Process adapter requires its canonical source-tree launcher.")
    root = launcher.parent.parent
    manifest = launcher.with_name("prepared-manifest.json")
    ordinary(manifest)
    boundary.require(clock)
    with manifest.open("rb") as source:
        data = source.read(STREAM_BYTES + 1)
    if len(data) > STREAM_BYTES or hashlib.sha256(data).hexdigest() != expected:
        raise QualificationError("Process preparation manifest changed or exceeded its bound.")
    value = decode_object(data)
    if (
        set(value) != {"schema", "version", "files", "nonce", "case_packets"}
        or value["schema"] != SCHEMA or type(value["version"]) is not int or value["version"] != 1
        or not isinstance(value["nonce"], str) or re.fullmatch(r"[a-f0-9]{32}", value["nonce"]) is None
        or not isinstance(value["files"], list) or not value["files"]
    ):
        raise QualificationError("Process preparation has an invalid explicit schema.")
    names: set[str] = set()
    rows = []
    for row in value["files"]:
        boundary.require(clock)
        if not isinstance(row, dict) or set(row) != {"name", "bytes", "sha256"}:
            raise QualificationError("Process preparation has an invalid source row.")
        name = row["name"]
        if (
            not isinstance(name, str) or not name or any(c in name for c in ("\\", ":", "\0"))
            or PurePosixPath(name).is_absolute() or any(p in {"", ".", ".."} for p in name.split("/"))
            or name.casefold() in names or name.casefold() == "tools/prepared-manifest.json"
            or type(row["bytes"]) is not int or row["bytes"] < 0
            or not isinstance(row["sha256"], str) or re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) is None
        ):
            raise QualificationError("Process preparation has an unsafe or duplicate source.")
        names.add(name.casefold())
        path = root / name
        ordinary(path)
        if path.stat().st_size != row["bytes"] or digest(path, boundary, clock) != row["sha256"]:
            raise QualificationError("Process preparation source bytes changed.")
        rows.append({"path": str(path), "bytes": row["bytes"], "sha256": row["sha256"]})
    required = {f"tools/{name}" for name in TOOLS}
    required.update(f"libs/agent-procutil/tests/{name}" for name in SDK_HELPERS)
    package = root / "libs/agent-procutil/src/agent_procutil"
    required.update(path.relative_to(root).as_posix() for path in package.glob("*.py"))
    if "libs/agent-procutil/src/agent_procutil/__init__.py" not in required:
        raise QualificationError("Process preparation is missing its original owner package.")
    if not required <= names or Path(__file__).resolve() != launcher.with_name("native_process_sources.py"):
        raise QualificationError("Process adapter owner/source closure is incomplete or foreign.")
    actual: set[str] = set()
    pending = [root]
    while pending:
        boundary.require(clock)
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                boundary.require(clock)
                path = Path(entry.path)
                if entry.is_symlink() or path.resolve() != path:
                    raise QualificationError("Process preparation contains an indirect tree entry.")
                if entry.is_dir(follow_symlinks=False):
                    if path.name.casefold() == "__pycache__":
                        raise QualificationError("Prepared process source must not load cached bytecode.")
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    if path.suffix.casefold() in {".pyc", ".pyo"}:
                        raise QualificationError("Prepared process source must not load cached bytecode.")
                    actual.add(path.relative_to(root).as_posix().casefold())
                else:
                    raise QualificationError("Process preparation contains an unsupported tree entry.")
    if actual != names | {"tools/prepared-manifest.json"}:
        raise QualificationError("Process preparation has undeclared source-tree bytes.")
    # Bind loaded local modules too: hashing another copy cannot qualify execution.
    for module in tuple(sys.modules.values()):
        origin = getattr(module, "__file__", None)
        if not origin:
            continue
        path = Path(origin)
        if path.name in TOOLS and path.resolve() != launcher.with_name(path.name):
            raise QualificationError("Loaded process adapter belongs to another preparation.")
        if str(getattr(module, "__name__", "")).startswith("agent_procutil"):
            ordinary(path)
            if path not in {Path(row["path"]) for row in rows}:
                raise QualificationError("Loaded process owner package is outside its prepared closure.")
    boundary.require(clock)
    return root, value, rows


def sdk(
    root: Path, rows: list[dict[str, Any]], packets: object,
) -> tuple[Any, list[dict[str, Any]]]:
    """Load only the hash-verified fixture helpers, preserving their own admission."""
    helpers = root / "libs/agent-procutil/tests"
    previous = sys.path.copy()
    sys.path.insert(0, str(helpers))
    try:
        for name in ("sdk_visibility_sources", "sdk_visibility_cases", "sdk_visibility"):
            loaded = sys.modules.get(name)
            if loaded is not None and Path(loaded.__file__).resolve() != helpers / f"{name}.py":
                raise QualificationError("Loaded SDK fixture belongs to another preparation.")
        sources = importlib.import_module("sdk_visibility_sources")
        cases = importlib.import_module("sdk_visibility_cases")
        runner = importlib.import_module("sdk_visibility")
    finally:
        sys.path[:] = previous
    required = {str(root / name) for name in sources.SDK_FILES}
    selected = [row for row in rows if row["path"] in required]
    sources.require_sdk_sources(root, selected, runner=Path(runner.__file__))
    cases.validate_case_packets(root, packets)
    return runner, selected
