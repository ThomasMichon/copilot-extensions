"""Main's consumption-side release scope: policy admission and index projection."""
from __future__ import annotations

import dataclasses
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
    }
    policy.update(overrides)
    return policy


def _patterns(patterns: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "schema": scope_io.PATTERNS_SCHEMA,
        "excluded_patterns": patterns if patterns is not None else {
            "*/libs/*/tests": "vendored tests", "*/*/libs/*/tests": "vendored tests"},
    }


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
    scope = dataclasses.replace(
        scope_io.parse_scope(
            _policy(release_roots=[".github", "libs", "plugins", "tools", "worktree-manager"])),
        excluded_patterns=scope_io.parse_patterns(_patterns()))
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


@pytest.mark.parametrize("patterns, message", [
    ({"*/libs/*/tests": " "}, "review reason"),
    ({"plugins/a*/tests": "x"}, "whole path segment"),
    ({"plugins/**/tests": "x"}, "whole path segment"),
    ({"plugins/*": "x"}, "literal segment"),
    ({".github/*/x": "x"}, "Main-owned control"),
])
def test_ambiguous_or_unsafe_patterns_are_refused(patterns: dict[str, str], message: str) -> None:
    with pytest.raises(scope_io.ReleaseScopeError, match=message):
        scope_io.parse_patterns(_patterns(patterns))


def test_primary_policy_keeps_the_v1_shape_installed_readers_accept(tmp_path: Path) -> None:
    """An installed Manager packages its own reader; patterns must not change the primary file."""
    (tmp_path / "tools").mkdir()
    (tmp_path / scope_io.POLICY_PATH).write_text(json.dumps(_policy()), encoding="utf-8")
    assert scope_io.SCHEMA == "copilot-extensions.promotion-release-scope/v1"
    with pytest.raises(scope_io.ReleaseScopeError, match="exactly schema, release_roots and excluded"):
        scope_io.parse_scope({**_policy(), "excluded_patterns": {}})
    without = scope_io.load_scope(tmp_path)
    assert without is not None and without.excluded_patterns == ()
    (tmp_path / scope_io.PATTERNS_PATH).write_text(json.dumps(_patterns()), encoding="utf-8")
    combined = scope_io.load_scope(tmp_path)
    assert combined is not None and combined.excluded == without.excluded
    assert combined.excluded_patterns == (("*", "*", "libs", "*", "tests"), ("*", "libs", "*", "tests"))


@pytest.mark.parametrize("overrides, message", [
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
    (tmp_path / scope_io.PATTERNS_PATH).write_text(json.dumps(_patterns()), encoding="utf-8")
    calls: list[tuple[list[str], str | None]] = []

    def git(args: list[str], *, cwd: Path, input_text: str | None = None) -> str:
        assert cwd == tmp_path
        calls.append((args, input_text))
        return ("README.md\0efforts/a.md\0plugins/agent-bridge/contract/x.json\0tools/a.py\0"
                "plugins/x/libs/y/tests/t.py")

    removed = ["efforts/a.md", "plugins/agent-bridge/contract/x.json", "plugins/x/libs/y/tests/t.py"]
    assert scope_io.project_index(tmp_path, git) == removed
    assert calls[1] == (
        ["update-index", "-z", "--force-remove", "--stdin"], "".join(f"{path}\0" for path in removed),
    )


def test_tree_without_a_policy_is_not_projected(tmp_path: Path) -> None:
    def git(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("no policy means no index change")

    assert scope_io.project_index(tmp_path, git) == []

