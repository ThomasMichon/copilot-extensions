from __future__ import annotations

import builtins
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, NoReturn, Protocol, cast

import pytest

if TYPE_CHECKING:
    import copilot_mention_guard as guard_source

    class GuardModule(Protocol):
        DECISION_BUDGET_SECONDS: float
        sys: ModuleType
        time: ModuleType

        def main(self) -> int: ...
        def run_guard(self, *, wrapped: bool = False) -> int: ...
        def target_repo_from_manifest(self, plugin_root: Path, deadline: float | None = None) -> str | None: ...
        def current_repo_candidates(self, cwd: str | None = None, *, deadline: float | None = None) -> list[str]: ...
        def current_repo_matches(
            self, target: str, cwd: str | None = None, *, deadline: float | None = None,
        ) -> bool: ...
        def adopting_harness(self, cwd: str | None, deadline: float) -> bool: ...
        def command_publishes_copilot_mention(
            self, command: str, *, statements: list[list[str]] | None = None,
            deadline: float | None = None, cwd: str | None = None,
        ) -> str | None: ...
        def _read_bounded(
            self, path: str, limit: int = 200_000, *, deadline: float | None = None,
            errors: str = "replace", cwd: str | None = None,
        ) -> str | None: ...

    _guard_contract: GuardModule = guard_source

PLUGIN = Path(__file__).resolve().parents[1]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SKIPPED = "[copilot-extensions-maintainer] mention guard unavailable; skipped\n"


def _hook(variant: str) -> str:
    return cast(str, json.loads((PLUGIN / "hooks.json").read_text())["hooks"]["preToolUse"][0][variant])


def _native_bash() -> str | None:
    """Return a POSIX bash; on Windows only Git for Windows' bash, never WSL."""
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if not git:
        return None
    for parent in Path(git).resolve().parents:
        candidate = parent / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def _bash_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    text = str(path).replace("\\", "/")
    return "/" + text[0].lower() + text[2:] if text[1:2] == ":" else text


def _python_shims(directory: Path) -> Path:
    """Put interpreter shims first on PATH; any launch leaves a marker file."""
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / "python-started"
    for name in ("python", "python3", "py"):
        script = directory / name
        script.write_text(f"#!/bin/sh\necho {name} >> '{_bash_path(marker)}'\nexit 3\n", encoding="utf-8")
        script.chmod(0o755)
        (directory / f"{name}.cmd").write_text(
            f"@echo {name}>>\"{marker}\"\r\n@exit /b 3\r\n", encoding="utf-8")
    return marker


def _load_guard() -> GuardModule:
    path = PLUGIN / "scripts" / "copilot-mention-guard.py"
    spec = importlib.util.spec_from_file_location("copilot_mention_guard", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast("GuardModule", module)


def _enable_harness(root: Path) -> None:
    settings = root / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"enabledPlugins": {
        "copilot-extensions-maintainer@copilot-extensions": True,
    }}), encoding="utf-8")


@pytest.fixture
def adopted_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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


def test_current_repo_empty_outside_any_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
def test_hook_denies_inside_the_target_repo(tmp_path: Path, adopted: bool) -> None:
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
def test_irrelevant_hook_has_no_discovery_or_body_io(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str,
) -> None:
    guard = _load_guard()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "toolName": "powershell", "toolArgs": {"command": command},
    })))

    def unexpected(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("irrelevant command performed discovery or body IO")

    monkeypatch.setattr(subprocess, "run", unexpected)
    monkeypatch.setattr(guard, "target_repo_from_manifest", unexpected)
    monkeypatch.setattr(guard, "_extract_body_text", unexpected)
    assert guard.main() == 0
    assert capsys.readouterr().out == ""


def test_irrelevant_entry_does_not_import_subprocess(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    real_import = builtins.__import__
    imports = []

    def track_import(name: str, *args: Any, **kwargs: Any) -> ModuleType:
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


def test_deferred_import_is_charged_to_remaining_decision_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = _load_guard()
    real_import = builtins.__import__
    clock = [10.0]
    timeouts = []

    def slow_import(name: str, *args: Any, **kwargs: Any) -> ModuleType:
        if name == "subprocess":
            clock[0] += 0.25
        return real_import(name, *args, **kwargs)

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
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
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    command: str, scoped: bool, adopted_harness: Path,
) -> None:
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


def test_remote_discovery_is_one_process_with_remaining_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = _load_guard()
    calls = []
    monkeypatch.setattr(guard.time, "monotonic", lambda: 10.25)

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
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


def test_expired_scope_budget_does_not_read_body(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], adopted_harness: Path,
) -> None:
    guard = _load_guard()
    clock = [0.0]
    monkeypatch.setattr(guard.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "toolName": "bash",
        "toolArgs": {"command": "gh pr comment 1 --body-file missing.md"},
    })))
    monkeypatch.setattr(guard, "target_repo_from_manifest", lambda *a: "o/r")

    def matches(*args: object, deadline: float | None = None) -> bool:
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


