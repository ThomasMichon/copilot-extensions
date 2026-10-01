"""Regression tests for the internal-identifier pre-push guard (#541, #3923).

The guard used to scan the *entire* tracked tree, so a pre-existing identifier
in an untouched file blocked every unrelated push. It now scans only the push
diff (``<base>...HEAD``) by default, while ``--all`` still audits the whole
tree. These tests drive the real script as a subprocess inside a throwaway git
repo so the git-diff scoping is exercised end-to-end.

Run:  python -m pytest tools/test_check_no_internal_identifiers.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-no-internal-identifiers.py"
LEAK = "acme-internal-id"
CI_TOKEN = "widget-facility"
CI_REASON = "replace with generic widget | never name the facility"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A git repo with a simulated ``origin/main`` base carrying a pre-existing
    leak in an untouched file, and a HEAD that changes only a clean file."""
    r = tmp_path / "repo"
    (r / "tools").mkdir(parents=True)
    shutil.copy(SCRIPT, r / "tools" / SCRIPT.name)

    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    _git(r, "checkout", "-q", "-b", "main")

    # Base commit: a pre-existing leak in an untouched file.
    _write(r, "plugins/old/legacy.txt", f"this file mentions {LEAK} already\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=r, capture_output=True, text=True, check=True
    ).stdout.strip()
    # Simulate the remote the guard diffs against.
    _git(r, "update-ref", "refs/remotes/origin/main", base_sha)

    # New commit: touch only a clean file.
    _write(r, "plugins/new/clean.txt", "nothing sensitive here\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "clean change")
    return r


def _run(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / SCRIPT.name), *extra],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={**_base_env(), "COPILOT_EXTENSIONS_FORBIDDEN_IDS": LEAK},
    )


def _base_env() -> dict[str, str]:
    import os

    # Keep PATH/SYSTEMROOT so git + python resolve on every platform.
    keep = ("PATH", "SYSTEMROOT", "SystemRoot", "HOME", "USERPROFILE", "TEMP", "TMP")
    return {k: v for k, v in os.environ.items() if k in keep}


def test_diff_scope_ignores_pre_existing_leak_in_untouched_file(repo: Path):
    # The bug fix: default (push-diff) scope must NOT flag the pre-existing leak
    # in plugins/old/legacy.txt because this push doesn't touch it.
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_all_flag_still_audits_whole_tree(repo: Path):
    # --all restores the full-tree sweep and catches the pre-existing leak.
    result = _run(repo, "--all")
    assert result.returncode == 1
    assert LEAK in result.stdout


