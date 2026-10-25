"""First-party wheel bundles built from a promotion's composed candidate."""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import replace
from pathlib import Path
from email.message import Message
from typing import BinaryIO
from artifact_source_build import (
    PromotionArtifactError, parse_wheel_identity, validate_static_build,
    validate_runtime_metadata as validate_runtime_metadata,
)
from standalone_python_artifacts import COMPONENTS, artifact_label, project_key

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
from installer_wheel_inputs import WheelLimits, bounded_tag, inspect_archive

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
_WHEEL_TAG_LIMIT = 10_000


def _artifact_label(record: dict) -> str:
    try:
        return artifact_label(record)
    except (KeyError, TypeError, ValueError) as exc:
        raise PromotionArtifactError("invalid artifact identity") from exc


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
    for component, relative in COMPONENTS.items():
        project = root / relative / "pyproject.toml"
        if not (root / relative).exists():
            continue
        try:
            data = tomllib.loads(project.read_text(encoding="utf-8"))
            projects[f"standalone:{component}"] = data["project"]["version"]
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
            raise PromotionArtifactError(f"{project}: invalid standalone Python project") from exc
    return projects


def bundle_wheels(manifest: dict, wheel_dir: Path, out_dir: Path) -> dict:
    """Bundle only the builder's declared first-party wheels and manifest."""
    plugin, version = _artifact_label(manifest), manifest["version"]
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
                wheel["role"] not in (
                    ("standalone", "vendored-lib") if "component" in manifest
                    else ("plugin", "vendored-lib")
                )
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
        **{key: manifest[key] for key in ("plugin", "component") if key in manifest},
        "version": version, "artifact_id": artifact_id,
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
    from standalone_python_artifacts import build_standalone_artifacts, source_closure, validate_build
    for plugin in projects:
        if plugin.startswith("standalone:"):
            component = plugin.split(":", 1)[1]
            directory, dependencies, _resources = source_closure(component, root, builder)
            validate_build(directory, component=component, root=root)
            for _name, path in dependencies:
                validate_static_build(path)
            continue
        directory = builder.PLUGINS_DIR / plugin
        for source in [directory, *(path for _name, path in builder.resolve_vendored_libs(directory))]:
            from plugin_runtime_resources import build_inputs
            build_inputs(source, root=root)
    entries = []
    with tempfile.TemporaryDirectory(prefix="promotion-build-toolchain-") as temporary:
        toolchain = resolve_toolchain_lock(
            Path(temporary) / "venv", python=sys.executable,
            source_policy=source_policy, min_age_days=min_age_days,
        )
        for plugin, version in projects.items():
            standalone = plugin.startswith("standalone:")
            component = plugin.split(":", 1)[1] if standalone else None
            wheel_dir = out_dir / (f"standalone-{component}" if standalone else plugin)
            manifest = (
                build_standalone_artifacts(
                    component, out_dir=wheel_dir, toolchain=toolchain, builder=builder,
                ) if standalone else builder.build_plugin_artifacts(
                    plugin, out_dir=wheel_dir, toolchain=toolchain,
                )
            )
            if project_key(manifest) != plugin or manifest["version"] != version:
                raise PromotionArtifactError(f"{plugin}: builder returned another project identity")
            if manifest["build_toolchain"] != toolchain.record:
                raise PromotionArtifactError(f"{plugin}: builder did not use the trusted shared toolchain")
            entries.append(bundle_wheels(manifest, wheel_dir, out_dir))
            shutil.rmtree(wheel_dir)
    (out_dir / _BUNDLES_NAME).write_text(json.dumps(entries), encoding="utf-8")