def test_git_timeout_reaps_the_actual_child(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = _load_guard()
    real_run = subprocess.run
    real_popen = subprocess.Popen
    children = []

    def popen(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    def slow_git(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        return real_run([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess, "run", slow_git)
    started = time.monotonic()
    assert guard.current_repo_candidates(deadline=started + 0.15) == []
    assert time.monotonic() - started < 2
    assert len(children) == 1
    assert children[0].poll() is not None


def test_body_read_wait_consumes_only_remaining_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    guard = _load_guard()
    release = threading.Event()
    finished = threading.Event()

    def blocked_open(*args: object, **kwargs: object) -> NoReturn:
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


def test_repo_probe_preserves_fetch_url_rewrites_and_ignores_push_urls(tmp_path: Path) -> None:
    guard = _load_guard()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, creationflags=NO_WINDOW)

    git("init", "-q")
    git("config", "url.https://github.com/example/.insteadOf", "fixture:")
    git("remote", "add", "upstream", "fixture:target.git")
    git("config", "remote.upstream.promisor", "true")
    git("config", "remote.upstream.partialclonefilter", "blob:none")
    git("config", "--add", "remote.upstream.url", "https://github.com/example/second.git")
    git("remote", "set-url", "--push", "upstream", "https://github.com/example/push.git")
    assert guard.current_repo_candidates(str(tmp_path)) == ["example/target"]


def test_local_relevance_preserves_file_input_without_literal_mention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    adopted_harness: Path,
) -> None:
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


def _native_shell(variant: str) -> str:
    if variant == "bash":
        shell = _native_bash()
    else:
        shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip(f"No native {variant} shell is available")
    return shell


def _guarded_workspace(root: Path, *, adopted: bool = True, remote: str | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, timeout=10, creationflags=NO_WINDOW)
    target = remote or _load_guard().target_repo_from_manifest(PLUGIN)
    assert target
    with (root / ".git" / "config").open("a", encoding="utf-8") as stream:
        stream.write(f'\n[remote "origin"]\nurl = https://github.com/{target}.git\n')
    if adopted:
        _enable_harness(root)
    (root / "pretool.json").write_text('{"active": false}', encoding="utf-8")
    (root / "body.txt").write_text("hello @copilot please", encoding="utf-8")
    (root / "clean.txt").write_text("hello copilot-extensions reviewers", encoding="utf-8")
    (root / "body.json").write_text(json.dumps({"body": "@Copilot fix"}), encoding="utf-8")
    (root / "broken.json").write_text("{nope", encoding="utf-8")
    (root / "nul-split.txt").write_bytes(b"hello @copilot\x00-extensions")
    (root / "nul-join.txt").write_bytes(b"hello @cop\x00ilot")
    (root / "nul.json").write_bytes(b'{"body": "ok\x00"}')
    return root


def _run_native(
    variant: str, root: Path, event: dict[str, Any], shims: Path, *, plugin: Path = PLUGIN,
) -> subprocess.CompletedProcess[str]:
    shell = _native_shell(variant)
    convert = _bash_path if variant == "bash" else str
    payload = {**event, "cwd": convert(root)}
    env = {
        **os.environ,
        "COPILOT_PLUGIN_ROOT": convert(plugin),
        "PRETOOL_KILLSWITCH_STATE_FILE": convert(root / "pretool.json"),
        "PATH": str(shims) + os.pathsep + os.environ.get("PATH", ""),
    }
    env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    env.pop("COPILOT_PROJECT_DIR", None)
    if variant == "bash":
        hook_script = shims / "native-hook.sh"
        hook_script.write_text(_hook("bash") + "\n", encoding="utf-8", newline="\n")
        argv = [shell, convert(hook_script)]
    else:
        argv = [shell, "-NoProfile", "-NonInteractive", "-Command", _hook("powershell")]
    return subprocess.run(
        argv, input=json.dumps(payload), cwd=root, env=env, capture_output=True,
        text=True, encoding="utf-8", timeout=15, creationflags=NO_WINDOW,
    )


def _run_reference(root: Path, event: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "COPILOT_PLUGIN_ROOT": str(PLUGIN),
        "PRETOOL_KILLSWITCH_STATE_FILE": str(root / "pretool.json"),
    }
    env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    env.pop("COPILOT_PROJECT_DIR", None)
    return subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / "copilot-mention-guard.py"), "--wrapped"],
        input=json.dumps({**event, "cwd": str(root)}), cwd=root, env=env, capture_output=True,
        text=True, encoding="utf-8", timeout=15, creationflags=NO_WINDOW,
    )


