"""Static build-backend admission shared by plugin and standalone artifact paths."""
from __future__ import annotations

import importlib.machinery
import configparser
import re
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from email.message import Message
from pathlib import Path, PurePosixPath
from typing import Any
import zipfile

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


class PromotionArtifactError(RuntimeError):
    pass


def payload_hash_directories(
    directories: Sequence[Path], *, root: Path, directory_hash: Callable[[Path], str],
    hash_fields: Callable[..., str], workers: int = 1,
) -> str:
    """Hash the same directory identities with bounded, fully settled workers."""
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("payload hashing requires between one and four workers")

    def item(directory: Path) -> tuple[str, str]:
        return directory.resolve().relative_to(root).as_posix(), directory_hash(directory)

    if workers == 1 or len(directories) < 2:
        pairs = [item(directory) for directory in directories]
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(workers, len(directories))) as executor:
            pairs = list(executor.map(item, directories))
    return "sha256:" + hash_fields(*(field for pair in sorted(pairs) for field in pair))


def read_build_system_requires(
    source_dir: Path, *, error_type: type[Exception] = PromotionArtifactError,
) -> list[str]:
    """Read static build constraints without importing or executing a backend."""
    pyproject = source_dir / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise error_type(f"{pyproject}: could not read/parse: {exc}") from exc
    build_system = data.get("build-system", {})
    if not isinstance(build_system, dict):
        raise error_type(f"{pyproject}: [build-system] is not a table")
    requires = build_system.get("requires", [])
    if not isinstance(requires, list) or not all(isinstance(r, str) for r in requires):
        raise error_type(f"{pyproject}: [build-system].requires is not a list of strings")
    return requires


def read_project_version(
    project_dir: Path, *, error_type: type[Exception] = PromotionArtifactError,
) -> str:
    """Read the raw declared version, preserving its release-tag spelling."""
    pyproject = project_dir / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise error_type(f"{pyproject}: could not read/parse: {exc}") from exc
    project_table = data.get("project", {})
    if not isinstance(project_table, dict):
        raise error_type(f"{pyproject}: [project] is not a table")
    version = project_table.get("version")
    if not isinstance(version, str) or not version:
        raise error_type(
            f"{pyproject}: [project].version is missing or not a string"
        )
    return version


def parse_wheel_identity(
    path: Path, *, error_type: type[Exception] = PromotionArtifactError,
    numeric_version: bool = False,
) -> dict[str, str]:
    """Parse PEP 427's name/version/build/tag tokens without importing a builder.

    Distribution names occupy one hyphen-delimited token. Reading compatibility
    tags from the right preserves optional build tags without accidentally
    accepting an unnormalized, hyphenated distribution name as a version.
    """
    if not path.name.endswith(".whl"):
        raise error_type(f"{path}: not a well-formed wheel filename")
    tokens = path.name[: -len(".whl")].split("-")
    if len(tokens) < 5:
        raise error_type(f"{path}: not a well-formed wheel filename")
    platform_tag, abi_tag, python_tag = tokens[-1], tokens[-2], tokens[-3]
    name = tokens[0]
    middle = tokens[1:-3]
    if len(middle) == 1:
        version = middle[0]
    elif len(middle) == 2 and re.match(r"^[0-9][^-]*$", middle[1]):
        version = middle[0]
    else:
        raise error_type(
            f"{path}: not a well-formed wheel filename (ambiguous "
            "distribution/version/build-tag split)"
        )
    pattern = r"^[0-9]" if numeric_version else r"^[vV]?[0-9]"
    if not name or not re.match(pattern, version):
        raise error_type(f"{path}: not a well-formed wheel filename")
    return {
        "name": name,
        "version": version,
        "python_tag": python_tag,
        "abi_tag": abi_tag,
        "platform_tag": platform_tag,
    }


def validate_runtime_metadata(project: dict[str, Any], metadata: Message) -> None:
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

    def requirement_key(value: str, extra: str | None = None) -> tuple[Any, ...]:
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


