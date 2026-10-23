"""Static build-backend admission shared by plugin and standalone artifact paths."""
from __future__ import annotations

import importlib.machinery
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


class PromotionArtifactError(RuntimeError):
    pass


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
