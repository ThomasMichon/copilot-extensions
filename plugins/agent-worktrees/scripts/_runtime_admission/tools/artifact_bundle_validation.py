"""Canonical read-only first-party bundle, wheel and source validation."""
from __future__ import annotations

import configparser
import hashlib
import json
import re
import stat
import sys
import tempfile
import zipfile
from email.message import Message
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO

from artifact_source_build import PromotionArtifactError, validate_runtime_metadata
from standalone_python_artifacts import COMPONENTS, artifact_label

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
from installer_wheel_inputs import WheelLimits
from runtime_wheel_inspection import inspect_wheel_facts

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

_DIGEST = re.compile(r"[0-9a-f]{64}")
_PLUGIN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_VERSION = re.compile(r"[0-9][A-Za-z0-9.+-]*")
_MANIFEST_LIMIT = 1 << 20
_BUNDLE_LIMIT = 512 << 20
_WHEEL_TAG_LIMIT = 10_000


def _artifact_label(record: dict) -> str:
    try:
        return artifact_label(record)
    except (KeyError, TypeError, ValueError) as exc:
        raise PromotionArtifactError("invalid artifact identity") from exc


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_artifact_entry(entry: dict) -> str:
    """Return only the exact filename declared by a valid artifact identity."""
    if not isinstance(entry, dict):
        raise PromotionArtifactError("invalid artifact entry")
    plugin, version = _artifact_label(entry), entry.get("version")
    identity, digest = entry.get("artifact_id"), entry.get("sha256")
    if (
        not isinstance(plugin, str) or not _PLUGIN.fullmatch(plugin)
        or not isinstance(version, str) or not _VERSION.fullmatch(version)
        or not isinstance(identity, str) or not identity.startswith("sha256:")
        or not _DIGEST.fullmatch(identity[7:])
        or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
    ):
        raise PromotionArtifactError("invalid artifact entry identity")
    name = f"{plugin}-{version}-{identity[7:]}.zip"
    if entry.get("filename") != name:
        raise PromotionArtifactError("duplicate or mismatched artifact filename")
    return name


def validate_bundle_binding(
    path: Path, entry: dict, *, candidate_root: Path | None = None,
) -> tuple[dict, str]:
    """Bind the canonical manifest to its actual archive and manifest bytes."""
    name = validate_artifact_entry(entry)
    if path.name != name or path.is_symlink() or not path.is_file() or _digest(path) != entry["sha256"]:
        raise PromotionArtifactError(f"{name}: artifact digest mismatch")
    manifest = validate_bundle(path, entry, candidate_root=candidate_root)
    with zipfile.ZipFile(path) as bundle:
        manifest_bytes = bundle.read("manifest.json")
    if json.loads(manifest_bytes) != manifest or _digest(path) != entry["sha256"]:
        raise PromotionArtifactError("runtime-profile bundle changed during validation")
    return manifest, "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()


