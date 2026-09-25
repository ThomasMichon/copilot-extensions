from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]


def _load_guard():
    path = PLUGIN / "scripts" / "copilot-mention-guard.py"
    spec = importlib.util.spec_from_file_location("copilot_mention_guard", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# command_publishes_copilot_mention -- same logic as the facility-wide guard
# this plugin's version was derived from; kept in lockstep intentionally.
# ---------------------------------------------------------------------------

def test_pr_comment_with_mention_is_denied() -> None:
    guard = _load_guard()
    cmd = 'gh pr comment 3554 -R ThomasMichon/copilot-extensions --body "@copilot review"'
    assert guard.command_publishes_copilot_mention(cmd) is True


def test_pr_comment_without_mention_is_allowed() -> None:
    guard = _load_guard()
    cmd = (
        'gh pr comment 3554 -R ThomasMichon/copilot-extensions '
        '--body "Fixed the typo, please take another look."'
    )
    assert guard.command_publishes_copilot_mention(cmd) is False


def test_api_comment_reply_with_mention_is_denied() -> None:
    guard = _load_guard()
    cmd = (
        "gh api repos/ThomasMichon/copilot-extensions/pulls/3554/comments/123/replies "
        '-f body="@copilot please re-review"'
    )
    assert guard.command_publishes_copilot_mention(cmd) is True


def test_api_read_only_comment_listing_is_allowed() -> None:
    guard = _load_guard()
    cmd = "gh api repos/ThomasMichon/copilot-extensions/pulls/3554/comments"
    assert guard.command_publishes_copilot_mention(cmd) is False


def test_legitimate_copilot_extensions_handle_is_allowed() -> None:
    guard = _load_guard()
    cmd = (
        'gh pr comment 42 -R ThomasMichon/copilot-extensions '
        '--body "Reviewed against agent-worktrees@copilot-extensions v1.5.5."'
    )
    assert guard.command_publishes_copilot_mention(cmd) is False


def test_body_file_with_mention_is_denied(tmp_path: Path) -> None:
    guard = _load_guard()
    body_file = tmp_path / "body.md"
    body_file.write_text("Please have @copilot take a look.\n", encoding="utf-8")
    cmd = f'gh pr comment 3554 -R owner/repo --body-file "{body_file}"'
    assert guard.command_publishes_copilot_mention(cmd) is True


def test_semicolon_inside_quoted_body_does_not_truncate_the_scan() -> None:
    guard = _load_guard()
    cmd = 'gh pr comment 1 -R owner/repo --body "Fixed the bug; @copilot review"'
    assert guard.command_publishes_copilot_mention(cmd) is True


def test_pipe_inside_quoted_body_does_not_truncate_the_scan() -> None:
    guard = _load_guard()
    cmd = 'gh pr comment 1 -R owner/repo --body "note: use pipe | then @copilot"'
    assert guard.command_publishes_copilot_mention(cmd) is True


def test_non_gh_command_mentioning_copilot_is_allowed() -> None:
    guard = _load_guard()
    cmd = 'echo "never use @copilot in a PR comment" >> docs/notes.md'
    assert guard.command_publishes_copilot_mention(cmd) is False


# ---------------------------------------------------------------------------
# Repo scoping -- this guard applies ONLY to this plugin's own repo.
# ---------------------------------------------------------------------------

def test_target_repo_is_read_from_manifest() -> None:
    guard = _load_guard()
    assert guard.target_repo_from_manifest(PLUGIN) == "thomasmichon/copilot-extensions"


def test_target_repo_survives_a_git_suffix(tmp_path: Path) -> None:
    guard = _load_guard()
    (tmp_path / "plugin.json").write_text(
        json.dumps({"repository": "https://github.com/someorg/some-repo.git"}),
        encoding="utf-8",
    )
    assert guard.target_repo_from_manifest(tmp_path) == "someorg/some-repo"


def test_current_repo_reads_the_git_remote(tmp_path: Path) -> None:
    guard = _load_guard()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:someorg/some-repo.git"],
        cwd=tmp_path, check=True,
    )
    assert guard.current_repo(str(tmp_path)) == "someorg/some-repo"


def test_current_repo_none_outside_any_git_repo(tmp_path: Path) -> None:
    guard = _load_guard()
    assert guard.current_repo(str(tmp_path)) is None


def test_hook_is_a_noop_outside_the_target_repo(tmp_path: Path) -> None:
    """The end-to-end entry point: a session working in an unrelated repo
    (even with this plugin enabled purely for its instruction projections)
    must never be denied, no matter what the command says."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:someorg/unrelated-repo.git"],
        cwd=tmp_path, check=True,
    )
    payload = json.dumps({
        "toolName": "bash",
        "cwd": str(tmp_path),
        "toolArgs": {"command": 'gh pr comment 1 -R owner/repo --body "@copilot review"'},
    })
    result = subprocess.run(
        ["python3", str(PLUGIN / "scripts" / "copilot-mention-guard.py")],
        input=payload, capture_output=True, text=True,
        env={"COPILOT_PLUGIN_ROOT": str(PLUGIN)},
        cwd=str(tmp_path),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_hook_denies_inside_the_target_repo() -> None:
    """End-to-end: run from a real checkout of this plugin's own target
    repo (this repo itself), the guard must fire."""
    payload = json.dumps({
        "toolName": "bash",
        "toolArgs": {"command": 'gh pr comment 1 -R owner/repo --body "@copilot review"'},
    })
    repo_root = PLUGIN.resolve().parents[1]
    result = subprocess.run(
        ["python3", str(PLUGIN / "scripts" / "copilot-mention-guard.py")],
        input=payload, capture_output=True, text=True,
        env={"COPILOT_PLUGIN_ROOT": str(PLUGIN)},
        cwd=str(repo_root),
    )
    assert result.returncode == 0
    decision = json.loads(result.stdout)
    assert decision["permissionDecision"] == "deny"