_REFERENCE_CACHE: dict[int, str] = {}

PARITY_EVENTS: list[tuple[str, str, dict[str, Any]]] = [
    (workspace, tool, {"command": command})
    for workspace, tool, command in [
        ("adopted", "bash", 'gh pr comment 1 -R ThomasMichon/copilot-extensions --body "@copilot review"'),
        ("adopted", "bash", 'gh pr comment 1 --body "Fixed the typo, please take another look."'),
        ("adopted", "powershell", 'gh api repos/o/r/pulls/1/comments/2/replies -f body="@copilot please re-review"'),
        ("adopted", "powershell", "gh api repos/o/r/pulls/1/comments"),
        ("adopted", "bash", 'gh pr comment 42 --body "Reviewed against agent-worktrees@copilot-extensions v1.5."'),
        ("adopted", "bash", "gh pr comment 1 --body-file body.txt"),
        ("adopted", "bash", "gh pr comment 1 --body-file=clean.txt"),
        ("adopted", "bash", "gh pr comment 1 --body-file -"),
        ("adopted", "bash", "gh api repos/o/r/issues/1/comments --input body.json"),
        ("adopted", "bash", "gh api repos/o/r/issues/1/comments --input broken.json"),
        ("adopted", "bash", "gh api repos/o/r/issues/1/comments -F body=@missing.md"),
        ("adopted", "bash", 'env GH_TOKEN=x command gh issue comment 1 --body "@Copilot"'),
        ("adopted", "bash", 'echo "gh pr comment 1 --body @copilot"'),
        ("adopted", "bash", 'git status --short && gh pr review 1 --comment -b "a; @copilot b"'),
        ("adopted", "bash", 'gh pr comment 1 --body "line one\n@copilot line two"'),
        ("adopted", "bash", 'gh pr comment 1 --body "$BODY"'),
        ("adopted", "bash", 'gh pr edit 1 --body "@copilot-extensions and x@copilot"'),
        ("adopted", "bash", 'gh pr edit 1 --body "@cop\u0131lot \u00e9\U0001f680"'),
        ("adopted", "bash", "gh issue create --title x --body $'@copilot'"),
        ("adopted", "bash", "gh pr comment 1 --body-file nul-split.txt"),
        ("adopted", "powershell", "gh pr comment 1 --body-file nul-join.txt"),
        ("adopted", "bash", "gh api repos/o/r/issues/1/comments --input nul.json"),
        ("adopted", "powershell", "git status --short"),
        ("adopted", "view", 'gh pr comment 1 --body "@copilot review"'),
        ("unadopted", "bash", 'gh pr comment 1 --body "@copilot review"'),
        ("foreign", "bash", 'gh pr comment 1 --body "@copilot review"'),
    ]
]


