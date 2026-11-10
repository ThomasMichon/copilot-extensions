"""Actor-free permission controls for fresh retained-helper publication."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("permissions", ["private", "group-writable"])
def test_incoming_helper_protection_preserves_content(tmp_path, monkeypatch, permissions):
    source = ROOT / "libs/versioned-runtime/versioned_runtime_publication.py"
    spec = importlib.util.spec_from_file_location("_unit_retained_publication", source)
    publication = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, publication)
    spec.loader.exec_module(publication)
    slot = tmp_path / "versions/1.0"
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(parents=True)
    launcher = bootstrap / "runtime_launcher.py"
    content = b"retained construction input\n"
    launcher.write_bytes(content)
    launcher.chmod(0o664 if permissions == "group-writable" else 0o644)
    generation = bootstrap / "runtime-generation"
    identity = b"a" * 32 + b"\n"
    generation.write_bytes(identity)
    generation.chmod(0o664 if permissions == "group-writable" else 0o600)
    if os.name == "nt":
        monkeypatch.setattr(publication, "_windows_owned", lambda _fd: True)
    else:
        # Attribute the syscall to its exact temporary object; native tests use fchmod.
        def attributed_chmod(fd, mode):
            opened = os.fstat(fd)
            path = next(
                path for path in (launcher, generation)
                if (path.stat().st_dev, path.stat().st_ino) == (opened.st_dev, opened.st_ino)
            )
            named = path.stat()
            assert (opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino)
            path.chmod(mode)

        monkeypatch.setattr(publication.os, "fchmod", attributed_chmod)
    publication.prepare_retained_generation(slot)
    assert launcher.read_bytes() == content
    assert generation.read_bytes() == identity
    assert not (slot / ".install-complete.json").exists()
    if os.name != "nt":
        assert stat.S_IMODE(launcher.stat().st_mode) == 0o644
        assert stat.S_IMODE(generation.stat().st_mode) == (
            0o644 if permissions == "group-writable" else 0o600
        )


@pytest.mark.parametrize("growth", [False, True])
@pytest.mark.parametrize("proof_kind", ["fd", "path"])
def test_incoming_generation_proof_bounds_reads(tmp_path, monkeypatch, growth, proof_kind):
    source = ROOT / "libs/versioned-runtime/versioned_runtime_publication.py"
    spec = importlib.util.spec_from_file_location("_unit_bounded_publication", source)
    publication = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, publication)
    spec.loader.exec_module(publication)
    path = tmp_path / "runtime-generation"
    path.write_bytes(b"a" * (64 if growth else 65))
    if os.name == "nt":
        monkeypatch.setattr(publication, "_windows_owned", lambda _fd: True)
    original_read = os.read
    original_fstat = os.fstat
    reads = []

    def bounded_read(fd, size):
        reads.append(size)
        return b"a" * size if growth else original_read(fd, size)

    monkeypatch.setattr(publication.os, "read", bounded_read)
    with path.open("rb") as stream:
        with pytest.raises(publication.PublicationProofError, match="size limit"):
            if proof_kind == "path":
                publication.state_path(path, max_bytes=64)
            else:
                publication.state_fd(stream.fileno(), max_bytes=64)
        assert stream.tell() == 0
        assert original_fstat(stream.fileno()).st_size == (64 if growth else 65)
    assert reads == ([65] if growth else [])


def test_incoming_generation_rejects_binary_eof(tmp_path, monkeypatch):
    source = ROOT / "libs/versioned-runtime/versioned_runtime_publication.py"
    spec = importlib.util.spec_from_file_location("_unit_binary_generation", source)
    publication = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, publication)
    spec.loader.exec_module(publication)
    slot = tmp_path / "versions/1.0"
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(parents=True)
    launcher = bootstrap / "runtime_launcher.py"
    launcher.write_bytes(b"retained construction input\n")
    launcher.chmod(0o600)
    generation = bootstrap / "runtime-generation"
    content = b"a" * 32 + b"\x1a"
    generation.write_bytes(content)
    generation.chmod(0o600)
    if os.name == "nt":
        monkeypatch.setattr(publication, "_windows_owned", lambda _fd: True)
    with pytest.raises(publication.PublicationProofError, match="invalid or changed"):
        publication.prepare_retained_generation(slot)
    assert generation.read_bytes() == content
