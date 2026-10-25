"""Pinned prepared inputs, separate from the installed runtime's command owner."""
from __future__ import annotations

import ast
import dataclasses
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from agent_procutil import no_window_kwargs

from .installation_probe import strict_json_object

if TYPE_CHECKING:
    from .update_runtime_plan import RuntimeUpdateStep

PIN_ENV = "COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN"
SOURCES_SCHEMA = "copilot-extensions.prepared-runtime-sources"
SOURCES_CONTRACT = 1


def parse_sources(raw: str | None) -> dict[str, PreparedRuntimeSource] | None:
    """Decode a complete queued selection; absence alone means marketplace."""
    if raw is None:
        return None
    packet = json.loads(raw, object_pairs_hook=strict_json_object)
    if (
        not isinstance(packet, dict) or set(packet) != {"schema", "version", "sources"}
        or packet["schema"] != SOURCES_SCHEMA or type(packet["version"]) is not int
        or packet["version"] != SOURCES_CONTRACT
        or not isinstance(packet["sources"], dict) or not packet["sources"]
    ):
        raise ValueError("Unsupported prepared-runtime-sources packet")
    sources: dict[str, PreparedRuntimeSource] = {}
    selection: tuple[str, str] | None = None
    for name, record in packet["sources"].items():
        if (
            not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
            or not isinstance(record, dict) or set(record) != {"payload", "identity"}
            or not isinstance(record["payload"], str) or not record["payload"]
            or not Path(record["payload"]).is_absolute()
        ):
            raise ValueError(f"Invalid prepared-runtime-sources entry: {name}")
        source = PreparedRuntimeSource(Path(record["payload"]), json.dumps(record["identity"]))
        identity = json.loads(source.identity)["runtime_source"]
        commit, tree = identity.get("source_commit"), identity.get("source_tree")
        if (
            identity["plugin"] != name or not source.payload.is_dir()
            or not isinstance(commit, str) or not isinstance(tree, str)
            or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit)
            or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", tree)
        ):
            raise ValueError(f"Prepared runtime selection does not identify {name}")
        if selection is not None and selection != (commit, tree):
            raise ValueError("Prepared runtimes must use one exact source commit and tree")
        selection = commit, tree
        sources[name] = source
    return sources


def check_source_coverage(
    sources: Mapping[str, PreparedRuntimeSource], steps: Sequence[RuntimeUpdateStep],
) -> None:
    eligible = {step.name for step in steps if step.eligible}
    missing, unexpected = eligible - sources.keys(), sources.keys() - eligible
    details = [
        f"{label}: {', '.join(sorted(names))}"
        for label, names in (("missing", missing), ("unexpected", unexpected)) if names
    ]
    if details:
        raise ValueError("Prepared runtime portfolio differs from eligible runtimes; " + "; ".join(details))


