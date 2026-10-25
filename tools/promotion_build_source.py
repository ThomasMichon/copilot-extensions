"""Retain original build inputs as data, never as a qualified landing proposal."""
from __future__ import annotations

import hashlib
import re

from promotion_complete_source import canonical_bytes, tree_archive, verify_tree_archive
from promotion_unit_contract import MAX_PACKET_FILE_BYTES, decode_json

SCHEMA = "copilot-extensions.promotion-build-source"
IDENTITY_FIELDS = {
    "dev_sha", "candidate_sha", "main_before", "control_sha", "content_tree", "tree",
    "source_index_sha256",
}


def validate_identity(identity: dict) -> None:
    if not isinstance(identity, dict) or set(identity) != IDENTITY_FIELDS or any(
        not isinstance(value, str) or not re.fullmatch(
            r"[0-9a-f]{64}" if key == "source_index_sha256" else r"[0-9a-f]{40}", value,
        ) for key, value in identity.items()
    ):
        raise ValueError("invalid original build-source identity")


def build_identity(index_raw: bytes, *, candidate_sha: str, tree: str) -> dict:
    index = decode_json(index_raw)
    admission = index.get("admission") if isinstance(index, dict) else None
    if (
        not isinstance(index, dict) or type(index.get("schema_version")) is not int
        or index["schema_version"] != 2 or not isinstance(admission, dict)
        or admission.get("source_ref") != "refs/heads/main"
        or admission.get("source_digest") != admission.get("signer_digest")
    ):
        raise ValueError("original build source requires a trusted Main source index")
    identity = {
        "dev_sha": index.get("dev_head"), "candidate_sha": candidate_sha,
        "main_before": index.get("main_before"), "control_sha": admission.get("signer_digest"),
        "content_tree": index.get("content_tree"), "tree": tree,
        "source_index_sha256": hashlib.sha256(index_raw).hexdigest(),
    }
    validate_identity(identity)
    return identity


def asset_name(kind: str, identity: dict, digest: str) -> str:
    validate_identity(identity)
    if kind not in ("build-source", "build-manifest") or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid build-source asset identity")
    extension = "zip" if kind == "build-source" else "json"
    return f"promotion-{kind}-{identity['dev_sha']}-{identity['candidate_sha']}-sha256-{digest}.{extension}"


def build_archive(files: dict[str, tuple[str, bytes]], *, identity: dict) -> tuple[bytes, dict]:
    validate_identity(identity)
    raw, inventory = tree_archive(files, tree=identity["tree"])
    manifest = {
        "schema": SCHEMA, "version": 1, **identity,
        "archive_sha256": hashlib.sha256(raw).hexdigest(), "files": inventory,
    }
    if len(canonical_bytes(manifest)) > MAX_PACKET_FILE_BYTES:
        raise ValueError("build-source manifest exceeds its byte bound")
    return raw, manifest


def verify_build_archive(raw: bytes, manifest: dict, *, identity: dict) -> dict:
    validate_identity(identity)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != IDENTITY_FIELDS | {"schema", "version", "archive_sha256", "files"}
        or manifest["schema"] != SCHEMA or type(manifest["version"]) is not int or manifest["version"] != 1
        or any(manifest[key] != value for key, value in identity.items())
        or not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != manifest["archive_sha256"]
        or len(canonical_bytes(manifest)) > MAX_PACKET_FILE_BYTES
    ):
        raise ValueError("build-source manifest differs from the original build identity")
    return verify_tree_archive(raw, manifest["files"], tree=identity["tree"])