@pytest.fixture(scope="module")
def parity_workspaces(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    _REFERENCE_CACHE.clear()
    base = tmp_path_factory.mktemp("native-parity")
    return {
        "adopted": _guarded_workspace(base / "adopted"),
        "unadopted": _guarded_workspace(base / "unadopted", adopted=False),
        "foreign": _guarded_workspace(base / "foreign", remote="example/elsewhere"),
    }


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.parametrize("variant", ["powershell", "bash"])
@pytest.mark.parametrize("case", range(len(PARITY_EVENTS)))
def test_native_hook_matches_reference_guard_without_python(
    tmp_path: Path, parity_workspaces: dict[str, Path], variant: str, case: int,
) -> None:
    workspace, tool, args = PARITY_EVENTS[case]
    root = parity_workspaces[workspace]
    marker = _python_shims(tmp_path / "shims")
    # Alternate the toolArgs encoding so both forms stay covered without
    # doubling the process count; the reference result is shared by variants.
    event = {"toolName": tool, "toolArgs": json.dumps(args) if case % 2 else args}
    # Both guards share a 1.5 s decision budget, so a loaded host can expire
    # either one; retry a bounded number of times before failing.
    for attempt in range(3):
        if attempt or case not in _REFERENCE_CACHE:
            reference = _run_reference(root, event)
            assert reference.returncode == 0, reference.stderr
            _REFERENCE_CACHE[case] = reference.stdout.strip()
        actual = _run_native(variant, root, event, marker.parent)
        assert actual.returncode == 0, actual.stderr
        assert actual.stderr == ""
        if actual.stdout.strip() == _REFERENCE_CACHE[case]:
            break
    assert actual.stdout.strip() == _REFERENCE_CACHE[case], args
    assert not marker.exists(), "native mention-guard hook started a Python interpreter"


@pytest.mark.parametrize("variant", ["powershell", "bash"])
@pytest.mark.parametrize("name", ["irrelevant", "relevant"])
def test_native_hook_latency_without_python(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], variant: str, name: str,
) -> None:
    """Observe the shipped hook at its real CLI boundary without running gh."""
    root = _guarded_workspace(tmp_path / "workspace")
    marker = _python_shims(tmp_path / "shims")
    command = "gh pr comment 1 --body-file -" if name == "relevant" else "git status --short"
    elapsed = []
    for _ in range(3):
        started = time.monotonic()
        result = _run_native(variant, root, {"toolName": variant, "toolArgs": {"command": command}}, marker.parent)
        elapsed.append(time.monotonic() - started)
        assert result.returncode == 0, result.stderr
        assert bool(result.stdout) == (name == "relevant")
    assert not marker.exists(), "native mention-guard hook started a Python interpreter"
    metrics = {
        "boundary": f"{variant}_{name}",
        "samples": len(elapsed),
        "median_seconds": round(statistics.median(elapsed), 4),
        "max_seconds": round(max(elapsed), 4),
    }
    with capsys.disabled():
        print("\nmention-guard native measurement: " + json.dumps(metrics, sort_keys=True))