@dataclasses.dataclass(frozen=True)
class PreparedRuntimeSource:
    payload: Path
    identity: str

    def __post_init__(self) -> None:
        fields = json.loads(self.identity, object_pairs_hook=strict_json_object)
        if (
            not isinstance(fields, dict)
            or set(fields) != {"runtime_source", "runtime_version"}
            or not isinstance(fields["runtime_source"], dict)
            or not isinstance(fields["runtime_version"], str)
            or not fields["runtime_version"]
            or not isinstance(fields["runtime_source"].get("package_version"), str)
            or not fields["runtime_source"]["package_version"]
            or not isinstance(fields["runtime_source"].get("plugin"), str)
            or not fields["runtime_source"]["plugin"]
        ):
            raise ValueError("Prepared runtime requires a complete source selection")
        object.__setattr__(self, "payload", self.payload.resolve(strict=True))
        object.__setattr__(self, "identity", json.dumps(fields, sort_keys=True))

    @property
    def version(self) -> str:
        return json.loads(self.identity)["runtime_version"]

    @property
    def package_version(self) -> str:
        return json.loads(self.identity)["runtime_source"]["package_version"]

    def validate(
        self, name: str, owner: Path, environment: Mapping[str, str],
    ) -> None:
        """Use the installed owner's validator, never import prepared code."""
        source = self.payload / "scripts" / "versioned_runtime_source.py"
        declaration = ast.parse(source.read_text(encoding="utf-8"))
        if not any(
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "SOURCE_PIN_ENV"
                    for target in node.targets)
            and isinstance(node.value, ast.Constant) and node.value.value == PIN_ENV
            for node in declaration.body
        ):
            raise ValueError("Prepared installer does not support transaction source pinning")
        if "COPILOT_EXTENSIONS_CONTEXT" in environment:
            raise ValueError("Prepared-source updates require an authorized legacy installation")
        validator = owner / "scripts" / "versioned_runtime_source.py"
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(validator),
             "--payload", str(self.payload), "--package-version", self.package_version,
             "--plugin", name, "--expected-runtime-version", self.version,
             "--expected-source-identity", self.identity, "--format", "json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=dict(environment), timeout=30, check=False, **no_window_kwargs(),
        )
        if result.returncode:
            raise ValueError(
                f"Prepared source validation failed: {result.stderr.strip() or result.returncode}"
            )
        if result.stdout.strip() != self.identity:
            raise ValueError("Installed source validator returned a different selection")
        if not (owner / "scripts" / "command_binding.py").is_file():
            raise ValueError("Installed payload has no command lifecycle helper")

    def deployed_version(self, root: Path, owner: Path) -> str | None:
        """Only the selected slot and its complete source receipt can be current."""
        path = root / "deploy-manifest.json"
        try:
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError("Source deployment receipt must be an ordinary file")
            manifest = json.loads(
                path.read_text(encoding="utf-8"), object_pairs_hook=strict_json_object,
            )
        except FileNotFoundError:
            return None
        if not isinstance(manifest, dict):
            raise ValueError("Source deployment receipt must be an object")
        fields = json.loads(self.identity)
        if manifest.get("service") != fields["runtime_source"]["plugin"]:
            raise ValueError("Source deployment receipt names another runtime")
        if any(manifest.get(key) != value for key, value in fields.items()):
            return None
        marker = root / "current-version"
        if marker.is_symlink() or (marker.exists() and not marker.is_file()):
            raise ValueError("Source runtime selector must be an ordinary file")
        try:
            selected = marker.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        slot = root / "versions" / self.version
        if (
            selected != self.version or not slot.is_dir() or slot.is_symlink()
            or not isinstance(manifest.get("venv"), str)
            or Path(manifest["venv"]) != slot
        ):
            return None
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-B", str(owner / "scripts" / "versioned_runtime.py"),
                 "--root", str(root), "--json", "is-complete", self.version],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30, check=False, **no_window_kwargs(),
            )
        except subprocess.SubprocessError as error:
            raise ValueError(f"Source completion probe failed: {error}") from error
        if result.returncode not in (0, 1):
            raise ValueError(
                f"Source completion probe failed: {result.stderr.strip() or result.returncode}"
            )
        try:
            complete = json.loads(result.stdout, object_pairs_hook=strict_json_object)
        except ValueError as error:
            raise ValueError(
                f"Source completion probe failed: {result.stderr.strip() or 'invalid JSON response'}"
            ) from error
        if (
            not isinstance(complete, dict) or complete.get("version") != self.version
            or type(complete.get("complete")) is not bool
            or result.returncode != (0 if complete["complete"] else 1)
        ):
            raise ValueError("Source completion probe returned an invalid result")
        return self.version if complete["complete"] else None

    def lifecycle_argv(
        self, owner: Path, name: str, root: Path, project: Path, argv: tuple[str, ...],
    ) -> list[str]:
        executable = shutil.which(argv[0])
        if executable is None:
            raise ValueError(f"Prepared runtime installer executable not found: {argv[0]}")
        return [
            sys.executable, "-I", "-B", str(owner / "scripts" / "command_binding.py"),
            "--payload-root", str(owner), "--source-payload-root", str(owner),
            "--plugin-id", name, "--legacy-root", str(root),
            "--project-root", str(project), "--run-lifecycle", "--managed-deployment",
            "--", os.path.abspath(executable), *argv[1:],
        ]
