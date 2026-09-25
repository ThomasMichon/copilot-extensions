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
    assert guard.command_publishes_copilot_mention(cmd) is not None


def test_pr_comment_without_mention_is_allowed() -> None:
    guard = _load_guard()
    cmd = (
        'gh pr comment 3554 -R ThomasMichon/copilot-extensions '
        '--body "Fixed the typo, please take another look."'
    )
    assert guard.command_publishes_copilot_mention(cmd) is None


def test_api_comment_reply_with_mention_is_denied() -> None:
    guard = _load_guard()
    cmd = (
        "gh api repos/ThomasMichon/copilot-extensions/pulls/3554/comments/123/replies "
        '-f body="@copilot please re-review"'
    )
    assert guard.command_publishes_copilot_mention(cmd) is not None


def test_api_read_only_comment_listing_is_allowed() -> None:
    guard = _load_guard()
    cmd = "gh api repos/ThomasMichon/copilot-extensions/pulls/3554/comments"
    assert guard.command_publishes_copilot_mention(cmd) is None


def test_legitimate_copilot_extensions_handle_is_allowed() -> None:
    guard = _load_guard()
    cmd = (
        'gh pr comment 42 -R ThomasMichon/copilot-extensions '
        '--body "Reviewed against agent-worktrees@copilot-extensions v1.5.5."'
    )
    assert guard.command_publishes_copilot_mention(cmd) is None


def test_body_file_with_mention_is_denied(tmp_path: Path) -> None:
    guard = _load_guard()
    body_file = tmp_path / "body.md"
    body_file.write_text("Please have @copilot take a look.\n", encoding="utf-8")
    cmd = f'gh pr comment 3554 -R owner/repo --body-file "{body_file}"'
    assert guard.command_publishes_copilot_mention(cmd) is not None


def test_body_file_equals_form_with_mention_is_denied(tmp_path: Path) -> None:
    """PR #3663 review: ``--body-file=PATH`` (the equals form gh also
    accepts) was only ever scanned as a literal path string, not read as a
    file -- a mention-bearing body file silently bypassed the guard."""
    guard = _load_guard()
    body_file = tmp_path / "body.md"
    body_file.write_text("Please have @copilot take a look.\n", encoding="utf-8")
    cmd = f'gh pr comment 3554 -R owner/repo --body-file="{body_file}"'
    assert guard.command_publishes_copilot_mention(cmd) is not None


def test_semicolon_inside_quoted_body_does_not_truncate_the_scan() -> None:
    guard = _load_guard()
    cmd = 'gh pr comment 1 -R owner/repo --body "Fixed the bug; @copilot review"'
    assert guard.command_publishes_copilot_mention(cmd) is not None


def test_pipe_inside_quoted_body_does_not_truncate_the_scan() -> None:
    guard = _load_guard()
    cmd = 'gh pr comment 1 -R owner/repo --body "note: use pipe | then @copilot"'
    assert guard.command_publishes_copilot_mention(cmd) is not None


def test_multiline_command_is_still_inspected() -> None:
    """PR #3663 review (round 4): shlex.shlex treats '\\n' as ordinary
    whitespace, never a statement separator, regardless of
    punctuation_chars -- a genuine multi-line tool command (a very common
    shape: 'echo ok\\ngh pr comment ...') was read as a single token stream
    starting with 'echo', so _is_gh() skipped it and the gh invocation on
    the second line was never reached at all."""
    guard = _load_guard()
    cmd = 'echo ok\ngh pr comment 1 -R owner/repo --body "@copilot review"'
    assert guard.command_publishes_copilot_mention(cmd) is not None


def test_multiline_command_with_a_real_newline_inside_a_quoted_body() -> None:
    """The companion case: a literal newline INSIDE a quoted --body value is
    real body content, not a statement boundary -- it must stay part of the
    same token, not fool the new newline-splitter into treating what
    follows as a separate (and therefore skipped) statement."""
    guard = _load_guard()
    cmd = 'gh pr comment 1 -R owner/repo --body "line one\nline two @copilot review"'
    assert guard.command_publishes_copilot_mention(cmd) is not None


def test_non_gh_command_mentioning_copilot_is_allowed() -> None:
    guard = _load_guard()
    cmd = 'echo "never use @copilot in a PR comment" >> docs/notes.md'
    assert guard.command_publishes_copilot_mention(cmd) is None


def test_stdin_body_file_is_denied_fail_closed() -> None:
    """PR #3663 review (round 3): `gh` accepts `--body-file -` to read the
    body from stdin, which a preToolUse hook cannot inspect (it runs before
    the command executes, with no visibility into what will be piped in) --
    must fail CLOSED (deny) rather than silently allow an unscannable
    publish through."""
    guard = _load_guard()
    cmd = "printf '@copilot review' | gh pr comment 1 -R owner/repo --body-file -"
    assert guard.command_publishes_copilot_mention(cmd) == "unscannable"


def test_stdin_body_file_equals_form_is_denied_fail_closed() -> None:
    guard = _load_guard()
    cmd = "printf '@copilot review' | gh pr comment 1 -R owner/repo --body-file=-"
    assert guard.command_publishes_copilot_mention(cmd) == "unscannable"


def test_api_stdin_field_is_denied_fail_closed() -> None:
    guard = _load_guard()
    cmd = "printf '@copilot review' | gh api repos/o/r/pulls/1/comments -F body=@-"
    assert guard.command_publishes_copilot_mention(cmd) == "unscannable"


