"""Regression tests for tools/check-gh-aw-compiler-version.py.

Covers the real defect this guard exists to catch: `gh aw compile` is an
externally-versioned CLI tool with no dependency-manifest entry this repo's
own tooling tracks -- a contributor's local install can silently drift from
whatever version the rest of the team compiled with, changing generated job
structure/permissions/security-scanning behavior with no source-level diff
to review.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parent / "check-gh-aw-compiler-version.py"
SPEC = importlib.util.spec_from_file_location(
    "check_gh_aw_compiler_version", MODULE_PATH
)
check_gh_aw_compiler_version = importlib.util.module_from_spec(SPEC)
sys.modules["check_gh_aw_compiler_version"] = check_gh_aw_compiler_version
SPEC.loader.exec_module(check_gh_aw_compiler_version)


PINNED_VERSION = "v0.89.21"


@pytest.fixture
def workflows_dir(tmp_path, monkeypatch):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    version_file = tmp_path / ".github" / "gh-aw-version.txt"
    version_file.write_text(f"{PINNED_VERSION}\n", encoding="utf-8")
    monkeypatch.setattr(check_gh_aw_compiler_version, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(check_gh_aw_compiler_version, "WORKFLOWS_DIR", workflows)
    monkeypatch.setattr(check_gh_aw_compiler_version, "VERSION_FILE", version_file)
    return workflows


def _write_lock(workflows_dir: Path, compiler_version: str | None) -> Path:
    lock_file = workflows_dir / "example.lock.yml"
    metadata = {"schema_version": "v4", "compiler_version": compiler_version}
    lines = [f"# gh-aw-metadata: {json.dumps(metadata)}", "jobs:", "  agent: {}"]
    lock_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lock_file


def test_matching_compiler_version_is_accepted(workflows_dir):
    _write_lock(workflows_dir, PINNED_VERSION)
    assert check_gh_aw_compiler_version.find_version_mismatches() == []


def test_mismatched_compiler_version_is_rejected(workflows_dir):
    _write_lock(workflows_dir, "v0.90.0")
    violations = check_gh_aw_compiler_version.find_version_mismatches()
    assert len(violations) == 1
    assert "v0.90.0" in violations[0]
    assert PINNED_VERSION in violations[0]
    assert "example.lock.yml" in violations[0]


def test_missing_metadata_comment_is_rejected(workflows_dir):
    lock_file = workflows_dir / "example.lock.yml"
    lock_file.write_text("jobs:\n  agent: {}\n", encoding="utf-8")
    violations = check_gh_aw_compiler_version.find_version_mismatches()
    assert len(violations) == 1
    assert "no gh-aw-metadata comment" in violations[0]


def test_malformed_metadata_json_is_rejected(workflows_dir):
    lock_file = workflows_dir / "example.lock.yml"
    lock_file.write_text(
        "# gh-aw-metadata: {not valid json}\njobs:\n  agent: {}\n",
        encoding="utf-8",
    )
    violations = check_gh_aw_compiler_version.find_version_mismatches()
    assert len(violations) == 1
    assert "not valid JSON" in violations[0]


def test_no_lock_files_is_not_a_violation(workflows_dir):
    assert check_gh_aw_compiler_version.find_version_mismatches() == []


def test_multiple_lock_files_each_checked_independently(workflows_dir):
    _write_lock(workflows_dir, PINNED_VERSION)
    other = workflows_dir / "other.lock.yml"
    metadata = {"compiler_version": "stale"}
    other.write_text(f"# gh-aw-metadata: {json.dumps(metadata)}\njobs: {{}}\n", encoding="utf-8")
    violations = check_gh_aw_compiler_version.find_version_mismatches()
    assert len(violations) == 1
    assert "other.lock.yml" in violations[0]
