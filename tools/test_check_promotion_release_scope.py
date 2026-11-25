"""Main release scope guard contracts, independent of the live tree."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "release_scope_guard", Path(__file__).with_name("check-promotion-release-scope.py"))
assert SPEC is not None and SPEC.loader is not None
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def _repo(root: Path, readme: str) -> None:
    (root / "tools").mkdir()
    (root / "tools/promotion-release-scope.json").write_text(json.dumps({
        "schema": "copilot-extensions.promotion-release-scope/v1",
        "release_roots": ["README.md", "docs", "tools"],
        "excluded": {"docs/notes": "Dev only.", "efforts": "Dev only."},
    }), encoding="utf-8")
    (root / "tools/promotion-unit-manifest.json").write_text(json.dumps({
        "sources": ["tools/a.py"], "coverage_targets": ["docs/notes/b.py"],
        "node_groups": [["tools/test_a.py", []]],
    }), encoding="utf-8")
    (root / "README.md").write_text(readme, encoding="utf-8")


def test_reports_unclassified_entries_unreleased_sources_and_links(tmp_path: Path) -> None:
    _repo(tmp_path, "[guide](docs/guide.md)\n[notes](docs/notes/plan.md)\n"
          "[effort](efforts/plan.md#plan)\n[dev](https://github.com/o/r/blob/dev/efforts/plan.md)\n")
    found = guard.problems(tmp_path, ["README.md", "efforts/plan.md", "new/x"], ("README.md",))
    assert found == [
        "unclassified top-level entry: new",
        "promotion unit source is not released to Main: docs/notes/b.py",
        "README.md links into a path Main does not release; use its Dev URL: docs/notes/plan.md",
        "README.md links into a path Main does not release; use its Dev URL: efforts/plan.md#plan",
    ]


def test_a_classified_tree_passes(tmp_path: Path) -> None:
    _repo(tmp_path, "[guide](docs/guide.md)\n")
    manifest = tmp_path / "tools/promotion-unit-manifest.json"
    manifest.write_text(json.dumps({"sources": [], "coverage_targets": [], "node_groups": []}),
                        encoding="utf-8")
    assert guard.problems(tmp_path, ["README.md", "efforts/x", "tools/y"], ("README.md",)) == []