def validate_bundle(path: Path, entry: dict, *, candidate_root: Path | None = None) -> dict:
    """Inspect untrusted ZIP bytes without extracting or executing candidate code."""
    try:
        with zipfile.ZipFile(path) as bundle:
            members = bundle.infolist()
            names = [member.filename for member in members]
            if (
                len(names) != len(set(names)) or "manifest.json" not in names
                or sum(member.file_size for member in members) > _BUNDLE_LIMIT
                or any(
                    member.is_dir() or member.flag_bits & 1
                    or stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG)
                    for member in members
                )
            ):
                raise PromotionArtifactError(f"{path.name}: unsafe bundle members")
            if bundle.getinfo("manifest.json").file_size > _MANIFEST_LIMIT:
                raise PromotionArtifactError(f"{path.name}: oversized artifact manifest")
            manifest = json.loads(bundle.read("manifest.json"))
            if (
                not isinstance(manifest, dict)
                or manifest.get("schema") != "copilot-extensions.python-artifact-manifest"
                or type(manifest.get("schema_version")) is not int
                or manifest["schema_version"] != (
                    5 if "composition" in entry else 4 if "component" in entry else 3
                )
                or _artifact_label(manifest) != _artifact_label(entry)
                or any(manifest.get(key) != entry[key] for key in ("version", "artifact_id"))
            ):
                raise PromotionArtifactError(f"{path.name}: artifact manifest identity mismatch")
            wheels = manifest.get("wheels")
            if not isinstance(wheels, list) or not wheels:
                raise PromotionArtifactError(f"{path.name}: missing first-party wheel records")
            declared = {"manifest.json"}
            plugin_wheels = 0
            primary_role = "standalone" if "component" in entry else "plugin"
            core = "composition" in entry
            for wheel in wheels:
                if not isinstance(wheel, dict):
                    raise PromotionArtifactError(f"{path.name}: invalid wheel record")
                name, digest, role = wheel.get("filename"), wheel.get("sha256"), wheel.get("role")
                if (
                    role not in (("plugin", "standalone", "vendored-lib") if core else (primary_role, "vendored-lib"))
                    or not isinstance(name, str) or "/" in name or "\\" in name
                    or not name.endswith(".whl") or name in declared
                    or not isinstance(digest, str) or not digest.startswith("sha256:")
                    or not _DIGEST.fullmatch(digest[7:])
                ):
                    raise PromotionArtifactError(f"{path.name}: invalid first-party wheel record")
                declared.add(name)
                plugin_wheels += role == primary_role
            if (not core and plugin_wheels != 1) or set(names) != declared:
                raise PromotionArtifactError(f"{path.name}: first-party wheel member set mismatch")
            validate_manifest(manifest)
            if candidate_root is not None:
                validate_source_binding(manifest, candidate_root)
            for wheel in wheels:
                digest = hashlib.sha256()
                with tempfile.TemporaryFile() as archive:
                    with bundle.open(wheel["filename"]) as stream:
                        for chunk in iter(lambda: stream.read(1 << 20), b""):
                            digest.update(chunk)
                            archive.write(chunk)
                    if f"sha256:{digest.hexdigest()}" != wheel["sha256"]:
                        raise PromotionArtifactError(f"{path.name}: wheel digest mismatch")
                    archive.seek(0)
                    validate_wheel(
                        archive, wheel,
                        source_dir=candidate_root / wheel["source"] if candidate_root is not None else None,
                        component=(
                            next((key for key, source in COMPONENTS.items() if source == wheel["source"]), None)
                            if core else entry.get("component") if wheel["role"] == primary_role else None
                        ),
                        candidate_root=candidate_root,
                    )
            return manifest
    except (
        OSError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile,
        RuntimeError, NotImplementedError,
    ) as exc:
        if isinstance(exc, PromotionArtifactError):
            raise
        raise PromotionArtifactError(f"{path.name}: cannot read artifact bundle: {exc}") from exc


def validate_wheel(
    archive: BinaryIO, wheel: dict, *, source_dir: Path | None = None,
    component: str | None = None, candidate_root: Path | None = None,
) -> None:
    """Validate wheel bytes, metadata and RECORD without extracting package code."""
    if not isinstance(wheel["generator"], str) or not wheel["generator"]:
        raise PromotionArtifactError("first-party wheel requires its locked generator")
    inspect_wheel(
        archive, wheel, expected_generator=wheel["generator"],
        record_algorithms=frozenset({"sha256"}),
        max_archive_bytes=_BUNDLE_LIMIT,
        limits=WheelLimits(
            max_archive_bytes=_BUNDLE_LIMIT, max_expanded_bytes=_BUNDLE_LIMIT,
            max_member_bytes=_BUNDLE_LIMIT, max_metadata_bytes=3 * _MANIFEST_LIMIT,
            max_members=100_000, max_tags=_WHEEL_TAG_LIMIT,
            max_metadata_member_bytes=_MANIFEST_LIMIT,
        ),
        source_dir=source_dir, component=component, candidate_root=candidate_root,
    )


