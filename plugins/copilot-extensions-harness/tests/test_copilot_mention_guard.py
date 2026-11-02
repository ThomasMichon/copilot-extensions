from __future__ import annotations

import builtins
import importlib.util
import io
import json
import os
import shlex
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LEGACY_POWERSHELL_LOOKUP = (
    "$py = $null; foreach ($candidate in @('python3','python','py')) { "
    "$found = Get-Command $candidate -CommandType Application -All -ErrorAction SilentlyContinue "
    "| Where-Object { $_.Source -notmatch '\\\\WindowsApps\\\\' } | Select-Object -First 1; "
    "if ($found) { $py = $found; break } }; "
)


def _load_guard():
    path = PLUGIN / "scripts" / "copilot-mention-guard.py"
    spec = importlib.util.spec_from_file_location("copilot_mention_guard", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _enable_harness(root):
    settings = root / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"enabledPlugins": {
        "copilot-extensions-harness@copilot-extensions": True,
    }}), encoding="utf-8")


@pytest.fixture
def adopted_harness(tmp_path, monkeypatch):
    _enable_harness(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COPILOT_PROJECT_DIR", str(tmp_path))
    return tmp_path


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
    cmd = f"gh api repos/owner/repo/issues/1/comments --method POST --input {shlex.quote(str(payload))}"
    assert guard.command_publishes_copilot_mention(cmd) == "mention"


def test_api_input_file_without_mention_is_allowed(tmp_path: Path) -> None:
    guard = _load_guard()
    payload = tmp_path / "body.json"
    payload.write_text('{"body": "Looks good, thanks!"}', encoding="utf-8")
    cmd = f"gh api repos/owner/repo/issues/1/comments --method POST --input {shlex.quote(str(payload))}"
    assert guard.command_publishes_copilot_mention(cmd) is None


def test_api_input_equals_form_with_mention_is_denied(tmp_path: Path) -> None:
    guard = _load_guard()
    payload = tmp_path / "body.json"
    payload.write_text('{"body": "@copilot review"}', encoding="utf-8")
    cmd = f"gh api repos/owner/repo/issues/1/comments --method POST --input={shlex.quote(str(payload))}"
    assert guard.command_publishes_copilot_mention(cmd) == "mention"


def test_api_input_stdin_is_denied_fail_closed() -> None:
    guard = _load_guard()
    cmd = "printf '{}' | gh api repos/owner/repo/issues/1/comments --method POST --input -"
    assert guard.command_publishes_copilot_mention(cmd) == "unscannable"


def test_api_input_unparseable_json_is_denied_fail_closed(tmp_path: Path) -> None:
    guard = _load_guard()
    payload = tmp_path / "body.json"
    payload.write_text("not valid json", encoding="utf-8")
    cmd = f"gh api repos/owner/repo/issues/1/comments --method POST --input {shlex.quote(str(payload))}"
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


def test_unresolved_body_file_variable_is_denied_fail_closed() -> None:
    """PR #3663 review (round 8): _read_bounded() previously returned "" for
    any OSError, making an uninspectable body file look identical to an
    empty, harmless one. `BODY_FILE=x.md; gh pr comment ... --body-file
    "$BODY_FILE"` is valid shell -- this tokenizer sees the literal
    (unexpanded) '$BODY_FILE' string, the open() fails, and the guard must
    NOT read that failure as "nothing to worry about"."""
    guard = _load_guard()
    cmd = 'gh pr comment 1 -R owner/repo --body-file "$BODY_FILE_DOES_NOT_EXIST"'
    assert guard.command_publishes_copilot_mention(cmd) == "unscannable"


def test_missing_body_file_equals_form_is_denied_fail_closed() -> None:
    guard = _load_guard()
    cmd = "gh pr comment 1 -R owner/repo --body-file=/nonexistent/path.md"
    assert guard.command_publishes_copilot_mention(cmd) == "unscannable"


def test_missing_api_f_body_file_is_denied_fail_closed() -> None:
    guard = _load_guard()
    cmd = "gh api repos/o/r/pulls/1/comments -F body=@/nonexistent/path.md"
    assert guard.command_publishes_copilot_mention(cmd) == "unscannable"


def test_readable_body_file_is_still_scanned_normally(tmp_path: Path) -> None:
    """Companion true-negative: a body file that DOES exist and contains no
    mention must still be allowed -- the fail-closed fix must not turn
    every --body-file into an automatic deny."""
    guard = _load_guard()
    body_file = tmp_path / "body.md"
    body_file.write_text("Looks good, thanks!\n", encoding="utf-8")
    cmd = f'gh pr comment 1 -R owner/repo --body-file "{body_file}"'
    assert guard.command_publishes_copilot_mention(cmd) is None


def test_oversized_body_file_is_denied_fail_closed(tmp_path: Path) -> None:
    """PR #3663 review (round 9): _read_bounded() silently dropped
    everything past its 200,000-character limit, so a mention appearing
    only after that boundary published undetected. Must fail closed on
    truncation instead of treating a cut-off prefix as complete. Exercises
    the truncation branch directly via a small *limit* override (writing an
    actual 200KB+ fixture file would be wasteful for a unit test)."""
    guard = _load_guard()
    big_file = tmp_path / "big.md"
    big_file.write_text("y" * 10, encoding="utf-8")
    assert guard._read_bounded(str(big_file), limit=5) is None
    assert guard._read_bounded(str(big_file), limit=20) == "y" * 10


def test_api_issue_body_write_with_mention_is_denied() -> None:
    """PR #3663 review (round 9): the endpoint filter only recognized
    /comments and /reviews, so a PR/issue's own body update (PATCH
    /issues/1 or /pulls/1) -- also part of this guard's documented scope
    -- was treated as a non-write and allowed."""
    guard = _load_guard()
    cmd = "gh api repos/o/r/issues/1 --method PATCH -f 'body=cc @copilot review'"
    assert guard.command_publishes_copilot_mention(cmd) == "mention"


def test_api_long_field_form_file_ref_is_scanned(tmp_path: Path) -> None:
    """PR #3663 review (round 9): the classifier recognized --field as an
    alias of -F, but extraction only handled -f/-F literally -- a
    --field body=@path file reference fell through to scanning the literal
    filename instead of reading it."""
    guard = _load_guard()
    body_file = tmp_path / "body.md"
    body_file.write_text("@copilot review", encoding="utf-8")
    cmd = f"gh api repos/o/r/issues/1/comments --field body=@{shlex.quote(str(body_file))}"
    assert guard.command_publishes_copilot_mention(cmd) == "mention"


def test_unexpanded_shell_variable_body_is_denied_fail_closed() -> None:
    """PR #3663 review (round 9, previously-missed finding): shlex's quote
    removal leaves an unexpanded $VAR/${VAR}/$(...)/backtick reference as
    plain text -- shell expansion happens only when gh actually runs, after
    this hook has already decided. The real published text is never the
    literal '$BODY' string this hook can see."""
    guard = _load_guard()
    assert guard.command_publishes_copilot_mention(
        'gh pr comment 1 -R owner/repo --body "$BODY"'
    ) == "unscannable"
    assert guard.command_publishes_copilot_mention(
        'gh pr comment 1 -R owner/repo --body "${BODY}"'
    ) == "unscannable"
    assert guard.command_publishes_copilot_mention(
        "gh pr comment 1 -R owner/repo --body \"$(cat notes.txt)\""
    ) == "unscannable"
    assert guard.command_publishes_copilot_mention(
        'gh api repos/o/r/issues/1 --method PATCH -f body="$BODY"'
    ) == "unscannable"


def test_literal_dollar_sign_without_variable_syntax_is_still_scanned() -> None:
    """Companion true-negative: a literal '$' that isn't actually a
    variable/command-substitution reference (no following identifier char,
    '{', '(', or backtick) must not be treated as unscannable."""
    guard = _load_guard()
    cmd = 'gh pr comment 1 -R owner/repo --body "Cost is $5, all good"'
    assert guard.command_publishes_copilot_mention(cmd) is None


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
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, creationflags=NO_WINDOW)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:someorg/some-repo.git"],
        cwd=tmp_path, check=True, creationflags=NO_WINDOW,
    )
    assert guard.current_repo_candidates(str(tmp_path)) == ["someorg/some-repo"]
    assert guard.current_repo_matches("someorg/some-repo", str(tmp_path)) is True
    assert guard.current_repo_matches("someorg/other-repo", str(tmp_path)) is False


