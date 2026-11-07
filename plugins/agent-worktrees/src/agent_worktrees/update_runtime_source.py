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
from .update_runtime_application import PreparedApplication

if TYPE_CHECKING:
    from .update_runtime_plan import RuntimeUpdateStep

PIN_ENV = "COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN"
SOURCES_SCHEMA = "copilot-extensions.prepared-runtime-sources"
SOURCES_CONTRACT = 2


def parse_sources(raw: str | None) -> dict[str, PreparedRuntimeSource] | None:
    """Decode a complete queued selection; absence alone means marketplace."""
    if raw is None:
        return None
    if len(raw.encode("utf-8")) > 4 << 20:
        raise ValueError("Prepared-runtime-sources packet exceeds its document bound")
    packet = json.loads(raw, object_pairs_hook=strict_json_object)
    if (
        not isinstance(packet, dict) or set(packet) != {"schema", "version", "source", "sources"}
        or packet["schema"] != SOURCES_SCHEMA or type(packet["version"]) is not int
        or packet["version"] != SOURCES_CONTRACT
        or not isinstance(packet["sources"], dict) or not packet["sources"]
        or len(packet["sources"]) > 64
        or not isinstance(packet["source"], dict)
        or set(packet["source"]) != {"commit", "tree", "main_seed"}
        or any(not isinstance(value, str)
               or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is None
               for value in packet["source"].values())
    ):
        raise ValueError("Unsupported prepared-runtime-sources packet")
    sources: dict[str, PreparedRuntimeSource] = {}
    selection: str | None = None
    for name, record in packet["sources"].items():
        if (
            not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
            or not isinstance(record, dict) or set(record) != {"snapshot", "profile"}
            or not isinstance(record["snapshot"], str) or not record["snapshot"]
            or not Path(record["snapshot"]).is_absolute()
        ):
            raise ValueError(f"Invalid prepared-runtime-sources entry: {name}")
        application = PreparedApplication(
            Path(record["snapshot"]), name, json.dumps(record["profile"], sort_keys=True),
        )
        source = PreparedRuntimeSource(application.payload, application.identity, application)
        identity = json.loads(source.identity)["runtime_source"]
        commit, tree = identity.get("source_commit"), identity.get("source_tree")
        association = json.loads(application.association)
        if (
            identity["plugin"] != name or not source.payload.is_dir()
            or not isinstance(commit, str) or not isinstance(tree, str)
            or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit)
            or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", tree)
            or packet["source"] != {
                "commit": commit, "tree": tree, "main_seed": association["main_head"],
            }
        ):
            raise ValueError(f"Prepared runtime selection does not identify {name}")
        composition = json.dumps(association.get("composition"), sort_keys=True)
        if selection is not None and selection != composition:
            raise ValueError("Prepared runtimes must use one exact source composition")
        selection = composition
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
    application: PreparedApplication | None = None

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
        if self.application is not None and (
            self.application.payload != self.payload or self.application.identity != self.identity
        ):
            raise ValueError("Prepared source differs from its complete application selection")

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
        """Only the complete selected application receipt establishes currentness."""
        return self.application.deployed_version(root, owner) if self.application is not None else None

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
            *(["--deferred-runtime"] if self.application is not None else []),
            "--", os.path.abspath(executable), *argv[1:],
        ]
