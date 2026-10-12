"""Commit-bound receipts for complete, standalone materialized marketplace trees."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

RECEIPT = "MATERIALIZED.json"
SCHEMA = "copilot-extensions.materialized-source"


def extract_source(tree: tarfile.TarFile, root: Path) -> None:
    """Extract regular Git archive bytes without version-specific tar filters."""
    members = tree.getmembers()
    for member in members:
        parts = member.name.rstrip("/").split("/")
        if (not member.name or member.name.startswith(("/", "\\"))
                or any(part in ("", ".", "..") or "\\" in part or ":" in part for part in parts)
                or not (member.isdir() or member.isfile())):
            raise ValueError(f"unsafe source archive member: {member.name}")
        destination = root.joinpath(*parts)
        if not destination.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"source archive member escapes root: {member.name}")
    for member in members:
        destination = root / member.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if member.isdir():
            destination.mkdir(exist_ok=True)
            continue
        stream = tree.extractfile(member)
        if stream is None:
            raise ValueError(f"source archive member has no data: {member.name}")
        # The private temporary root contains only validated directories/files.
        with stream, destination.open("xb") as output:
            shutil.copyfileobj(stream, output)
        destination.chmod(0o755 if member.mode & 0o111 else 0o644)


def content_manifest(root: Path) -> dict[str, dict]:
    """Inventory every artifact path, excluding only the root receipt itself."""
    if (root.is_symlink() or not root.is_dir()
            or getattr(root.lstat(), "st_file_attributes", 0) & 0x400):
        raise ValueError("materialized root must be a real directory")
    entries = {}

    def refuse_unreadable(error: OSError) -> None:
        raise error

    for directory, children, files in os.walk(
        root, followlinks=False, onerror=refuse_unreadable,
    ):
        children.sort()
        for name in sorted(children + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative == RECEIPT:
                continue
            info = path.lstat()
            mode = info.st_mode
            if getattr(info, "st_file_attributes", 0) & 0x400 and not stat.S_ISLNK(mode):
                raise ValueError(f"unsupported materialized reparse path: {relative}")
            if stat.S_ISLNK(mode):
                if not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError(f"materialized link escapes root: {relative}")
                entries[relative] = {"kind": "symlink", "target": os.readlink(path)}
                if name in children:
                    children.remove(name)
            elif stat.S_ISDIR(mode):
                entries[relative] = {"kind": "directory"}
            elif stat.S_ISREG(mode):
                if info.st_nlink != 1:
                    raise ValueError(f"materialized file must be single-link: {relative}")
                entries[relative] = {
                    "kind": "file", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "executable": bool(mode & 0o111),
                }
            else:
                raise ValueError(f"unsupported materialized path: {relative}")
    return entries


def verify(root: Path, expected_head: str) -> str:
    receipt_path = root / RECEIPT
    info = receipt_path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("materialized receipt must be a single-link regular file")
    fd = os.open(receipt_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        opened = os.fstat(stream.fileno())
        if ((info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1):
            raise ValueError("materialized receipt identity changed")
        receipt = json.load(stream)
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
    if dest.exists() or dest.is_symlink():
        raise ValueError("candidate destination must not already exist")
    git_env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    head = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "--verify", "HEAD^{commit}"],
        check=True, capture_output=True, text=True, timeout=10, env=git_env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()
    archive = subprocess.run(
        ["git", "-C", str(source_root), "archive", "--format=tar", head],
        check=True, capture_output=True, timeout=120, env=git_env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout
    with tempfile.TemporaryDirectory(prefix="materialized-source-") as temporary:
        source = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
            extract_source(tree, source)
        if (source / RECEIPT).exists() or (source / RECEIPT).is_symlink():
            raise ValueError("source must not contain a generated materialized receipt")
        program = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "sys.dont_write_bytecode = True\n"
            "root = Path(sys.argv[1])\n"
            "sys.path.insert(0, str(root / 'tools'))\n"
            "import materialize_main\n"
            "print(json.dumps(materialize_main.build("
            "Path(sys.argv[2]), source_root=root, replace_existing=False)))\n"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c", program, str(source), str(dest.absolute())],
            capture_output=True, text=True, timeout=120, env=git_env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise ValueError(f"committed materializer failed: {result.stderr.strip()}")
        log = json.loads(result.stdout)
        if not isinstance(log, list) or any(not isinstance(line, str) for line in log):
            raise ValueError("committed materializer returned an invalid log")
    if any(line.startswith("SKIP") for line in log):
        raise ValueError("candidate materialization refused an unresolved source")
    receipt = {"schema": SCHEMA, "version": 1, "source_commit": head,
               "paths": content_manifest(dest)}
    (dest / RECEIPT).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    verify(dest, head)
    return log
