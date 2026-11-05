"""Read a source target using the installed owner's declared profile policy."""
from __future__ import annotations

import ast
import dataclasses
import json
import os
from collections.abc import Mapping
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import TYPE_CHECKING

from agent_procutil import no_window_kwargs

from .installation_probe import strict_json_object

if TYPE_CHECKING:
    from .update_runtime_source import PreparedRuntimeSource

TARGET_SCHEMA = "copilot-extensions.runtime-source-target"
TARGET_ENV = "COPILOT_EXTENSIONS_RUNTIME_SOURCE_TARGET"
WHEEL_SELECTION_ENV = "COPILOT_EXTENSIONS_NATIVE_INSTALL_SOURCE_SELECTION"
WHEEL_BASE_ENV = "COPILOT_EXTENSIONS_NATIVE_WHEEL_BASE_PYTHON"
WHEEL_PROVIDER_SCHEMA = "copilot-extensions.runtime-wheel-source-provider"
WHEEL_PROVIDER_FILE = "runtime-wheel-source.json"
PROVIDER_SCHEMA = "copilot-extensions.runtime-source-target-provider"
PROVIDER_FILE = "runtime-source-target.json"
TARGET_CONTRACT = 1


def _provider(payload: Path) -> dict | None:
    path = payload / PROVIDER_FILE
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Source target declaration must be an ordinary file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_json_object)
    except FileNotFoundError:
        return None
    if (
        not isinstance(value, dict) or set(value) != {"schema", "version", "planner"}
        or value["schema"] != PROVIDER_SCHEMA or type(value["version"]) is not int
        or value["version"] != TARGET_CONTRACT or not isinstance(value["planner"], str)
        or re.fullmatch(r"scripts/[a-z][a-z0-9_]*\.py", value["planner"]) is None
    ):
        raise ValueError("Unsupported source target declaration")
    planner = payload / value["planner"]
    if (
        (payload / "scripts").is_symlink() or planner.is_symlink()
        or not planner.is_file() or not planner.resolve().is_relative_to(payload.resolve())
        or planner.resolve().parent != payload.resolve() / "scripts"
    ):
        raise ValueError("Source target planner must be an owned ordinary script")
    return value


@dataclasses.dataclass(frozen=True)
class SourceRuntimeTarget:
    document: str

    @property
    def version(self) -> str:
        return json.loads(self.document)["runtime_version"]

    @property
    def profile(self) -> str:
        return json.loads(self.document)["profile"]


@dataclasses.dataclass(frozen=True)
class SourceWheelTarget:
    document: str
    environment: str = "{}"
    recovery: bool = False

    @property
    def source_descriptor(self) -> dict[str, str]:
        return json.loads(self.document)["source_descriptor"]

    @property
    def version(self) -> str:
        return json.loads(self.document)["runtime_version"]

    @property
    def payload_hash(self) -> str:
        return json.loads(self.document)["payload_hash"]


def _ordinary(path: Path, *, directory: bool = False) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return bool(
        (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        and not stat.S_ISLNK(info.st_mode)
        and not getattr(info, "st_file_attributes", 0) & 0x400
    )


def _wheel_provider(payload: Path) -> dict | None:
    path = payload / WHEEL_PROVIDER_FILE
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not _ordinary(path) or info.st_size > 16384:
        raise ValueError("Wheel source support declaration must be a bounded ordinary file")
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_json_object)
    if (
        not isinstance(value, dict) or value.get("schema") != WHEEL_PROVIDER_SCHEMA
        or type(value.get("version")) is not int or value["version"] not in (2, 3)
        or value.get("selection_env") != WHEEL_SELECTION_ENV
        or set(value) != {"schema", "version", "selection_env"} | (
            {"recovery"} if value["version"] == 3 else set()
        )
        or value["version"] == 3 and (
            not isinstance(value["recovery"], dict)
            or type(value["recovery"].get("version")) is not int
            or value["recovery"] != {
                "schema": "copilot-extensions.worktrees-recovery", "version": 1, "platform": "windows",
            }
        )
    ):
        raise ValueError("Unsupported wheel source support declaration")
    scripts = payload / "scripts"
    installer = scripts / "runtime_install.py"
    if not _ordinary(scripts, directory=True) or not _ordinary(installer):
        raise ValueError("Wheel source support requires its owned runtime installer")
    if value["version"] == 3 and not _ordinary(scripts / "runtime_install_recovery.py"):
        raise ValueError("Guarded wheel source support requires its owned recovery lifecycle")
    return value


