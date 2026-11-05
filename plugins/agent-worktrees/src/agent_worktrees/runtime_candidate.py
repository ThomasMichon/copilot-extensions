"""Installed-copy preparation and health for the owning runtime installer."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import time
import uuid
from datetime import datetime, timezone
from importlib.metadata import distributions
from importlib.metadata import version as distribution_version
from pathlib import Path
from urllib.parse import unquote, urlsplit

from packaging.version import Version

# Installed control code must support the frozen-source mode-owner contract.
MODE_OWNER_ABI = 1

_LIFECYCLE_FILES = (
    "runtime_install.py", "runtime_install_governance.py", "runtime_install_modes.py",
    "runtime_install_publication.py", "installer_engine.py", "installer_engine_health.py",
    "runtime_install_driver.py", "runtime_install_recheck.py", "runtime_install_source.py",
    "runtime_install_recovery.py",
    "installer_engine_process.py", "installer_dependency_policy.py",
    "installer_marker_environment.py",
    "installer_wheel_snapshot.py", "installer_wheel_inputs.py",
    "installer_dependency_config.py", "installer_dependency_arguments.py", "installer_retained_content.py",
    "registry_root.py", "command_binding.py", "command_lifecycle.py",
    "installer_source_build.py", "installer_source_wheel_cache.py",
    "install_source.py",
)


def _native(path: Path) -> Path:
    value = os.path.abspath(path)
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return Path(value)


def prepare_runtime_candidate(provenance: dict[str, str] | None = None) -> None:
    """Finish the unpublished installed copy before its full health probe."""
    import agent_worktrees

    package = Path(agent_worktrees.__file__).resolve().parent
    environment = Path(sys.prefix).resolve()
    slot = environment.parent if environment.name == ".venv" else environment
    if not package.is_relative_to(slot):
        raise RuntimeError("Candidate preparation resolved outside its interpreter")
    version = distribution_version("agent-worktrees")
    application = (slot / "application.json").is_file()
    if provenance is None:
        if not application:
            raise RuntimeError("Candidate preparation requires its selected source")
        source = json.loads(_native(slot / "runtime-source.json").read_text(encoding="utf-8"))
        if source["plugin"] != "agent-worktrees":
            raise RuntimeError("Candidate preparation selected a foreign application")
        provenance = {
            "version": source["package_version"], "commit": source["source_commit"],
            "branch": "source", "source": "source:" + source["source_commit"],
            "deployment_source": json.dumps(source),
        }
    if Version(provenance["version"]) != Version(version):
        raise RuntimeError("Frozen package version differs from the requested runtime")

    source = json.loads(provenance["deployment_source"])
    if "frozen_plugin" in provenance:
        from .update_stage import fingerprint

        source["payload_fingerprint"] = fingerprint(Path(provenance["frozen_plugin"]))
    bootstrap = slot / ".runtime-bootstrap"
    frozen_scripts = (
        package / "_runtime_assets" / "scripts" if application
        else Path(provenance["frozen_plugin"]) / "scripts"
    )
    for name in _LIFECYCLE_FILES:
        shutil.copyfile(_native(frozen_scripts / name), _native(bootstrap / name))
    validators = bootstrap / "installation-context"
    if _native(validators).is_symlink():
        raise RuntimeError("Installed lifecycle validators cannot be a symlink")
    if _native(validators).exists():
        shutil.rmtree(_native(validators))
    shutil.copytree(_native(frozen_scripts / "installation-context"), _native(validators))
    info = {
        **provenance,
        "deployment_source": json.dumps(source),
        "version": provenance["version"],
        "runtime_version": slot.name,
        "build_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime_generation": uuid.uuid4().hex,
    }
    info.pop("frozen_plugin", None)
    info_path = slot / ".runtime-bootstrap" / "build-info.json" if application else package / "_build_info.py"
    temporary_info = info_path.with_name(f".{info_path.name}.{uuid.uuid4().hex}")
    _native(temporary_info).write_text(
        json.dumps(info, sort_keys=True) + "\n" if application else (
            '"""Build provenance -- generated before runtime publication."""\n'
            "from __future__ import annotations\n\n"
            f"BUILD_INFO: dict[str, str] = {info!r}\n"
        ),
        encoding="utf-8",
    )
    try:
        os.replace(_native(temporary_info), _native(info_path))
    finally:
        _native(temporary_info).unlink(missing_ok=True)
    _native(slot / ".runtime-bootstrap" / "runtime-generation").write_text(
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
    environment = Path(sys.prefix).resolve()
    slot = environment.parent if environment.name == ".venv" else environment
    if _native(slot / ".runtime-bootstrap" / "runtime-generation").read_text(
        encoding="utf-8",
    ).strip() != BUILD_INFO["runtime_generation"]:
        raise RuntimeError("Runtime bootstrap generation differs from installed provenance")
    for name in (
        "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_source.py", "versioned_runtime_construction.py", *_LIFECYCLE_FILES,
        "versioned_runtime_publication.py",
    ):
        path = slot / ".runtime-bootstrap" / name
        if not _native(path).is_file() or _native(path).is_symlink():
            raise RuntimeError(f"Candidate has no immutable process-retention bootstrap: {name}")
    validators = slot / ".runtime-bootstrap" / "installation-context"
    helper = validators / "installation_context.py"
    if _native(validators).is_symlink() or _native(helper).is_symlink():
        raise RuntimeError("Installed lifecycle validators cannot be a symlink")
    spec = importlib.util.spec_from_file_location("_installed_lifecycle_validator", _native(helper))
    if spec is None or spec.loader is None:
        raise ImportError("Installed lifecycle validator cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if os.name == "nt" and not (slot / "application.json").is_file() and any(
        (slot / "Scripts").glob("agent-*.exe")
    ):
        raise RuntimeError("Candidate still contains unsigned agent console trampolines")


def _closed_wheel_distributions(slot: Path, receipt: object, *, application: bool) -> dict:
    def ordinary(path: Path, *, directory: bool = False) -> bool:
        try:
            info = path.lstat()
        except OSError:
            return False
        return (
            (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and not getattr(info, "st_file_attributes", 0) & 0x400
        )

    bootstrap = _native(slot / ".runtime-bootstrap")
    helper = bootstrap / "installer_wheel_snapshot.py"
    if not ordinary(bootstrap, directory=True) or not ordinary(helper):
        raise RuntimeError("Retained wheel receipt validator is unavailable")
    name = "_retained_wheel_snapshot_" + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(name, helper)
    if spec is None or spec.loader is None:
        raise RuntimeError("Retained wheel receipt validator is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(bootstrap))
    try:
        spec.loader.exec_module(module)
        canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
        inputs = module.WheelReuseInputs(slot.name, canonical, time.monotonic() + 120)
        if application:
            health_name = name + "_health"
            health_helper = bootstrap / "installer_engine_health.py"
            if not ordinary(health_helper):
                raise RuntimeError("Retained application wheel validator is unavailable")
            health_spec = importlib.util.spec_from_file_location(
                health_name, health_helper,
            )
            if health_spec is None or health_spec.loader is None:
                raise RuntimeError("Retained application wheel validator is unavailable")
            health = importlib.util.module_from_spec(health_spec)
            sys.modules[health_name] = health
            try:
                health_spec.loader.exec_module(health)
                health.verify_application_wheels(inputs, _native(slot))
            finally:
                del sys.modules[health_name]
        return inputs.receipt()["distributions"]
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Retained wheel receipt is invalid") from error
    finally:
        sys.path.remove(str(bootstrap))
        del sys.modules[name]


def _application_wheel_url(slot: Path, wheel: dict, url: object) -> bool:
    if not isinstance(url, str):
        return False
    try:
        actual = urlsplit(url)
        expected = urlsplit((slot / "wheels" / wheel["filename"]).as_uri())
        return (
            actual.scheme == expected.scheme and actual.netloc == expected.netloc
            and unquote(actual.path) == unquote(expected.path)
            and not actual.query and not actual.fragment
        )
    except ValueError:
        return False


def verify_installed_origins() -> None:
    """Check a retained build's own graph, without consulting today's sources."""
    environment = Path(sys.prefix).resolve()
    slot = environment.parent if environment.name == ".venv" else environment
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
        or receipt["version"] not in (1, 2)
        or not isinstance(receipt.get("packages"), dict)
        or not receipt["packages"]
    ):
        raise RuntimeError("Frozen origin evidence has an unsupported schema")
    recorded = receipt["packages"]
    source_wheels = receipt.get("source_wheels")
    closed_wheels = receipt["version"] == 2
    application = closed_wheels and environment != slot
    if closed_wheels:
        if source_wheels is not None:
            raise RuntimeError("Closed wheels cannot also claim source-wheel origins")
        source_wheels = _closed_wheel_distributions(
            slot, receipt.get("wheel_inputs"), application=application,
        )
    if source_wheels is not None and (
        not isinstance(source_wheels, dict) or not source_wheels or set(source_wheels) != set(recorded)
    ):
        raise RuntimeError("Source wheel evidence differs from its frozen origins")
    observed = {}
    if closed_wheels and os.name == "nt":
        site = (environment / "Lib" / "site-packages").resolve()
        search = [
            str(_native(site)) if Path(path).resolve() == site else path
            for path in sys.path
        ]
        installed = distributions(path=search)
    else:
        installed = distributions()
    for distribution in installed:
        direct_url = distribution.read_text("direct_url.json")
        if direct_url is None:
            if closed_wheels:
                raise RuntimeError("Closed wheel runtime has an unattributed distribution")
            continue
        origin = json.loads(direct_url)
        name = distribution.metadata["Name"]
        if not isinstance(name, str) or not name:
            raise RuntimeError("Installed distribution has no name")
        if source_wheels is not None:
            name = re.sub(r"[-_.]+", "-", name).lower()
        if closed_wheels and name not in recorded:
            raise RuntimeError("Closed wheel runtime has an unexpected distribution")
        if name not in recorded and "dir_info" not in origin:
            continue
        valid_origin = origin.get("dir_info") in ({}, {"editable": False})
        if source_wheels is not None and name in source_wheels:
            wheel = source_wheels[name]
            digest = wheel["sha256"].removeprefix("sha256:")
            archive = origin.get("archive_info")
            valid_origin = (
                "dir_info" not in origin
                and distribution.version == wheel["version"]
                and isinstance(archive, dict)
                and set(archive) <= {"hash", "hashes"}
                and archive.get("hash", "sha256=" + digest) == "sha256=" + digest
                and (archive == {} or archive.get("hashes") == {"sha256": digest})
                and (
                    (
                        application
                        and _application_wheel_url(slot, wheel, origin.get("url"))
                        and _application_wheel_url(slot, wheel, recorded.get(name))
                    )
                    or (
                        not application
                        and str(origin.get("url", "")).endswith("#sha256=" + digest)
                    )
                )
            )
        if (
            not _native(distribution.locate_file("")).resolve().is_relative_to(_native(slot).resolve())
            or not valid_origin or name in observed
        ):
            raise RuntimeError(f"{name} is not an attributable non-editable installation")
        observed[name] = recorded[name] if application else origin.get("url")
    if observed != recorded or any(
        not isinstance(url, str) or not url.startswith("file:")
        for url in recorded.values()
    ):
        raise RuntimeError("Installed dependencies differ from their frozen origin receipt")