def inspect_wheel(
    archive: BinaryIO, wheel: dict, *, expected_generator: str | None = None,
    max_unpacked_bytes: int = _BUNDLE_LIMIT, max_metadata_bytes: int = _MANIFEST_LIMIT,
    max_archive_bytes: int = _BUNDLE_LIMIT,
    record_algorithms: frozenset[str] = frozenset({"sha256", "sha384", "sha512"}),
    source_dir: Path | None = None, component: str | None = None,
    candidate_root: Path | None = None,
    limits: WheelLimits | None = None, deadline: float | None = None,
) -> tuple[Message, int]:
    """Inspect a wheel without execution; return actual metadata and unpacked size.

    First-party promotion additionally requires its locked build-tool generator.
    Runtime wheels use their exact archive digest, not a claimed generator, and
    must supply their own acquisition/approval evidence outside this parser.
    """
    try:
        facts = inspect_wheel_facts(
            archive, wheel, expected_generator=expected_generator,
            max_unpacked_bytes=max_unpacked_bytes, max_metadata_bytes=max_metadata_bytes,
            max_archive_bytes=max_archive_bytes,
            record_algorithms=record_algorithms, limits=limits, deadline=deadline,
        )
        if source_dir is not None:
            with zipfile.ZipFile(archive) as bundle:
                validate_wheel_sources(
                    bundle, facts.member_hashes, source_dir=source_dir, prefix=facts.prefix,
                    metadata=facts.metadata,
                    component=component, candidate_root=candidate_root,
                )
        return facts.metadata, facts.unpacked_size
    except (
        OSError, UnicodeDecodeError, zipfile.BadZipFile, RuntimeError, NotImplementedError,
        ValueError,
    ) as exc:
        if isinstance(exc, PromotionArtifactError):
            raise
        raise PromotionArtifactError(f"{wheel['filename']}: invalid wheel archive: {exc}") from exc


