"""Retain the authenticated first-party core from a frozen source release."""
from __future__ import annotations

import hashlib
import json
import stat
import time
import zipfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from artifact_attestation import (
    INDEX_LIMIT,
    INDEX_NAME,
    PromotionArtifactError,
    _gh,
    verify_attestation_subject,
)
from artifact_release_discovery import download, read_admitted_source_index
from core_runtime_profile import _core_manifest
from installer_dependency_config import native_retained_path
from installer_dependency_policy import _private_retained_root, _temporary_inputs
from installer_wheel_inputs import (
    WheelError,
    WheelLimits,
    _check_deadline,
    _member_chunks,
    _ordinary,
    preflight_archive,
)
from installer_wheel_snapshot import (
    _open,
    _selected_hashes,
    _unchanged,
    retain_wheel_inputs,
)
from runtime_profile_delivery import _held
from runtime_profile_release import _unique_object


@dataclass(frozen=True)
class CoreReleaseInputs:
    """Paths are private and valid only while the retaining context is open."""

    index_path: Path
    bundle_path: Path
    manifest_path: Path
    manifest_data: bytes
    wheel_dir: Path


def _unpack_core(stream, entry, root, *, deadline, max_bytes, max_wheels):
    limits = WheelLimits(
        max_archive_bytes=max_bytes, max_expanded_bytes=max_bytes,
        max_member_bytes=max_bytes, max_metadata_bytes=min(max_bytes, 16 << 20),
        max_members=max_wheels + 1, max_tags=1,
        max_metadata_member_bytes=min(max_bytes, 1 << 20),
    )
    preflight_archive(stream, limits=limits, deadline=deadline)
    with zipfile.ZipFile(stream) as archive:
        members = archive.infolist()
        names = [member.filename for member in members]
        if (
            len(names) != len(set(names)) or "manifest.json" not in names
            or sum(member.file_size for member in members) > max_bytes
            or any(
                member.orig_filename != member.filename or member.is_dir() or member.flag_bits & 1
                or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                or stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG)
                or member.external_attr & 0x10
                for member in members
            )
        ):
            raise WheelError("unsafe core release bundle members")
        manifest_data = b"".join(_member_chunks(
            stream, archive, archive.getinfo("manifest.json"),
            limit=min(max_bytes, 1 << 20), deadline=deadline,
        ))
        manifest = _core_manifest(manifest_data)
        if any(manifest.get(key) != entry.get(key) for key in ("composition", "version", "artifact_id")):
            raise WheelError("core release manifest differs from the signed index")
        rows = manifest["wheels"]
        expected = {row["filename"]: row["sha256"] for row in rows}
        if len(expected) != len(rows):
            raise WheelError("duplicate core release wheel")
        expected = _selected_hashes(expected, max_wheels, deadline)
        if set(names) != {"manifest.json", *expected}:
            raise WheelError("core release wheel set differs from its manifest")
        directory = root / "unpacked"
        directory.mkdir(mode=0o700)
        paths = {}
        for name, digest in expected.items():
            path = directory / name
            calculated = hashlib.sha256()
            with path.open("xb") as output:
                for chunk in _member_chunks(
                    stream, archive, archive.getinfo(name), limit=max_bytes, deadline=deadline,
                ):
                    calculated.update(chunk)
                    output.write(chunk)
            if "sha256:" + calculated.hexdigest() != digest:
                raise WheelError("core release wheel differs from its manifest")
            path.chmod(0o400)
            paths[name] = path
    return manifest_data, paths, expected


