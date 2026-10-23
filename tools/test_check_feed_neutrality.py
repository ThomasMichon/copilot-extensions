"""Tests for the feed-neutrality guard (private-downstream-repo feed-neutral-build-config
effort follow-through; see #2389 / tools/clean-room's --block-public-feeds).

The guard flags a hardcoded public package-feed URL (pypi.org,
files.pythonhosted.org, registry.npmjs.org, download.pytorch.org) unless it
sits behind a real override mechanism (Dockerfile ARG default, shell
${VAR:-<url>} expansion, PowerShell ?? '<url>' default) or an inline
'# feed-guard: allow <reason>' escape hatch, matching this repo's established
guard convention.

Run:  python -m pytest tools/test_check_feed_neutrality.py
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent / "check-feed-neutrality.py"
_spec = importlib.util.spec_from_file_location("check_feed_neutrality", _SCRIPT)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _legacy_candidates(repo):
    files = {
        path
        for pattern in guard._SCAN_GLOBS
        for path in repo.rglob(pattern)
        if not guard._SCAN_DIR_EXCLUDE_PARTS.intersection(path.relative_to(repo).parts)
    }
    workflows = repo / ".github" / "workflows"
    if workflows.is_dir():
        for pattern in ("*.yml", "*.yaml"):
            files.update(workflows.glob(pattern))
    return sorted(files)


def test_candidate_scan_preserves_patterns_and_diagnostics(tmp_path, monkeypatch):
    text = "first\nhttps://pypi.org/simple\nhttps://registry.npmjs.org/\n"
    relatives = (
        "pyproject.toml", "uv.toml", ".npmrc", "package.json", "Dockerfile",
        "nested/Dockerfile.worker", "nested/install.sh", "nested/install.ps1",
        "nested/INSTALL.PS1", ".hidden/install.sh", ".git/install.ps1",
        ".test-venvs/fixture/uv.toml", ".worktrees/pkg/package.json",
        "node_modules-copy/package.json", "BUILD/package.json",
        ".github/workflows/check.yml", ".github/workflows/check.yaml",
        ".github/workflows/CHECK.YML", ".github/workflows/deep/ignored.yml",
        "nested/not-a-workflow.yaml", "package-lock.json", "unrelated.txt",
    )
    for relative in relatives:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for excluded in guard._SCAN_DIR_EXCLUDE_PARTS:
        path = tmp_path / "nested" / excluded / "package.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (tmp_path / "directory.ps1").mkdir()
    monkeypatch.setattr(guard, "REPO", tmp_path)
    legacy = _legacy_candidates(tmp_path)
    expected = []
    for path in legacy:
        if path.is_file():
            expected.extend(guard.scan_text(text, rel=path.relative_to(tmp_path).as_posix()))

    assert guard._iter_candidate_files() == legacy
    assert guard.verify() == expected
    assert tmp_path / "directory.ps1" in legacy
    assert tmp_path / ".github" / "workflows" / "deep" / "ignored.yml" not in legacy
    assert tmp_path / "nested" / "not-a-workflow.yaml" not in legacy


def test_candidate_scan_prunes_only_existing_exclusions(tmp_path, monkeypatch):
    for directory in (*guard._SCAN_DIR_EXCLUDE_PARTS, ".hidden", ".test-venvs", ".worktrees"):
        path = tmp_path / "nested" / directory / "deep" / "package.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    legacy = _legacy_candidates(tmp_path)
    original = os.scandir
    visited = set()

    def scandir(path):
        directory = Path(path)
        assert not guard._SCAN_DIR_EXCLUDE_PARTS.intersection(
            directory.relative_to(tmp_path).parts)
        visited.add(directory)
        return original(path)

    monkeypatch.setattr(guard, "REPO", tmp_path)
    monkeypatch.setattr(os, "scandir", scandir)
    assert guard._iter_candidate_files() == legacy
    for directory in (".hidden", ".test-venvs", ".worktrees"):
        assert tmp_path / "nested" / directory / "deep" in visited


def test_candidate_scan_fails_on_unreadable_directory(tmp_path, monkeypatch):
    blocked = tmp_path / "docs"
    blocked.mkdir()
    original = os.scandir

    def scandir(path):
        if Path(path) == blocked:
            raise PermissionError("unreadable guard directory")
        return original(path)

    monkeypatch.setattr(guard, "REPO", tmp_path)
    monkeypatch.setattr(os, "scandir", scandir)
    with pytest.raises(PermissionError, match="unreadable guard directory"):
        guard._iter_candidate_files()


@pytest.mark.parametrize("excluded", sorted(guard._SCAN_DIR_EXCLUDE_PARTS))
def test_excluded_root_name_is_not_a_relative_exclusion(tmp_path, monkeypatch, excluded):
    repo = tmp_path / excluded
    repo.mkdir()
    (repo / "install.sh").write_text("", encoding="utf-8")
    monkeypatch.setattr(guard, "REPO", repo)
    assert guard._iter_candidate_files() == _legacy_candidates(repo) == [repo / "install.sh"]


def test_bare_dockerfile_pin_is_flagged():
    text = "RUN pip install --index-url https://download.pytorch.org/whl/cu128 torch\n"
    problems = guard.scan_text(text, rel="Dockerfile")
    assert len(problems) == 1
    assert "download.pytorch.org" in problems[0]


def test_dockerfile_arg_default_is_exempt():
    text = (
        "ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128\n"
        'RUN pip install --index-url "${TORCH_INDEX_URL}" torch\n'
    )
    assert guard.scan_text(text, rel="Dockerfile") == []


def test_quoted_dockerfile_arg_default_is_exempt():
    text = 'ARG TORCH_INDEX_URL="https://download.pytorch.org/whl/cu128"\n'
    assert guard.scan_text(text, rel="Dockerfile") == []


def test_shell_default_expansion_is_exempt():
    text = 'TORCH_INDEX="${TORCH_INDEX_CU118:-https://download.pytorch.org/whl/cu118}"\n'
    assert guard.scan_text(text, rel="install.sh") == []


def test_bare_shell_assignment_is_flagged():
    text = 'TORCH_INDEX="https://download.pytorch.org/whl/cu118"\n'
    problems = guard.scan_text(text, rel="install.sh")
    assert len(problems) == 1


def test_powershell_coalesce_default_is_exempt():
    text = "$desired = $configuredIndexUrl ?? 'https://pypi.org/simple'\n"
    assert guard.scan_text(text, rel="install.ps1") == []


def test_comment_line_is_exempt():
    text = "# Matches download.pytorch.org/whl/cu128 for parity with the Dockerfile\n"
    assert guard.scan_text(text, rel="install.sh") == []


def test_inline_allow_comment_is_exempt():
    text = (
        'env["NPM_CONFIG_REGISTRY"] = "https://registry.npmjs.org/"  '
        "# feed-guard: allow deliberate public-npm repair path\n"
    )
    assert guard.scan_text(text, rel="foo.sh") == []


def test_inline_allow_comment_requires_a_reason():
    text = 'env["NPM_CONFIG_REGISTRY"] = "https://registry.npmjs.org/"  # feed-guard: allow\n'
    assert len(guard.scan_text(text, rel="foo.sh")) == 1


def test_allow_comment_not_shadowed_by_an_in_url_hash():
    # A pip VCS/egg-fragment URL contains its own '#' before the real
    # trailing comment marker; naively taking the FIRST '#' would miss the
    # allow-comment entirely.
    text = (
        'RUN pip install "https://pypi.org/simple#egg=x"  '
        "# feed-guard: allow legacy egg url\n"
    )
    assert guard.scan_text(text, rel="Dockerfile") == []


def test_non_public_host_is_not_flagged():
    text = "RUN pip install --index-url https://internal-feed.example.com/simple torch\n"
    assert guard.scan_text(text, rel="Dockerfile") == []


def test_bare_pin_not_masked_by_colocated_legit_override():
    text = (
        "RUN pip install --index-url ${A:-https://pypi.org/simple} "
        "--extra-index-url https://registry.npmjs.org/\n"
    )
    problems = guard.scan_text(text, rel="Dockerfile")
    assert len(problems) == 1
    assert "registry.npmjs.org" in problems[0]


def test_verify_scans_real_repo_tree_with_zero_findings():
    # Regression proof: run the guard against this repo's actual tracked
    # tree (the module's own REPO constant, not a fixture) and confirm no
    # hardcoded public-feed pin currently exists uncovered.
    problems = guard.verify()
    assert problems == [], "\n".join(problems)
