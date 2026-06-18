"""Smoke tests for the agent-logger scaffold."""

from __future__ import annotations

from pathlib import Path

from agent_logger import __version__
from agent_logger.config import DEFAULTS, load_config
from agent_logger.segmenter.platform import detect_machine, sanitize_path_component


def test_version_matches_build_info() -> None:
    assert __version__ == "0.1.0-dev1"


def test_config_defaults_and_home(tmp_path: Path) -> None:
    cfg = load_config(home=tmp_path)
    assert cfg.home == tmp_path
    # Unset paths resolve under the home dir.
    assert cfg.store_dir == tmp_path / "session-digests"
    assert cfg.sync_path == tmp_path / "sessions"
    assert cfg.sync_target == "local"
    assert cfg.voice_pack == "none"
    assert cfg.note_marker == DEFAULTS["log"]["note_marker"]


def test_config_user_override(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        "sync:\n  target: onedrive\nlog:\n  voice_pack: aperture\n",
        encoding="utf-8",
    )
    cfg = load_config(home=tmp_path)
    assert cfg.sync_target == "onedrive"
    assert cfg.voice_pack == "aperture"


def test_detect_machine_nonempty() -> None:
    assert detect_machine()


def test_sanitize_path_component_ntfs() -> None:
    assert sanitize_path_component('a:b/c"d') == "a -b-c'd"
    assert sanitize_path_component("   ") == "Untitled"
    # Reserved device name gets prefixed.
    assert sanitize_path_component("CON").startswith("_")
