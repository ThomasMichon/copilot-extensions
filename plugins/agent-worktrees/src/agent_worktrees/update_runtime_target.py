"""Read a source target using the installed owner's declared profile policy."""
from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from pathlib import Path
import re
import subprocess
import sys
from typing import TYPE_CHECKING

from agent_procutil import no_window_kwargs

from .installation_probe import strict_json_object

if TYPE_CHECKING:
    from .update_runtime_source import PreparedRuntimeSource

TARGET_SCHEMA = "copilot-extensions.runtime-source-target"
TARGET_ENV = "COPILOT_EXTENSIONS_RUNTIME_SOURCE_TARGET"
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


def plan_target(
    source: PreparedRuntimeSource, name: str, owner: Path, root: Path,
    environment: Mapping[str, str],
) -> SourceRuntimeTarget | None:
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
    expected: SourceRuntimeTarget | None, source: PreparedRuntimeSource, name: str,
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
