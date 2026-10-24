"""Installed-copy preparation and health for the owning runtime installer."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from importlib.metadata import distributions
from importlib.metadata import version as distribution_version
from pathlib import Path

from packaging.version import Version

_LIFECYCLE_FILES = (
    "runtime_install.py", "runtime_install_governance.py", "runtime_install_modes.py",
    "runtime_install_publication.py", "installer_engine.py",
    "installer_engine_process.py", "installer_dependency_policy.py",
    "installer_wheel_snapshot.py", "installer_wheel_inputs.py",
    "installer_dependency_config.py", "registry_root.py", "command_binding.py", "command_lifecycle.py",
)


def prepare_runtime_candidate(provenance: dict[str, str]) -> None:
    """Finish the unpublished installed copy before its full health probe."""
    import agent_worktrees

    package = Path(agent_worktrees.__file__).resolve().parent
    slot = Path(sys.prefix).resolve()
    if not package.is_relative_to(slot):
        raise RuntimeError("Candidate preparation resolved outside its interpreter")
    version = distribution_version("agent-worktrees")
    if Version(provenance["version"]) != Version(version):
        raise RuntimeError("Frozen package version differs from the requested runtime")
    from .update_stage import fingerprint

    source = json.loads(provenance["deployment_source"])
    source["payload_fingerprint"] = fingerprint(Path(provenance["frozen_plugin"]))
    bootstrap = slot / ".runtime-bootstrap"
    frozen_scripts = Path(provenance["frozen_plugin"]) / "scripts"
    for name in _LIFECYCLE_FILES:
        shutil.copyfile(frozen_scripts / name, bootstrap / name)
    validators = bootstrap / "installation-context"
    if validators.is_symlink():
        raise RuntimeError("Installed lifecycle validators cannot be a symlink")
    if validators.exists():
        shutil.rmtree(validators)
    shutil.copytree(frozen_scripts / "installation-context", validators)
    info = {
        **provenance,
        "deployment_source": json.dumps(source),
        "version": provenance["version"],
        "runtime_version": slot.name,
        "build_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime_generation": uuid.uuid4().hex,
    }
    info.pop("frozen_plugin")
    (package / "_build_info.py").write_text(
        '"""Build provenance -- generated before runtime publication."""\n'
        "from __future__ import annotations\n\n"
        f"BUILD_INFO: dict[str, str] = {info!r}\n",
        encoding="utf-8",
    )
    (slot / ".runtime-bootstrap" / "runtime-generation").write_text(
        info["runtime_generation"] + "\n", encoding="utf-8",
    )
    if os.name == "nt":
        for trampoline in (slot / "Scripts").glob("agent-*.exe"):
            trampoline.unlink()


def verify_runtime_candidate() -> None:
    from . import __main__ as core
    from ._build_info import BUILD_INFO

    core._load_full_command_surface()
    if Version(BUILD_INFO.get("version", "")) != Version(distribution_version("agent-worktrees")):
        raise RuntimeError("Installed build provenance has the wrong package version")
    for field in ("commit", "branch", "source", "build_timestamp", "runtime_generation"):
        if not isinstance(BUILD_INFO.get(field), str) or not BUILD_INFO[field]:
            raise RuntimeError(f"Installed build provenance has no {field}")
    if (Path(sys.prefix) / ".runtime-bootstrap" / "runtime-generation").read_text(
        encoding="utf-8",
    ).strip() != BUILD_INFO["runtime_generation"]:
        raise RuntimeError("Runtime bootstrap generation differs from installed provenance")
    for name in (
        "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_source.py", "versioned_runtime_construction.py", *_LIFECYCLE_FILES,
        "versioned_runtime_publication.py",
    ):
        path = Path(sys.prefix) / ".runtime-bootstrap" / name
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"Candidate has no immutable process-retention bootstrap: {name}")
    validators = Path(sys.prefix) / ".runtime-bootstrap" / "installation-context"
    helper = validators / "installation_context.py"
    if validators.is_symlink() or helper.is_symlink():
        raise RuntimeError("Installed lifecycle validators cannot be a symlink")
    spec = importlib.util.spec_from_file_location("_installed_lifecycle_validator", helper)
    if spec is None or spec.loader is None:
        raise ImportError("Installed lifecycle validator cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if os.name == "nt" and any((Path(sys.prefix) / "Scripts").glob("agent-*.exe")):
        raise RuntimeError("Candidate still contains unsigned agent console trampolines")


def verify_installed_origins() -> None:
    """Check a retained build's own graph, without consulting today's sources."""
    slot = Path(sys.prefix).resolve()
    proof = slot / ".runtime-input-origins.json"
    if proof.is_symlink():
        raise RuntimeError("Frozen origin evidence is a symlink")
    if not proof.exists():
        return  # Older numbered builds predate the origin receipt.
    receipt = json.loads(proof.read_text(encoding="utf-8"))
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != "installer-engine.frozen-input-origins"
        or type(receipt.get("version")) is not int
        or receipt["version"] != 1
        or not isinstance(receipt.get("packages"), dict)
        or not receipt["packages"]
    ):
        raise RuntimeError("Frozen origin evidence has an unsupported schema")
    recorded = receipt["packages"]
    observed = {}
    for distribution in distributions():
        direct_url = distribution.read_text("direct_url.json")
        if direct_url is None:
            continue
        origin = json.loads(direct_url)
        name = distribution.metadata["Name"]
        if name not in recorded and "dir_info" not in origin:
            continue
        if (
            not Path(distribution.locate_file("")).resolve().is_relative_to(slot)
            or origin.get("dir_info") not in ({}, {"editable": False})
            or name in observed
        ):
            raise RuntimeError(f"{name} is not an attributable non-editable installation")
        observed[name] = origin.get("url")
    if observed != recorded or any(
        not isinstance(url, str) or not url.startswith("file:")
        for url in recorded.values()
    ):
        raise RuntimeError("Installed dependencies differ from their frozen origin receipt")
