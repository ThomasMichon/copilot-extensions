"""Main's consumption-side release scope: policy admission and index projection."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import promotion_release_scope as scope_io


def _policy(**overrides: Any) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "schema": scope_io.SCHEMA,
        "release_roots": ["plugins", "tools", "README.md"],
        "excluded": {"efforts": "planning", "plugins/agent-bridge/contract": "evidence"},
        "excluded_patterns": {"*/libs/*/tests": "vendored tests", "*/*/libs/*/tests": "vendored tests"},
    }
    policy.update(overrides)
    return policy


def test_projection_keeps_release_roots_and_drops_excluded_and_unclassified() -> None:
    scope = scope_io.parse_scope(_policy())
    tracked = [
        "README.md", "tools/promote_release.py", "plugins/agent-bridge/src/main.py",
        "plugins/agent-bridge/contract/registry.json", "plugins/agent-bridge/contractual.md",
        "efforts/active/x/README.md", "brand-new-root/build.py",
    ]
    assert scope_io.removed_paths(scope, tracked) == [
        "brand-new-root/build.py", "efforts/active/x/README.md",
        "plugins/agent-bridge/contract/registry.json",
    ]
    assert scope.unclassified(tracked) == ["brand-new-root"]


def test_patterns_drop_vendored_library_tests_but_keep_canonical_and_plugin_tests() -> None:
    scope = scope_io.parse_scope(
        _policy(release_roots=[".github", "libs", "plugins", "tools", "worktree-manager"]))
    tracked = [
        "libs/ssh/tests/test_a.py", "libs/ssh/src/ssh/a.py",
        "plugins/agent-ssh/libs/ssh/src/ssh/a.py", "plugins/agent-ssh/libs/ssh/tests/test_a.py",
        "plugins/agent-ssh/libs/ssh/tests", "plugins/agent-ssh/tests/test_b.py",
        "plugins/agent-ssh/libs/ssh/testsuite/x.py", "worktree-manager/libs/ssh/tests/data/x.json",
        "tools/libs/x/tests/a.py", ".github/libs/x/tests/a.yml",
    ]
    assert scope_io.removed_paths(scope, tracked) == [
        "plugins/agent-ssh/libs/ssh/tests", "plugins/agent-ssh/libs/ssh/tests/test_a.py",
        "tools/libs/x/tests/a.py", "worktree-manager/libs/ssh/tests/data/x.json",
    ]


@pytest.mark.parametrize("overrides, message", [
    ({"excluded_patterns": {"*/libs/*/tests": " "}}, "review reason"),
    ({"excluded_patterns": {"plugins/a*/tests": "x"}}, "whole path segment"),
    ({"excluded_patterns": {"plugins/*": "x"}}, "literal segment"),
    ({"excluded_patterns": {".github/*/x": "x"}}, "Main-owned control"),
    ({"excluded_patterns": {"plugins/**/tests": "x"}}, "whole path segment"),
    ({"excluded": {"plugins": "both"}}, "both released and excluded"),
    ({"excluded": {".github/workflows": "control"}}, "Main-owned control"),
    ({"excluded": {"efforts": " "}}, "review reason"),
    ({"release_roots": ["plugins/agent-bridge"]}, "top-level"),
    ({"release_roots": ["../outside"]}, "repository-relative"),
    ({"schema": "other"}, "unsupported"),
])
def test_ambiguous_or_unsafe_policies_are_refused(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(scope_io.ReleaseScopeError, match=message):
        scope_io.parse_scope(_policy(**overrides))


def test_index_projection_removes_out_of_scope_entries(tmp_path: Path) -> None:
    (tmp_path / "tools").mkdir()
    (tmp_path / scope_io.POLICY_PATH).write_text(json.dumps(_policy()), encoding="utf-8")
    calls: list[tuple[list[str], str | None]] = []

    def git(args: list[str], *, cwd: Path, input_text: str | None = None) -> str:
        assert cwd == tmp_path
        calls.append((args, input_text))
        return "README.md\0efforts/a.md\0plugins/agent-bridge/contract/x.json\0tools/a.py"

    assert scope_io.project_index(tmp_path, git) == ["efforts/a.md", "plugins/agent-bridge/contract/x.json"]
    assert calls[1] == (
        ["update-index", "-z", "--force-remove", "--stdin"],
        "efforts/a.md\0plugins/agent-bridge/contract/x.json\0",
    )


def test_tree_without_a_policy_is_not_projected(tmp_path: Path) -> None:
    def git(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("no policy means no index change")

    assert scope_io.project_index(tmp_path, git) == []

