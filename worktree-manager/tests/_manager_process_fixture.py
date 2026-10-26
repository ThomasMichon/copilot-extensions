"""Synthetic installed prefixes for process-ownership tests, not install proofs."""

import os
import shutil
import sysconfig
import sys
import venv
from pathlib import Path

from worktree_manager import self_install as si


def stub_runtime(slot: Path) -> Path:
    slot.mkdir(parents=True, exist_ok=True)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(exist_ok=True)
    python.touch()
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(exist_ok=True)
    (bootstrap / "runtime_launcher.py").touch()
    si._runtime_installer().runtime.mark_complete(slot.parent.parent, slot.name, payload_hash="a" * 64)
    return python


def synthetic_runtime(root: Path, version: str, *, program: str | None = None) -> Path:
    slot = root / "versions" / version
    venv.EnvBuilder(with_pip=False).create(slot)
    source = Path(__file__).resolve().parents[1]
    package_source = source
    if program is None:
        si._copy_payload(source, slot / "payload")
        package_source = slot / "payload"
    else:
        # Program controls need the real shell boundary, not standalone assembly.
        shutil.copytree(source / "bin", slot / "payload" / "bin")
    purelib = slot / ("Lib/site-packages" if os.name == "nt" else
                      f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    purelib.mkdir(parents=True, exist_ok=True)
    adapter = si._runtime_installer()
    for package in adapter.engine._package_roots(package_source):
        shutil.copytree(
            package / "src", purelib, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"),
        )
    # External dependencies are reused from this test environment. The Manager
    # and its first-party closure above are installed inside the fixture prefix.
    (purelib / "test-dependencies.pth").write_text(sysconfig.get_path("purelib") + "\n", encoding="utf-8")
    if program is not None:
        (purelib / "worktree_manager" / "__main__.py").write_text(program, encoding="utf-8")
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    for name in (
        "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_source.py", "versioned_runtime_construction.py",
        "versioned_runtime_publication.py",
    ):
        shutil.copyfile(source / "scripts" / name, bootstrap / name)
    adapter.runtime.mark_complete(root, version, payload_hash="a" * 64)
    return slot
