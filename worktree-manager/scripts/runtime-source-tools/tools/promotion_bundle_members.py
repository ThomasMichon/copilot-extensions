"""Canonical project roster and members of the existing first-party promotion bundle."""
from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

from artifact_source_build import PromotionArtifactError


def candidate_projects(root: Path) -> dict[str, str]:
    from application_sources import declared_projects, tomllib
    from standalone_python_artifacts import COMPONENTS
    from core_python_artifacts import enabled, release_version

    try:
        locations = {
            path.parent.name: path.parent for path in (root / "plugins").glob("*/pyproject.toml")
        }
        locations.update(declared_projects(root))
        locations = dict(sorted(locations.items()))
        locations.update({
            "standalone:" + component: root / relative
            for component, relative in COMPONENTS.items() if (root / relative).exists()
        })
        result = {}
        for name, directory in locations.items():
            project = directory / "pyproject.toml"
            try:
                data = tomllib.loads(project.read_text(encoding="utf-8"))
                result[name] = data["project"]["version"]
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
                kind = "standalone Python project" if name.startswith("standalone:") else "Python project"
                raise ValueError(f"{project}: invalid {kind}") from exc
        if enabled(root):
            result["composition:core"] = release_version(root)
        return result
    except (OSError, ValueError) as exc:
        raise PromotionArtifactError(f"invalid repository Python project declarations: {exc}") from exc


def _bundle_manifest(manifest: dict) -> bytes:
    wheels = sorted(manifest["wheels"], key=lambda wheel: wheel["filename"])
    return (json.dumps({**manifest, "wheels": wheels}, sort_keys=True) + "\n").encode("utf-8")


def _write_bundle_members(bundle, manifest: dict, write_wheel) -> None:
    names = {"manifest.json"}

    def member(name: str) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        return info

    bundle.writestr(member("manifest.json"), _bundle_manifest(manifest))
    wheels = sorted(manifest["wheels"], key=lambda wheel: wheel["filename"])
    for wheel in wheels:
        name = wheel["filename"]
        if (
            wheel["role"] not in (
                ("plugin", "standalone", "vendored-lib") if "composition" in manifest else
                ("standalone", "vendored-lib") if "component" in manifest
                else ("plugin", "vendored-lib")
            )
            or not isinstance(name, str) or Path(name).name != name
            or "/" in name or "\\" in name or not name.endswith(".whl")
            or name in names
        ):
            raise PromotionArtifactError("invalid or duplicate first-party wheel entry")
        names.add(name)
        write_wheel(wheel, member(name))
