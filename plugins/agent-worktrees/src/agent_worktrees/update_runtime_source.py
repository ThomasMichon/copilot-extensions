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
    from .update_runtime_target import SourceRuntimeTarget

PIN_ENV = "COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN"
SOURCES_SCHEMA = "copilot-extensions.prepared-runtime-sources"
SOURCES_CONTRACT = 2
LEGACY_SOURCES_CONTRACT = 1
PREPARED_INPUTS_CONTRACT = 2


def _existing_source_entry(payload: Path, platform: str) -> dict[str, str] | None:
    """Recognize the already-released native protocol before sidecar declarations."""
    native = payload / "scripts/runtime_install.py"
    if not native.is_file() or native.is_symlink():
        return None
    try:
        tree = ast.parse(native.read_text(encoding="utf-8"))
    except SyntaxError as error:
        raise ValueError("Selected native installer cannot describe source inputs") from error
    if not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and any(isinstance(arg, ast.Constant) and arg.value == "--install-source" for arg in node.args)
        for node in ast.walk(tree)
    ) or not any(
        isinstance(node, ast.ImportFrom) and node.module == "install_source"
        for node in ast.walk(tree)
    ):
        return None
    windows = platform == "windows"
    extension = "ps1" if windows else "sh"
    for relative in (f"scripts/install.{extension}", f"bootstrap.{extension}"):
        script = payload / relative
        if script.is_symlink() or not script.is_file():
            continue
        text = script.read_text(encoding="utf-8")
        if "--install-source" not in text:
            continue
        if windows and relative.startswith("scripts/"):
            if re.search(r"\[string\]\s*\$InstallSource\b", text, re.IGNORECASE) is None:
                continue
            argument = "-InstallSource"
        else:
            argument = "--install-source"
        return {"script": relative, "argument": argument}
    return None


def source_install_entry(payload: Path, platform: str) -> dict[str, str] | None:
    """A package opts into prepared inputs through its native installer declaration."""
    path = payload / "source-install.json"
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Native source installation declaration must be ordinary")
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_json_object)
    except FileNotFoundError:
        return _existing_source_entry(payload, platform)
    if (
        not isinstance(value, dict) or set(value) != {"schema", "version", "windows", "posix"}
        or value["schema"] != "copilot-extensions.native-source-install"
        or type(value["version"]) is not int or value["version"] != 1
    ):
        raise ValueError("Unsupported native source installation declaration")
    for key in ("windows", "posix"):
        row = value[key]
        if (
            not isinstance(row, dict) or set(row) != {"script", "argument"}
            or not isinstance(row["script"], str)
            or not re.fullmatch(r"(?:scripts/)?[a-z][a-z0-9_-]*\.(?:ps1|sh)", row["script"])
            or not row["script"].endswith(".ps1" if key == "windows" else ".sh")
            or not isinstance(row["argument"], str)
            or not re.fullmatch(r"--?[A-Za-z][A-Za-z-]*", row["argument"])
        ):
            raise ValueError("Invalid native source installation entry")
        script = payload / row["script"]
        if (
            script.is_symlink() or (payload / "scripts").is_symlink()
            or not script.is_file() or not script.resolve().is_relative_to(payload.resolve())
        ):
            raise ValueError("Native source installation script is unavailable or indirect")
    return value["windows" if platform == "windows" else "posix"]


