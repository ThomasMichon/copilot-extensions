"""Read an adopted Manager application's own wheel and source association."""

from __future__ import annotations

from collections.abc import Callable
from email.parser import BytesParser
import hashlib
from importlib import import_module
import io
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from .installation_probe import strict_json_object

try:
    tomllib = import_module("tomllib")
except ModuleNotFoundError:
    tomllib = import_module("tomli")

_PACKAGE = "copilot-extensions-worktree-manager"


def inspect_application(
    slot: Path, project_bytes: bytes, read: Callable[[Path], bytes | None],
) -> tuple[bytes, list[tuple[Path, str | None]]]:
    evidence: list[tuple[Path, str | None]] = [
        (slot / "pyproject.toml", hashlib.sha256(project_bytes).hexdigest()),
    ]

    def captured(path: Path, limit: int = 4 << 20) -> bytes:
        if path.stat().st_size > limit:
            raise ValueError("Manager application input exceeds its size bound")
        content = read(path)
        if content is None or len(content) > limit:
            raise ValueError("Manager application input is unavailable or oversized")
        evidence.append((path, hashlib.sha256(content).hexdigest()))
        return content

    def document(name: str) -> dict[str, Any]:
        value = json.loads(captured(slot / name), object_pairs_hook=strict_json_object)
        if not isinstance(value, dict):
            raise ValueError("Manager application input must be an object")
        return value

    project = tomllib.loads(project_bytes.decode("utf-8"))
    metadata = project["project"]
    version = Version(metadata["version"])
    dependencies = [
        Requirement(value) for value in metadata["dependencies"]
    ]
    roots = [item for item in dependencies if canonicalize_name(item.name) == _PACKAGE]
    if len(roots) != 1:
        raise ValueError("Manager application requires exactly one Manager dependency")
    requirement = roots[0]
    pins = list(requirement.specifier)
    if (
        requirement.url is not None or requirement.marker is not None or requirement.extras
        or len(pins) != 1 or pins[0].operator != "=="
        or Version(pins[0].version) != version
    ):
        raise ValueError("Manager application dependency is not its exact package version")
    sources = project["tool"]["uv"]["sources"]
    associated = [
        row for name, row in sources.items() if canonicalize_name(name) == _PACKAGE
    ]
    if len(associated) != 1 or set(associated[0]) != {"path"}:
        raise ValueError("Manager application requires its own wheel source")
    member = associated[0]["path"]
    if not isinstance(member, str) or re.fullmatch(r"wheels/[A-Za-z0-9._+-]+\.whl", member) is None:
        raise ValueError("Manager application wheel must be an owned relative member")
    wheel = slot.joinpath(*PurePosixPath(member).parts)
    distribution, wheel_version, _build, _tags = parse_wheel_filename(wheel.name)
    if distribution != _PACKAGE or wheel_version != version:
        raise ValueError("Manager application wheel belongs to another package or version")

    declaration, source = document("application.json"), document("runtime-source.json")
    construction = declaration["construction"]
    identity = construction["identity"]
    source_version = source["runtime_version"]
    receipt = construction["wheel_inputs"]
    binding = receipt["binding"]
    lock_id = receipt["lock_id"]
    separator = "." if "+" in source["package_version"] else "+"
    if (
        declaration.get("name") != "worktree-manager"
        or declaration.get("module") != "worktree_manager.__main__:console_entry"
        or source.get("schema") != "copilot-extensions.runtime-source"
        or type(source.get("version")) is not int or source["version"] != 1
        or source.get("plugin") != "worktree-manager"
        or Version(source["package_version"]) != version
        or any(
            not isinstance(source.get(key), str) or re.fullmatch(pattern, source[key]) is None
            for key, pattern in (
                ("source_commit", r"(?:[0-9a-f]{40}|[0-9a-f]{64})"),
                ("source_tree", r"(?:[0-9a-f]{40}|[0-9a-f]{64})"),
                ("assembly_hash", r"[0-9a-f]{64}"),
            )
        )
        or source_version != (
            source["package_version"] + separator + "source." + source["source_commit"]
            + "." + source["assembly_hash"][:16]
        )
        or identity != {"runtime_source": source, "runtime_version": source_version}
        or not isinstance(lock_id, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", lock_id) is None
        or construction.get("runtime_version") != source_version + ".profile." + lock_id[7:23]
        or re.fullmatch(
            re.escape(construction["runtime_version"]) + r"(?:[.+]repair\.[0-9a-f]{32})?",
            slot.name,
        ) is None
        or binding.get("component") != "worktree-manager"
        or binding.get("release") != {
            "identity": "source:" + source["source_commit"],
            "sha256": "sha256:" + source["assembly_hash"],
        }
        or not isinstance(construction.get("local_packages"), list)
        or _PACKAGE not in construction.get("local_packages", ())
    ):
        raise ValueError("Manager application differs from its owner or source identity")
    rows = [row for row in binding["wheels"] if row.get("name") == _PACKAGE]
    if (
        len(rows) != 1 or rows[0].get("filename") != wheel.name
        or Version(rows[0]["version"]) != version
    ):
        raise ValueError("Manager wheel differs from its application source profile")
    content = captured(wheel, 128 << 20)
    if rows[0].get("sha256") != "sha256:" + hashlib.sha256(content).hexdigest():
        raise ValueError("Manager wheel differs from its application source profile")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        metadata_members = [
            item for item in archive.infolist()
            if re.fullmatch(r"[^/]+\.dist-info/METADATA", item.filename)
        ]
        defaults = [
            item for item in archive.infolist()
            if item.filename == "worktree_manager/source_config.py"
        ]
        if (
            len(metadata_members) != 1 or len(defaults) != 1
            or metadata_members[0].file_size > 1 << 20
            or defaults[0].file_size > 1 << 20
        ):
            raise ValueError("Manager wheel has no unambiguous bounded package/source metadata")
        package = BytesParser().parsebytes(archive.read(metadata_members[0]), headersonly=True)
        if (
            len(package.get_all("Name", ())) != 1
            or len(package.get_all("Version", ())) != 1
            or canonicalize_name(str(package["Name"])) != _PACKAGE
            or Version(str(package["Version"])) != version
        ):
            raise ValueError("Manager wheel metadata belongs to another package or version")
        return archive.read(defaults[0]), evidence
