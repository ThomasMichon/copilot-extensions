"""First-party wheel bundles built from a promotion's composed candidate."""
from __future__ import annotations

import argparse
import base64
import csv
import configparser
import hashlib
import importlib.machinery
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from email import policy
from email.parser import Parser
from email.message import Message
from typing import BinaryIO

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 uses the existing tooling backport.
    import tomli as tomllib

SCHEMA = "copilot-extensions.promotion-python-artifacts"
INDEX_NAME = "python-artifacts.json"
_BUNDLES_NAME = "wheel-bundles.json"
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_PLUGIN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_VERSION = re.compile(r"[0-9][A-Za-z0-9.+-]*")
_MANIFEST_LIMIT = 1 << 20
_BUNDLE_LIMIT = 512 << 20


class PromotionArtifactError(RuntimeError):
    pass


def _run(argv: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise PromotionArtifactError(
            f"{argv[0]} failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def candidate_projects(root: Path) -> dict[str, str]:
    projects = {}
    for project in sorted((root / "plugins").glob("*/pyproject.toml")):
        try:
            data = tomllib.loads(project.read_text(encoding="utf-8"))
            projects[project.parent.name] = data["project"]["version"]
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
            raise PromotionArtifactError(f"{project}: invalid Python project") from exc
    return projects


def bundle_wheels(manifest: dict, wheel_dir: Path, out_dir: Path) -> dict:
    """Bundle only the builder's declared first-party wheels and manifest."""
    plugin, version = manifest["plugin"], manifest["version"]
    artifact_id = manifest["artifact_id"]
    if (
        not isinstance(plugin, str) or not _PLUGIN.fullmatch(plugin)
        or not isinstance(version, str) or not _VERSION.fullmatch(version)
        or not isinstance(artifact_id, str)
        or not artifact_id.startswith("sha256:")
        or not _DIGEST.fullmatch(artifact_id[7:])
    ):
        raise PromotionArtifactError("invalid artifact bundle identity")
    filename = f"{plugin}-{version}-{artifact_id[7:]}.zip"
    destination = out_dir / filename
    names = {"manifest.json"}

    def member(name: str) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        return info

    wheels = sorted(manifest["wheels"], key=lambda wheel: wheel["filename"])
    canonical_manifest = {**manifest, "wheels": wheels}
    # Wheels are already compressed; storing them avoids zlib-version drift too.
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr(member("manifest.json"), json.dumps(canonical_manifest, sort_keys=True) + "\n")
        for wheel in wheels:
            name = wheel["filename"]
            if (
                wheel["role"] not in ("plugin", "vendored-lib")
                or not isinstance(name, str) or Path(name).name != name
                or "/" in name or "\\" in name or not name.endswith(".whl")
                or name in names
            ):
                raise PromotionArtifactError("invalid or duplicate first-party wheel entry")
            names.add(name)
            path = wheel_dir / name
            try:
                if path.is_symlink() or f"sha256:{_digest(path)}" != wheel["sha256"]:
                    raise PromotionArtifactError(f"{name}: wheel digest changed before bundling")
                with path.open("rb") as source, bundle.open(member(name), "w") as target:
                    shutil.copyfileobj(source, target, length=1 << 20)
            except OSError as exc:
                raise PromotionArtifactError(f"{name}: cannot read wheel for bundling: {exc}") from exc
    return {
        "plugin": plugin, "version": version, "artifact_id": artifact_id,
        "filename": filename, "sha256": _digest(destination),
    }


def build_candidate_bundles(
    root: Path, out_dir: Path, *, source_policy: str, min_age_days: int | None,
) -> None:
    """Trusted driver builds candidate data using exactly one shared lock."""
    import promote_release as promotion
    from build_toolchain_lock import resolve_toolchain_lock

    builder = promotion._load_module(
        Path(__file__).with_name("build_python_artifacts.py"), "trusted_promotion_builder",
    )
    builder.REPO, builder.PLUGINS_DIR, builder.LIBS_DIR = root, root / "plugins", root / "libs"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        if any(out_dir.iterdir()):
            raise PromotionArtifactError("candidate bundle directory must be empty")
    except OSError as exc:
        raise PromotionArtifactError(f"{out_dir}: cannot prepare candidate bundle directory: {exc}") from exc
    projects = candidate_projects(root)
    for plugin in projects:
        directory = builder.PLUGINS_DIR / plugin
        for source in [directory, *(path for _name, path in builder.resolve_vendored_libs(directory))]:
            validate_static_build(source)
    entries = []
    with tempfile.TemporaryDirectory(prefix="promotion-build-toolchain-") as temporary:
        toolchain = resolve_toolchain_lock(
            Path(temporary) / "venv", python=sys.executable,
            source_policy=source_policy, min_age_days=min_age_days,
        )
        for plugin, version in projects.items():
            wheel_dir = out_dir / plugin
            manifest = builder.build_plugin_artifacts(plugin, out_dir=wheel_dir, toolchain=toolchain)
            if manifest["plugin"] != plugin or manifest["version"] != version:
                raise PromotionArtifactError(f"{plugin}: builder returned another project identity")
            if manifest["build_toolchain"] != toolchain.record:
                raise PromotionArtifactError(f"{plugin}: builder did not use the trusted shared toolchain")
            entries.append(bundle_wheels(manifest, wheel_dir, out_dir))
            shutil.rmtree(wheel_dir)
    (out_dir / _BUNDLES_NAME).write_text(json.dumps(entries), encoding="utf-8")


def export_artifacts(
    *, repo: Path, commit: str, content_tree: str, dev_head: str, main_before: str,
    out_dir: Path, source_policy: str = "governed", min_age_days: int | None = None,
) -> dict:
    """Build in a disposable candidate checkout; never mutate the promoted tree."""
    import promote_release as promotion

    if out_dir.exists():
        raise PromotionArtifactError(f"{out_dir}: export destination already exists")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="python-artifacts-", dir=out_dir.parent))
    scratch = None
    try:
        scratch = promotion.add_scratch_worktree(commit, repo=repo)
        projects = candidate_projects(scratch)
        argv = [
            sys.executable, str(Path(__file__).resolve()),
            "--repo", str(scratch), "--out-dir", str(staging), "--toolchain-source", source_policy,
        ]
        if min_age_days is not None:
            argv += ["--toolchain-min-age-days", str(min_age_days)]
        _run(argv, cwd=Path(__file__).resolve().parent)
        bundles_path = staging / _BUNDLES_NAME
        try:
            entries = json.loads(bundles_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PromotionArtifactError("could not read candidate bundle records") from exc
        bundles_path.unlink()
        index = {
            "schema": SCHEMA, "schema_version": 1,
            "dev_head": dev_head, "main_before": main_before,
            "content_tree": content_tree, "artifacts": entries,
        }
        (staging / INDEX_NAME).write_text(
            json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        validate_artifacts(
            staging, content_tree=content_tree, dev_head=dev_head, main_before=main_before,
            projects=projects,
            candidate_root=scratch,
            source_policy=source_policy, min_age_days=min_age_days,
        )
        staging.rename(out_dir)
        return index
    finally:
        if scratch is not None:
            promotion.remove_scratch_worktree(scratch, repo=repo)
        if staging.exists():
            shutil.rmtree(staging)


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
                or manifest["schema_version"] != 3
                or any(manifest.get(key) != entry[key] for key in ("plugin", "version", "artifact_id"))
            ):
                raise PromotionArtifactError(f"{path.name}: artifact manifest identity mismatch")
            wheels = manifest.get("wheels")
            if not isinstance(wheels, list) or not wheels:
                raise PromotionArtifactError(f"{path.name}: missing first-party wheel records")
            declared = {"manifest.json"}
            plugin_wheels = 0
            for wheel in wheels:
                if not isinstance(wheel, dict):
                    raise PromotionArtifactError(f"{path.name}: invalid wheel record")
                name, digest, role = wheel.get("filename"), wheel.get("sha256"), wheel.get("role")
                if (
                    role not in ("plugin", "vendored-lib")
                    or not isinstance(name, str) or "/" in name or "\\" in name
                    or not name.endswith(".whl") or name in declared
                    or not isinstance(digest, str) or not digest.startswith("sha256:")
                    or not _DIGEST.fullmatch(digest[7:])
                ):
                    raise PromotionArtifactError(f"{path.name}: invalid first-party wheel record")
                declared.add(name)
                plugin_wheels += role == "plugin"
            if plugin_wheels != 1 or set(names) != declared:
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
                    archive.seek(0)
                    validate_wheel(
                        archive, wheel,
                        source_dir=candidate_root / wheel["source"] if candidate_root is not None else None,
                    )
                if f"sha256:{digest.hexdigest()}" != wheel["sha256"]:
                    raise PromotionArtifactError(f"{path.name}: wheel digest mismatch")
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
) -> None:
    """Validate wheel bytes, metadata and RECORD without extracting package code."""
    from build_python_artifacts import parse_wheel_filename

    info = parse_wheel_filename(Path(wheel["filename"]))
    prefix = f"{info['name']}-{info['version']}.dist-info/"
    metadata_names = {prefix + name for name in ("WHEEL", "METADATA", "RECORD")}
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            names = [member.filename for member in members]
            if (
                len(names) != len(set(names))
                or sum(member.file_size for member in members) > _BUNDLE_LIMIT
                or any(
                    member.flag_bits & 1 or "\\" in member.orig_filename
                    or "\0" in member.orig_filename or member.filename.startswith("/")
                    or ":" in member.filename
                    or any(part in ("", ".", "..") for part in member.filename.rstrip("/").split("/"))
                    or stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or (
                        any(part.endswith(".dist-info") for part in member.filename.rstrip("/").split("/"))
                        and not member.filename.startswith(prefix)
                    )
                    for member in members
                )
                or not metadata_names.issubset(names)
                or any(bundle.getinfo(name).file_size > _MANIFEST_LIMIT for name in metadata_names)
            ):
                raise PromotionArtifactError("unsafe or incomplete wheel members")
            headers = {
                name: Parser(policy=policy.default).parsestr(
                    bundle.read(prefix + name).decode("utf-8"),
                ) for name in ("WHEEL", "METADATA")
            }
            wheel_metadata, package_metadata = headers["WHEEL"], headers["METADATA"]
            expected_tags = {
                f"{python}-{abi}-{platform}"
                for python in info["python_tag"].split(".")
                for abi in info["abi_tag"].split(".")
                for platform in info["platform_tag"].split(".")
            }
            tags = wheel_metadata.get_all("Tag", [])
            names_header = package_metadata.get_all("Name", [])
            metadata_versions = package_metadata.get_all("Metadata-Version", [])
            if (
                any(header.defects for header in headers.values())
                or wheel_metadata.get_all("Wheel-Version") != ["1.0"]
                or wheel_metadata.get_all("Root-Is-Purelib") not in (["true"], ["false"])
                or wheel_metadata.get_all("Generator") != [wheel["generator"]]
                or len(tags) != len(set(tags)) or set(tags) != expected_tags
                or len(names_header) != 1
                or len(metadata_versions) != 1
                or not re.fullmatch(r"2\.[0-9]+", str(metadata_versions[0]))
                or re.sub(r"[-_.]+", "_", str(names_header[0])).lower() != info["name"].lower()
                or package_metadata.get_all("Version") != [info["version"]]
            ):
                raise PromotionArtifactError("wheel metadata differs from declared source/toolchain")
            rows = list(csv.reader(io.StringIO(bundle.read(prefix + "RECORD").decode("utf-8"))))
            if (
                any(len(row) != 3 for row in rows)
                or len(rows) != len({row[0] for row in rows})
                or {row[0] for row in rows} != {member.filename for member in members if not member.is_dir()}
            ):
                raise PromotionArtifactError("wheel RECORD member set mismatch")
            verified_hashes = {}
            for name, recorded_hash, recorded_size in rows:
                if name == prefix + "RECORD":
                    if recorded_hash or recorded_size:
                        raise PromotionArtifactError("wheel RECORD must not hash itself")
                    continue
                digest = hashlib.sha256()
                with bundle.open(name) as stream:
                    for chunk in iter(lambda: stream.read(1 << 20), b""):
                        digest.update(chunk)
                encoded = base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode("ascii")
                if recorded_hash != f"sha256={encoded}" or recorded_size != str(bundle.getinfo(name).file_size):
                    raise PromotionArtifactError("wheel RECORD digest or size mismatch")
                verified_hashes[name] = digest.hexdigest()
            if source_dir is not None:
                validate_wheel_sources(
                    bundle, verified_hashes, source_dir=source_dir, prefix=prefix,
                    metadata=package_metadata,
                )
    except (
        OSError, UnicodeDecodeError, zipfile.BadZipFile, RuntimeError, NotImplementedError,
        csv.Error,
    ) as exc:
        if isinstance(exc, PromotionArtifactError):
            raise
        raise PromotionArtifactError(f"{wheel['filename']}: invalid wheel archive: {exc}") from exc


