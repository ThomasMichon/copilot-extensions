"""Bounded parallel source hashing preserves the canonical payload identity."""
from __future__ import annotations

import threading
import time

import build_python_artifacts as builder
import pytest
from promotion_python_artifacts import PromotionArtifactError, validate_source_binding


def test_parallel_payload_hash_preserves_identity_and_overlaps_reads(tmp_path, monkeypatch):
    directories = []
    for name in ("alpha", "beta", "gamma", "delta"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "source.py").write_text(name, encoding="utf-8")
        directories.append(directory)
    monkeypatch.setattr(builder, "REPO", tmp_path)
    expected = builder.compute_payload_hash(directories)
    original = builder.directory_content_hash
    gate = threading.Barrier(2)
    lock = threading.Lock()
    started = 0

    def observed(directory):
        nonlocal started
        with lock:
            started += 1
            order = started
        if order <= 2:
            gate.wait(timeout=5)
        return original(directory)

    monkeypatch.setattr(builder, "directory_content_hash", observed)
    assert builder.compute_payload_hash(directories, workers=4) == expected


def test_parallel_hash_failure_waits_for_other_reads(tmp_path, monkeypatch):
    directories = [tmp_path / "alpha", tmp_path / "beta"]
    for directory in directories:
        directory.mkdir()
    monkeypatch.setattr(builder, "REPO", tmp_path)
    started, finished = threading.Event(), threading.Event()

    def failing(directory):
        if directory.name == "alpha":
            assert started.wait(timeout=5)
            raise OSError("hash failed")
        started.set()
        time.sleep(0.1)
        finished.set()
        return "unused"

    monkeypatch.setattr(builder, "directory_content_hash", failing)
    with pytest.raises(OSError, match="hash failed"):
        builder.compute_payload_hash(directories, workers=2)
    assert finished.is_set()


@pytest.mark.parametrize("workers", [0, 5, True])
def test_payload_hash_rejects_unbounded_worker_counts_before_read(tmp_path, monkeypatch, workers):
    monkeypatch.setattr(builder, "directory_content_hash", lambda _path: pytest.fail("unexpected read"))
    with pytest.raises(ValueError, match="one and four"):
        builder.compute_payload_hash([tmp_path], workers=workers)
    with pytest.raises(PromotionArtifactError, match="one and four"):
        validate_source_binding({}, tmp_path, core_hash_workers=workers)
