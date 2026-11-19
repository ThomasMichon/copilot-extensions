"""Native Windows owner/DACL admission and its steady-state memory use."""

import gc
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tracemalloc

import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "libs/versioned-runtime/versioned_runtime_leases.py"
SPEC = importlib.util.spec_from_file_location("native_windows_owned_file", SOURCE)
assert SPEC and SPEC.loader
leases = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = leases
SPEC.loader.exec_module(leases)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Actual Windows token and DACL")


def _admits(path: Path) -> bool:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        return leases.windows_owned_file(fd)
    finally:
        os.close(fd)


def test_admits_private_file_and_rejects_public_write(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.write_bytes(b"")
    public = tmp_path / "public"
    public.write_bytes(b"")
    subprocess.run(["icacls", str(public), "/grant", "*S-1-1-0:(W)"],
                   check=True, capture_output=True)

    assert _admits(private)
    assert not _admits(public)


def test_repeated_admission_does_not_grow_memory(tmp_path: Path) -> None:
    # A long-lived owner polls this check about once per second.
    path = tmp_path / "lease"
    path.write_bytes(b"")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        for _ in range(5):
            assert leases.windows_owned_file(fd)
        gc.collect()
        tracemalloc.start()
        try:
            before = tracemalloc.get_traced_memory()[0]
            for _ in range(200):
                assert leases.windows_owned_file(fd)
            gc.collect()
            growth = tracemalloc.get_traced_memory()[0] - before
        finally:
            tracemalloc.stop()
    finally:
        os.close(fd)

    assert growth < 256 * 1024, f"200 admissions retained {growth} bytes"