class _ExactEntryPointParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def validate_entry_points(
    project: dict[str, Any], bundle: zipfile.ZipFile, hashes: Mapping[str, str], prefix: str,
) -> None:
    """Keep wheel entry-point groups and case-sensitive names equal to source metadata."""
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
        parser = _ExactEntryPointParser(interpolation=None)
        parser.read_string(bundle.read(entries_name).decode("utf-8"))
        actual_entries = {section: dict(parser[section]) for section in parser.sections()}
    if actual_entries != expected_entries:
        raise PromotionArtifactError("wheel entry points differ from candidate source")


def data_file_sources(source_dir: Path, settings: dict[str, Any]) -> dict[str, Path]:
    """Bind explicit share-data destinations to regular, unlinked project files."""
    declarations = settings.get("data-files", {})
    if not isinstance(declarations, dict):
        raise PromotionArtifactError("invalid static data-files declarations")
    if not declarations:
        return {}
    from build_python_artifacts import _PAYLOAD_IGNORE_DIR_NAMES, _PAYLOAD_IGNORE_SUFFIXES

    def relative(value: object) -> PurePosixPath:
        if not isinstance(value, str) or not value or any(
            character in value for character in "\\:*?[]"
        ) or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise PromotionArtifactError("data-files require explicit canonical paths")
        path = PurePosixPath(value)
        if (
            path.is_absolute() or not path.parts or path.as_posix() != value
            or any(part in (".", "..") or part.endswith((".", " ")) for part in path.parts)
        ):
            raise PromotionArtifactError("data-files require explicit canonical paths")
        return path

    result: dict[str, Path] = {}
    destinations: set[str] = set()
    directories: set[str] = set()
    root = source_dir.resolve()
    for destination, files in declarations.items():
        target = relative(destination)
        if target.parts[0] != "share" or not isinstance(files, list) or not files:
            raise PromotionArtifactError("unsupported static share-data layout")
        for value in files:
            declared = relative(value)
            if (
                ".test-runs" in declared.parts
                or any(part in _PAYLOAD_IGNORE_DIR_NAMES for part in declared.parts[:-1])
                or any(part.endswith(".egg-info") for part in declared.parts)
                or declared.suffix in _PAYLOAD_IGNORE_SUFFIXES
            ):
                raise PromotionArtifactError("data-file source is outside the hashed source closure")
            if declared.name.casefold().endswith(".pth"):
                raise PromotionArtifactError("data-files cannot install Python startup hooks")
            source = source_dir.joinpath(*declared.parts)
            try:
                valid = (
                    source.resolve(strict=True) == root.joinpath(*declared.parts)
                    and source.is_file() and not source.is_symlink()
                )
            except (OSError, RuntimeError):
                valid = False
            if not valid:
                raise PromotionArtifactError("data-file source is missing, linked or outside its project")
            installed = target / declared.name
            name = installed.as_posix()
            folded = name.casefold()
            parents = {parent.as_posix().casefold() for parent in installed.parents}
            if folded in destinations or folded in directories or parents & destinations:
                raise PromotionArtifactError("ambiguous static data-file destination")
            destinations.add(folded)
            directories.update(parents)
            result[name] = source
    return result


def validate_static_build(
    source_dir: Path, *, trusted_setup: bytes | None = None,
) -> dict[str, Any]:
    """Authenticate the setuptools contract before building or source mapping."""
    try:
        data: dict[str, Any] = tomllib.loads(
            (source_dir / "pyproject.toml").read_text(encoding="utf-8"),
        )
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
    from native_daemon_artifacts import trusted_setup as native_setup

    native = native_setup(source_dir, data)
    if native is not None:
        if trusted_setup is not None and trusted_setup != native:
            raise PromotionArtifactError("conflicting trusted process owner setup")
        trusted_setup = native
    for name in ("setup.py", "setup.cfg"):
        if (source_dir / name).exists() or (source_dir / name).is_symlink():
            if (
                name == "setup.py" and trusted_setup is not None
                and not (source_dir / name).is_symlink()
                # Git archives retain LF even when the trusted working copy uses CRLF.
                and (source_dir / name).read_bytes().replace(b"\r\n", b"\n")
                == trusted_setup.replace(b"\r\n", b"\n")
            ):
                continue
            raise PromotionArtifactError(f"unsupported executable or alternate build configuration: {name}")
    if trusted_setup is not None and not (source_dir / "setup.py").is_file():
        raise PromotionArtifactError("registered standalone build configuration is missing")
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
    data_file_sources(source_dir, settings)
    return data
