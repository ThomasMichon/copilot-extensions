"""Tests for the `identifiers` CLI dispatch surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import identifier_blocklist_cli as iblk_cli
from agent_worktrees import repos


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """Redirect ~ so the registry reads/writes under a tmp dir.

    Every repo in this file is registered with an explicit ``plat="windows"``
    path, so ``_current_platform()`` must be pinned to ``"windows"`` too --
    otherwise ``RepoEntry.local_path()`` resolves against whatever OS the
    test suite actually runs on (Ubuntu in CI), finds no matching path, and
    every sweep silently skips every source.
    """
    monkeypatch.setattr(repos.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.setattr(repos, "_current_platform", lambda: "windows")
    return tmp_path


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def test_parse_sweep_args_defaults():
    target, fmt, error = iblk_cli._parse_sweep_args([])
    assert target is None
    assert fmt == "ci"
    assert error is None


def test_parse_sweep_args_repo_and_format():
    target, fmt, error = iblk_cli._parse_sweep_args(["--repo", "foo", "--format", "json"])
    assert target == "foo"
    assert fmt == "json"
    assert error is None


def test_parse_sweep_args_json_shorthand():
    target, fmt, error = iblk_cli._parse_sweep_args(["--json"])
    assert fmt == "json"
    assert error is None


def test_parse_sweep_args_missing_repo_value_errors():
    _, _, error = iblk_cli._parse_sweep_args(["--repo"])
    assert error == "--repo requires a value"


def test_parse_sweep_args_missing_format_value_errors():
    _, _, error = iblk_cli._parse_sweep_args(["--format"])
    assert error == "--format requires a value"


def test_parse_sweep_args_unknown_format_errors():
    _, _, error = iblk_cli._parse_sweep_args(["--format", "yaml"])
    assert "unknown --format" in error


def test_parse_sweep_args_unknown_flag_errors():
    _, _, error = iblk_cli._parse_sweep_args(["--bogus"])
    assert error == "unknown argument: --bogus"


# ---------------------------------------------------------------------------
# cmd_identifiers_dispatch(): stdout/stderr discipline + exit codes
# ---------------------------------------------------------------------------

def test_sweep_rejects_unknown_argument(home: Path, capsys):
    rc = iblk_cli.cmd_identifiers_dispatch(["sweep", "--bogus"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "unknown argument" in captured.out


def test_sweep_empty_result_emits_nothing_on_stdout(home: Path, capsys):
    rc = iblk_cli.cmd_identifiers_dispatch(["sweep", "--repo", "no-such-repo"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no applicable blocklist entries" in captured.err


def test_sweep_ci_output_on_stdout_only(home: Path, tmp_path: Path, capsys):
    source = tmp_path / "source"
    source.mkdir()
    d = source / ".identifier-blocklist"
    d.mkdir()
    (d / "block-for-public.yaml").write_text("entries:\n  - token: foo\n", encoding="utf-8")
    repos.add_repo("source", str(source), repo_class="worktree", plat="windows")
    repos.add_repo("target", str(tmp_path / "target"), repo_class="worktree",
                   visibility="public", plat="windows")
    capsys.readouterr()  # discard add_repo's own "registered" confirmations

    rc = iblk_cli.cmd_identifiers_dispatch(["sweep", "--repo", "target"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "foo"


def test_sweep_json_format_actually_emits_json(home: Path, tmp_path: Path, capfd):
    rc = iblk_cli.cmd_identifiers_dispatch(["sweep", "--repo", "no-such-repo", "--format", "json"])
    assert rc == 0
    captured = capfd.readouterr()
    assert captured.out.strip().startswith("{")


def test_sweep_malformed_blocklist_reports_error(home: Path, tmp_path: Path, capsys):
    source = tmp_path / "source"
    source.mkdir()
    d = source / ".identifier-blocklist"
    d.mkdir()
    (d / "block-for-public.yaml").write_text("entries: [unterminated", encoding="utf-8")
    repos.add_repo("source", str(source), repo_class="worktree", plat="windows")
    repos.add_repo("target", str(tmp_path / "target"), repo_class="worktree",
                   visibility="public", plat="windows")
    capsys.readouterr()  # discard add_repo's own "registered" confirmations

    rc = iblk_cli.cmd_identifiers_dispatch(["sweep", "--repo", "target"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "invalid YAML" in captured.err
    assert captured.out == ""


def test_sweep_malformed_blocklist_withholds_ci_stdout_entirely(
    home: Path, tmp_path: Path, capsys,
):
    """The ci-format stream is all-or-nothing: a failure must never emit a
    partial denylist on stdout, since a consumer piping it directly (e.g.
    `secret set`) could silently replace a complete denylist with a partial
    one. Partial results remain available via --format json for diagnostics
    only (see test_sweep_json_format_includes_partial_entries_on_failure)."""
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".identifier-blocklist").mkdir()
    (broken / ".identifier-blocklist" / "block-for-public.yaml").write_text(
        "entries: [unterminated", encoding="utf-8",
    )
    good = tmp_path / "good"
    good.mkdir()
    (good / ".identifier-blocklist").mkdir()
    (good / ".identifier-blocklist" / "block-for-public.yaml").write_text(
        "entries:\n  - token: still-valid\n", encoding="utf-8",
    )
    repos.add_repo("broken", str(broken), repo_class="worktree", plat="windows")
    repos.add_repo("good", str(good), repo_class="worktree", plat="windows")
    repos.add_repo("target", str(tmp_path / "target"), repo_class="worktree",
                   visibility="public", plat="windows")
    capsys.readouterr()  # discard add_repo's own "registered" confirmations

    rc = iblk_cli.cmd_identifiers_dispatch(["sweep", "--repo", "target"])
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid YAML" in captured.err


def test_sweep_json_format_includes_partial_entries_on_failure(
    home: Path, tmp_path: Path, capfd,
):
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".identifier-blocklist").mkdir()
    (broken / ".identifier-blocklist" / "block-for-public.yaml").write_text(
        "entries: [unterminated", encoding="utf-8",
    )
    good = tmp_path / "good"
    good.mkdir()
    (good / ".identifier-blocklist").mkdir()
    (good / ".identifier-blocklist" / "block-for-public.yaml").write_text(
        "entries:\n  - token: still-valid\n", encoding="utf-8",
    )
    repos.add_repo("broken", str(broken), repo_class="worktree", plat="windows")
    repos.add_repo("good", str(good), repo_class="worktree", plat="windows")
    repos.add_repo("target", str(tmp_path / "target"), repo_class="worktree",
                   visibility="public", plat="windows")
    capfd.readouterr()  # discard add_repo's own "registered" confirmations

    rc = iblk_cli.cmd_identifiers_dispatch(["sweep", "--repo", "target", "--format", "json"])
    assert rc == 1
    captured = capfd.readouterr()
    payload = json.loads(captured.out)
    assert "invalid YAML" in payload["error"]
    assert [e["token"] for e in payload["entries"]] == ["still-valid"]