def _wheel_target(source: PreparedRuntimeSource, name: str, owner: Path, environment: Mapping[str, str]):
    if name != "agent-worktrees" or source.install_source is None:
        raise ValueError("Prepared wheel selection requires the Worktrees owner")
    declared = _wheel_provider(owner)
    if declared is None or _wheel_provider(source.payload) != declared:
        raise ValueError("Prepared wheel selection lacks matching installed and source support")
    scripts = owner / "scripts"
    validator = scripts / "install_source.py"
    if (
        not _ordinary(scripts, directory=True) or not _ordinary(validator)
        or validator.resolve().parent != scripts.resolve()
    ):
        raise ValueError("Installed wheel selection validator is unavailable")
    result = subprocess.run(
        [
            sys.executable, "-I", "-B", str(validator),
            "--install-source", str(source.install_source), "--component", name,
            "--payload", str(source.payload), "--native-inputs-with-target",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, env=dict(environment), timeout=60, check=False,
        **no_window_kwargs(),
    )
    if result.returncode:
        raise ValueError(f"Installed wheel selection failed: {result.stderr.strip() or result.returncode}")
    packet = json.loads(result.stdout, object_pairs_hook=strict_json_object)
    if (
        not isinstance(packet, dict) or set(packet) != {"selection", "environment"}
        or not isinstance(packet["environment"], dict) or not packet["environment"]
        or any(not isinstance(key, str) or not isinstance(value, str)
               for key, value in packet["environment"].items())
    ):
        raise ValueError("Installed wheel selection has an invalid target environment")
    value = packet["selection"]
    descriptor = value.get("source_descriptor") if isinstance(value, dict) else None
    local = value.get("local_packages") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != {"identity", "source_descriptor", "runtime_version", "payload_hash", "extras", "local_packages"}
        or json.dumps(value["identity"], sort_keys=True) != source.identity
        or not isinstance(value["runtime_version"], str)
        or re.fullmatch(re.escape(source.version) + r"\.profile\.[0-9a-f]{16}", value["runtime_version"]) is None
        or not isinstance(value["payload_hash"], str)
        or re.fullmatch(r"[0-9a-f]{64}", value["payload_hash"]) is None
        or value["extras"] != []
        or not isinstance(local, list) or "agent-worktrees" not in local
        or any(not isinstance(item, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item) is None for item in local)
        or local != sorted(set(local))
        or not isinstance(descriptor, dict) or set(descriptor) != {"kind", "canonical", "ref"}
        or any(not isinstance(item, str) or any(char in item for char in "\r\n\0")
               for item in descriptor.values())
        or not descriptor["kind"] or not descriptor["canonical"]
    ):
        raise ValueError("Installed wheel selection differs from the prepared source")
    return SourceWheelTarget(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False),
        json.dumps(packet["environment"], sort_keys=True, separators=(",", ":"), allow_nan=False),
        recovery=declared["version"] == 3,
    )