def test_current_repo_empty_outside_any_git_repo(tmp_path: Path, monkeypatch) -> None:
    guard = _load_guard()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert guard.current_repo_candidates(str(tmp_path)) == []
    assert guard.current_repo_matches("someorg/some-repo", str(tmp_path)) is False


def test_current_repo_matches_a_non_origin_remote(tmp_path: Path) -> None:
    """PR #3663 review (round 5): this codebase's own convention allows a
    configurable remote name (RepoConfig.remote), and a fork checkout
    commonly also carries an 'upstream' remote -- checking only 'origin'
    silently no-ops the guard for either shape."""
    guard = _load_guard()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, creationflags=NO_WINDOW)
    subprocess.run(
        ["git", "remote", "add", "upstream", "git@github.com:someorg/some-repo.git"],
        cwd=tmp_path, check=True, creationflags=NO_WINDOW,
    )
    assert guard.current_repo_matches("someorg/some-repo", str(tmp_path)) is True


def test_hook_is_a_noop_outside_the_target_repo(tmp_path: Path) -> None:
    """The end-to-end entry point: a session working in an unrelated repo
    (even with this plugin enabled purely for its instruction projections)
    must never be denied, no matter what the command says."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, creationflags=NO_WINDOW)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:someorg/unrelated-repo.git"],
        cwd=tmp_path, check=True, creationflags=NO_WINDOW,
    )
    payload = json.dumps({
        "toolName": "bash",
        "cwd": str(tmp_path),
        "toolArgs": {"command": 'gh pr comment 1 -R owner/repo --body "@copilot review"'},
    })
    result = subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / "copilot-mention-guard.py")],
        input=payload, capture_output=True, text=True,
        env={**os.environ, "COPILOT_PLUGIN_ROOT": str(PLUGIN)}, creationflags=NO_WINDOW,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


@pytest.mark.parametrize("adopted", [False, True])
def test_hook_denies_inside_the_target_repo(tmp_path, adopted) -> None:
    """End-to-end: run from a real checkout of this plugin's own target
    repo (this repo itself), the guard must fire."""
    payload = json.dumps({
        "toolName": "bash",
        "toolArgs": {"command": 'gh pr comment 1 -R owner/repo --body "@copilot review"'},
    })
    repo_root = tmp_path
    if adopted:
        _enable_harness(repo_root)
    subprocess.run(["git", "init", "-q"], cwd=repo_root, check=True, creationflags=NO_WINDOW)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/ThomasMichon/copilot-extensions.git"],
                   cwd=repo_root, check=True, creationflags=NO_WINDOW)
    result = subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / "copilot-mention-guard.py")],
        input=payload, capture_output=True, text=True,
        env={**os.environ, "COPILOT_PLUGIN_ROOT": str(PLUGIN), "COPILOT_PROJECT_DIR": str(repo_root)}, creationflags=NO_WINDOW,
        cwd=str(repo_root),
    )
    assert result.returncode == 0
    if adopted:
        assert json.loads(result.stdout)["permissionDecision"] == "deny"
    else:
        assert result.stdout == ""


@pytest.mark.parametrize("command", [
    "git status --short",
    "python -m pytest",
    'echo "gh pr comment --body @copilot"',
    "gh api repos/owner/repo/issues/1/comments",
    "gh pr view 1",
    "env GH_TOKEN=x command gh issue list",
])
def test_irrelevant_hook_has_no_discovery_or_body_io(monkeypatch, capsys, command):
    guard = _load_guard()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "toolName": "powershell", "toolArgs": {"command": command},
    })))

    def unexpected(*args, **kwargs):
        pytest.fail("irrelevant command performed discovery or body IO")

    monkeypatch.setattr(subprocess, "run", unexpected)
    monkeypatch.setattr(guard, "target_repo_from_manifest", unexpected)
    monkeypatch.setattr(guard, "_extract_body_text", unexpected)
    assert guard.main() == 0
    assert capsys.readouterr().out == ""


def test_irrelevant_entry_does_not_import_subprocess(monkeypatch, capsys):
    real_import = builtins.__import__
    imports = []

    def track_import(name, *args, **kwargs):
        imports.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", track_import)
    guard = _load_guard()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "toolName": "powershell", "toolArgs": {"command": "git status --short"},
    })))
    assert guard.main() == 0
    assert capsys.readouterr().out == ""
    assert "subprocess" not in imports


def test_deferred_import_is_charged_to_remaining_decision_budget(monkeypatch):
    guard = _load_guard()
    real_import = builtins.__import__
    clock = [10.0]
    timeouts = []

    def slow_import(name, *args, **kwargs):
        if name == "subprocess":
            clock[0] += 0.25
        return real_import(name, *args, **kwargs)

    def run(argv, **kwargs):
        timeouts.append(kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, "")

    monkeypatch.setattr(builtins, "__import__", slow_import)
    monkeypatch.setattr(guard.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(subprocess, "run", run)
    assert guard.current_repo_candidates(deadline=11.0) == []
    assert timeouts == [0.75]


@pytest.mark.parametrize("command", [
    "gh pr comment 1 --body-file -",
    "env GH_TOKEN=x command gh issue create --fill",
    "echo ready\ngh api repos/o/r/issues/1/comments --input -",
    'gh pr review 1 --body "$BODY"',
    "gh issue comment 1 --body-file missing.md",
])
@pytest.mark.parametrize("scoped", [True, False])
def test_relevance_preserves_unknown_body_and_repository_scope(
    monkeypatch, capsys, command, scoped, adopted_harness,
):
    guard = _load_guard()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "toolName": "bash", "toolArgs": {"command": command},
    })))
    monkeypatch.setattr(guard, "target_repo_from_manifest", lambda *a: "o/r")
    monkeypatch.setattr(guard, "current_repo_matches", lambda *a, **kw: scoped)
    assert guard.main() == 0
    output = capsys.readouterr().out
    assert bool(output) == scoped
    if scoped:
        assert json.loads(output)["permissionDecision"] == "deny"


def test_remote_discovery_is_one_process_with_remaining_budget(monkeypatch):
    guard = _load_guard()
    calls = []
    monkeypatch.setattr(guard.time, "monotonic", lambda: 10.25)

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        lines = [
            f"remote{i}\thttps://github.com/o/r{i}.git (fetch) [blob:none]\n"
            f"remote{i}\thttps://github.com/o/push{i}.git (push)\n"
            for i in range(100)
        ]
        return subprocess.CompletedProcess(argv, 0, "".join(lines))

    monkeypatch.setattr(subprocess, "run", run)
    assert len(guard.current_repo_candidates(deadline=11.0)) == 100
    assert len(calls) == 1
    assert calls[0][0] == ["git", "remote", "--verbose"]
    assert calls[0][1]["timeout"] == pytest.approx(0.75)
    assert guard.current_repo_candidates(deadline=10.0) == []
    assert len(calls) == 1


def test_expired_scope_budget_does_not_read_body(monkeypatch, capsys, adopted_harness):
    guard = _load_guard()
    clock = [0.0]
    monkeypatch.setattr(guard.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "toolName": "bash",
        "toolArgs": {"command": "gh pr comment 1 --body-file missing.md"},
    })))
    monkeypatch.setattr(guard, "target_repo_from_manifest", lambda *a: "o/r")

    def matches(*args, deadline=None):
        assert deadline == guard.DECISION_BUDGET_SECONDS
        clock[0] = deadline
        return True

    monkeypatch.setattr(guard, "current_repo_matches", matches)
    monkeypatch.setattr(
        guard, "_extract_body_text",
        lambda *a: pytest.fail("body read started after the decision deadline"),
    )
    assert guard.main() == 0
    assert json.loads(capsys.readouterr().out)["permissionDecision"] == "deny"


def test_git_timeout_reaps_the_actual_child(monkeypatch):
    guard = _load_guard()
    real_run = subprocess.run
    real_popen = subprocess.Popen
    children = []

    def popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    def slow_git(argv, **kwargs):
        return real_run([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess, "run", slow_git)
    started = time.monotonic()
    assert guard.current_repo_candidates(deadline=started + 0.15) == []
    assert time.monotonic() - started < 2
    assert len(children) == 1
    assert children[0].poll() is not None


def test_body_read_wait_consumes_only_remaining_deadline(monkeypatch):
    import threading

    guard = _load_guard()
    release = threading.Event()
    finished = threading.Event()

    def blocked_open(*args, **kwargs):
        try:
            release.wait(5)
            raise OSError("fixture read unavailable")
        finally:
            finished.set()

    monkeypatch.setattr("builtins.open", blocked_open)
    started = time.monotonic()
    try:
        assert guard._read_bounded("blocked", deadline=started + 0.05) is None
        assert time.monotonic() - started < 1
    finally:
        release.set()
        assert finished.wait(2)


def test_repo_probe_preserves_fetch_url_rewrites_and_ignores_push_urls(tmp_path):
    guard = _load_guard()

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, creationflags=NO_WINDOW)

    git("init", "-q")
    git("config", "url.https://github.com/example/.insteadOf", "fixture:")
    git("remote", "add", "upstream", "fixture:target.git")
    git("config", "remote.upstream.promisor", "true")
    git("config", "remote.upstream.partialclonefilter", "blob:none")
    git("config", "--add", "remote.upstream.url", "https://github.com/example/second.git")
    git("remote", "set-url", "--push", "upstream", "https://github.com/example/push.git")
    assert guard.current_repo_candidates(str(tmp_path)) == ["example/target"]


def test_local_relevance_preserves_file_input_without_literal_mention(tmp_path, monkeypatch, capsys, adopted_harness):
    guard = _load_guard()
    body = tmp_path / "body.txt"
    body.write_text("@copilot review", encoding="utf-8")
    command = f"env command gh pr comment 1 --body-file {shlex.quote(str(body))}"
    assert "@copilot" not in command
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "toolName": "bash", "toolArgs": {"command": command},
    })))
    monkeypatch.setattr(guard, "target_repo_from_manifest", lambda *a: "o/r")
    monkeypatch.setattr(guard, "current_repo_matches", lambda *a, **kw: True)
    assert guard.main() == 0
    assert json.loads(capsys.readouterr().out)["permissionDecision"] == "deny"


@pytest.mark.parametrize("case", [
    "python_startup", "python_irrelevant", "python_relevant",
    "shell_startup", "shell_irrelevant", "shell_relevant",
    "shell_legacy-irrelevant", "shell_legacy-relevant",
])
def test_native_hook_process_counts_and_latency(tmp_path, capsys, case):
    """Observe real CLI boundaries without executing the proposed gh command."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, creationflags=NO_WINDOW)
    _enable_harness(tmp_path)
    config = tmp_path / ".git" / "config"
    with config.open("a", encoding="utf-8") as stream:
        for index in range(24):
            stream.write(
                f'\n[remote "r{index}"]\n'
                "url = https://github.com/ThomasMichon/copilot-extensions.git\n"
            )
    script = PLUGIN / "scripts" / "copilot-mention-guard.py"
    audited = (
        "import atexit,json,runpy,sys; calls=[];"
        "sys.addaudithook(lambda event,args: calls.append(args[1]) "
        "if event=='subprocess.Popen' else None);"
        "atexit.register(lambda: print(json.dumps(calls),file=sys.stderr));"
        "runpy.run_path(sys.argv[1],run_name='__main__')"
    )
    env = {**os.environ, "COPILOT_PLUGIN_ROOT": str(PLUGIN), "COPILOT_PROJECT_DIR": str(tmp_path)}
    cases = {
        "irrelevant": "git status --short",
        "relevant": "gh pr comment 1 --body-file -",
    }
    boundary, name = case.split("_")
    if boundary == "shell":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if os.name != "nt" or not shell:
            pytest.skip("Windows PowerShell hook measurement")
        hook = json.loads((PLUGIN / "hooks.json").read_text())["hooks"]["preToolUse"][0]["powershell"]
        if name.startswith("legacy-"):
            name = name.removeprefix("legacy-")
            hook = (
                hook[:hook.index("$py = $null;")]
                + LEGACY_POWERSHELL_LOOKUP
                + hook[hook.index("if (-not $py)"): ]
            )
        argv = [shell, "-NoProfile", "-NonInteractive", "-Command",
                "exit 0" if name == "startup" else hook]
    else:
        argv = [sys.executable, "-c", "pass" if name == "startup" else audited, str(script)]
    payload = json.dumps({
        "toolName": "powershell", "toolArgs": {"command": cases.get(name, "git status --short")},
    })
    elapsed = []
    for _ in range(3):
        started = time.monotonic()
        result = subprocess.run(
            argv, input=payload, cwd=tmp_path, env=env, capture_output=True,
            text=True, timeout=8, creationflags=NO_WINDOW,
        )
        elapsed.append(time.monotonic() - started)
        assert result.returncode == 0, result.stderr
        if boundary == "python" and name != "startup":
            calls = json.loads(result.stderr)
            expected = "git remote --verbose" if os.name == "nt" else ["git", "remote", "--verbose"]
            assert calls == ([] if name == "irrelevant" else [expected])
        assert bool(result.stdout) == (name == "relevant")
    metrics = {
        "boundary": case,
        "samples": len(elapsed),
        "median_seconds": round(statistics.median(elapsed), 4),
        "max_seconds": round(max(elapsed), 4),
    }
    if boundary == "python" and name != "startup":
        metrics["git_children"] = len(calls)
    with capsys.disabled():
        print("\nmention-guard native measurement: " + json.dumps(metrics, sort_keys=True))