def test_api_input_file_with_mention_is_denied(tmp_path: Path) -> None:
    """PR #3663 review (round 6): gh api also accepts --input <file>/--input -
    to send a raw JSON request body -- the -f/-F body= detector alone missed
    this entirely, so a mention inside the JSON body field published
    unchecked."""
    guard = _load_guard()
    payload = tmp_path / "body.json"
    payload.write_text('{"body": "@copilot review"}', encoding="utf-8")
    cmd = f"gh api repos/owner/repo/issues/1/comments --method POST --input {payload}"
    assert guard.command_publishes_copilot_mention(cmd) == "mention"


def test_api_input_file_without_mention_is_allowed(tmp_path: Path) -> None:
    guard = _load_guard()
    payload = tmp_path / "body.json"
    payload.write_text('{"body": "Looks good, thanks!"}', encoding="utf-8")
    cmd = f"gh api repos/owner/repo/issues/1/comments --method POST --input {payload}"
    assert guard.command_publishes_copilot_mention(cmd) is None


def test_api_input_equals_form_with_mention_is_denied(tmp_path: Path) -> None:
    guard = _load_guard()
    payload = tmp_path / "body.json"
    payload.write_text('{"body": "@copilot review"}', encoding="utf-8")
    cmd = f"gh api repos/owner/repo/issues/1/comments --method POST --input={payload}"
    assert guard.command_publishes_copilot_mention(cmd) == "mention"


def test_api_input_stdin_is_denied_fail_closed() -> None:
    guard = _load_guard()
    cmd = "printf '{}' | gh api repos/owner/repo/issues/1/comments --method POST --input -"
    assert guard.command_publishes_copilot_mention(cmd) == "unscannable"


def test_api_input_unparseable_json_is_denied_fail_closed(tmp_path: Path) -> None:
    guard = _load_guard()
    payload = tmp_path / "body.json"
    payload.write_text("not valid json", encoding="utf-8")
    cmd = f"gh api repos/owner/repo/issues/1/comments --method POST --input {payload}"
    assert guard.command_publishes_copilot_mention(cmd) == "unscannable"


def test_mention_and_unscannable_are_distinct_reasons() -> None:
    guard = _load_guard()
    mention_cmd = 'gh pr comment 1 -R owner/repo --body "@copilot review"'
    assert guard.command_publishes_copilot_mention(mention_cmd) == "mention"


def test_env_assignment_prefix_is_recognized() -> None:
    """PR #3663 review (round 7): `gh` need not be the literal first token
    of a shell statement -- an ordinary env-var-assignment prefix like
    `GH_TOKEN="$TOKEN" gh pr comment ...` is valid shell and must not skip
    detection just because `gh` isn't tokens[0]."""
    guard = _load_guard()
    cmd = 'GH_TOKEN="x" gh pr comment 1 -R owner/repo --body "@copilot review"'
    assert guard.command_publishes_copilot_mention(cmd) == "mention"


def test_env_wrapper_prefix_is_recognized() -> None:
    guard = _load_guard()
    cmd = 'env GH_TOKEN=x gh pr comment 1 -R owner/repo --body "@copilot review"'
    assert guard.command_publishes_copilot_mention(cmd) == "mention"


def test_pr_create_without_explicit_body_is_denied_fail_closed() -> None:
    """PR #3663 review (round 7): `gh pr create`/`gh issue create` can
    derive their body from an interactive prompt or `--fill` (commit
    messages) when no explicit --body/--body-file/--input is given --
    content that never appears in argv at all and so can never be scanned;
    must fail CLOSED rather than silently allow it."""
    guard = _load_guard()
    cmd = "gh pr create --title x -R owner/repo --fill"
    assert guard.command_publishes_copilot_mention(cmd) == "unscannable"


def test_pr_create_with_explicit_body_is_still_scanned() -> None:
    guard = _load_guard()
    denied = 'gh pr create --title x -R owner/repo --body "@copilot review"'
    assert guard.command_publishes_copilot_mention(denied) == "mention"
    allowed = 'gh pr create --title x -R owner/repo --body "Looks good"'
    assert guard.command_publishes_copilot_mention(allowed) is None


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


def test_target_repo_allows_a_dot_in_the_repo_name(tmp_path: Path) -> None:
    """PR #3663 review: the repo-name capture excluded ``.``, so a real repo
    with a dot in its name (GitHub permits this) silently disabled the
    guard entirely (both sides returned None -> scope check always no-op)."""
    guard = _load_guard()
    (tmp_path / "plugin.json").write_text(
        json.dumps({"repository": "https://github.com/someorg/repo.name"}),
        encoding="utf-8",
    )
    assert guard.target_repo_from_manifest(tmp_path) == "someorg/repo.name"


def test_current_repo_reads_the_git_remote(tmp_path: Path) -> None:
    guard = _load_guard()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:someorg/some-repo.git"],
        cwd=tmp_path, check=True,
    )
    assert guard.current_repo_candidates(str(tmp_path)) == ["someorg/some-repo"]
    assert guard.current_repo_matches("someorg/some-repo", str(tmp_path)) is True
    assert guard.current_repo_matches("someorg/other-repo", str(tmp_path)) is False


def test_current_repo_empty_outside_any_git_repo(tmp_path: Path) -> None:
    guard = _load_guard()
    assert guard.current_repo_candidates(str(tmp_path)) == []
    assert guard.current_repo_matches("someorg/some-repo", str(tmp_path)) is False


def test_current_repo_matches_a_non_origin_remote(tmp_path: Path) -> None:
    """PR #3663 review (round 5): this codebase's own convention allows a
    configurable remote name (RepoConfig.remote), and a fork checkout
    commonly also carries an 'upstream' remote -- checking only 'origin'
    silently no-ops the guard for either shape."""
    guard = _load_guard()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "remote", "add", "upstream", "git@github.com:someorg/some-repo.git"],
        cwd=tmp_path, check=True,
    )
    assert guard.current_repo_matches("someorg/some-repo", str(tmp_path)) is True


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
