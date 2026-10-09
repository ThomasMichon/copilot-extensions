from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-docs-consistency.py"
_spec = importlib.util.spec_from_file_location("check_docs_consistency", SCRIPT)
assert _spec and _spec.loader
checker = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = checker
_spec.loader.exec_module(checker)


def _legacy_paths(repo: Path) -> list[Path]:
    return [
        path for path in sorted(repo.glob("**/*.md"))
        if ".worktrees" not in path.parts and "node_modules" not in path.parts
    ]


def _legacy_counts(repo: Path, expected: dict[str, int]) -> list[str]:
    problems: list[str] = []
    for md in _legacy_paths(repo):
        raw = md.read_text(encoding="utf-8")
        text = raw.replace("*", "").replace("`", "")
        rel = md.relative_to(repo)
        for rx, kind in checker.COUNT_PATTERNS:
            for match in rx.finditer(text):
                got = checker._num(match.group(1))
                want = expected[kind]
                if got is not None and got != want:
                    line = text[:match.start()].count("\n") + 1
                    problems.append(
                        f"  {rel}:{line}: says {match.group(1)!r} for {kind} "
                        f"(expected {want}/{checker.NUM2WORD.get(want, want)}): "
                        f'"{match.group(0).strip()}"'
                    )
    return problems


def _write(repo: Path, relative: str, text: str) -> Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_whole_tree_candidates_and_diagnostics_equal_legacy(tmp_path, monkeypatch):
    text = (
        "header\n"
        "**two** plugins, one marketplace; `three` runtime plugins\n"
        "How the FOUR copilot-extensions plugins fit together\n"
        "all five copilot-extensions plugins; six-plugin suite\n"
        "seven ship a runtime; eight are payload-only\n"
        "bundles nine focused skills; unknown runtime plugins\n"
        "41-plugin marketplace; 40 runtime plugins; one is payload-only\n"
        "one plugins, one marketplace; bundles one focused skills\n"
    )
    relatives = [
        "z.md", "a.md", "README.MD", ".hidden/nested.md",
        "plugins/example/skills/example/SKILL.md", "docs/deep/counts.md",
        ".test-venvs/example/README.md", ".venv/pkg/README.md",
        ".git/example.md", ".worktrees-copy/README.md",
        "node_modules-copy/README.md", "NODE_MODULES/README.md",
        "nested/.worktrees/deep/ignored.md",
        "nested/node_modules/deep/ignored.md",
        ".worktrees/ignored.md", "node_modules/ignored.md",
    ]
    for relative in relatives:
        _write(tmp_path, relative, text)
    _write(tmp_path, "docs/not-markdown.txt", text)
    monkeypatch.setattr(checker, "REPO", tmp_path)
    expected = dict.fromkeys(("total", "runtime", "payload", "cc_skills"), 1)
    legacy_paths = _legacy_paths(tmp_path)
    legacy_problems = _legacy_counts(tmp_path, expected)

    assert sorted(checker._markdown_paths()) == legacy_paths
    assert checker.check_counts(expected) == legacy_problems
    assert len(legacy_problems) == len(legacy_paths) * 10


def test_excluded_subtrees_are_not_enumerated(tmp_path, monkeypatch):
    for relative in (
        ".worktrees/deep/ignored.md", "node_modules/deep/ignored.md",
        "docs/.worktrees/deep/ignored.md", "docs/node_modules/deep/ignored.md",
        ".test-venvs/pkg/README.md", ".hidden/deep/README.md",
        "docs/deep/README.md", "node_modules-copy/deep/README.md",
    ):
        _write(tmp_path, relative, "two plugins, one marketplace")
    legacy_paths = _legacy_paths(tmp_path)
    real_scandir = os.scandir
    visited: set[Path] = set()

    def checked_scandir(path):
        directory = Path(path)
        assert not {".worktrees", "node_modules"}.intersection(directory.parts)
        visited.add(directory)
        return real_scandir(path)

    monkeypatch.setattr(checker, "REPO", tmp_path)
    monkeypatch.setattr(os, "scandir", checked_scandir)

    assert sorted(checker._markdown_paths()) == legacy_paths
    assert tmp_path / ".test-venvs" / "pkg" in visited
    assert tmp_path / ".hidden" / "deep" in visited
    assert tmp_path / "docs" / "deep" in visited
    assert tmp_path / "node_modules-copy" / "deep" in visited


def test_unreadable_markdown_subtree_fails_closed(tmp_path, monkeypatch):
    blocked = tmp_path / "docs"
    blocked.mkdir()
    real_scandir = os.scandir

    def denied_scandir(path):
        if Path(path) == blocked:
            raise PermissionError("cannot enumerate Markdown subtree")
        return real_scandir(path)

    monkeypatch.setattr(checker, "REPO", tmp_path)
    monkeypatch.setattr(os, "scandir", denied_scandir)
    expected = dict.fromkeys(("total", "runtime", "payload", "cc_skills"), 1)
    with pytest.raises(PermissionError, match="cannot enumerate Markdown subtree"):
        checker.check_counts(expected)


@pytest.mark.parametrize("excluded", [".worktrees", "node_modules"])
def test_excluded_ancestor_still_excludes_entire_tree(tmp_path, monkeypatch, excluded):
    repo = tmp_path / excluded / "repo"
    _write(repo, "README.md", "two plugins, one marketplace")
    assert _legacy_paths(repo) == []
    monkeypatch.setattr(checker, "REPO", repo)

    def unexpected_scandir(path):
        pytest.fail(f"excluded root was enumerated: {path}")

    monkeypatch.setattr(os, "scandir", unexpected_scandir)
    assert list(checker._markdown_paths()) == []


@pytest.mark.parametrize("invalid", ["directory", "encoding"])
def test_markdown_read_errors_match_legacy(tmp_path, monkeypatch, invalid):
    path = tmp_path / "a.md"
    if invalid == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"\xff")
    _write(tmp_path, "z.md", "two plugins, one marketplace")
    monkeypatch.setattr(checker, "REPO", tmp_path)
    expected = dict.fromkeys(("total", "runtime", "payload", "cc_skills"), 1)
    assert sorted(checker._markdown_paths()) == _legacy_paths(tmp_path)

    with pytest.raises((OSError, UnicodeError)) as legacy:
        _legacy_counts(tmp_path, expected)
    with pytest.raises(type(legacy.value)) as current:
        checker.check_counts(expected)
    assert str(current.value) == str(legacy.value)


def test_symlink_candidates_equal_legacy_without_following_directories(tmp_path, monkeypatch):
    target = _write(tmp_path, "target/nested.md", "two plugins, one marketplace")
    try:
        (tmp_path / "linked").symlink_to(target.parent, target_is_directory=True)
        (tmp_path / "linked.md").symlink_to(target)
        (tmp_path / "missing.md").symlink_to(tmp_path / "missing")
        (tmp_path / "directory.md").symlink_to(target.parent, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    monkeypatch.setattr(checker, "REPO", tmp_path)

    paths = sorted(checker._markdown_paths())
    assert paths == _legacy_paths(tmp_path)
    assert tmp_path / "linked" / "nested.md" not in paths
    assert tmp_path / "linked.md" in paths
    assert tmp_path / "missing.md" in paths
    assert tmp_path / "directory.md" in paths