def test_hook_exits_with_blocked_file_reader(tmp_path):
    _enable_harness(tmp_path)
    script = PLUGIN / "scripts" / "copilot-mention-guard.py"
    wrapper = """
import runpy, sys, time
guard = runpy.run_path(sys.argv[1])
scope = guard["main"].__globals__
scope["target_repo_from_manifest"] = lambda *a: "o/r"
scope["current_repo_matches"] = lambda *a, **kw: True
scope["adopting_harness"] = lambda *a, **kw: True
def blocked_open(*a, **kw):
    time.sleep(30)
scope["open"] = blocked_open
raise SystemExit(guard["main"]())
"""
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", wrapper, str(script)],
        input=json.dumps({"toolName": "bash", "toolArgs": {
            "command": "gh pr comment 1 --body-file blocked",
        }}),
        cwd=tmp_path, capture_output=True, text=True, timeout=6, creationflags=NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["permissionDecision"] == "deny"
    assert time.monotonic() - started < 6


@pytest.mark.parametrize("names,expected", [
    (["bin/python.cmd", "bin/python3.cmd"], "bin/python3.cmd"),
    (["WindowsApps/python3.cmd", "bin/python3.cmd"], "bin/python3.cmd"),
    (["WindowsApps/python3.cmd", "bin/python.cmd"], "bin/python.cmd"),
    (["bin/py.cmd"], "bin/py.cmd"),
    ([], None),
])
def test_powershell_lookup_preserves_selection_without_cmdlet_pipelines(tmp_path, names, expected):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if os.name != "nt" or not shell:
        pytest.skip("Windows application resolver contract")
    for directory in ("WindowsApps", "bin"):
        (tmp_path / directory).mkdir()
    for name in names:
        (tmp_path / name).write_text("@echo off\n", encoding="utf-8")
    hook = json.loads((PLUGIN / "hooks.json").read_text())["hooks"]["preToolUse"][0]["powershell"]
    lookup = hook[:hook.index("try { $result =")] + "if ($py) { Write-Output $py.Source }"
    legacy = LEGACY_POWERSHELL_LOOKUP + "if (-not $py) { exit 0 }; Write-Output $py.Source"
    env = {
        **os.environ,
        "PATH": os.pathsep.join(str(tmp_path / directory) for directory in ("WindowsApps", "bin")),
        "COPILOT_PLUGIN_ROOT": str(PLUGIN),
    }
    for command in (legacy, lookup):
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command", command],
            env=env, capture_output=True, text=True, timeout=8, creationflags=NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == (str(tmp_path / expected) if expected else "")


