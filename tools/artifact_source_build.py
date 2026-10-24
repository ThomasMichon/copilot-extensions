"""Static build-backend admission shared by plugin and standalone artifact paths."""
from __future__ import annotations

import importlib.machinery
import re
import sys
from collections import Counter
from email.message import Message
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


class PromotionArtifactError(RuntimeError):
    pass


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


def validate_static_build(source_dir: Path, *, trusted_setup: bytes | None = None) -> dict:
    """Authenticate the setuptools contract before building or source mapping."""
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
    from native_daemon_artifacts import trusted_setup as native_setup

    native = native_setup(source_dir, data)
    if native is not None:
        if trusted_setup is not None and trusted_setup != native:
            raise PromotionArtifactError("conflicting trusted native build ownership")
        trusted_setup = native
    for name in ("setup.py", "setup.cfg"):
        if (source_dir / name).exists() or (source_dir / name).is_symlink():
            if (
                name == "setup.py" and trusted_setup is not None
                and not (source_dir / name).is_symlink()
                and (source_dir / name).read_bytes() == trusted_setup
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
    return data
