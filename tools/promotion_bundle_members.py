"""Canonical members of the existing first-party promotion bundle."""
from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

from artifact_source_build import PromotionArtifactError


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