def test_hot_killswitch_restores_mention_guard(tmp_path, monkeypatch, capsys, adopted_harness):
    guard = _load_guard()
    state = tmp_path / "pretool.json"
    monkeypatch.setenv("PRETOOL_KILLSWITCH_STATE_FILE", str(state))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(guard, "current_repo_matches", lambda *a, **k: True)
    payload = json.dumps({"toolName": "powershell", "toolArgs": {
        "command": 'gh pr comment 42 -R ThomasMichon/copilot-extensions --body "@copilot review"',
    }})
    for active in (False, True, False):
        state.write_text(json.dumps({"active": active}), encoding="utf-8")
        monkeypatch.setattr(guard.sys, "stdin", io.StringIO(payload))
        assert guard.main() == 0
        output = capsys.readouterr().out
        assert output == "" if active else json.loads(output)["permissionDecision"] == "deny"


def test_active_killswitch_returns_before_reading_input(tmp_path, monkeypatch):
    guard = _load_guard()
    state = tmp_path / "pretool.json"
    state.write_text('{"active": true}', encoding="utf-8")
    monkeypatch.setenv("PRETOOL_KILLSWITCH_STATE_FILE", str(state))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    class Unreadable:
        def read(self, *args):
            pytest.fail("active switch reached guard input")
    monkeypatch.setattr(guard.sys, "stdin", Unreadable())
    assert guard.main() == 0