def test_registered_hooks_never_launch_python() -> None:
    hook = json.loads((PLUGIN / "hooks.json").read_text())["hooks"]["preToolUse"][0]
    for variant in ("bash", "powershell"):
        assert not re.search(r"python|\bpy\b|\.py\b", hook[variant], re.IGNORECASE), variant
    assert "copilot-mention-guard.sh" in hook["bash"]
    assert "copilot-mention-guard.ps1" in hook["powershell"]


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.parametrize("variant", ["powershell", "bash"])
def test_native_hook_bounds_a_stalled_body_reader(tmp_path: Path, variant: str) -> None:
    """A body file that never delivers data is denied as unscannable in time."""
    root = _guarded_workspace(tmp_path / "workspace")
    marker = _python_shims(tmp_path / "shims")
    stdin_event = {"toolName": "bash", "toolArgs": {"command": "gh pr comment 1 --body-file -"}}
    expected = _run_reference(root, stdin_event).stdout.strip()
    assert expected
    if sys.platform == "win32":
        import _winapi

        path = rf"\\.\pipe\mention-guard-{os.getpid()}-{variant}"
        # A byte-mode pipe instance that accepts a client but never writes.
        handle = _winapi.CreateNamedPipe(
            path, _winapi.PIPE_ACCESS_DUPLEX, _winapi.PIPE_WAIT, _winapi.PIPE_UNLIMITED_INSTANCES,
            65536, 65536, 0, _winapi.NULL,
        )
    else:
        path = str(tmp_path / "stalled")
        os.mkfifo(path)
    try:
        started = time.monotonic()
        event = {"toolName": "bash", "toolArgs": {"command": f"gh pr comment 1 --body-file '{path}'"}}
        result = _run_native(variant, root, event, marker.parent)
        elapsed = time.monotonic() - started
    finally:
        if sys.platform == "win32":
            _winapi.CloseHandle(handle)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
    assert elapsed < 10
    assert not marker.exists(), "native mention-guard hook started a Python interpreter"


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_native_bash_hook_kills_and_reaps_a_stalled_git(tmp_path: Path) -> None:
    shell = _native_shell("bash")
    root = _guarded_workspace(tmp_path / "workspace")
    marker = _python_shims(tmp_path / "shims")
    pidfile = tmp_path / "git.pid"
    git = marker.parent / "git"
    git.write_text(
        f"#!/usr/bin/env bash\necho $$ > '{_bash_path(pidfile)}'\nexec sleep 30\n",
        encoding="utf-8", newline="\n",
    )
    git.chmod(0o755)
    probe = tmp_path / "probe.sh"
    probe.write_text(
        'case $1 in\n'
        '  ready) (( BASH_VERSINFO[0] * 100 + BASH_VERSINFO[1] >= 404 )) || exit 2\n'
        '         [ "$(command -v git)" -ef "$2" ] || exit 3 ;;\n'
        '  alive) kill -0 "$2" 2>/dev/null ;;\n'
        'esac\n',
        encoding="utf-8", newline="\n",
    )
    ready = subprocess.run(
        [shell, _bash_path(probe), "ready", _bash_path(git)], timeout=15,
        env={**os.environ, "PATH": str(marker.parent) + os.pathsep + os.environ.get("PATH", "")},
        creationflags=NO_WINDOW,
    ).returncode
    if ready == 2:
        pytest.skip("bash before 4.4 cannot signal a process substitution")
    if ready != 0:
        pytest.skip("this bash puts its own git ahead of the caller's PATH")
    started = time.monotonic()
    result = _run_native("bash", root, {"toolName": "bash", "toolArgs": {
        "command": 'gh pr comment 1 --body "@copilot review"',
    }}, marker.parent)
    assert time.monotonic() - started < 10
    assert result.returncode == 0, result.stderr
    # The repository check failed, so the guard treats the call as out of scope.
    assert result.stdout == ""
    pid = pidfile.read_text(encoding="utf-8").strip()
    assert pid.isdigit()
    alive = subprocess.run(
        [shell, _bash_path(probe), "alive", pid], timeout=15, creationflags=NO_WINDOW,
    )
    assert alive.returncode != 0, "the timed-out git process is still running"
    assert not marker.exists(), "native mention-guard hook started a Python interpreter"


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(
    os.name != "nt" or not os.environ.get("COPILOT_MENTION_PROFILE_BASELINE"),
    reason="Explicit contained Windows publication-wrapper attribution",
)
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("event", ["read", "shell-read"])
def test_windows_wrapper_module_attribution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], shell: str, event: str,
) -> None:
    executable = shutil.which(shell)
    if not executable:
        pytest.fail(f"Required profiling shell {shell} is unavailable")
    git = shutil.which("git")
    assert git, "Required comparison Git executable is unavailable"
    revision = os.environ["COPILOT_MENTION_PROFILE_BASELINE"]
    assert len(revision) == 40 and all(char in "0123456789abcdef" for char in revision)
    hook = _historical_hook(git, os.environ["COPILOT_MENTION_PROFILE_BASELINE_REPO"], revision)
    candidate = json.loads((PLUGIN / "hooks.json").read_text())["hooks"]["preToolUse"][0]["powershell"]
    state = tmp_path / "pretool.json"
    state.write_text('{"active": false}', encoding="utf-8")
    environment = {
        **os.environ,
        "COPILOT_PLUGIN_ROOT": str(PLUGIN),
        "COPILOT_PROJECT_DIR": str(tmp_path),
        "PRETOOL_KILLSWITCH_STATE_FILE": str(state),
    }
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    payload = {"toolName": "view", "cwd": str(tmp_path), "toolArgs": {"path": "example.txt"}}
    if event == "shell-read":
        payload.update(toolName="powershell", toolArgs={"command": "git status --short"})
    samples = []
    for variant, command in (("baseline", hook), ("candidate", candidate), ("baseline", hook)):
        assert command.endswith("exit 0")
        observed = command
        if "copilot-mention-guard.py" in command:
            # Python-era wrappers must prove their interpreter child completed.
            observed = (
                "$global:LASTEXITCODE=913; "
                + command.removesuffix("exit 0").replace("exit 0", "throw 'test interpreter unavailable'")
                + "if ($LASTEXITCODE -ne 0) { throw 'guard child did not complete' }; exit 0"
            )
        started = time.monotonic()
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", observed],
            input=json.dumps(payload), cwd=tmp_path, env=environment,
            capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW,
        )
        elapsed = time.monotonic() - started
        assert result.returncode == 0, result.stderr
        assert result.stdout == result.stderr == ""
        samples.append({"variant": variant, "process_seconds": elapsed})
    with capsys.disabled():
        print(json.dumps({
            "shell": shell, "event": event, "baseline_revision": revision,
            "fixed_fixture_aba": samples,
        }))