def export_artifacts(
    *, repo: Path, commit: str, content_tree: str, dev_head: str, main_before: str,
    out_dir: Path, source_policy: str = "governed", min_age_days: int | None = None,
    reuse_repository: str | None = None,
) -> dict:
    """Build in a disposable candidate checkout; never mutate the promoted tree."""
    import promote_release as promotion

    if out_dir.exists():
        raise PromotionArtifactError(f"{out_dir}: export destination already exists")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="python-artifacts-", dir=out_dir.parent))
    scratch = None
    source_checkout = None
    try:
        scratch = promotion.add_scratch_worktree(commit, repo=repo)
        source_checkout = tempfile.TemporaryDirectory(prefix="promotion-artifact-source-")
        candidate = Path(source_checkout.name).resolve() / "inputs" / "source"
        candidate.mkdir(parents=True)
        _run(
            ["git", f"--attr-source={content_tree}", "checkout-index", "--all",
             f"--prefix={candidate}{os.sep}"], cwd=scratch,
        )
        projects = candidate_projects(candidate)
        if reuse_repository is not None:
            from reuse_promotion_artifacts import reuse_artifacts

            reused = reuse_artifacts(
                staging, repository=reuse_repository, dev_head=dev_head, main_before=main_before,
                content_tree=content_tree, candidate_root=candidate, repo=repo,
                source_policy=source_policy, min_age_days=min_age_days,
            )
            if reused is not None:
                staging.rename(out_dir)
                return reused
        argv = [
            sys.executable, str(Path(__file__).resolve()),
            "--repo", str(candidate), "--out-dir", str(staging), "--toolchain-source", source_policy,
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
            "schema": SCHEMA, "schema_version": 2,
            "dev_head": dev_head,
            "content_tree": content_tree, "artifacts": entries,
        }
        if reuse_repository is not None:
            signer, source, ref = (
                os.environ.get("GITHUB_WORKFLOW_SHA"), os.environ.get("GITHUB_SHA"),
                os.environ.get("GITHUB_REF"),
            )
            if (
                not isinstance(signer, str) or not _SHA.fullmatch(signer)
                or source != signer or ref != "refs/heads/main"
                or not promotion._is_ancestor(signer, main_before, cwd=repo)
            ):
                raise PromotionArtifactError("source artifact admission requires pinned trusted Main identity")
            index["admission"] = {
                "signer_digest": signer, "source_digest": source, "source_ref": ref,
            }
        (staging / INDEX_NAME).write_text(
            json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        validate_artifacts(
            staging, content_tree=content_tree, dev_head=dev_head, main_before=main_before,
            projects=projects,
            candidate_root=candidate,
            source_policy=source_policy, min_age_days=min_age_days,
        )
        staging.rename(out_dir)
        return index
    finally:
        if source_checkout is not None:
            source_checkout.cleanup()
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
                or manifest["schema_version"] != (4 if "component" in entry else 3)
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
            for wheel in wheels:
                if not isinstance(wheel, dict):
                    raise PromotionArtifactError(f"{path.name}: invalid wheel record")
                name, digest, role = wheel.get("filename"), wheel.get("sha256"), wheel.get("role")
                if (
                    role not in (primary_role, "vendored-lib")
                    or not isinstance(name, str) or "/" in name or "\\" in name
                    or not name.endswith(".whl") or name in declared
                    or not isinstance(digest, str) or not digest.startswith("sha256:")
                    or not _DIGEST.fullmatch(digest[7:])
                ):
                    raise PromotionArtifactError(f"{path.name}: invalid first-party wheel record")
                declared.add(name)
                plugin_wheels += role == primary_role
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
                    if f"sha256:{digest.hexdigest()}" != wheel["sha256"]:
                        raise PromotionArtifactError(f"{path.name}: wheel digest mismatch")
                    archive.seek(0)
                    validate_wheel(
                        archive, wheel,
                        source_dir=candidate_root / wheel["source"] if candidate_root is not None else None,
                        component=entry.get("component") if wheel["role"] == primary_role else None,
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
    if any(type(value) is not int or value <= 0 for value in (
        max_unpacked_bytes, max_metadata_bytes, max_archive_bytes,
    )):
        raise PromotionArtifactError("wheel inspection requires positive byte limits")
    if not record_algorithms or not record_algorithms <= {"sha256", "sha384", "sha512"}:
        raise PromotionArtifactError("unsupported wheel RECORD digest policy")
    info = parse_wheel_identity(Path(wheel["filename"]))
    prefix = f"{info['name']}-{info['version']}.dist-info/"
    try:
        deadline = time.monotonic() + 120 if deadline is None else deadline
        limits = limits or WheelLimits(
            max_archive_bytes=max_archive_bytes, max_expanded_bytes=max_unpacked_bytes,
            max_member_bytes=max_unpacked_bytes,
            max_metadata_bytes=max(max_archive_bytes, 3 * max_metadata_bytes),
            max_members=100_000, max_tags=_WHEEL_TAG_LIMIT,
            max_metadata_member_bytes=max_metadata_bytes,
        )
        facts = inspect_archive(
            archive, limits=replace(
                limits,
                max_archive_bytes=min(limits.max_archive_bytes, max_archive_bytes),
                max_expanded_bytes=min(limits.max_expanded_bytes, max_unpacked_bytes),
                max_member_bytes=min(limits.max_member_bytes, max_unpacked_bytes),
                max_metadata_member_bytes=min(limits.max_metadata_member_bytes, max_metadata_bytes),
            ), deadline=deadline, record_algorithms=record_algorithms,
        )
        wheel_metadata, package_metadata = facts.wheel_metadata, facts.metadata
        canonical_tags, _ = bounded_tag(
            "-".join(info[key] for key in ("python_tag", "abi_tag", "platform_tag")),
            limit=limits.max_tags, deadline=deadline,
        )
        python_tags, abi_tags, platform_tags = canonical_tags.split("-")
        expected_tags = {
            f"{python}-{abi}-{platform}"
            for python in python_tags.split(".")
            for abi in abi_tags.split(".")
            for platform in platform_tags.split(".")
        }
        version_matches = facts.version == info["version"]
        if expected_generator is None:
            from packaging.version import Version
            version_matches = Version(facts.version) == Version(info["version"])
        if (
            facts.prefix != prefix
            or (
                expected_generator is not None
                and wheel_metadata.get_all("Generator") != [expected_generator]
            )
            or set(facts.tags) != expected_tags
            or re.sub(r"[-_.]+", "_", facts.name).lower()
            != re.sub(r"[-_.]+", "_", info["name"]).lower()
            or not version_matches
        ):
            raise PromotionArtifactError("wheel metadata differs from declared source/toolchain")
        if source_dir is not None:
            with zipfile.ZipFile(archive) as bundle:
                validate_wheel_sources(
                    bundle, facts.member_hashes, source_dir=source_dir, prefix=prefix,
                    metadata=package_metadata,
                    component=component, candidate_root=candidate_root,
                )
        return package_metadata, facts.unpacked_size
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
            required_python.update(injected)
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
        primary_role = "standalone" if standalone else "plugin"
        source_root = COMPONENTS[manifest["component"]] if standalone else f"plugins/{label}"
        if standalone:
            from standalone_consumers import standalone_build_resources
            expected_resources = [
                path.as_posix() for path in standalone_build_resources(
                    source_root, source_root=Path("."),
                )
            ]
            if manifest.get("build_resources") != expected_resources:
                raise PromotionArtifactError("invalid manifest build resource closure")
        for wheel in manifest["wheels"]:
            info = parse_wheel_filename(Path(wheel["filename"]))
            source = wheel.get("source")
            if wheel["role"] == primary_role:
                _assert_version_corresponds(
                    raw_version=manifest["version"], wheel_version=info["version"],
                    label=label,
                )
            source_matches = (
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
                or wheel["role"] not in (primary_role, "vendored-lib")
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
    label = _artifact_label(manifest)
    standalone = "component" in manifest
    plugin_dir = root / COMPONENTS[manifest["component"]] if standalone else builder.PLUGINS_DIR / label
    try:
        if plugin_dir.is_symlink() or builder.uer._find_symlink(plugin_dir) is not None:
            raise PromotionArtifactError("artifact source tree contains a symlink")
        resources = ()
        if standalone:
            from standalone_python_artifacts import source_closure
            plugin_dir, libs, resources = source_closure(manifest["component"], root, builder)
            if manifest.get("build_resources") != [
                path.relative_to(root).as_posix() for path in resources
            ]:
                raise PromotionArtifactError("manifest build resource closure differs from candidate")
        else:
            libs = builder.resolve_vendored_libs(plugin_dir)
            from plugin_runtime_resources import build_inputs
            build_inputs(plugin_dir, root=root)
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
        actual = builder.compute_payload_hash([*directories, *resources])
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


def validate_composed_artifacts(
    directory: Path, *, scratch: Path, content_tree: str,
    dev_head: str, main_before: str,
    source_policy: str | None = None, min_age_days: int | None = None,
) -> dict:
    """Validate a composed index through its canonical checkout representation."""
    if not isinstance(content_tree, str) or not _SHA.fullmatch(content_tree):
        raise PromotionArtifactError("invalid composed source tree identity")
    actual_tree = _run(["git", "write-tree"], cwd=scratch).strip()
    if actual_tree != content_tree:
        raise PromotionArtifactError(
            f"composed source index changed (expected={content_tree}, actual={actual_tree}); "
            "recompose the candidate before validating artifacts"
        )
    with tempfile.TemporaryDirectory(prefix="promotion-artifact-validation-") as temporary:
        candidate = Path(temporary).resolve()
        _run(
            ["git", f"--attr-source={content_tree}", "checkout-index", "--all",
             f"--prefix={candidate}{os.sep}"], cwd=scratch,
        )
        return validate_artifacts(
            directory, content_tree=content_tree,
            dev_head=dev_head, main_before=main_before, projects=candidate_projects(candidate),
            candidate_root=candidate,
            source_policy=source_policy, min_age_days=min_age_days,
        )


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
        return validate_composed_artifacts(
            directory, scratch=scratch, content_tree=content_tree,
            dev_head=dev_head, main_before=main_before,
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
            or type(index.get("schema_version")) is not int or index["schema_version"] not in (1, 2)
        ):
            raise PromotionArtifactError("unsupported promotion artifact index")
        bindings = [("dev_head", dev_head), ("content_tree", content_tree)]
        if index["schema_version"] == 1:
            bindings.append(("main_before", main_before))
        elif "main_before" in index:
            raise PromotionArtifactError("source-owned artifact index must not bind a main container")
        for field, expected in bindings:
            if not isinstance(expected, str) or not _SHA.fullmatch(expected) or index.get(field) != expected:
                raise PromotionArtifactError(f"promotion artifact {field} mismatch")
        entries = index.get("artifacts")
        if not isinstance(entries, list):
            raise PromotionArtifactError("artifact entries must be a list")
        names, plugins = {INDEX_NAME}, set()
        shared_toolchain = None
        profile_manifests = {}
        for entry in entries:
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
            key = project_key(entry)
            if entry.get("filename") != name or name in names or key in plugins:
                raise PromotionArtifactError("duplicate or mismatched artifact filename")
            names.add(name)
            plugins.add(key)
            path = directory / name
            if path.is_symlink() or not path.is_file() or _digest(path) != digest:
                raise PromotionArtifactError(f"{name}: artifact digest mismatch")
            manifest = validate_bundle(path, entry, candidate_root=candidate_root)
            if "runtime_profiles" in index:
                with zipfile.ZipFile(path) as bundle:
                    manifest_bytes = bundle.read("manifest.json")
                if json.loads(manifest_bytes) != manifest or _digest(path) != digest:
                    raise PromotionArtifactError("runtime-profile bundle changed during validation")
                profile_manifests[identity] = (
                    manifest, "sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
                )
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
        if "runtime_profiles" in index:
            from runtime_profile import ProfileError
            from runtime_profile_release import validate_release_bindings

            try:
                validate_release_bindings(index, profile_manifests)
            except ProfileError as exc:
                raise PromotionArtifactError(f"invalid release runtime profiles: {exc}") from exc
        if {path.name for path in directory.iterdir()} != names:
            raise PromotionArtifactError("unexpected files in promotion artifact directory")
        if projects is not None and {
            project_key(entry): entry["version"] for entry in entries
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
