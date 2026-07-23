"""Smoke tests for the agent-logger scaffold."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from agent_logger import __version__
from agent_logger.config import DEFAULTS, find_repo_config, load_config
from agent_logger.segmenter import prepare_log
from agent_logger.segmenter.platform import detect_machine, sanitize_path_component


def test_version_matches_build_info() -> None:
    """``_build_info.__version__`` must track ``pyproject.toml`` (version triplet)."""
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    assert match, "version not found in pyproject.toml"
    assert __version__ == match.group(1)


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


def test_repo_config_overrides_log_layout_only(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "sync:\n  target: onedrive\nlog:\n  path_template: global/{title}.md\n",
        encoding="utf-8",
    )

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / ".agent-logger.yaml").write_text(
        "\n".join(
            [
                "sync:",
                "  target: ssh",
                "log:",
                "  root: .",
                "  path_template: logs/{year}/{month}.{day} {title}.md",
                "  template: |",
                "    # {title}",
                "",
                "    **Date:** {date}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(repo / "src")

    cfg = load_config(home=home)

    assert cfg.repo_config_path == repo / ".agent-logger.yaml"
    assert find_repo_config() == repo / ".agent-logger.yaml"
    assert cfg.sync_target == "onedrive"
    assert cfg.log_root == repo
    assert cfg.log_path_template == "logs/{year}/{month}.{day} {title}.md"
    assert cfg.log_template is not None
    assert "**Date:** {date}" in cfg.log_template


def test_repo_config_can_be_disabled(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(
        "log:\n  path_template: logs/{title}.md\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENT_LOGGER_REPO_CONFIG", "0")

    cfg = load_config(home=tmp_path / "home")

    assert cfg.repo_config_path is None
    assert cfg.log_path_template == DEFAULTS["log"]["path_template"]


def test_prepare_log_reports_repo_organization(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(
        "\n".join(
            [
                "log:",
                "  root: .",
                '  path_template: "logs/{year}/{month}.{day} {title}.md"',
                "  template: |",
                "    ## Summary",
                "",
                "    ## Follow-up",
            ]
        ),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    session = home / ".copilot" / "session-state" / "session-id"
    session.mkdir(parents=True)
    (session / "events.jsonl").write_text(
        '{"timestamp": "2026-07-22T18:00:00Z"}\n', encoding="utf-8"
    )
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(home / ".agent-logger"))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(
        sys,
        "argv",
        ["prepare-session-log", "--json", "--session", "session-id", "--title", "Test"],
    )

    prepare_log.main()

    result = json.loads(capsys.readouterr().out)
    assert result["output_root"] == str(repo)
    assert result["log_path_template"] == "logs/{year}/{month}.{day} {title}.md"
    assert result["log_template"] == "## Summary\n\n## Follow-up"
    assert Path(result["log_path"]).parent == repo / "logs" / result["date"][:4]


def test_detect_machine_nonempty() -> None:
    assert detect_machine()


def test_sanitize_path_component_ntfs() -> None:
    assert sanitize_path_component('a:b/c"d') == "a -b-c'd"
    assert sanitize_path_component("   ") == "Untitled"
    # Reserved device name gets prefixed.
    assert sanitize_path_component("CON").startswith("_")
