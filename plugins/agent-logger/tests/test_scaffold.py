"""Smoke tests for the agent-logger scaffold."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from agent_logger import __version__
from agent_logger.__main__ import main as cli_main
from agent_logger.config import (
    DEFAULTS,
    RepositoryConfigError,
    find_repo_config,
    load_config,
)
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
                "schema_version: 1",
                "log:",
                "  root: .",
                "  path_template: logs/{year}/{month}.{day} {title}.md",
                "  template: |",
                "    # {title}",
                "",
                "    **Date:** {date}",
                "  narration_style: Use brief section introductions.",
                "  exemplars:",
                "    - docs/example.md",
                "  closing_remark: End with one concise takeaway.",
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
    assert cfg.narration_style == "Use brief section introductions."
    assert cfg.exemplars == ["docs/example.md"]
    assert cfg.closing_remark == "End with one concise takeaway."


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("schema_version: 2\nlog: {}\n", "schema_version must be 1"),
        ("schema_version: 1\nlog:\n  root: ../logs\n", "must not escape"),
        (
            'schema_version: 1\nlog:\n  path_template: "{repository}/{title}.md"\n',
            r"unsupported placeholder \{repository\}",
        ),
        (
            "schema_version: 1\nlog:\n  template: ['not markdown']\n",
            "log.template must be a non-empty string",
        ),
        (
            "schema_version: 1\nlog:\n  voice_pack: surprise\n",
            r"unsupported field\(s\): voice_pack",
        ),
        (
            "schema_version: 1\nlog:\n  closing_remark: [not, text]\n",
            "log.closing_remark must be null or a non-empty string",
        ),
        (
            "schema_version: 1\nlog:\n  exemplars: [valid, '']\n",
            "log.exemplars must be null",
        ),
        (
            "schema_version: 1\nsync:\n  target: ssh\nlog: {}\n",
            r"unsupported field\(s\): sync",
        ),
    ],
)
def test_repo_config_validation_errors(
    tmp_path: Path, monkeypatch, body: str, message: str
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(body, encoding="utf-8")
    monkeypatch.chdir(repo)

    with pytest.raises(RepositoryConfigError, match=message):
        load_config(home=tmp_path / "home")


def test_non_logging_config_ignores_invalid_repo_file(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(
        "schema_version: 1\nlog:\n  root: ../outside\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    cfg = load_config(home=tmp_path / "home", include_repo=False)

    assert cfg.repo_config_path is None
    assert cfg.sync_target == "local"


def test_missing_explicit_repo_config_is_an_error(
    tmp_path: Path, monkeypatch
) -> None:
    missing = tmp_path / "missing.yaml"
    monkeypatch.setenv("AGENT_LOGGER_REPO_CONFIG", str(missing))

    with pytest.raises(RepositoryConfigError, match="does not name a file"):
        load_config(home=tmp_path / "home")


def test_config_cli_reports_repository_validation_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(
        "schema_version: 1\nlog:\n  root: ../outside\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(tmp_path / "home"))

    assert cli_main(["config"]) == 2
    assert "invalid repository configuration" in capsys.readouterr().err


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


def test_organization_cli_reports_manifest_ready_config(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                "log:",
                "  root: records",
                "  closing_remark: End with a short summary.",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(tmp_path / "home"))

    assert cli_main(["organization"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["repository_root"] == str(repo)
    assert result["config_path"] == str(repo / ".agent-logger.yaml")
    assert result["manifest"]["output_root"] == str(repo / "records")
    assert result["manifest"]["closing_remark"] == "End with a short summary."


def test_organization_defaults_to_repo_logs(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(tmp_path / "home"))

    assert cli_main(["organization"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["manifest"]["output_root"] == str(repo / "logs")
    assert result["manifest"]["closing_remark"] is None


def test_prepare_log_reports_repo_organization(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                "log:",
                "  root: .",
                '  path_template: "logs/{year}/{month}.{day} {title}.md"',
                "  template: |",
                "    ## Summary",
                "",
                "    ## Follow-up",
                "  narration_style: Use short asides.",
                "  closing_remark: Close with one sentence.",
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
    assert result["narration_style"] == "Use short asides."
    assert result["closing_remark"] == "Close with one sentence."
    assert Path(result["log_path"]).parent == repo / "logs" / result["date"][:4]


def test_detect_machine_nonempty() -> None:
    assert detect_machine()


def test_sanitize_path_component_ntfs() -> None:
    assert sanitize_path_component('a:b/c"d') == "a -b-c'd"
    assert sanitize_path_component("   ") == "Untitled"
    # Reserved device name gets prefixed.
    assert sanitize_path_component("CON").startswith("_")