def validate_wheel_sources(
    bundle: zipfile.ZipFile, hashes: dict[str, str], *, source_dir: Path, prefix: str,
    metadata: Message,
    component: str | None = None, candidate_root: Path | None = None,
) -> None:
    """Bind static setuptools payload bytes and generated entry points to source data."""
    try:
        injected = {}
        if component is not None:
            from standalone_python_artifacts import injected_resources, validate_build
            data = validate_build(source_dir, component=component, root=candidate_root)
            injected = injected_resources(component, candidate_root)
        else:
            from plugin_runtime_resources import build_inputs
            data, injected = build_inputs(source_dir, root=candidate_root)
        from native_daemon_artifacts import generated_members

        generated_members(bundle, source_dir=source_dir, data=data, prefix=prefix)
        tool = data.get("tool", {})
        settings = tool.get("setuptools", {}) if isinstance(tool, dict) else None
        if not isinstance(settings, dict):
            raise PromotionArtifactError("invalid setuptools source configuration")
        modules = settings.get("py-modules")
        packages = settings.get("packages", {})
        if not isinstance(packages, dict) or set(packages) - {"find"}:
            raise PromotionArtifactError("unsupported wheel source package layout")
        find = packages.get("find", {})
        if not isinstance(find, dict) or set(find) - {"where"} or find.get("where", ["src"]) != ["src"]:
            raise PromotionArtifactError("unsupported wheel source discovery configuration")
        if modules is not None:
            if not isinstance(modules, list) or any(
                not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                for name in modules
            ) or packages:
                raise PromotionArtifactError("unsupported wheel source module layout")
            base = source_dir
            required_python = {name + ".py" for name in modules}
        else:
            base = source_dir / "src"
            if not base.resolve().is_relative_to(source_dir.resolve()) or base.is_symlink():
                raise PromotionArtifactError("wheel source root escapes candidate")
            required_python = {
                path.relative_to(base).as_posix() for path in base.rglob("*.py")
                if not any(
                    part in ("__pycache__", "build", "dist") or part.endswith(".egg-info")
                    for part in path.relative_to(base).parts
                )
            }
            required_python.update(name for name in injected if name.endswith(".py"))
        if settings.get("package-dir", {"": str(base.relative_to(source_dir))}) != {
            "": str(base.relative_to(source_dir)),
        }:
            raise PromotionArtifactError("unsupported wheel source package-dir mapping")
        payload = {name for name in hashes if not name.startswith(prefix)}
        if {name for name in payload if name.endswith(".py")} != required_python:
            raise PromotionArtifactError("wheel Python member set differs from candidate source")
        for name in payload:
            path = injected.get(name, base / name)
            if (
                not path.resolve().is_relative_to(
                    candidate_root.resolve() if name in injected else source_dir.resolve()
                )
                or not path.is_file() or path.is_symlink() or _digest(path) != hashes[name]
            ):
                raise PromotionArtifactError(f"{name}: wheel payload differs from candidate source")
        package_data = settings.get("package-data", {})
        if not isinstance(package_data, dict) or settings.get("exclude-package-data") or settings.get("data-files"):
            raise PromotionArtifactError("invalid wheel package-data configuration")
        required_data: set[str] = {name for name in injected if not name.endswith(".py")}
        for package, patterns in package_data.items():
            if not isinstance(package, str) or not isinstance(patterns, list) or any(
                not isinstance(pattern, str) or "\\" in pattern or pattern.startswith("/")
                or ".." in pattern.split("/") for pattern in patterns
            ):
                raise PromotionArtifactError("unsupported wheel package-data pattern")
            if package == "*":
                roots = {base / Path(name).parent for name in required_python}
            elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", package):
                roots = {base.joinpath(*package.split("."))}
            else:
                raise PromotionArtifactError("invalid wheel package-data package")
            required_data.update({
                path.relative_to(base).as_posix()
                for root in roots for pattern in patterns for path in root.glob(pattern) if path.is_file()
            })
        if payload != required_python | required_data:
            raise PromotionArtifactError("wheel payload set differs from declared candidate members")
        project = data["project"]
        validate_runtime_metadata(project, metadata)
        entries = project.get("entry-points", {})
        if not isinstance(entries, dict) or any(
            not isinstance(group, str) or not isinstance(values, dict) or any(
                not isinstance(name, str) or not name or not isinstance(value, str) or not value
                for name, value in values.items()
            ) for group, values in entries.items()
        ):
            raise PromotionArtifactError("invalid candidate entry-point declarations")
        expected_entries = dict(entries)
        for field, group in (("scripts", "console_scripts"), ("gui-scripts", "gui_scripts")):
            values = project.get(field, {})
            if not isinstance(values, dict) or any(
                not isinstance(name, str) or not name or not isinstance(value, str) or not value
                for name, value in values.items()
            ):
                raise PromotionArtifactError("invalid candidate script declarations")
            if values:
                if group in expected_entries:
                    raise PromotionArtifactError("ambiguous candidate entry-point group")
                expected_entries[group] = values
        entries_name = prefix + "entry_points.txt"
        actual_entries = {}
        if entries_name in hashes:
            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str
            parser.read_string(bundle.read(entries_name).decode("utf-8"))
            actual_entries = {section: dict(parser[section]) for section in parser.sections()}
        if actual_entries != expected_entries:
            raise PromotionArtifactError("wheel entry points differ from candidate source")
        top_name = prefix + "top_level.txt"
        if top_name in hashes:
            expected_top = {name.split("/")[0].removesuffix(".py") for name in required_python}
            if set(bundle.read(top_name).decode("utf-8").split()) != expected_top:
                raise PromotionArtifactError("wheel top-level modules differ from candidate source")
        notice = source_dir / "LICENSE"
        if (notice.exists() or notice.is_symlink()) and (
            notice.is_symlink() or not notice.is_file() or notice.stat().st_size == 0
            or not any(hashes.get(prefix + relative) == _digest(notice) for relative in (
                "licenses/LICENSE", "LICENSE",
            ))
        ):
            raise PromotionArtifactError("wheel omits or changes the source LICENSE notice")
        generated = {"WHEEL", "METADATA", "RECORD", "entry_points.txt", "top_level.txt"}
        for name in hashes:
            if not name.startswith(prefix) or name[len(prefix):] in generated:
                continue
            relative = name[len(prefix):].removeprefix("licenses/")
            path = source_dir / relative
            if (
                not path.resolve().is_relative_to(source_dir.resolve())
                or not path.is_file() or path.is_symlink() or _digest(path) != hashes[name]
            ):
                raise PromotionArtifactError(f"{name}: generated wheel member is not candidate source data")
    except (
        OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError,
        ValueError, configparser.Error,
    ) as exc:
        raise PromotionArtifactError(f"cannot bind wheel payload to candidate source: {exc}") from exc


