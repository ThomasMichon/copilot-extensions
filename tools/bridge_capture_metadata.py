"""Retain capture-time registry metadata with its original Git Merkle proof."""
from __future__ import annotations

import base64
import hashlib
import re
import subprocess
import sys
from pathlib import Path

from agent_bridge_contract_git import clean_git_environment, require_local_object_reads
import agent_bridge_contract_registry as codec

PREFIX = "plugins/agent-bridge/contract/source-blobs/capture-metadata/"


def _directory(commit: str) -> str:
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("capture metadata requires an exact source commit")
    return PREFIX + commit + "/"


def _identity(kind: str, data: bytes) -> str:
    return hashlib.sha1(
        kind.encode() + b" " + str(len(data)).encode() + b"\0" + data,
        usedforsecurity=False,
    ).hexdigest()


def _unchanged(relative: str, actual: bytes, expected: bytes) -> bool:
    if relative == PREFIX + ".gitattributes" or relative.endswith("/proof.json"):
        actual = actual.replace(b"\r\n", b"\n")
    return actual == expected


def _entry(data: bytes, name: str) -> tuple[str, str]:
    offset = 0
    entries = {}
    while offset < len(data):
        space = data.index(b" ", offset)
        end = data.index(b"\0", space)
        mode = data[offset:space].decode("ascii")
        filename = data[space + 1:end].decode("utf-8")
        identity = data[end + 1:end + 21]
        if len(identity) != 20 or filename in entries:
            raise ValueError("invalid capture metadata tree")
        entries[filename] = (mode, identity.hex())
        offset = end + 21
    if name not in entries:
        raise ValueError("capture metadata path is absent from the source tree")
    return entries[name]


def _snapshot(root: Path, commit: str, read_object) -> codec.Snapshot:
    first = read_object(commit, "commit").split(b"\n", 1)[0]
    match = re.fullmatch(rb"tree ([0-9a-f]{40})", first)
    if match is None:
        raise ValueError("capture metadata commit has no original tree")
    tree = match[1].decode("ascii")

    def read(relative: str) -> bytes:
        codec.safe(root, relative)
        parts = relative.split("/")
        current = tree
        for index, name in enumerate(parts):
            mode, identity = _entry(read_object(current, "tree"), name)
            if index < len(parts) - 1:
                if mode != "40000":
                    raise ValueError("capture metadata ancestor is not a plain tree")
                current = identity
            else:
                if mode not in {"100644", "100755"}:
                    raise ValueError("capture metadata is not a plain blob")
                return read_object(identity, "blob")
        raise ValueError("capture metadata path is empty")

    return codec.load(root, read=read)


def load(root: Path, commit: str) -> codec.Snapshot | None:
    """Read only metadata authenticated by the immutable captured commit ID."""
    directory = _directory(commit)
    path = codec.safe(root, directory + "proof.json")
    if not path.exists():
        return None
    proof = codec.decode(codec.read_file(root, directory + "proof.json"))
    if (set(proof) != {"version", "objects"} or type(proof["version"]) is not int
            or proof["version"] != 1 or not isinstance(proof["objects"], dict)
            or len(proof["objects"]) > 512):
        raise ValueError("invalid capture metadata proof")

    def read_object(identity: str, kind: str) -> bytes:
        if kind == "blob":
            data = codec.read_file(root, directory + identity + ".txt")
        else:
            record = proof["objects"].get(identity)
            if (not isinstance(record, dict) or set(record) != {"kind", "base64"}
                    or record["kind"] != kind or not isinstance(record["base64"], str)):
                raise ValueError("capture metadata proof lacks the original object")
            data = base64.b64decode(record["base64"], validate=True)
        if _identity(kind, data) != identity:
            raise ValueError("capture metadata Git object identity mismatch")
        return data

    return _snapshot(root, commit, read_object)


def retain(root: Path, commit: str) -> codec.Snapshot:
    """Explicitly preserve exact local objects; never fetch or replace evidence."""
    directory = _directory(commit)
    require_local_object_reads()
    objects = {}
    blobs = {}

    def read_object(identity: str, kind: str) -> bytes:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(root), "cat-file", kind, identity],
            capture_output=True, check=True, timeout=10, env=clean_git_environment(),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        data = result.stdout
        if _identity(kind, data) != identity:
            raise ValueError("local capture metadata object identity mismatch")
        if kind == "blob":
            blobs[directory + identity + ".txt"] = data
        else:
            objects[identity] = {
                "kind": kind, "base64": base64.b64encode(data).decode("ascii"),
            }
        return data

    snapshot = _snapshot(root, commit, read_object)
    plan = {
        **blobs,
        directory + "proof.json": codec.encode({"version": 1, "objects": objects}),
        PREFIX + ".gitattributes": b"*.txt -text\n",
    }
    for relative, data in plan.items():
        if len(data) > codec.MAX_ARTIFACT_BYTES:
            raise ValueError("capture metadata exceeds the unchanged artifact size guard")
        path = codec.safe(root, relative)
        if path.exists() and not _unchanged(relative, path.read_bytes(), data):
            raise ValueError(f"retained capture metadata must not be replaced: {relative}")
    for relative, data in plan.items():
        path = codec.safe(root, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as stream:
                stream.write(data)
        except FileExistsError:
            if not _unchanged(relative, path.read_bytes(), data):
                raise ValueError("retained capture metadata changed during publication") from None
    return snapshot
