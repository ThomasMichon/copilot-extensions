"""Regression tests for tools/check-gh-aw-action-pins.py.

Covers the real defect this guard exists to catch (PR #4155, round 4):
`gh aw compile` silently reverts to a mutable version-tag action reference
unless `--action-tag <sha>` is passed on every recompile, and nothing in the
committed source would otherwise flag the regression.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parent / "check-gh-aw-action-pins.py"
SPEC = importlib.util.spec_from_file_location("check_gh_aw_action_pins", MODULE_PATH)
check_gh_aw_action_pins = importlib.util.module_from_spec(SPEC)
sys.modules["check_gh_aw_action_pins"] = check_gh_aw_action_pins
SPEC.loader.exec_module(check_gh_aw_action_pins)


SHA = "c35393777e5604a63721d09512263b1383301d4f"


@pytest.fixture
def workflows_dir(tmp_path, monkeypatch):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    monkeypatch.setattr(check_gh_aw_action_pins, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(check_gh_aw_action_pins, "WORKFLOWS_DIR", workflows)
    return workflows


def _write_lock(workflows_dir: Path, ref: str) -> Path:
    lock_file = workflows_dir / "example.lock.yml"
    lock_file.write_text(
        "jobs:\n"
        "  setup:\n"
        "    steps:\n"
        f"      - uses: github/gh-aw/actions/setup@{ref}\n",
        encoding="utf-8",
    )
    return lock_file


def test_sha_pinned_action_ref_is_accepted(workflows_dir):
    _write_lock(workflows_dir, SHA)
    assert check_gh_aw_action_pins.find_unpinned_refs() == []


def test_mutable_version_tag_is_rejected(workflows_dir):
    _write_lock(workflows_dir, "v0.89.21")
    violations = check_gh_aw_action_pins.find_unpinned_refs()
    assert len(violations) == 1
    assert "v0.89.21" in violations[0]
    assert "example.lock.yml" in violations[0]


def test_mutable_branch_ref_is_rejected(workflows_dir):
    _write_lock(workflows_dir, "main")
    violations = check_gh_aw_action_pins.find_unpinned_refs()
    assert len(violations) == 1
    assert "main" in violations[0]


def test_no_lock_files_is_not_a_violation(workflows_dir):
    assert check_gh_aw_action_pins.find_unpinned_refs() == []


def test_single_quoted_mutable_tag_is_rejected(workflows_dir):
    lock_file = workflows_dir / "example.lock.yml"
    lock_file.write_text(
        "jobs:\n"
        "  setup:\n"
        "    steps:\n"
        "      - uses: 'github/gh-aw/actions/setup@v0.89.21'\n",
        encoding="utf-8",
    )
    violations = check_gh_aw_action_pins.find_unpinned_refs()
    assert len(violations) == 1
    assert "v0.89.21" in violations[0]


def test_double_quoted_mutable_tag_is_rejected(workflows_dir):
    lock_file = workflows_dir / "example.lock.yml"
    lock_file.write_text(
        "jobs:\n"
        "  setup:\n"
        "    steps:\n"
        '      - uses: "github/gh-aw/actions/setup@v0.89.21"\n',
        encoding="utf-8",
    )
    violations = check_gh_aw_action_pins.find_unpinned_refs()
    assert len(violations) == 1
    assert "v0.89.21" in violations[0]


def test_single_quoted_sha_pin_is_accepted(workflows_dir):
    lock_file = workflows_dir / "example.lock.yml"
    lock_file.write_text(
        "jobs:\n"
        "  setup:\n"
        "    steps:\n"
        f"      - uses: 'github/gh-aw/actions/setup@{SHA}'\n",
        encoding="utf-8",
    )
    assert check_gh_aw_action_pins.find_unpinned_refs() == []


def test_nested_action_path_mutable_tag_is_rejected(workflows_dir):
    lock_file = workflows_dir / "example.lock.yml"
    lock_file.write_text(
        "jobs:\n"
        "  setup:\n"
        "    steps:\n"
        "      - uses: github/gh-aw/actions/foo/bar@v0.89.21\n",
        encoding="utf-8",
    )
    violations = check_gh_aw_action_pins.find_unpinned_refs()
    assert len(violations) == 1
    assert "v0.89.21" in violations[0]


def test_nested_action_path_sha_pin_is_accepted(workflows_dir):
    lock_file = workflows_dir / "example.lock.yml"
    lock_file.write_text(
        "jobs:\n"
        "  setup:\n"
        "    steps:\n"
        f"      - uses: github/gh-aw/actions/foo/bar@{SHA}\n",
        encoding="utf-8",
    )
    assert check_gh_aw_action_pins.find_unpinned_refs() == []


def test_non_gh_aw_actions_are_ignored(workflows_dir):
    lock_file = workflows_dir / "example.lock.yml"
    lock_file.write_text(
        "jobs:\n"
        "  setup:\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: github/gh-aw-actions/setup@v0.89.21\n",
        encoding="utf-8",
    )
    # `github/gh-aw-actions` (a different, similarly-named repo) is
    # deliberately out of scope for this guard -- it only checks refs
    # under the `github/gh-aw` monorepo path this repo's compiles actually
    # resolve against.
    assert check_gh_aw_action_pins.find_unpinned_refs() == []