def parse_sources(raw: str | None) -> dict[str, PreparedRuntimeSource] | None:
    """Decode a complete queued selection; absence alone means marketplace."""
    if raw is None:
        return None
    if len(raw.encode("utf-8")) > 4 << 20:
        raise ValueError("Prepared-runtime-sources packet exceeds its document bound")
    packet = json.loads(raw, object_pairs_hook=strict_json_object)
    if (
        not isinstance(packet, dict)
        or set(packet) not in (
            {"schema", "version", "sources"},
            {"schema", "version", "source", "sources"},
        )
        or packet["schema"] != SOURCES_SCHEMA or type(packet["version"]) is not int
        or packet["version"] not in (LEGACY_SOURCES_CONTRACT, SOURCES_CONTRACT)
        or not isinstance(packet["sources"], dict) or not packet["sources"]
        or len(packet["sources"]) > 64
    ):
        raise ValueError("Unsupported prepared-runtime-sources packet")
    application_packet = "source" in packet
    if application_packet and (
        packet["version"] != SOURCES_CONTRACT
        or not isinstance(packet["source"], dict)
        or set(packet["source"]) != {"commit", "tree", "main_seed"}
        or any(not isinstance(value, str)
               or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is None
               for value in packet["source"].values())
    ):
        raise ValueError("Unsupported prepared-runtime-sources packet")
    sources: dict[str, PreparedRuntimeSource] = {}
    selection: str | tuple[str, str] | None = None
    for name, record in packet["sources"].items():
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or not isinstance(record, dict):
            raise ValueError(f"Invalid prepared-runtime-sources entry: {name}")
        if application_packet:
            if (
                set(record) != {"snapshot", "profile"}
                or not isinstance(record["snapshot"], str) or not record["snapshot"]
                or not Path(record["snapshot"]).is_absolute()
            ):
                raise ValueError(f"Invalid prepared-runtime-sources entry: {name}")
            application = PreparedApplication(
                Path(record["snapshot"]), name, json.dumps(record["profile"], sort_keys=True),
            )
            source = PreparedRuntimeSource(application.payload, application.identity, application)
        else:
            if (
                set(record) not in (
                    {"payload", "identity"},
                    *([{"payload", "identity", "install_source", "native"}]
                      if packet["version"] == PREPARED_INPUTS_CONTRACT else []),
                )
                or not isinstance(record["payload"], str) or not record["payload"]
                or not Path(record["payload"]).is_absolute()
                or ("install_source" in record and (
                    not isinstance(record["install_source"], str) or not record["install_source"]
                ))
            ):
                raise ValueError(f"Invalid prepared-runtime-sources entry: {name}")
            source = PreparedRuntimeSource(
                Path(record["payload"]), json.dumps(record["identity"]),
                install_source=Path(record["install_source"]) if "install_source" in record else None,
                native=json.dumps(record["native"], sort_keys=True) if "native" in record else None,
            )
        identity = json.loads(source.identity)["runtime_source"]
        commit, tree = identity.get("source_commit"), identity.get("source_tree")
        if (
            identity["plugin"] != name or not source.payload.is_dir()
            or not isinstance(commit, str) or not isinstance(tree, str)
            or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit)
            or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", tree)
        ):
            raise ValueError(f"Prepared runtime selection does not identify {name}")
        if application_packet:
            association = json.loads(application.association)
            if packet["source"] != {
                "commit": commit, "tree": tree, "main_seed": association["main_head"],
            }:
                raise ValueError(f"Prepared runtime selection does not identify {name}")
            composition = json.dumps(association.get("composition"), sort_keys=True)
        else:
            composition = (commit, tree)
        if selection is not None and selection != composition:
            raise ValueError("Prepared runtimes must use one exact source commit, tree and composition")
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
    install_source: Path | None = None
    native: str | None = None

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
            or self.install_source is not None or self.native is not None
        ):
            raise ValueError("Prepared source differs from its complete application selection")
        if (self.install_source is None) != (self.native is None):
            raise ValueError("Prepared installation requires both its snapshot and native selection")
        if self.install_source is not None:
            if not self.install_source.is_absolute() or not self.install_source.is_dir():
                raise ValueError("Prepared installation requires an absolute snapshot")
            native = json.loads(self.native, object_pairs_hook=strict_json_object)
            if (
                not isinstance(native, dict) or native.get("identity") != fields
                or not isinstance(native.get("runtime_version"), str)
                or not native["runtime_version"].startswith(fields["runtime_version"] + ".profile.")
                or not isinstance(native.get("payload_hash"), str)
                or re.fullmatch(r"[0-9a-f]{64}", native["payload_hash"]) is None
            ):
                raise ValueError("Prepared installation has a conflicting native selection")

    @property
    def version(self) -> str:
        if self.application is not None:
            return self.application.version
        return json.loads(self.native or self.identity)["runtime_version"]

    def installer_arguments(self, platform: str) -> list[str]:
        if self.install_source is None:
            return []
        entry = source_install_entry(self.payload, platform)
        if entry is None:
            raise ValueError("Prepared payload has no native installation-source capability")
        return [entry["argument"], str(self.install_source)]

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
             "--plugin", name, "--expected-runtime-version", json.loads(self.identity)["runtime_version"],
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
        if self.install_source is not None:
            if (self.payload / "runtime-source-target.json").exists():
                raise ValueError(
                    "Prepared wheel inputs cannot bypass a package's independent source target policy"
                )
            result = subprocess.run(
                [sys.executable, "-I", "-B", str(owner / "scripts/install_source.py"),
                 "--install-source", str(self.install_source), "--component", name,
                 "--payload", str(self.payload), "--native-inputs"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                env=dict(environment), timeout=60, check=False, **no_window_kwargs(),
            )
            if result.returncode or json.loads(
                result.stdout, object_pairs_hook=strict_json_object,
            ) != json.loads(self.native):
                raise ValueError("Installed native source validator returned a different selection")
        if not (owner / "scripts" / "command_binding.py").is_file():
            raise ValueError("Installed payload has no command lifecycle helper")

    def deployed_version(
        self, root: Path, owner: Path, *, target: SourceRuntimeTarget | None = None,
    ) -> str | None:
        """Only the selected slot and its complete source receipt can be current."""
        if self.application is not None:
            if target is not None:
                raise ValueError("Application selection cannot use a legacy source target")
            return self.application.deployed_version(root, owner)
        path = root / "deploy-manifest.json"
        try:
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError("Source deployment receipt must be an ordinary file")
            content = path.read_text(encoding="utf-8-sig")
        except (FileNotFoundError, UnicodeError):
            return None
        try:
            manifest = json.loads(content, object_pairs_hook=strict_json_object)
        except ValueError:
            return None
        if not isinstance(manifest, dict):
            return None
        fields = json.loads(self.identity)
        if manifest.get("service") is None:
            return None
        if manifest["service"] != fields["runtime_source"]["plugin"]:
            raise ValueError("Source deployment receipt names another runtime")
        if json.dumps(manifest.get("runtime_source"), sort_keys=True) != json.dumps(
            fields["runtime_source"], sort_keys=True,
        ):
            return None
        expected_version = target.version if target else self.version
        if target is not None and manifest.get("runtime_profile") != target.profile:
            return None
        marker = root / "current-version"
        if marker.is_symlink() or (marker.exists() and not marker.is_file()):
            raise ValueError("Source runtime selector must be an ordinary file")
        try:
            selected = marker.read_text(encoding="utf-8-sig").strip()
        except (FileNotFoundError, UnicodeError):
            return None
        if not (
            selected == expected_version
            or (target is None and re.fullmatch(
                re.escape(expected_version) + r"\.repair\.[0-9a-f]{32}", selected,
            ))
        ):
            return None
        slot = root / "versions" / selected
        if (
            manifest.get("runtime_version") != selected
            or not slot.is_dir() or slot.is_symlink()
            or not isinstance(manifest.get("venv"), str)
            or Path(manifest["venv"]) != slot
        ):
            return None
        hash_arguments: list[str] = []
        if self.native is not None:
            hash_arguments = ["--expect-hash", json.loads(self.native)["payload_hash"]]
        if target is not None:
            from .update_runtime_target import profile_hash

            digest = profile_hash(slot, target, fields["runtime_source"])
            if digest is None:
                return None
            hash_arguments = ["--expect-hash", digest]
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-B", str(owner / "scripts" / "versioned_runtime.py"),
                 "--root", str(root), "--json", "is-complete", selected, *hash_arguments],
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
            not isinstance(complete, dict) or complete.get("version") != selected
            or type(complete.get("complete")) is not bool
            or result.returncode != (0 if complete["complete"] else 1)
        ):
            raise ValueError("Source completion probe returned an invalid result")
        return expected_version if complete["complete"] else None

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
