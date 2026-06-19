"""Tests for the de-facility-ized segmenter wiring.

These assert the config-driven store paths and that the three console-script
entry points are importable and callable. End-to-end collation is covered by
manual/functional checks.
"""

from __future__ import annotations

from pathlib import Path

import agent_logger.segmenter.collate as collate
import agent_logger.segmenter.read_digest as read_digest


def test_segmenter_mains_callable() -> None:
    from agent_logger.segmenter import prepare_log

    assert callable(collate.main)
    assert callable(read_digest.main)
    assert callable(prepare_log.main)


def test_collate_default_digest_root_honors_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(tmp_path))
    root = collate._default_digest_root()
    assert Path(root) == tmp_path / "session-digests"


def test_persistent_digest_root_honors_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(tmp_path))
    assert read_digest._persistent_digest_root() == tmp_path / "session-digests"


def test_remote_store_root_optional(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AGENT_LOGGER_REMOTE_STORE", raising=False)
    assert read_digest._remote_store_root() is None
    monkeypatch.setenv("AGENT_LOGGER_REMOTE_STORE", str(tmp_path))
    assert read_digest._remote_store_root() == tmp_path