def validate_static_build(source_dir: Path) -> dict:
    """Authenticate the static setuptools contract before building or source mapping."""
    try:
        data = tomllib.loads((source_dir / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PromotionArtifactError(f"{source_dir}: cannot read static build configuration: {exc}") from exc
    build = data.get("build-system")
    if (
        not isinstance(build, dict) or set(build) != {"requires", "build-backend"}
        or build["build-backend"] != "setuptools.build_meta"
        or not isinstance(build["requires"], list) or not build["requires"]
        or any(not isinstance(value, str) or not value for value in build["requires"])
    ):
        raise PromotionArtifactError("unsupported candidate build backend or backend-path")
    for name in ("setup.py", "setup.cfg"):
        if (source_dir / name).exists() or (source_dir / name).is_symlink():
            raise PromotionArtifactError(f"unsupported executable or alternate build configuration: {name}")
    reserved = sys.stdlib_module_names | {"setuptools", "wheel", "packaging", "sitecustomize", "usercustomize"}
    suffixes = ["", *importlib.machinery.all_suffixes()]
    for name in sorted(reserved):
        if any(
            path.exists() or path.is_symlink()
            for path in (source_dir / (name + suffix) for suffix in suffixes)
        ):
            raise PromotionArtifactError(f"candidate source shadows trusted build tooling: {name}")
    tool = data.get("tool", {})
    settings = tool.get("setuptools", {}) if isinstance(tool, dict) else None
    project = data.get("project")
    if (
        not isinstance(settings, dict)
        or not isinstance(project, dict)
        or any(key in settings for key in ("cmdclass", "dynamic", "ext-modules"))
        or project.get("dynamic")
    ):
        raise PromotionArtifactError("unsupported dynamic or custom setuptools build configuration")
    return data


def validate_wheel_sources(
    bundle: zipfile.ZipFile, hashes: dict[str, str], *, source_dir: Path, prefix: str,
    metadata: Message,
) -> None:
    """Bind static setuptools payload bytes and generated entry points to source data."""
    try:
        data = validate_static_build(source_dir)
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
        if settings.get("package-dir", {"": str(base.relative_to(source_dir))}) != {
            "": str(base.relative_to(source_dir)),
        }:
            raise PromotionArtifactError("unsupported wheel source package-dir mapping")
        payload = {name for name in hashes if not name.startswith(prefix)}
        if {name for name in payload if name.endswith(".py")} != required_python:
            raise PromotionArtifactError("wheel Python member set differs from candidate source")
        for name in payload:
            path = base / name
            if (
                not path.resolve().is_relative_to(source_dir.resolve())
                or not path.is_file() or path.is_symlink() or _digest(path) != hashes[name]
            ):
                raise PromotionArtifactError(f"{name}: wheel payload differs from candidate source")
        package_data = settings.get("package-data", {})
        if not isinstance(package_data, dict) or settings.get("exclude-package-data") or settings.get("data-files"):
            raise PromotionArtifactError("invalid wheel package-data configuration")
        required_data: set[str] = set()
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


def validate_runtime_metadata(project: dict, metadata: Message) -> None:
    """Compare runtime declarations using the standard PEP 440/508 parser."""
    try:
        from packaging.markers import Marker
        from packaging.requirements import Requirement
        from packaging.specifiers import SpecifierSet
        from packaging.utils import canonicalize_name
    except ImportError as exc:
        raise PromotionArtifactError(
            "runtime metadata validation requires packaging; use tools/artifact_validation_requirements.txt"
        ) from exc
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list) or any(not isinstance(field, str) for field in dynamic):
        raise PromotionArtifactError("invalid candidate dynamic metadata declarations")
    if set(dynamic) & {"dependencies", "optional-dependencies", "requires-python"}:
        raise PromotionArtifactError("dynamic runtime metadata has no trusted static source binding")
    dependencies = project.get("dependencies", [])
    optional = project.get("optional-dependencies", {})
    if not isinstance(dependencies, list) or not isinstance(optional, dict) or any(
        not isinstance(extra, str) or not isinstance(values, list) for extra, values in optional.items()
    ):
        raise PromotionArtifactError("invalid candidate runtime dependency declarations")

    def requirement_key(value: str, extra: str | None = None) -> tuple:
        if not isinstance(value, str):
            raise PromotionArtifactError("runtime requirements must be strings")
        requirement = Requirement(value)
        if requirement.url is not None:
            raise PromotionArtifactError("direct runtime URLs cannot bypass governed-feed declarations")
        if extra is not None:
            marker = f"({requirement.marker}) and " if requirement.marker is not None else ""
            requirement.marker = Marker(marker + f"extra == {extra!r}")
        return (
            canonicalize_name(requirement.name, validate=True),
            tuple(sorted(canonicalize_name(name, validate=True) for name in requirement.extras)),
            requirement.specifier,
            str(requirement.marker) if requirement.marker is not None else None,
        )

    extras = [canonicalize_name(extra, validate=True) for extra in optional]
    if len(extras) != len(set(extras)):
        raise PromotionArtifactError("ambiguous normalized candidate extras")
    if Counter(canonicalize_name(value, validate=True) for value in metadata.get_all("Provides-Extra", [])) != Counter(extras):
        raise PromotionArtifactError("wheel extras differ from candidate runtime declarations")
    expected = Counter(requirement_key(value) for value in dependencies)
    for extra, values in optional.items():
        expected.update(
            requirement_key(value, canonicalize_name(extra, validate=True)) for value in values
        )
    if Counter(requirement_key(value) for value in metadata.get_all("Requires-Dist", [])) != expected:
        raise PromotionArtifactError("wheel runtime dependencies differ from candidate")
    python = project.get("requires-python")
    actual_python = metadata.get_all("Requires-Python", [])
    if python is None:
        if actual_python:
            raise PromotionArtifactError("wheel adds an undeclared Python requirement")
    elif (
        not isinstance(python, str) or len(actual_python) != 1
        or SpecifierSet(actual_python[0]) != SpecifierSet(python)
    ):
        raise PromotionArtifactError("wheel Python requirement differs from candidate")


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
        for wheel in manifest["wheels"]:
            info = parse_wheel_filename(Path(wheel["filename"]))
            source = wheel.get("source")
            if wheel["role"] == "plugin":
                _assert_version_corresponds(
                    raw_version=manifest["version"], wheel_version=info["version"],
                    label=manifest["plugin"],
                )
            source_matches = (
                source == f"plugins/{manifest['plugin']}" if wheel["role"] == "plugin"
                else isinstance(source, str) and source.startswith(("libs/", f"plugins/{manifest['plugin']}/"))
            )
            if (
                wheel.get("name") != info["name"]
                or any(wheel.get(tag) != info[tag] for tag in ("python_tag", "abi_tag", "platform_tag"))
                or wheel.get("generator") != toolchain.generator
                or not isinstance(source, str) or "\\" in source
                or any(part in ("", ".", "..") for part in source.split("/"))
                or not source_matches
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
    except ArtifactBuildError as exc:
        raise PromotionArtifactError(f"invalid manifest provenance: {exc}") from exc


def validate_source_binding(manifest: dict, root: Path) -> None:
    """Hash the trusted-discovered closure in candidate data, never import its code."""
    import promote_release as promotion
    from build_toolchain_lock import ArtifactBuildError

    builder = promotion._load_module(
        Path(__file__).parent / "build_python_artifacts.py", "trusted_artifact_sources",
    )
    builder.REPO = root
    builder.PLUGINS_DIR = root / "plugins"
    builder.LIBS_DIR = root / "libs"
    plugin_dir = builder.PLUGINS_DIR / manifest["plugin"]
    try:
        if plugin_dir.is_symlink() or builder.uer._find_symlink(plugin_dir) is not None:
            raise PromotionArtifactError("artifact source tree contains a symlink")
        libs = builder.resolve_vendored_libs(plugin_dir)
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
        if manifest["payload_hash"] != builder.compute_payload_hash(directories):
            raise PromotionArtifactError("manifest payload hash differs from candidate")
    except (
        ArtifactBuildError, OSError, UnicodeDecodeError, tomllib.TOMLDecodeError,
        KeyError, TypeError, ValueError,
    ) as exc:
        raise PromotionArtifactError(f"cannot validate candidate artifact sources: {exc}") from exc


def validate_candidate_artifacts(
    directory: Path, *, repo: Path, dev_head: str, main_before: str,
    source_policy: str | None = None, min_age_days: int | None = None,
) -> dict:
    """Recompose with trusted tools; candidate checkouts supply only source data."""
    import promote_release as promotion

    if not _SHA.fullmatch(dev_head) or not _SHA.fullmatch(main_before):
        raise PromotionArtifactError("invalid expected candidate commit identity")
    scratch = promotion.add_scratch_worktree(dev_head, repo=repo)
    try:
        state = promotion._read_pipeline_state(main_before, repo=repo)
        summary = promotion.consume_pending_changes(
            scratch, main_head=main_before, repo=repo,
            last_dev_head=(state.get("last_promotion") or {}).get("dev_head"),
            trusted_source=Path(__file__).resolve().parent.parent,
        )
        content_tree = promotion.build_promotion_tree(
            scratch, control=summary.get("control_preserved"),
        )
        # A fresh destination applies checkout attributes even to stat-clean generated files.
        with tempfile.TemporaryDirectory(prefix="promotion-artifact-validation-") as temporary:
            candidate = Path(temporary).resolve()
            promotion._git(
                ["checkout-index", "--all", f"--prefix={candidate}{os.sep}"], cwd=scratch,
            )
            return validate_artifacts(
                directory, content_tree=content_tree,
                dev_head=dev_head, main_before=main_before, projects=candidate_projects(candidate),
                candidate_root=candidate,
                source_policy=source_policy, min_age_days=min_age_days,
            )
    except promotion.PromotionError as exc:
        raise PromotionArtifactError(f"cannot compose trusted artifact sources: {exc}") from exc
    finally:
        promotion.remove_scratch_worktree(scratch, repo=repo)


def validate_artifacts(
    directory: Path, *, content_tree: str, dev_head: str, main_before: str,
    projects: dict[str, str] | None = None,
    candidate_root: Path | None = None,
    source_policy: str | None = None, min_age_days: int | None = None,
) -> dict:
    """Validate transfer and exact composition binding, not signer authenticity."""
    try:
        index_path = directory / INDEX_NAME
        if index_path.is_symlink():
            raise PromotionArtifactError("artifact index must not be a symlink")
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(index, dict) or (
            index.get("schema") != SCHEMA
            or type(index.get("schema_version")) is not int or index["schema_version"] != 1
        ):
            raise PromotionArtifactError("unsupported promotion artifact index")
        for field, expected in (
            ("dev_head", dev_head), ("main_before", main_before),
            ("content_tree", content_tree),
        ):
            if not isinstance(expected, str) or not _SHA.fullmatch(expected) or index.get(field) != expected:
                raise PromotionArtifactError(f"promotion artifact {field} mismatch")
        entries = index.get("artifacts")
        if not isinstance(entries, list):
            raise PromotionArtifactError("artifact entries must be a list")
        names, plugins = {INDEX_NAME}, set()
        shared_toolchain = None
        for entry in entries:
            if not isinstance(entry, dict):
                raise PromotionArtifactError("invalid artifact entry")
            plugin, version = entry.get("plugin"), entry.get("version")
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
            if entry.get("filename") != name or name in names or plugin in plugins:
                raise PromotionArtifactError("duplicate or mismatched artifact filename")
            names.add(name)
            plugins.add(plugin)
            path = directory / name
            if path.is_symlink() or not path.is_file() or _digest(path) != digest:
                raise PromotionArtifactError(f"{name}: artifact digest mismatch")
            manifest = validate_bundle(path, entry, candidate_root=candidate_root)
            toolchain = manifest["build_toolchain"]
            if shared_toolchain is not None and toolchain != shared_toolchain:
                raise PromotionArtifactError("artifact sets do not share one build-toolchain lock")
            shared_toolchain = toolchain
            provenance = toolchain["provenance"]
            if source_policy is not None and provenance["source_policy"] != source_policy:
                raise PromotionArtifactError("artifact toolchain source policy differs from required policy")
            if source_policy == "seasoned-public":
                from seasoned_public_feed import DEFAULT_MIN_AGE_DAYS
                floor = DEFAULT_MIN_AGE_DAYS if min_age_days is None else min_age_days
                if provenance["min_age_days"] != floor:
                    raise PromotionArtifactError("artifact toolchain seasoning differs from required policy")
        if {path.name for path in directory.iterdir()} != names:
            raise PromotionArtifactError("unexpected files in promotion artifact directory")
        if projects is not None and {
            entry["plugin"]: entry["version"] for entry in entries
        } != projects:
            raise PromotionArtifactError("artifact project set differs from promotion candidate")
        return index
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionArtifactError(f"cannot read promotion artifacts: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", nargs="?", choices=("build", "validate"), default="build")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--dev-head")
    parser.add_argument("--main-before")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument(
        "--toolchain-source", choices=("governed", "seasoned-public"), default="governed",
    )
    parser.add_argument("--toolchain-min-age-days", type=int, default=None)
    args = parser.parse_args(argv)
    if args.operation == "validate":
        if not args.dev_head or not args.main_before:
            parser.error("validate requires --dev-head and --main-before")
        try:
            index_path = args.out_dir / INDEX_NAME
            if index_path.is_symlink():
                raise PromotionArtifactError("artifact index must not be a symlink")
            index = json.loads(index_path.read_text(encoding="utf-8"))
            if not isinstance(index, dict):
                raise PromotionArtifactError("invalid artifact index")
            validate_candidate_artifacts(
                args.out_dir, repo=args.repo, dev_head=args.dev_head, main_before=args.main_before,
                source_policy=args.toolchain_source, min_age_days=args.toolchain_min_age_days,
            )
        except (PromotionArtifactError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            print(f"promotion-python-artifacts: FAILED: {exc}", file=sys.stderr)
            return 1
        return 0
    from build_toolchain_lock import ArtifactBuildError
    try:
        build_candidate_bundles(
            args.repo, args.out_dir,
            source_policy=args.toolchain_source, min_age_days=args.toolchain_min_age_days,
        )
    except (PromotionArtifactError, ArtifactBuildError) as exc:
        print(f"promotion-python-artifacts: FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