@pytest.mark.parametrize("tool", ["bash", "powershell", "view", "rg", "glob", "PowerShell", "vendor.bash", None])
def test_registered_matcher_preserves_original_policy_domain(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tool: str | None,
) -> None:
    guard = _load_guard()
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "fixture")
    monkeypatch.setattr(guard, "target_repo_from_manifest", lambda *args: "owner/repository")
    monkeypatch.setattr(guard, "adopting_harness", lambda *args: True)
    monkeypatch.setattr(guard, "current_repo_matches", lambda *args, **kwargs: True)
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO(json.dumps({
        "toolName": tool, "toolArgs": {"command": 'gh pr comment 1 --body "@copilot review"'},
    })))
    assert guard.main() == 0
    result = capsys.readouterr()
    assert result.err == ""
    hook = json.loads((PLUGIN / "hooks.json").read_text())["hooks"]["preToolUse"][0]
    matcher = hook.get("matcher")
    selected = matcher is None or re.fullmatch(matcher, tool or "") is not None
    if result.out:
        assert json.loads(result.out)["permissionDecision"] == "deny"
        assert selected, "Registration excluded an event the original evaluator denies"
    else:
        assert not selected, "Registration still launches the evaluator for its no-op domain"


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(os.name != "nt", reason="Native Windows publication-wrapper contract")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", [
    "read", "mention", "uninspectable", "stop", "malformed-stop",
    "missing-guard", "child-failed", "echo", "relative-echo",
])
def test_windows_wrapper_preserves_policy_and_failure_contract(
    tmp_path: Path, shell: str, case: str,
) -> None:
    executable = shutil.which(shell)
    if not executable:
        pytest.fail(f"Required native shell {shell} is unavailable")
    subprocess.run(
        ["git", "init", "-q"], cwd=tmp_path, check=True,
        timeout=10, creationflags=NO_WINDOW,
    )
    target = _load_guard().target_repo_from_manifest(PLUGIN)
    assert target
    with (tmp_path / ".git" / "config").open("a", encoding="utf-8") as stream:
        stream.write(f'\n[remote "origin"]\nurl = https://github.com/{target}.git\n')
    _enable_harness(tmp_path)
    state = tmp_path / "pretool.json"
    state.write_text(
        '{"active":"invalid"}' if case == "malformed-stop"
        else json.dumps({"active": case == "stop"}),
        encoding="utf-8",
    )
    root = PLUGIN
    if case in {"missing-guard", "child-failed", "echo", "relative-echo"}:
        root = tmp_path / "payload space's [literal]"
        scripts = root / "scripts"
        scripts.mkdir(parents=True)
        if case == "child-failed":
            (scripts / "copilot-mention-guard.ps1").write_text(
                "'partial-policy'\nthrow 'guard failed'\n", encoding="utf-8",
            )
        elif case in {"echo", "relative-echo"}:
            # The hook passes the decoded raw payload and the plugin root.
            (scripts / "copilot-mention-guard.ps1").write_text(
                "param($Raw, $Root)\n[Console]::Out.Write($Raw)\n", encoding="utf-8",
            )
    command = 'gh pr comment 1 --body "@copilot review"'
    if case == "uninspectable":
        command = "gh pr comment 1 --body-file -"
    payload = {
        "toolName": "view" if case == "read" else "powershell",
        "cwd": str(tmp_path),
        "toolArgs": json.dumps({"command": command, "text": "\u00e9\u4e2d\U0001f680\nsecond line"}),
    }
    environment = {
        **os.environ,
        "COPILOT_PLUGIN_ROOT": str(root.relative_to(tmp_path)) if case == "relative-echo" else str(root),
        "COPILOT_PROJECT_DIR": str(tmp_path),
        "PRETOOL_KILLSWITCH_STATE_FILE": str(state),
    }
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    hook = json.loads((PLUGIN / "hooks.json").read_text())["hooks"]["preToolUse"][0]["powershell"]
    deny_expected = case in {"mention", "uninspectable", "malformed-stop"}
    for attempt in range(3):
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command",
             "function Join-Path { throw 'path cmdlet requested' }; "
             "function Write-Output { throw 'output cmdlet requested' }; " + hook],
            input=json.dumps(payload, indent=2), cwd=tmp_path, env=environment,
            capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW,
        )
        # A loaded host can expire the 1.5 s decision budget mid-scan (allow).
        if not deny_expected or result.stdout or attempt == 2:
            break
    assert result.returncode == 0, result.stderr
    if case in {"missing-guard", "child-failed"}:
        assert result.stdout == ""
        assert result.stderr == "[copilot-extensions-maintainer] mention guard unavailable; skipped\n"
    else:
        assert result.stderr == ""
        if case in {"read", "stop"}:
            assert result.stdout == ""
        elif case in {"echo", "relative-echo"}:
            assert json.loads(result.stdout) == payload
        else:
            assert json.loads(result.stdout)["permissionDecision"] == "deny"


