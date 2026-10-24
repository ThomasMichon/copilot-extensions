"""Offline immutable observer bundles; build separately, verify inside caller clocks.

An expected manifest digest is a caller-owned trust pin, not a digest read from
the bundle itself. This module never downloads, installs, activates or loads a
native library, and does not confer workload or baseline admission.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile

from . import observer_native_build as native

SCHEMA = "copilot-extensions.owned-observer-artifact/v1"
MAX_LIBRARY_BYTES = 16 * 1024 * 1024


def _read(path: Path, maximum: int) -> bytes:
    path = Path(path).absolute()
    for candidate in (*reversed(path.parents), path):
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError("observer artifact input must not contain link indirection")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("observer artifact input must be a single ordinary file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as source:
        opened = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise ValueError("observer artifact input changed during open")
        content = source.read(maximum + 1)
        if len(content) > maximum:
            raise ValueError("observer artifact input exceeds bound")
        after = os.fstat(source.fileno())
        current = path.lstat()
        stamp = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if stamp(opened) != stamp(after) or stamp(current) != stamp(opened) or len(content) != opened.st_size:
            raise ValueError("observer artifact input changed during read")
    return content


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _identity() -> dict:
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise ValueError("observer artifact currently supports native Linux x86-64 only")
    return {
        "platform": "linux-x86_64", "python_cache_tag": sys.implementation.cache_tag,
        "python_sha256": _sha(_read(Path(sys.executable).resolve(strict=True), 128 * 1024 * 1024)),
    }


def build_bundle(wheel: Path, source: Path, entry: Path, destination: Path) -> dict:
    """Build once from the governed, hash-pinned compiler and exact source inputs."""
    identity = _identity()
    c_bytes = _read(source, 1024 * 1024)
    entry_bytes = _read(entry, 1024 * 1024)
    if _sha(c_bytes) != native.C_SHA256:
        raise ValueError("observer artifact canonical C source identity differs")
    destination = Path(destination)
    destination.mkdir(mode=0o700)
    try:
        with tempfile.TemporaryDirectory(prefix="observer-artifact-build-", dir=destination) as temporary:
            root = Path(temporary)
            c_file = root / "observer.c"
            c_file.write_bytes(c_bytes)
            toolchain = native.extract_toolchain(wheel, root / "toolchain")
            library = root / "observer.so"
            entry_sha = _sha(entry_bytes)
            argv = native.build_argv(
                toolchain["compiler"], c_file, library, entry_sha, variant="original",
            )
            subprocess.run(
                argv, check=True, capture_output=True, text=True, timeout=300,
                env=dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(root / "cache-global"),
                         ZIG_LOCAL_CACHE_DIR=str(root / "cache-local")),
            )
            shape = native.verify_artifact(library, entry_sha)
            content = _read(library, MAX_LIBRARY_BYTES)
            manifest = {
                "schema": SCHEMA, **identity, "c_source_sha256": native.C_SHA256,
                "entry_sha256": entry_sha, "library_sha256": _sha(content),
                "library_bytes": len(content), "compiler_wheel_sha256": native.WHEEL_SHA256,
                "build_variant": "original", "constructor_entries": shape["constructor_entries"],
                "admission": "unqualified",
            }
            with (destination / "observer.so").open("xb") as output:
                output.write(content)
            encoded = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
            with (destination / "manifest.json").open("xb") as output:
                output.write(encoded)
            (destination / "observer.so").chmod(0o444)
            (destination / "manifest.json").chmod(0o444)
            return {"manifest_sha256": _sha(encoded), **manifest}
    except BaseException:
        # The destination was exclusively created here, never a caller's old bundle.
        shutil.rmtree(destination)
        raise


def stage_bundle(
    bundle: Path, destination: Path, *, expected_manifest_sha256: str,
    source: Path, entry: Path,
) -> dict:
    """Freshly verify and stage immutable bytes; charge this call to the workload."""
    identity = _identity()
    if (
        len(expected_manifest_sha256) != 64
        or any(char not in "0123456789abcdef" for char in expected_manifest_sha256)
    ):
        raise ValueError("observer artifact requires an independently trusted manifest pin")
    bundle, destination = Path(bundle), Path(destination)
    encoded = _read(bundle / "manifest.json", 8192)
    if _sha(encoded) != expected_manifest_sha256:
        raise ValueError("observer artifact manifest does not match the trusted pin")
    manifest = json.loads(encoded)
    if not isinstance(manifest, dict):
        raise ValueError("observer artifact manifest must be an object")
    expected = {
        "schema": SCHEMA, **identity, "c_source_sha256": _sha(_read(source, 1024 * 1024)),
        "entry_sha256": _sha(_read(entry, 1024 * 1024)),
        "compiler_wheel_sha256": native.WHEEL_SHA256, "build_variant": "original",
        "admission": "unqualified",
    }
    if expected["c_source_sha256"] != native.C_SHA256 or any(
        manifest.get(key) != value for key, value in expected.items()
    ):
        raise ValueError("observer artifact source, entry, toolchain or native identity differs")
    content = _read(bundle / "observer.so", MAX_LIBRARY_BYTES)
    if (
        manifest.get("library_sha256") != _sha(content)
        or type(manifest.get("library_bytes")) is not int
        or manifest["library_bytes"] != len(content)
    ):
        raise ValueError("observer artifact library identity differs")
    created = False
    try:
        with destination.open("xb") as output:
            created = True
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        destination.chmod(0o444)
        shape = native.verify_artifact(destination, expected["entry_sha256"])
        if shape["constructor_entries"] != manifest.get("constructor_entries"):
            raise ValueError("observer artifact constructor identity differs")
        return {
            **shape, "manifest_sha256": expected_manifest_sha256,
            "c_source_sha256": expected["c_source_sha256"],
            "compiler_wheel_sha256": native.WHEEL_SHA256,
            "native_identity": identity, "build_variant": "immutable-artifact",
            "admission": "unqualified",
        }
    except BaseException:
        if created:
            destination.chmod(0o600)
            destination.unlink()
        raise
