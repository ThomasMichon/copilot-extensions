"""Pure, local wheel-bundle integrity and revision-pinning contract.

This descriptor is not a signature or evidence that a supplied commit is an
approved main release. A trusted controller must supply expected_source_commit.
No wheels are extracted/imported, installed, fetched or executed. Third-party
dependencies, platform admission and store/queue migration safety are not
verified. Verification uses only the explicitly listed wheels, never unlisted
siblings. Callers must keep verified inputs immutable until later consumption.
The initial service contract requires an unconditional base core dependency and
an extra == "native" dependency on its store/server extras. All declarations
referencing bundled distributions are checked regardless of platform markers.
Resource policy: 1 MiB metadata/RECORD, 64 MiB per member, 256 MiB total
uncompressed data, 512 MiB archive bytes and 10,000 ZIP entries per wheel.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import stat
import zipfile
import zlib
from dataclasses import dataclass
from email import policy
from email.parser import Parser
from pathlib import Path
from typing import Any, BinaryIO

from packaging.requirements import InvalidRequirement, Requirement
from packaging.tags import parse_tag
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

_SCHEMA = "agent-index-service.release"
_DISTRIBUTIONS = frozenset({
    "agent-index-service", "agent-index", "agent-zdd", "agent-procutil", "agent-dropin-registry",
})
_FIELDS = {
    "schema", "schema_version", "source_commit", "service_version", "core_version",
    "config_schema_version", "artifacts",
}
_ARTIFACT_FIELDS = {"distribution", "version", "file", "sha256"}
_LIMIT = 1024 * 1024
_MEMBER_LIMIT = 64 * 1024 * 1024
_TOTAL_LIMIT = 256 * 1024 * 1024
_ARCHIVE_LIMIT = 512 * 1024 * 1024
_ENTRY_LIMIT = 10000
_REPEATED_HEADERS = {
    "requires-dist", "provides-extra", "classifier", "project-url", "requires-external",
    "platform", "supported-platform", "dynamic", "license-file", "tag",
    "provides-dist", "obsoletes-dist", "import-name", "import-namespace",
}


class ReleaseError(ValueError):
    """The bundle or pinned descriptor violates the release contract."""


@dataclass(frozen=True)
class _Wheel:
    artifact: dict[str, str]
    requirements: tuple[Requirement, ...]
    extras: frozenset[str]


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ReleaseError(f"{label} must be a nonempty trimmed string")
    if any(ord(char) < 32 for char in value):
        raise ReleaseError(f"{label} contains control characters")
    return value


def _commit(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ReleaseError("source_commit must be exactly 40 lowercase hexadecimal characters")
    return value


def _version(value: Any, label: str) -> str:
    try:
        return str(Version(_text(value, label)))
    except InvalidVersion as exc:
        raise ReleaseError(f"{label} is not a valid PEP 440 version") from exc


def _keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ReleaseError(f"{label} must contain exactly: {', '.join(sorted(expected))}")
    return value


def _filename(value: Any) -> str:
    name = _text(value, "artifact file")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+!-]*\.whl", name) is None:
        raise ReleaseError("wheel references must be plain relative wheel filenames")
    return name


def _stat(path: Path, *, directory: bool = False) -> os.stat_result:
    info = path.lstat()
    reparse = getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    if stat.S_ISLNK(info.st_mode) or reparse:
        raise ReleaseError(f"symlinks/reparse points are not allowed: {path.name}")
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ReleaseError(f"expected {'directory' if directory else 'regular file'}: {path.name}")
    return info


def _root(path: Path) -> Path:
    _stat(path, directory=True)
    return path.resolve(strict=True)


def _unchanged(before: os.stat_result, after: os.stat_result) -> None:
    def identity(info: os.stat_result) -> tuple[int, int, int, int]:
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    if identity(before) != identity(after):
        raise ReleaseError("release input changed while being read")


def _message(archive: zipfile.ZipFile, entry: zipfile.ZipInfo):
    if entry.file_size > _LIMIT:
        raise ReleaseError("wheel metadata exceeds the 1 MiB limit")
    with archive.open(entry) as stream:
        text = stream.read(_LIMIT + 1)
    if len(text) > _LIMIT:
        raise ReleaseError("wheel metadata exceeds the 1 MiB limit")
    message = Parser(policy=policy.default).parsestr(text.decode("utf-8"))
    if message.defects or message.is_multipart():
        raise ReleaseError("malformed wheel metadata headers")
    seen: set[str] = set()
    for key in message.keys():
        lowered = key.lower()
        if lowered in seen and lowered not in _REPEATED_HEADERS:
            raise ReleaseError(f"duplicate wheel metadata header: {key}")
        seen.add(lowered)
    return message


def _member_path(name: str, *, directory: bool = False) -> None:
    path = name[:-1] if directory and name.endswith("/") else name
    if (
        not path or "\\" in path or ":" in path
        or any(ord(char) < 32 for char in path)
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ReleaseError(f"unsafe wheel member path: {name!r}")


def _record(archive: zipfile.ZipFile, entries: list[zipfile.ZipInfo], directory: str) -> None:
    """Validate wheel RECORD per PyPA, with explicit verifier resource limits.

    Regular files require hashes of sha256 strength or better and sizes.
    Legacy RECORD.jws/p7s in this dist-info are excluded, not authenticated.
    Directory ZIP entries carry no payload and need not appear in RECORD.
    """
    record_name = f"{directory}/RECORD"
    records = [entry for entry in entries if entry.filename.rsplit("/", 1)[-1] == "RECORD"]
    if len(records) != 1 or records[0].filename != record_name:
        raise ReleaseError("wheel requires exactly one RECORD in its dist-info directory")
    if len(entries) > _ENTRY_LIMIT or sum(entry.file_size for entry in entries) > _TOTAL_LIMIT:
        raise ReleaseError("wheel exceeds member count or total decompression limit")
    files = {}
    for entry in entries:
        _member_path(entry.filename, directory=entry.is_dir())
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if mode not in {0, stat.S_IFDIR if entry.is_dir() else stat.S_IFREG}:
            raise ReleaseError("wheel members must be regular files or directories")
        if entry.file_size > _MEMBER_LIMIT or (entry.is_dir() and entry.file_size):
            raise ReleaseError("wheel exceeds member decompression limit")
        if not entry.is_dir():
            files[entry.filename] = entry
    if records[0].file_size > _LIMIT:
        raise ReleaseError("RECORD exceeds the 1 MiB limit")
    with archive.open(records[0]) as stream:
        data = stream.read(_LIMIT + 1)
    if len(data) > _LIMIT:
        raise ReleaseError("RECORD exceeds the 1 MiB limit")
    rows = {}
    try:
        for row in csv.reader(io.StringIO(data.decode("utf-8"), newline=""), strict=True):
            if len(row) != 3:
                raise ReleaseError("RECORD rows must have exactly three fields")
            name, digest, size = row
            _member_path(name)
            if name in rows:
                raise ReleaseError("duplicate RECORD row")
            rows[name] = (digest, size)
    except csv.Error as exc:
        raise ReleaseError(f"invalid RECORD CSV: {exc}") from exc
    signatures = {f"{directory}/RECORD.jws", f"{directory}/RECORD.p7s"}
    if set(rows) != set(files) - signatures:
        raise ReleaseError("RECORD inventory does not match wheel files")
    total = 0
    for name, entry in files.items():
        digest, size = rows.get(name, ("", ""))
        if size and (
            re.fullmatch(r"[0-9]+", size) is None
            or (size.lstrip("0") or "0") != str(entry.file_size)
        ):
            raise ReleaseError(f"RECORD size mismatch: {name}")
        hasher = None
        expected = ""
        if name == record_name:
            if digest or size:
                raise ReleaseError("RECORD must leave its own hash and size empty")
        elif name not in signatures:
            if not size or "=" not in digest:
                raise ReleaseError(f"RECORD hash and size required: {name}")
            algorithm, expected = digest.split("=", 1)
            try:
                hasher = hashlib.new(algorithm)
            except ValueError as exc:
                raise ReleaseError("unsupported RECORD hash algorithm") from exc
            if hasher.name in {"md5", "sha1", "md5-sha1"} or hasher.digest_size < 32:
                raise ReleaseError("RECORD requires sha256 or stronger fixed-length hashes")
            if re.fullmatch(r"[A-Za-z0-9_-]+", expected) is None:
                raise ReleaseError("RECORD digest must be URL-safe base64 without padding")
        count = 0
        with archive.open(entry) as stream:
            while block := stream.read(256 * 1024):
                count += len(block)
                total += len(block)
                if count > _MEMBER_LIMIT or total > _TOTAL_LIMIT:
                    raise ReleaseError("wheel exceeds streaming decompression limit")
                if hasher is not None:
                    hasher.update(block)
        if count != entry.file_size:
            raise ReleaseError(f"actual wheel member size mismatch: {name}")
        if hasher is not None and (
            base64.urlsafe_b64encode(hasher.digest()).rstrip(b"=").decode("ascii") != expected
        ):
            raise ReleaseError(f"RECORD digest mismatch: {name}")


def _metadata(stream: BinaryIO, filename: str, digest: str) -> _Wheel:
    try:
        project, file_version, build, tags = parse_wheel_filename(filename)
    except InvalidWheelFilename as exc:
        raise ReleaseError(f"invalid wheel filename: {filename}") from exc
    with zipfile.ZipFile(stream) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)):
            raise ReleaseError("duplicate members in wheel archive")
        metadata = [entry for entry in entries if entry.filename.rsplit("/", 1)[-1] == "METADATA"]
        wheel = [entry for entry in entries if entry.filename.rsplit("/", 1)[-1] == "WHEEL"]
        if len(metadata) != 1 or len(wheel) != 1:
            raise ReleaseError("wheel must contain exactly one METADATA and one WHEEL record")
        directory = metadata[0].filename.removesuffix("/METADATA")
        if (
            not directory.endswith(".dist-info") or "/" in directory or "\\" in directory
            or wheel[0].filename != f"{directory}/WHEEL"
        ):
            raise ReleaseError("wheel metadata must share one top-level dist-info directory")
        info_name, separator, info_version = directory.removesuffix(".dist-info").rpartition("-")
        if (
            not separator or canonicalize_name(info_name) != project
            or _version(info_version, "dist-info version") != str(file_version)
        ):
            raise ReleaseError("dist-info identity does not match wheel filename")
        _record(archive, entries, directory)
        message = _message(archive, metadata[0])
        wheel_message = _message(archive, wheel[0])
    name = canonicalize_name(_text(message.get("Name"), "metadata Name"))
    version = _version(message.get("Version"), "metadata Version")
    if name not in _DISTRIBUTIONS:
        raise ReleaseError(f"unknown bundled distribution: {name}")
    if name != project or version != str(file_version):
        raise ReleaseError("wheel filename/project/version identity mismatch")
    if message.get("Metadata-Version") not in {"2.1", "2.2", "2.3", "2.4", "2.5", "2.6"}:
        raise ReleaseError("unsupported or missing Metadata-Version")
    if (
        wheel_message.get("Wheel-Version") != "1.0"
        or wheel_message.get("Root-Is-Purelib") not in {"true", "false"}
        or wheel_message.get_payload().strip()
    ):
        raise ReleaseError("unsupported or malformed WHEEL metadata")
    declared_tags = wheel_message.get_all("Tag", [])
    try:
        actual_tags = set()
        for tag in declared_tags:
            parsed = parse_tag(tag)
            if actual_tags & parsed:
                raise ReleaseError("duplicate WHEEL tags")
            actual_tags.update(parsed)
    except ValueError as exc:
        raise ReleaseError("invalid WHEEL tags") from exc
    if not declared_tags or actual_tags != set(tags):
        raise ReleaseError("WHEEL tags do not match wheel filename")
    declared_build = wheel_message.get("Build")
    if declared_build is not None and (
        not build or declared_build != f"{build[0]}{build[1]}"
    ):
        raise ReleaseError("WHEEL build does not match wheel filename")
    requirements: list[Requirement] = []
    seen_requirements: set[Requirement] = set()
    for raw in message.get_all("Requires-Dist", []):
        try:
            requirement = Requirement(raw)
        except InvalidRequirement as exc:
            raise ReleaseError("invalid Requires-Dist metadata") from exc
        if requirement in seen_requirements:
            raise ReleaseError("duplicate Requires-Dist declaration")
        seen_requirements.add(requirement)
        requirements.append(requirement)
    extras = message.get_all("Provides-Extra", [])
    if any(re.fullmatch(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*", extra) is None for extra in extras):
        raise ReleaseError("invalid Provides-Extra metadata")
    normalized_extras = frozenset(canonicalize_name(extra) for extra in extras)
    if len(normalized_extras) != len(extras):
        raise ReleaseError("duplicate Provides-Extra declaration")
    return _Wheel(
        {"distribution": name, "version": version, "file": filename, "sha256": digest},
        tuple(requirements), normalized_extras,
    )


def _wheel(root: Path, filename: str) -> _Wheel:
    path = root / _filename(filename)
    before = _stat(path)
    if before.st_size > _ARCHIVE_LIMIT:
        raise ReleaseError("wheel exceeds the 512 MiB archive limit")
    if path.resolve(strict=True).parent != root:
        raise ReleaseError("wheel path escapes descriptor directory")
    with path.open("rb") as stream:
        _unchanged(before, os.fstat(stream.fileno()))
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(256 * 1024), b""):
            digest.update(block)
        stream.seek(0)
        result = _metadata(stream, filename, digest.hexdigest())
        _unchanged(before, os.fstat(stream.fileno()))
    _unchanged(before, _stat(path))
    return result


def _dependencies(wheels: dict[str, _Wheel]) -> None:
    if set(wheels) != _DISTRIBUTIONS:
        raise ReleaseError("bundle must contain exactly the five required distributions")
    for wheel in wheels.values():
        for requirement in wheel.requirements:
            target = canonicalize_name(requirement.name)
            if target not in wheels:
                continue  # Third-party dependencies are not bundled or resolved here.
            if requirement.url:
                raise ReleaseError("bundled dependencies must not use direct URLs")
            if not requirement.specifier.contains(
                wheels[target].artifact["version"], prereleases=True,
            ):
                raise ReleaseError(f"incompatible bundled requirement: {requirement}")
            needed_extras = {canonicalize_name(extra) for extra in requirement.extras}
            if not needed_extras <= wheels[target].extras:
                raise ReleaseError(
                    f"bundled dependency does not provide required extras: {requirement}"
                )
    service = wheels["agent-index-service"]
    core = wheels["agent-index"]
    core_requirements = [
        req for req in service.requirements if canonicalize_name(req.name) == "agent-index"
    ]
    if not any(req.marker is None and not req.extras for req in core_requirements):
        raise ReleaseError("service must declare a base agent-index requirement")
    if "native" not in service.extras or not any(
        req.marker is not None and str(req.marker) == 'extra == "native"'
        and {"store", "server"} <= {canonicalize_name(extra) for extra in req.extras}
        for req in core_requirements
    ):
        raise ReleaseError("service must declare a native agent-index[store,server] requirement")
    unconditional = {
        canonicalize_name(req.name) for req in core.requirements if req.marker is None
    }
    if not {"agent-zdd", "agent-procutil", "agent-dropin-registry"} <= unconditional:
        raise ReleaseError("core must declare the bundled shared-library requirements")


def _assemble(wheels: list[_Wheel], commit: str) -> dict[str, Any]:
    by_name: dict[str, _Wheel] = {}
    for wheel in wheels:
        name = wheel.artifact["distribution"]
        if name in by_name:
            raise ReleaseError(f"duplicate bundled distribution: {name}")
        by_name[name] = wheel
    _dependencies(by_name)
    return {
        "schema": _SCHEMA, "schema_version": 1, "source_commit": commit,
        "service_version": by_name["agent-index-service"].artifact["version"],
        "core_version": by_name["agent-index"].artifact["version"],
        "config_schema_version": 1,
        "artifacts": [by_name[name].artifact for name in sorted(by_name)],
    }


def build_descriptor(bundle: Path, *, source_commit: str) -> dict:
    """Read five wheels and return normalized schema 1 without writing files.

    source_commit is supplied provenance, not an attestation of release approval.
    All immediate .whl artifacts participate; third-party wheels are rejected.
    """
    commit = _commit(source_commit)
    try:
        root = _root(bundle)
        filenames = sorted(path.name for path in root.iterdir() if path.suffix.lower() == ".whl")
        return _assemble([_wheel(root, name) for name in filenames], commit)
    except (
        OSError, UnicodeError, zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error,
    ) as exc:
        raise ReleaseError(f"cannot read release bundle: {exc}") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseError(f"duplicate descriptor JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ReleaseError(f"invalid JSON constant: {value}")


def verify_descriptor(path: Path, *, expected_source_commit: str) -> dict:
    """Verify pinned revision, exact schema, wheel bytes/metadata and requirements.

    expected_source_commit must come from the trusted release controller. Hashes
    do not authenticate the descriptor or prove main-release or migration safety.
    """
    expected = _commit(expected_source_commit)
    try:
        before = _stat(path)
        with path.open("rb") as stream:
            _unchanged(before, os.fstat(stream.fileno()))
            text = stream.read(_LIMIT + 1)
            _unchanged(before, os.fstat(stream.fileno()))
        _unchanged(before, _stat(path))
        if len(text) > _LIMIT:
            raise ReleaseError("descriptor exceeds the 1 MiB limit")
        descriptor = _keys(json.loads(
            text.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        ), _FIELDS, "descriptor")
        if descriptor["schema"] != _SCHEMA or any(
            type(descriptor[key]) is not int or descriptor[key] != 1
            for key in ("schema_version", "config_schema_version")
        ):
            raise ReleaseError("unsupported descriptor/config schema")
        if _commit(descriptor["source_commit"]) != expected:
            raise ReleaseError("descriptor source_commit does not match trusted revision")
        artifacts = descriptor["artifacts"]
        if not isinstance(artifacts, list) or len(artifacts) != len(_DISTRIBUTIONS):
            raise ReleaseError("descriptor requires exactly five artifacts")
        root = _root(path.parent)
        wheels: list[_Wheel] = []
        for raw in artifacts:
            artifact = _keys(raw, _ARTIFACT_FIELDS, "artifact")
            if _text(artifact["distribution"], "artifact distribution") not in _DISTRIBUTIONS:
                raise ReleaseError("unexpected artifact distribution")
            version = _version(artifact["version"], "artifact version")
            digest = artifact["sha256"]
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ReleaseError("sha256 must be exactly 64 lowercase hexadecimal characters")
            wheel = _wheel(root, _filename(artifact["file"]))
            if wheel.artifact != {**artifact, "version": version}:
                raise ReleaseError("artifact digest or wheel identity does not match descriptor")
            wheels.append(wheel)
        result = _assemble(wheels, expected)
        if (
            _version(descriptor["service_version"], "service_version") != result["service_version"]
            or _version(descriptor["core_version"], "core_version") != result["core_version"]
        ):
            raise ReleaseError("descriptor service/core versions do not match wheels")
        return result
    except (
        OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile,
        RuntimeError, NotImplementedError, zlib.error,
    ) as exc:
        raise ReleaseError(f"cannot verify release descriptor: {exc}") from exc