def validate_manifest(manifest: dict) -> None:
    """Reuse trusted builder identity rules without resolving or running a toolchain."""
    from build_python_artifacts import (
        _assert_version_corresponds, overall_identity_tags, parse_wheel_filename,
    )
    from build_toolchain_lock import ArtifactBuildError, ToolchainLock, _hash_fields

    try:
        payload_hash = manifest.get("payload_hash")
        if (
            not isinstance(payload_hash, str) or not payload_hash.startswith("sha256:")
            or not _DIGEST.fullmatch(payload_hash[7:])
        ):
            raise PromotionArtifactError("invalid manifest payload hash")
        lock = manifest.get("build_toolchain")
        if not isinstance(lock, dict) or set(lock) != {"packages", "provenance", "lock_id"}:
            raise PromotionArtifactError("invalid manifest build-toolchain record")
        packages = lock["packages"]
        if (
            not isinstance(packages, dict)
            or set(packages) != {"setuptools", "wheel", "packaging"}
            or any(not isinstance(version, str) or not _VERSION.fullmatch(version) for version in packages.values())
        ):
            raise PromotionArtifactError("invalid manifest build-toolchain pins")
        toolchain = ToolchainLock(Path("."), packages, provenance=lock["provenance"])
        if lock != toolchain.record:
            raise PromotionArtifactError("manifest toolchain lock identity mismatch")
        wheel_infos = []
        label = _artifact_label(manifest)
        standalone = "component" in manifest
        core = "composition" in manifest
        primary_role = "standalone" if standalone else "plugin"
        source_root = COMPONENTS[manifest["component"]] if standalone else f"plugins/{label}"
        if core:
            selection = manifest.get("selection")
            if (
                not isinstance(selection, dict) or set(selection) != {"roots", "projects", "edges"}
                or any(not isinstance(selection[key], list) for key in selection)
                or not selection["roots"] or not selection["projects"]
                or not isinstance(manifest.get("build_resources"), list)
            ):
                raise PromotionArtifactError("invalid core manifest selection")
            sources = [item["source"] for item in selection["projects"]]
            if len(sources) != len(set(sources)) or len(manifest["wheels"]) != len(sources):
                raise PromotionArtifactError("invalid core manifest project set")
        if standalone:
            from standalone_consumers import standalone_build_resources
            expected_resources = [
                path.as_posix() for path in standalone_build_resources(
                    source_root, source_root=Path("."),
                )
            ]
            resources = manifest.get("build_resources")
            embedded_resources = (
                manifest["component"] == "worktree-manager"
                and isinstance(resources, list)
                and resources[:len(expected_resources)] == expected_resources
                and len(resources) > len(expected_resources)
                and all(
                    isinstance(path, str)
                    and re.fullmatch(r"(?:worktree-manager/)?libs/[a-z0-9]+(?:-[a-z0-9]+)*", path)
                    for path in resources
                )
                and len(resources) == len(set(resources))
            )
            if resources != expected_resources and not embedded_resources:
                raise PromotionArtifactError("invalid manifest build resource closure")
        for wheel in manifest["wheels"]:
            info = parse_wheel_filename(Path(wheel["filename"]))
            source = wheel.get("source")
            if not core and wheel["role"] == primary_role:
                _assert_version_corresponds(
                    raw_version=manifest["version"], wheel_version=info["version"],
                    label=label,
                )
            source_matches = (
                any(item["source"] == source and item["role"] == wheel["role"] for item in selection["projects"])
                and isinstance(source, str) and (
                    source in COMPONENTS.values() if wheel["role"] == "standalone" else
                    len(source.split("/")) == 2 and source.startswith(
                        "plugins/" if wheel["role"] == "plugin" else "libs/"
                    )
                )
                if core else
                source == source_root if wheel["role"] == primary_role
                else isinstance(source, str) and source.startswith((
                    "libs/", f"{source_root}/libs/" if standalone else f"{source_root}/",
                ))
            )
            if (
                wheel.get("name") != info["name"]
                or any(wheel.get(tag) != info[tag] for tag in ("python_tag", "abi_tag", "platform_tag"))
                or wheel.get("generator") != toolchain.generator
                or not isinstance(source, str) or "\\" in source
                or any(part in ("", ".", "..") for part in source.split("/"))
                or not source_matches
                or wheel["role"] not in (("plugin", "standalone", "vendored-lib") if core else (primary_role, "vendored-lib"))
            ):
                raise PromotionArtifactError("invalid manifest wheel provenance or tags")
            wheel_infos.append(info)
        tags = overall_identity_tags(wheel_infos)
        if any(manifest.get(tag) != value for tag, value in tags.items()):
            raise PromotionArtifactError("manifest aggregate wheel tags mismatch")
        wheel_fields = [
            field for wheel in sorted(manifest["wheels"], key=lambda item: item["filename"])
            for field in (wheel["filename"], wheel["sha256"])
        ]
        identity = "sha256:" + _hash_fields(
            payload_hash, tags["python_tag"], tags["abi_tag"], tags["platform_tag"],
            toolchain.lock_id, *wheel_fields,
        )
        if manifest["artifact_id"] != identity:
            raise PromotionArtifactError("manifest artifact identity mismatch")
    except (ArtifactBuildError, KeyError, TypeError, ValueError) as exc:
        raise PromotionArtifactError(f"invalid manifest provenance: {exc}") from exc


