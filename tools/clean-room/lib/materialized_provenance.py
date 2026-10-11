"""Commit-bound receipts for complete, standalone materialized marketplace trees."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile

RECEIPT = "MATERIALIZED.json"
SCHEMA = "copilot-extensions.materialized-source"


def content_manifest(root: Path) -> dict[str, dict]:
    """Inventory every artifact path, excluding only the root receipt itself."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("materialized root must be a real directory")
    entries = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == RECEIPT:
            continue
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError(f"materialized link escapes root: {relative}")
            entries[relative] = {"kind": "symlink", "target": os.readlink(path)}
        elif stat.S_ISDIR(mode):
            entries[relative] = {"kind": "directory"}
        elif stat.S_ISREG(mode):
            entries[relative] = {
                "kind": "file", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "executable": bool(mode & 0o111),
            }
        else:
            raise ValueError(f"unsupported materialized path: {relative}")
    return entries


def verify(root: Path, expected_head: str) -> str:
    receipt_path = root / RECEIPT
    if receipt_path.is_symlink():
        raise ValueError("materialized receipt must not be a symlink")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA or receipt.get("version") != 1:
        raise ValueError("unsupported materialized source receipt")
    head = receipt.get("source_commit")
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("materialized source commit is invalid")
    if head != expected_head:
        raise ValueError("materialized source commit does not match expected HEAD")
    if receipt.get("paths") != content_manifest(root):
        raise ValueError("materialized content manifest does not match artifact tree")
    return head


def build(dest: Path, *, source_root: Path) -> list[str]:
    """Materialize committed Git bytes, never claim HEAD for a dirty working copy."""
    import materialize_main

    if dest.exists() or dest.is_symlink():
        raise ValueError("candidate destination must not already exist")
    head = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "--verify", "HEAD^{commit}"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    archive = subprocess.run(
        ["git", "-C", str(source_root), "archive", "--format=tar", head],
        check=True, capture_output=True, timeout=120,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="materialized-source-") as temporary:
        source = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
            tree.extractall(source, filter="data")
        if (source / RECEIPT).exists() or (source / RECEIPT).is_symlink():
            raise ValueError("source must not contain a generated materialized receipt")
        log = materialize_main.build(dest, source_root=source)
    if any(line.startswith("SKIP") for line in log):
        raise ValueError("candidate materialization refused an unresolved source")
    receipt = {"schema": SCHEMA, "version": 1, "source_commit": head,
               "paths": content_manifest(dest)}
    (dest / RECEIPT).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    verify(dest, head)
    return log