def preflight_wheel_target(
    source: PreparedRuntimeSource, target: SourceWheelTarget, owner: Path, root: Path,
    environment: Mapping[str, str], platform_name: str,
) -> str:
    """Select and check the native interpreter before lifecycle reservation."""
    if target.recovery and platform_name != "windows":
        raise ValueError("Guarded recovery is not available on this native platform")

    def matching_script(name: str) -> Path:
        installed, prepared = owner / "scripts" / name, source.payload / "scripts" / name
        if (
            not _ordinary(installed) or not _ordinary(prepared)
            or installed.stat().st_size > 65536 or prepared.stat().st_size > 65536
            or installed.read_bytes().replace(b"\r\n", b"\n") != prepared.read_bytes().replace(b"\r\n", b"\n")
        ):
            raise ValueError("Prepared wheel interpreter support differs from the installed owner")
        return installed

    clean = {key: value for key, value in environment.items()
             if key.upper() not in {"PYTHONPATH", "PYTHONHOME"}}
    base = Path(getattr(sys, "_base_executable", None) or sys.executable)
    if platform_name == "windows":
        selector = matching_script("runtime-python.ps1")
        shell = shutil.which("pwsh", path=clean.get("PATH")) or shutil.which("powershell", path=clean.get("PATH"))
        if shell is None:
            raise ValueError("Prepared wheel target needs the installed PowerShell owner")
        result = subprocess.run(
            [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(selector), "-Probe"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30, check=False, env=clean, **no_window_kwargs(),
        )
        if result.returncode or len(result.stdout) > 16384:
            raise ValueError("Native wheel interpreter selection failed")
        selected = json.loads(result.stdout, object_pairs_hook=strict_json_object)
        if not isinstance(selected, dict) or set(selected) != {"python"} or (
            selected["python"] is not None and not isinstance(selected["python"], str)
        ):
            raise ValueError("Native wheel interpreter selection is invalid")
        if selected["python"] is not None:
            base = Path(selected["python"])
    if not base.is_absolute():
        raise ValueError("Native wheel interpreter must be absolute")
    base = Path(os.path.abspath(base))
    physical = base.resolve(strict=True)
    if not physical.is_file() or physical.is_relative_to(root.resolve()):
        raise ValueError("Native wheel interpreter must be outside the runtime")
    marker = matching_script("installer_marker_environment.py")
    declaration = ast.parse(marker.read_text(encoding="utf-8"))
    queries = [
        node.value.value for node in declaration.body
        if isinstance(node, ast.Assign)
        and any(isinstance(item, ast.Name) and item.id == "MARKER_ENVIRONMENT_QUERY" for item in node.targets)
        and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    ]
    if len(queries) != 1:
        raise ValueError("Installed wheel target probe is invalid")
    result = subprocess.run(
        [str(base), "-I", "-S", "-B", "-c", queries[0]],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30, check=False, env=clean, **no_window_kwargs(),
    )
    if result.returncode or len(result.stdout) > 16384:
        raise ValueError("Native wheel target probe failed")
    observed = json.loads(result.stdout, object_pairs_hook=strict_json_object)
    expected = json.loads(target.environment, object_pairs_hook=strict_json_object)
    if not isinstance(observed, dict) or not expected or observed != expected:
        raise ValueError("Native wheel target differs from the selected interpreter")
    return str(base)


def plan_target(
    source: PreparedRuntimeSource, name: str, owner: Path, root: Path,
    environment: Mapping[str, str],
) -> SourceRuntimeTarget | SourceWheelTarget | None:
    if source.install_source is not None:
        return _wheel_target(source, name, owner, environment)
    candidate, installed = _provider(source.payload), _provider(owner)
    if candidate is None and installed is None and name != "agent-containers":
        return None
    if candidate is None or installed is None or candidate != installed:
        raise ValueError(
            "Source target planning is unsupported by the installed owner or prepared payload; "
            "upgrade their tooling through the existing setup flow first"
        )
    result = subprocess.run(
        [
            sys.executable, "-I", "-B", str(owner / installed["planner"]),
            "--plan-source", "--root", str(root), "--payload", str(source.payload),
            "--expected-source-version", source.version,
            "--expected-source-identity", source.identity,
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, env=dict(environment), timeout=30, check=False,
        **no_window_kwargs(),
    )
    if result.returncode:
        raise ValueError(
            f"Installed source target planning failed: {result.stderr.strip() or result.returncode}"
        )
    value = json.loads(result.stdout, object_pairs_hook=strict_json_object)
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema", "version", "plugin", "runtime_root", "source", "profile", "runtime_version",
        }
        or value["schema"] != TARGET_SCHEMA or type(value["version"]) is not int
        or value["version"] != TARGET_CONTRACT or value["plugin"] != name
        or not isinstance(value["runtime_root"], str)
        or not Path(value["runtime_root"]).is_absolute()
        or Path(value["runtime_root"]).resolve() != root.resolve()
        or json.dumps(value["source"], sort_keys=True) != source.identity
        or not isinstance(value["profile"], str)
        or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value["profile"]) is None
        or not isinstance(value["runtime_version"], str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", value["runtime_version"]) is None
    ):
        raise ValueError("Installed source target planner returned a different or invalid selection")
    return SourceRuntimeTarget(json.dumps(value, sort_keys=True))


def check_target(
    expected: SourceRuntimeTarget | SourceWheelTarget | None, source: PreparedRuntimeSource, name: str,
    owner: Path, root: Path, environment: Mapping[str, str],
) -> None:
    if plan_target(source, name, owner, root, environment) != expected:
        raise ValueError("Source target changed after runtime selection")


def profile_hash(slot: Path, target: SourceRuntimeTarget, source_fields: dict) -> str | None:
    """Bind the profile receipt to the canonical completion probe's expected hash."""
    path = slot / "runtime-profile.json"
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Selected source profile receipt must be an ordinary file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_json_object)
    except FileNotFoundError:
        return None
    if (
        not isinstance(value, dict) or value.get("profile") != target.profile
        or json.dumps(value.get("runtimeSource"), sort_keys=True) != json.dumps(source_fields, sort_keys=True)
        or value.get("packageVersion") != source_fields["package_version"]
        or not isinstance(value.get("payloadHash"), str)
        or re.fullmatch(r"[0-9a-f]{64}", value["payloadHash"]) is None
    ):
        return None
    return value["payloadHash"]