def test_diff_scope_still_catches_introduced_leak(repo: Path):
    # A leak in a file the push actually changes is still caught in diff scope.
    _write(repo, "plugins/new/clean.txt", f"oops {LEAK} sneaked in\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "introduce leak")
    result = _run(repo)
    assert result.returncode == 1
    assert LEAK in result.stdout


def test_load_identifier_data_merges_plain_and_ci_sources_without_duplicates(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _load_module(repo)
    home_dir = repo / "home"
    home_dir.mkdir()
    monkeypatch.setattr(module, "HOME_LIST", home_dir / ".agent-codespaces" / "forbidden-identifiers.txt")
    monkeypatch.setenv(
        "COPILOT_EXTENSIONS_FORBIDDEN_IDS",
        f"{LEAK},{CI_TOKEN},CaseOnly,",
    )
    monkeypatch.setenv(
        module.CI_LIST_ENV,
        f"{CI_TOKEN}|{CI_REASON};second-token|why this matters\ncaseonly|reason that loses\n",
    )
    (home_dir / ".agent-codespaces").mkdir()
    (home_dir / ".agent-codespaces" / "forbidden-identifiers.txt").write_text(
        "# comment\nthird-token\nSECOND-token\n",
        encoding="utf-8",
    )

    identifiers, reasons = module._load_identifier_data()

    assert identifiers == [
        LEAK,
        CI_TOKEN,
        "caseonly",
        "third-token",
        "second-token",
    ]
    assert reasons == {
        CI_TOKEN: CI_REASON,
        "second-token": "why this matters",
        "caseonly": "reason that loses",
    }


def test_ci_loader_splits_first_pipe_only(repo: Path):
    module = _load_module(repo)
    assert module._load_ci_identifiers(
        f"{CI_TOKEN}|{CI_REASON};other-token|simple reason\nbare-token"
    ) == [
        (CI_TOKEN, CI_REASON),
        ("other-token", "simple reason"),
        ("bare-token", None),
    ]


def test_regex_ci_token_matches_whole_word_without_embedded_words(repo: Path):
    module = _load_module(repo)
    token = r"regex:\bexample\b"
    assert module._load_ci_identifiers(token + "|Use a placeholder") == [
        (token, "Use a placeholder")
    ]
    matches = module._scan_text(
        "plugins/new/clean.txt",
        "spexample example-ish EXAMPLE example_spoon teaspoon\n",
        [token, "missing-literal"],
        {token: "Use a placeholder"},
    )
    assert [(match.line, match.col, match.identifier, match.reason) for match in matches] == [
        (1, 11, "example", "Use a placeholder")
    ]


def test_regex_ci_loader_preserves_alternation_and_pipe_in_reason(repo: Path):
    module = _load_module(repo)
    assert module._load_ci_identifiers(
        r"regex:\b(foo||bar)\b|Use generic | not internal;plain|plain reason"
    ) == [
        (r"regex:\b(foo|bar)\b", "Use generic | not internal"),
        ("plain", "plain reason"),
    ]
    matches = module._scan_text(
        "plugins/new/clean.txt",
        "bar",
        [r"regex:\b(foo|bar)\b"],
        {},
    )
    assert [(match.col, match.identifier) for match in matches] == [(1, "bar")]


def test_regex_ci_mode_redacts_pattern_but_trusted_details_report_match(
    repo: Path, tmp_path: Path
):
    _write(repo, "plugins/new/clean.txt", "fool FOO teaspoon\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "introduce bounded identifier")
    details_out = tmp_path / "details.json"
    result = subprocess.run(
        [
            sys.executable,
            str(repo / "tools" / SCRIPT.name),
            "--ci",
            "--trusted-details-json-out",
            str(details_out),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            **_base_env(),
            "COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI": r"regex:\bfoo\b|Use a generic placeholder",
        },
    )
    assert result.returncode == 1
    assert "FOO" not in result.stdout
    assert "regex:" not in result.stdout
    assert json.loads(details_out.read_text(encoding="utf-8")) == [
        {
            "file": "plugins/new/clean.txt",
            "line": 1,
            "col": 6,
            "identifier": "FOO",
            "reason": "Use a generic placeholder",
        }
    ]


def test_invalid_regex_fails_explicitly_without_printing_token(repo: Path):
    module = _load_module(repo)
    with pytest.raises(ValueError, match="invalid regular expression in forbidden identifier list"):
        module._scan_text("example.txt", "anything", ["regex:(private-marker"], {})
    with pytest.raises(ValueError, match="empty regular expression match"):
        module._scan_text("example.txt", "anything", ["regex:(?=anything)"], {})


def test_load_paths_file_preserves_filename_whitespace(repo: Path):
    module = _load_module(repo)
    paths_file = repo / "paths.txt"
    paths_file.write_text(" leading space.txt \nplain.txt\n", encoding="utf-8")
    assert module._load_paths_file(paths_file) == [" leading space.txt ", "plain.txt"]


def test_json_out_writes_hashed_findings_without_raw_token_or_reason(repo: Path, tmp_path: Path):
    _write(repo, "plugins/new/clean.txt", f"oops {CI_TOKEN} sneaked in\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "introduce ci leak")
    json_out = tmp_path / "findings.json"
    result = subprocess.run(
        [
            sys.executable,
            str(repo / "tools" / SCRIPT.name),
            "--json-out",
            str(json_out),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            **_base_env(),
            "COPILOT_EXTENSIONS_FORBIDDEN_IDS": LEAK,
            "COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI": f"{CI_TOKEN}|{CI_REASON}",
        },
    )

    assert result.returncode == 1
    payload_text = json_out.read_text(encoding="utf-8")
    assert CI_TOKEN not in payload_text
    assert "generic widget" not in payload_text
    payload = json.loads(payload_text)
    assert payload == [
        {
            "file": "plugins/new/clean.txt",
            "line": 1,
            "col": 6,
            "identifier_hash": hashlib.sha256(CI_TOKEN.encode("utf-8")).hexdigest(),
            "has_reason": True,
        }
    ]


def test_git_ref_scan_reads_passive_pr_head_data_and_trusted_details(repo: Path, tmp_path: Path):
    _write(repo, "plugins/new/clean.txt", f"oops {CI_TOKEN} sneaked in\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "introduce ci leak")
    _write(repo, "plugins/new/clean.txt", "working tree cleaned after commit\n")
    paths_file = tmp_path / "paths.txt"
    json_out = tmp_path / "findings.json"
    details_out = tmp_path / "trusted-details.json"
    paths_file.write_text("plugins/new/clean.txt\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(repo / "tools" / SCRIPT.name),
            "--ci",
            "--paths-file",
            str(paths_file),
            "--git-ref",
            "HEAD",
            "--json-out",
            str(json_out),
            "--trusted-details-json-out",
            str(details_out),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            **_base_env(),
            "COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI": f"{CI_TOKEN}|{CI_REASON}",
        },
    )

    assert result.returncode == 1
    assert result.stdout.strip() == (
        "1 forbidden identifier(s) found -- see the 'identifier leak guard' Check Run output for details."
    )
    assert CI_TOKEN not in json_out.read_text(encoding="utf-8")
    assert CI_REASON not in json_out.read_text(encoding="utf-8")
    assert json.loads(details_out.read_text(encoding="utf-8")) == [
        {
            "file": "plugins/new/clean.txt",
            "line": 1,
            "col": 6,
            "identifier": CI_TOKEN,
            "reason": CI_REASON,
        }
    ]


def test_ci_mode_stdout_is_count_only(repo: Path):
    _write(repo, "plugins/new/clean.txt", f"oops {CI_TOKEN} sneaked in\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "introduce ci leak")
    result = subprocess.run(
        [sys.executable, str(repo / "tools" / SCRIPT.name), "--ci"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            **_base_env(),
            "COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI": f"{CI_TOKEN}|{CI_REASON}",
        },
    )

    assert result.returncode == 1
    assert CI_TOKEN not in result.stdout
    assert CI_REASON not in result.stdout
    assert result.stdout.strip() == (
        "1 forbidden identifier(s) found -- see the 'identifier leak guard' Check Run output for details."
    )


def test_scan_tracks_first_column_for_multi_occurrence_line(repo: Path):
    module = _load_module(repo)
    _write(repo, "plugins/new/clean.txt", f"prefix {LEAK} middle {LEAK} suffix\n")
    violations = module._scan(
        ["plugins/new/clean.txt"],
        [LEAK],
        {},
    )

    assert violations == [
        module.Violation(
            path="plugins/new/clean.txt",
            line=1,
            col=8,
            identifier=LEAK,
            reason=None,
        )
    ]


def test_all_and_paths_file_are_mutually_exclusive(repo: Path):
    result = _run(repo, "--all", "--paths-file", "ignored.txt")
    assert result.returncode == 2
    assert "mutually exclusive" in result.stderr


def _load_module(repo: Path):
    import importlib.util
    import sys as _sys

    spec = importlib.util.spec_from_file_location("check_no_internal_identifiers", repo / "tools" / SCRIPT.name)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    _sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
