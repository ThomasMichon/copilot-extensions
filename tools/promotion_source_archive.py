"""Read pinned Git objects once and prepare the existing publisher's source subjects."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess

from promotion_complete_source import (
    MAX_SOURCE_BYTES, MAX_SOURCE_FILES, canonical_bytes, source_archive, source_asset_name,
    validate_identity, verify_source_archive,
)
from promotion_unit_contract import MAX_PACKET_FILE_BYTES, decode_json


def _git(repo: Path, args: list[str], *, data: bytes | None = None) -> bytes:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    result = subprocess.run(
        ["git", "--no-replace-objects", *args], cwd=repo, env=env, input=data,
        capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise ValueError(f"cannot read complete-source Git data: {result.stderr.decode(errors='replace')}")
    return result.stdout


def read_tree(repo: Path, *, candidate_sha: str, main_before: str, tree: str) -> dict:
    """Batch raw blobs, not a worktree or git-archive's export-filtered representation."""
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value)
           for value in (candidate_sha, main_before, tree)):
        raise ValueError("invalid pinned complete-source Git identity")
    headers = _git(repo, ["cat-file", "commit", candidate_sha]).split(b"\n\n", 1)[0].splitlines()
    if (
        not headers or headers[0] != b"tree " + tree.encode()
        or [line for line in headers if line.startswith(b"parent ")] != [b"parent " + main_before.encode()]
    ):
        raise ValueError("complete-source candidate is not the admitted Main child/tree")
    records = _git(repo, ["ls-tree", "-rz", tree]).split(b"\0")
    entries = {}
    for record in records:
        if not record:
            continue
        metadata, path = record.split(b"\t", 1)
        mode, kind, blob = metadata.decode("ascii").split()
        name = path.decode("utf-8")
        if (
            kind != "blob" or mode not in ("100644", "100755", "120000")
            or not re.fullmatch(r"[0-9a-f]{40}", blob) or name in entries
        ):
            raise ValueError("complete source contains an unsupported or duplicate Git entry")
        entries[name] = (mode, blob)
    if not 1 <= len(entries) <= MAX_SOURCE_FILES:
        raise ValueError("complete-source Git inventory is empty or oversized")
    identifiers = sorted({blob for _mode, blob in entries.values()})
    request = ("\n".join(identifiers) + "\n").encode()
    metadata = _git(repo, ["cat-file", "--batch-check"], data=request).splitlines()
    if len(metadata) != len(identifiers):
        raise ValueError("incomplete complete-source blob metadata")
    sizes = {}
    for blob, line in zip(identifiers, metadata, strict=True):
        fields = line.decode("ascii").split()
        if len(fields) != 3 or fields[:2] != [blob, "blob"] or not fields[2].isdigit():
            raise ValueError("invalid complete-source blob metadata")
        sizes[blob] = int(fields[2])
    if (
        any(size > MAX_PACKET_FILE_BYTES for size in sizes.values())
        or sum(sizes[blob] for _mode, blob in entries.values()) > MAX_SOURCE_BYTES
    ):
        raise ValueError("complete-source Git blobs exceed the existing source bounds")
    output = _git(repo, ["cat-file", "--batch"], data=request)
    blobs, position = {}, 0
    for blob in identifiers:
        header = f"{blob} blob {sizes[blob]}\n".encode()
        if output[position:position + len(header)] != header:
            raise ValueError("complete-source batch returned another blob")
        position += len(header)
        end = position + sizes[blob]
        if output[end:end + 1] != b"\n":
            raise ValueError("truncated complete-source blob")
        blobs[blob] = output[position:end]
        position = end + 1
    if position != len(output):
        raise ValueError("unexpected complete-source batch bytes")
    return {name: (mode, blobs[blob]) for name, (mode, blob) in entries.items()}


def source_identity(index_raw: bytes, receipt: dict, *, qualification_digest: str) -> dict:
    index = decode_json(index_raw)
    if (
        not isinstance(index, dict) or index.get("schema_version") != 2
        or not isinstance(receipt, dict) or receipt.get("profile") not in ("source-units-v2", "candidate-units-v3")
        or index.get("dev_head") != receipt.get("dev_sha")
        or hashlib.sha256(canonical_bytes(receipt)).hexdigest() != qualification_digest
    ):
        raise ValueError("complete-source inputs differ from the source index or qualification")
    identity = {
        key: receipt.get(key)
        for key in ("dev_sha", "candidate_sha", "main_before", "control_sha", "tree")
    }
    identity.update(
        content_tree=index.get("content_tree"),
        source_index_sha256=hashlib.sha256(index_raw).hexdigest(),
        qualification_digest=qualification_digest,
    )
    validate_identity(identity)
    return identity


def _regular(path: Path, *, limit: int) -> bytes:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode) or path.is_symlink()
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or info.st_size > limit
    ):
        raise ValueError("complete-source subject is indirect, nonregular or oversized")
    raw = path.read_bytes()
    if len(raw) != info.st_size:
        raise ValueError("complete-source subject changed while reading")
    return raw


def read_source_assets(directory: Path, *, identity: dict) -> dict[str, bytes]:
    validate_identity(identity)
    info = directory.lstat()
    if (
        directory.is_symlink() or not stat.S_ISDIR(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError("complete-source subject directory is unavailable or indirect")
    paths = list(directory.iterdir())
    manifests = [path for path in paths if path.suffix == ".json"]
    archives = [path for path in paths if path.suffix == ".zip"]
    if len(paths) != 2 or len(manifests) != 1 or len(archives) != 1:
        raise ValueError("complete-source subjects have an unexpected inventory")
    manifest_raw = _regular(manifests[0], limit=MAX_PACKET_FILE_BYTES)
    manifest = decode_json(manifest_raw)
    archive_raw = _regular(archives[0], limit=MAX_SOURCE_BYTES)
    if (
        manifest_raw != canonical_bytes(manifest)
        or manifests[0].name != source_asset_name("source-manifest", identity, hashlib.sha256(manifest_raw).hexdigest())
        or archives[0].name != source_asset_name("complete-source", identity, hashlib.sha256(archive_raw).hexdigest())
    ):
        raise ValueError("complete-source subjects have a noncanonical identity")
    verify_source_archive(archive_raw, manifest, identity=identity)
    return {manifests[0].name: manifest_raw, archives[0].name: archive_raw}


def prepare_source(directory: Path, *, repo: Path, index_raw: bytes, receipt: dict) -> dict:
    identity = source_identity(
        index_raw, receipt, qualification_digest=hashlib.sha256(canonical_bytes(receipt)).hexdigest(),
    )
    raw, manifest = source_archive(
        read_tree(repo, candidate_sha=identity["candidate_sha"], main_before=identity["main_before"],
                  tree=identity["tree"]),
        identity=identity,
    )
    subjects = {
        source_asset_name("complete-source", identity, hashlib.sha256(raw).hexdigest()): raw,
        source_asset_name("source-manifest", identity, hashlib.sha256(canonical_bytes(manifest)).hexdigest()):
            canonical_bytes(manifest),
    }
    directory.mkdir(parents=True, exist_ok=False)
    for name, content in subjects.items():
        (directory / name).write_bytes(content)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--source-index", type=Path, required=True)
    parser.add_argument("--qualification-receipt", type=Path, required=True)
    args = parser.parse_args()
    prepare_source(
        args.directory, repo=args.repo, index_raw=_regular(args.source_index, limit=1 << 20),
        receipt=decode_json(_regular(args.qualification_receipt, limit=MAX_PACKET_FILE_BYTES)),
    )


if __name__ == "__main__":
    main()