def test_hook_exits_with_blocked_file_reader(tmp_path: Path) -> None:
    _enable_harness(tmp_path)
    script = PLUGIN / "scripts" / "copilot-mention-guard.py"
    wrapper = """
import runpy, sys, time
guard = runpy.run_path(sys.argv[1])
scope = guard["main"].__globals__
scope["target_repo_from_manifest"] = lambda *a: "o/r"
scope["current_repo_matches"] = lambda *a, **kw: True
scope["adopting_harness"] = lambda *a, **kw: True
def blocked_open(*a: object, **kw: object) -> None:
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


def test_hot_killswitch_restores_mention_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    adopted_harness: Path,
) -> None:
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


def test_active_killswitch_returns_before_reading_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    guard = _load_guard()
    state = tmp_path / "pretool.json"
    state.write_text('{"active": true}', encoding="utf-8")
    monkeypatch.setenv("PRETOOL_KILLSWITCH_STATE_FILE", str(state))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    class Unreadable:
        def read(self, *args: object) -> NoReturn:
            pytest.fail("active switch reached guard input")
    monkeypatch.setattr(guard.sys, "stdin", Unreadable())
    assert guard.main() == 0


@pytest.mark.parametrize("adoption", ["missing", "disabled", "malformed", "enabled", "local-disabled", "consumer-only"])
def test_only_project_adopters_reach_repository_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], adoption: str,
) -> None:
    guard = _load_guard()
    settings = tmp_path / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    if adoption in {"enabled", "local-disabled"}:
        _enable_harness(tmp_path)
    elif adoption == "disabled":
        settings.write_text('{"enabledPlugins":{"copilot-extensions-maintainer@copilot-extensions":false}}')
    elif adoption == "consumer-only":
        settings.write_text('{"enabledPlugins":{"copilot-extensions-harness@copilot-extensions":true}}')
    elif adoption == "malformed":
        settings.write_text("{")
    if adoption == "local-disabled":
        settings.with_name("settings.local.json").write_text(
            '{"enabledPlugins":{"copilot-extensions-maintainer@copilot-extensions":false}}')
    global_home = tmp_path / "global-home"
    global_settings = global_home / ".copilot" / "settings.json"
    global_settings.parent.mkdir(parents=True)
    global_settings.write_text('{"enabledPlugins":{"copilot-extensions-maintainer@copilot-extensions":true}}')
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


def test_adoption_stops_at_nested_repository_boundary(tmp_path: Path) -> None:
    guard = _load_guard()
    _enable_harness(tmp_path)
    nested = tmp_path / "nested"
    (nested / ".git").mkdir(parents=True)
    assert not guard.adopting_harness(str(nested), time.monotonic() + 1)
    assert guard.adopting_harness(str(tmp_path / "child"), time.monotonic() + 1)


def test_unexpected_guard_failure_is_a_successful_noop(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    guard = _load_guard()
    def failure() -> NoReturn:
        raise RuntimeError("private exception details")
    monkeypatch.setattr(guard, "main", failure)
    assert guard.run_guard() == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "skipped" in captured.err
    assert "private exception details" not in captured.err


@pytest.mark.parametrize("failure", ["missing", "crash", "caught"])
@pytest.mark.parametrize("native_shell", ["powershell.exe", "pwsh", "bash"])
def test_shipped_wrapper_fails_open_on_unavailable_guard(
    tmp_path: Path, failure: str, native_shell: str,
) -> None:
    shell = _native_bash() if native_shell == "bash" else shutil.which(native_shell)
    if shell is None:
        pytest.skip(f"{native_shell} is unavailable")
    payload = tmp_path / "payload"
    (payload / "scripts").mkdir(parents=True)
    if failure == "crash":
        (payload / "scripts" / "copilot-mention-guard.ps1").write_text(
            "'invalid partial output'\nthrow 'guard crashed'\n", encoding="utf-8")
        (payload / "scripts" / "copilot-mention-guard.sh").write_text(
            "echo 'invalid partial output'\nexit 7\n", encoding="utf-8", newline="\n")
    if failure == "caught":
        (payload / "scripts" / "copilot-mention-guard.ps1").write_text(
            "throw 'private exception details'\n", encoding="utf-8")
        (payload / "scripts" / "copilot-mention-guard.sh").write_text(
            "echo 'private exception details' >&2\nexit 1\n", encoding="utf-8", newline="\n")
    command = _hook("bash" if native_shell == "bash" else "powershell")
    argv = [shell, "-c", command] if native_shell == "bash" else [
        shell, "-NoProfile", "-NonInteractive", "-Command", command,
    ]
    marker = _python_shims(tmp_path / "shims")
    env = {**os.environ,
           "COPILOT_PLUGIN_ROOT": _bash_path(payload) if native_shell == "bash" else str(payload),
           "PATH": str(marker.parent) + os.pathsep + os.environ.get("PATH", "")}
    event = json.dumps({"toolName": "bash", "toolArgs": {"command": "gh pr comment 1 --body x"}})
    result = subprocess.run(argv, input=event, capture_output=True, text=True, env=env,
                            cwd=tmp_path, timeout=10, creationflags=NO_WINDOW)
    assert result.returncode == 0, result.stderr
    assert not result.stdout.strip()
    assert result.stderr == SKIPPED
    assert not marker.exists()


def test_plugin_process_directory_is_not_session_adoption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    guard = _load_guard()
    _enable_harness(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COPILOT_PROJECT_DIR", raising=False)
    assert not guard.adopting_harness(None, time.monotonic() + 1)
    monkeypatch.setenv("COPILOT_PROJECT_DIR", str(tmp_path))
    assert guard.adopting_harness(None, time.monotonic() + 1)


@pytest.mark.parametrize("nested_adopted", [False, True])
def test_explicit_tool_directory_precedes_project_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], nested_adopted: bool,
) -> None:
    guard = _load_guard()
    _enable_harness(tmp_path)
    nested = tmp_path / "nested"
    (nested / ".git").mkdir(parents=True)
    if nested_adopted:
        _enable_harness(nested)
    monkeypatch.setenv("COPILOT_PROJECT_DIR", str(tmp_path))
    inspected = []
    def matches(target: str, cwd: str | None, **kwargs: object) -> bool:
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
def test_relative_body_reads_use_resolved_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    body_args: str, mention_in_workspace: bool,
) -> None:
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
def test_linked_workspace_uses_physical_repository_boundary(tmp_path: Path, target_adopted: bool) -> None:
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


def _historical_hook(git: str, repository: str, revision: str) -> str:
    for package in ("copilot-extensions-maintainer", "copilot-extensions-harness"):
        baseline = subprocess.run(
            [git, "-C", repository, "show", f"{revision}:plugins/{package}/hooks.json"],
            capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW,
        )
        if baseline.returncode == 0:
            return cast(str, json.loads(baseline.stdout)["hooks"]["preToolUse"][0]["powershell"])
    raise AssertionError(baseline.stderr)


@pytest.mark.parametrize("available", ["maintainer", "harness", "missing"])
def test_historical_hook_resolves_package_at_the_selected_revision(
    monkeypatch: pytest.MonkeyPatch, available: str,
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        present = f"copilot-extensions-{available}/hooks.json" in argv[-1]
        content = json.dumps({"hooks": {"preToolUse": [{"powershell": "historical-wrapper"}]}})
        return subprocess.CompletedProcess(argv, 0 if present else 128, content, "missing baseline")

    monkeypatch.setattr(subprocess, "run", run)
    if available == "missing":
        with pytest.raises(AssertionError, match="missing baseline"):
            _historical_hook("git", "source", "a" * 40)
    else:
        assert _historical_hook("git", "source", "a" * 40) == "historical-wrapper"
    assert len(calls) == (1 if available == "maintainer" else 2)
    assert all(call[:4] == ["git", "-C", "source", "show"] for call in calls)
    assert all(call[-1].startswith("a" * 40 + ":plugins/") for call in calls)