@pytest.mark.parametrize("adoption", ["missing", "disabled", "malformed", "enabled", "local-disabled"])
def test_only_project_adopters_reach_repository_policy(tmp_path, monkeypatch, capsys, adoption):
    guard = _load_guard()
    settings = tmp_path / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    if adoption in {"enabled", "local-disabled"}:
        _enable_harness(tmp_path)
    elif adoption == "disabled":
        settings.write_text('{"enabledPlugins":{"copilot-extensions-harness@copilot-extensions":false}}')
    elif adoption == "malformed":
        settings.write_text("{")
    if adoption == "local-disabled":
        settings.with_name("settings.local.json").write_text(
            '{"enabledPlugins":{"copilot-extensions-harness@copilot-extensions":false}}')
    global_home = tmp_path / "global-home"
    global_settings = global_home / ".copilot" / "settings.json"
    global_settings.parent.mkdir(parents=True)
    global_settings.write_text('{"enabledPlugins":{"copilot-extensions-harness@copilot-extensions":true}}')
    monkeypatch.setenv("HOME", str(global_home))
    monkeypatch.setenv("USERPROFILE", str(global_home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(guard, "current_repo_matches", lambda *a, **kw: True)
    monkeypatch.setattr(guard, "target_repo_from_manifest", lambda *a: "o/r")
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO(json.dumps({
        "toolName": "powershell", "cwd": str(tmp_path),
        "toolArgs": {"command": 'gh pr comment 1 --body "@copilot review"'},
    })))
    assert guard.run_guard() == 0
    output = capsys.readouterr().out
    assert bool(output) == (adoption == "enabled")
    if output:
        assert json.loads(output)["permissionDecision"] == "deny"


def test_adoption_stops_at_nested_repository_boundary(tmp_path):
    guard = _load_guard()
    _enable_harness(tmp_path)
    nested = tmp_path / "nested"
    (nested / ".git").mkdir(parents=True)
    assert not guard.adopting_harness(str(nested), time.monotonic() + 1)
    assert guard.adopting_harness(str(tmp_path / "child"), time.monotonic() + 1)


def test_unexpected_guard_failure_is_a_successful_noop(monkeypatch, capsys):
    guard = _load_guard()
    def failure():
        raise RuntimeError("private exception details")
    monkeypatch.setattr(guard, "main", failure)
    assert guard.run_guard() == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "skipped" in captured.err
    assert "private exception details" not in captured.err


@pytest.mark.parametrize("failure", ["missing", "crash", "caught"])
@pytest.mark.parametrize("native_shell", ["powershell.exe", "pwsh", "bash"])
def test_shipped_wrapper_fails_open_on_unavailable_guard(tmp_path, failure, native_shell):
    shell = shutil.which(native_shell)
    if shell is None:
        pytest.skip(f"{native_shell} is unavailable")
    if os.name == "nt" and native_shell == "bash":
        pytest.skip("Windows bash may select an unrelated WSL environment")
    payload = tmp_path / "payload"
    (payload / "scripts").mkdir(parents=True)
    if failure == "crash":
        (payload / "scripts" / "copilot-mention-guard.py").write_text(
            "import sys\nprint('invalid partial output')\nraise SystemExit(7)\n", encoding="utf-8")
    if failure == "caught":
        source = str(PLUGIN / "scripts" / "copilot-mention-guard.py")
        (payload / "scripts" / "copilot-mention-guard.py").write_text(
            "import runpy,sys\nns=runpy.run_path(" + repr(source) + ")\n"
            + "def fail(): raise RuntimeError('private exception details')\n"
            + "ns['run_guard'].__globals__['main']=fail\n"
            + "raise SystemExit(ns['run_guard'](**({'wrapped':True} if '--wrapped' in sys.argv else {})))\n", encoding="utf-8")
    hook = json.loads((PLUGIN / "hooks.json").read_text())["hooks"]["preToolUse"][0]
    command = hook["bash" if native_shell == "bash" else "powershell"]
    argv = [shell, "-c", command] if native_shell == "bash" else [
        shell, "-NoProfile", "-NonInteractive", "-Command", command,
    ]
    env = {**os.environ, "COPILOT_PLUGIN_ROOT": str(payload),
           "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
    result = subprocess.run(argv, input="{}", capture_output=True, text=True, env=env,
                            cwd=tmp_path, timeout=10, creationflags=NO_WINDOW)
    assert result.returncode == 0, result.stderr
    assert not result.stdout.strip()
    assert "Traceback" not in result.stderr
    assert "skipped" in result.stderr
    assert "private exception details" not in result.stderr


def test_plugin_process_directory_is_not_session_adoption(tmp_path, monkeypatch):
    guard = _load_guard()
    _enable_harness(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COPILOT_PROJECT_DIR", raising=False)
    assert not guard.adopting_harness(None, time.monotonic() + 1)
    monkeypatch.setenv("COPILOT_PROJECT_DIR", str(tmp_path))
    assert guard.adopting_harness(None, time.monotonic() + 1)


@pytest.mark.parametrize("nested_adopted", [False, True])
def test_explicit_tool_directory_precedes_project_fallback(tmp_path, monkeypatch, capsys, nested_adopted):
    guard = _load_guard()
    _enable_harness(tmp_path)
    nested = tmp_path / "nested"
    (nested / ".git").mkdir(parents=True)
    if nested_adopted:
        _enable_harness(nested)
    monkeypatch.setenv("COPILOT_PROJECT_DIR", str(tmp_path))
    inspected = []
    def matches(target, cwd, **kwargs):
        inspected.append(cwd)
        return True
    monkeypatch.setattr(guard, "current_repo_matches", matches)
    monkeypatch.setattr(guard, "target_repo_from_manifest", lambda *a: "o/r")
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO(json.dumps({
        "toolName": "powershell", "toolArgs": {
            "cwd": str(nested), "command": 'gh pr comment 1 --body "@copilot review"',
        },
    })))
    assert guard.run_guard() == 0
    output = capsys.readouterr().out
    assert bool(output) == nested_adopted
    assert inspected == ([str(nested)] if nested_adopted else [])


@pytest.mark.parametrize("body_args", [
    "--body-file body.txt", "--body-file=body.txt", "-F body=@body.txt",
    "--input body.json", "--input=body.json",
])
@pytest.mark.parametrize("mention_in_workspace", [False, True])
def test_relative_body_reads_use_resolved_workspace(tmp_path, monkeypatch, capsys, body_args, mention_in_workspace):
    guard = _load_guard()
    workspace = tmp_path / "workspace"
    process = tmp_path / "process"
    workspace.mkdir()
    process.mkdir()
    _enable_harness(workspace)
    for directory, mention in ((workspace, mention_in_workspace), (process, not mention_in_workspace)):
        text = "@copilot review" if mention else "safe body"
        (directory / "body.txt").write_text(text, encoding="utf-8")
        (directory / "body.json").write_text(json.dumps({"body": text}), encoding="utf-8")
    monkeypatch.chdir(process)
    monkeypatch.setattr(guard, "current_repo_matches", lambda *a, **kw: True)
    monkeypatch.setattr(guard, "target_repo_from_manifest", lambda *a: "o/r")
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO(json.dumps({
        "cwd": str(workspace), "toolName": "bash", "toolArgs": {
            "command": ("gh pr comment 1 " if body_args.startswith("--body-file")
                        else "gh api repos/o/r/issues/1/comments --method POST ") + body_args,
        },
    })))
    assert guard.run_guard() == 0
    output = capsys.readouterr().out
    assert bool(output) == mention_in_workspace


@pytest.mark.parametrize("target_adopted", [False, True])
def test_linked_workspace_uses_physical_repository_boundary(tmp_path, target_adopted):
    guard = _load_guard()
    outer = tmp_path / "outer"
    _enable_harness(outer)
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    target = checkout / "subdirectory"
    target.mkdir()
    if target_adopted:
        _enable_harness(checkout)
    link = outer / "linked-subdirectory"
    if os.name == "nt":
        created = subprocess.run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW,
        )
        assert created.returncode == 0, (created.stdout, created.stderr)
    else:
        link.symlink_to(target, target_is_directory=True)
    assert guard.adopting_harness(str(link), time.monotonic() + 1) == target_adopted
