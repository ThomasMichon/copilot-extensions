"""The complete-source ZIP bound stays field-admissible while the expanded tree may be larger."""
from __future__ import annotations

import pytest

import promotion_complete_source as source


def _case() -> tuple[dict[str, tuple[str, bytes]], dict[str, str]]:
    files = {"a.txt": ("100644", b"a" * 4096), "b.txt": ("100644", b"b" * 4096)}
    identity = {
        "dev_sha": "a" * 40, "candidate_sha": "b" * 40, "main_before": "c" * 40,
        "control_sha": "d" * 40, "content_tree": "e" * 40,
        "tree": source._inventory(files)[1], "source_index_sha256": "f" * 64,
        "qualification_digest": "0" * 64,
    }
    return files, identity


def test_expanded_tree_may_exceed_the_archive_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    files, identity = _case()
    monkeypatch.setattr(source, "MAX_SOURCE_BYTES", 4096)
    raw, manifest = source.source_archive(files, identity=identity)
    assert len(raw) <= 4096 < sum(len(content) for _, content in files.values())
    assert source.verify_source_archive(raw, manifest, identity=identity) == files


def test_expanded_tree_bound_refuses_creation_and_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    files, identity = _case()
    raw, manifest = source.source_archive(files, identity=identity)
    monkeypatch.setattr(source, "MAX_SOURCE_TREE_BYTES", 4096)
    with pytest.raises(ValueError, match="tree exceeds its byte bound"):
        source.source_archive(files, identity=identity)
    with pytest.raises(ValueError, match="byte bound"):
        source.verify_source_archive(raw, manifest, identity=identity)


def test_archive_bound_refuses_creation_and_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    files, identity = _case()
    raw, manifest = source.source_archive(files, identity=identity)
    monkeypatch.setattr(source, "MAX_SOURCE_BYTES", len(raw) - 1)
    with pytest.raises(ValueError, match="archive exceeds its byte bound"):
        source.source_archive(files, identity=identity)
    with pytest.raises(ValueError):
        source.verify_source_archive(raw, manifest, identity=identity)