def validate_source_binding(manifest: dict, root: Path, *, core_hash_workers: int = 1) -> None:
    """Hash the trusted-discovered closure in candidate data, never import its code."""
    import build_python_artifacts as builder
    from build_toolchain_lock import ArtifactBuildError

    if type(core_hash_workers) is not int or not 1 <= core_hash_workers <= 4:
        raise PromotionArtifactError("core source binding requires between one and four hash workers")
    source_builder = SimpleNamespace(
        uer=builder.uer,
        resolve_vendored_libs=partial(builder.resolve_vendored_libs, root=root),
        compute_payload_hash=partial(builder.compute_payload_hash, root=root),
    )
    label = _artifact_label(manifest)
    standalone = "component" in manifest
    core = "composition" in manifest
    plugin_dir = root / COMPONENTS[manifest["component"]] if standalone else root / "plugins" / label
    try:
        if not core and (
            plugin_dir.is_symlink() or builder.uer._find_symlink(plugin_dir, skip_test_runs=False) is not None
        ):
            raise PromotionArtifactError("artifact source tree contains a symlink")
        resources = ()
        if core:
            from core_python_artifacts import payload_hash, source_closure
            closure = source_closure(root, source_builder)
            directories = [directory for _role, directory in closure.projects]
            resources = closure.resources
            if (
                manifest["version"] != closure.version or manifest.get("selection") != closure.selection
                or manifest.get("build_resources") != [path.relative_to(root).as_posix() for path in resources]
                or {(wheel["role"], wheel["source"]) for wheel in manifest["wheels"]} != {
                    (role, directory.relative_to(root).as_posix()) for role, directory in closure.projects
                }
            ):
                raise PromotionArtifactError("core manifest selection differs from candidate")
        elif standalone:
            from standalone_python_artifacts import source_closure
            plugin_dir, libs, resources = source_closure(manifest["component"], root, source_builder)
            if manifest.get("build_resources") != [
                path.relative_to(root).as_posix() for path in resources
            ]:
                raise PromotionArtifactError("manifest build resource closure differs from candidate")
        else:
            libs = source_builder.resolve_vendored_libs(plugin_dir)
            from plugin_runtime_resources import build_inputs
            build_inputs(plugin_dir, root=root)
        if not core:
            directories = [plugin_dir, *(directory for _name, directory in libs)]
        expected = {
            directory.resolve().relative_to(root.resolve()).as_posix(): directory
            for directory in directories
        }
        sources = [wheel["source"] for wheel in manifest["wheels"]]
        if len(sources) != len(set(sources)) or set(sources) != set(expected):
            raise PromotionArtifactError("manifest source closure differs from candidate")
        for wheel in manifest["wheels"]:
            project = tomllib.loads(
                (expected[wheel["source"]] / "pyproject.toml").read_text(encoding="utf-8"),
            )["project"]
            info = builder.parse_wheel_filename(Path(wheel["filename"]))
            if info["name"].lower() != re.sub(r"[-_.]+", "_", project["name"]).lower():
                raise PromotionArtifactError("manifest wheel distribution differs from candidate")
            builder._assert_version_corresponds(
                raw_version=project["version"], wheel_version=info["version"],
                label=wheel["source"],
            )
        actual = (
            payload_hash(source_builder, closure, workers=core_hash_workers)
            if core else source_builder.compute_payload_hash([*directories, *resources])
        )
        if manifest["payload_hash"] != actual:
            raise PromotionArtifactError(
                f"{label}@{manifest['version']}: manifest payload hash differs "
                f"from candidate (expected={manifest['payload_hash']}, actual={actual}); "
                "source content or checkout policy differs from the artifact build"
            )
    except (
        ArtifactBuildError, OSError, UnicodeDecodeError, tomllib.TOMLDecodeError,
        KeyError, TypeError, ValueError,
    ) as exc:
        raise PromotionArtifactError(f"cannot validate candidate artifact sources: {exc}") from exc