@contextmanager
def retain_released_core(
    *, repository: str, dev_head: str, main_head: str, content_tree: str,
    is_ancestor, deadline: float, max_bytes: int, max_wheels: int,
):
    """Authenticate and hold core inputs; caller owns source selection and admission.

    The caller supplies independent source facts and stages any output until this
    context exits successfully. This does not approve a runtime profile or publish.
    """
    _check_deadline(deadline)
    if type(max_bytes) is not int or max_bytes <= 0 or type(max_wheels) is not int or max_wheels <= 0:
        raise WheelError("core release limits must be explicit positive integers")

    def bounded_gh(argv, **kwargs):
        _check_deadline(deadline)
        kwargs["timeout"] = min(kwargs.get("timeout", 120), deadline - time.monotonic())
        return _gh(argv, **kwargs)

    with _temporary_inputs(None) as temporary:
        root = Path(native_retained_path(_private_retained_root(Path(temporary)).resolve()))
        signed = {}

        def verify_index(path, **identity):
            before = _ordinary(path)
            with _open(path) as stream:
                opened = _unchanged(path, before, stream)
                data = stream.read(INDEX_LIMIT + 1)
                if len(data) > INDEX_LIMIT:
                    raise PromotionArtifactError("source index exceeds its byte limit")
                verify_attestation_subject(path, **identity, runner=bounded_gh)
                _unchanged(path, before, stream, opened=opened)
                stream.seek(0)
                if stream.read(INDEX_LIMIT + 1) != data:
                    raise PromotionArtifactError("source index changed during attestation")
            signed["data"] = data

        admitted = read_admitted_source_index(
            root, repository=repository, dev_head=dev_head, main_before=main_head,
            content_tree=content_tree, is_ancestor=is_ancestor, runner=bounded_gh,
            verify_subject=verify_index,
        )
        if admitted is None:
            raise PromotionArtifactError("source release is unavailable")
        index, assets = admitted
        index_data = signed["data"]
        if json.loads(index_data, object_pairs_hook=_unique_object) != index:
            raise PromotionArtifactError("authenticated source index differs from discovery")
        index_digest = "sha256:" + hashlib.sha256(index_data).hexdigest()
        if (
            type(assets[INDEX_NAME].get("size")) is not int
            or assets[INDEX_NAME]["size"] != len(index_data)
            or assets[INDEX_NAME]["digest"] != index_digest
        ):
            raise PromotionArtifactError("source index size or digest differs from its asset")
        entries = [item for item in index["artifacts"] if item.get("composition") == "core"]
        if len(entries) != 1:
            raise PromotionArtifactError("source release requires exactly one core artifact")
        entry = entries[0]
        asset = assets[entry["filename"]]
        if type(asset.get("size")) is not int or not 0 < asset["size"] <= max_bytes:
            raise PromotionArtifactError("core release asset exceeds its byte limit")
        download(
            root, repository=repository, tag="python-artifacts-" + dev_head,
            name=entry["filename"], asset=asset, limit=max_bytes, runner=bounded_gh,
        )
        index_path, bundle_path = root / INDEX_NAME, root / entry["filename"]
        held_paths = {"index": index_path, "bundle": bundle_path}
        held_files = {
            "index": {"sha256": index_digest, "size": len(index_data)},
            "bundle": {"sha256": asset["digest"], "size": asset["size"]},
        }
        with _held(held_paths, held_files, deadline) as streams:
            control = index["admission"]
            verify_attestation_subject(
                bundle_path, repository=repository, signer_digest=control["signer_digest"],
                source_digest=control["source_digest"], source_ref=control["source_ref"],
                runner=bounded_gh,
            )
            try:
                manifest_data, paths, expected = _unpack_core(
                    streams["bundle"], entry, root, deadline=deadline,
                    max_bytes=max_bytes, max_wheels=max_wheels,
                )
            except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, UnicodeError) as error:
                raise WheelError("invalid core release archive") from error
            manifest_path = root / "core-manifest.json"
            manifest_path.write_bytes(manifest_data)
            manifest_path.chmod(0o400)
            manifest_files = {
                "manifest": {
                    "sha256": "sha256:" + hashlib.sha256(manifest_data).hexdigest(),
                    "size": len(manifest_data),
                },
            }
            with _held({"manifest": manifest_path}, manifest_files, deadline), retain_wheel_inputs(
                paths, expected, max_wheels=max_wheels, max_archive_bytes=max_bytes,
                max_total_bytes=max_bytes, max_snapshot_bytes=max_bytes, deadline=deadline,
            ) as wheels, _held(held_paths, held_files, deadline):
                yield CoreReleaseInputs(
                    index_path, bundle_path, manifest_path, manifest_data,
                    next(iter(wheels.paths.values())).parent,
                )
