#!/usr/bin/env python3
"""Dev-side guard: the reviewed Main release scope classifies this repository.

Fails when a tracked top-level entry is neither released nor excluded (a new
entry stays off Main until a reviewer classifies it), when a promotion unit
source that runs from the promoted candidate falls outside the release scope,
or when an adopter page links into a path Main does not release.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import promotion_release_scope as scope_io  # noqa: E402


def _docs() -> Any:
    spec = importlib.util.spec_from_file_location(
        "docs_consistency", ROOT / "tools" / "check-docs-consistency.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unreleased_links(root: Path, scope: scope_io.ReleaseScope, pages: tuple[str, ...]) -> list[str]:
    """Adopter-page links whose local target exists on Dev but not on Main."""
    local_links = _docs().local_links
    root = root.resolve()
    found = []
    for name in pages:
        page = root / name
        if not page.is_file():
            continue
        for link in local_links(page.read_text(encoding="utf-8")):
            path = urlsplit(link).path
            if not path:
                continue
            target = Path(os.path.abspath(page.parent / unquote(path)))
            try:
                relative = target.relative_to(root).as_posix()
            except ValueError:
                continue
            if relative != "." and not scope.released(relative):
                found.append(f"{name} links into a path Main does not release; use its Dev URL: {link}")
    return found


def problems(root: Path, tracked: list[str], pages: tuple[str, ...]) -> list[str]:
    scope = scope_io.load_scope(root)
    if scope is None:
        return [f"{scope_io.POLICY_PATH} is missing"]
    found = [f"unclassified top-level entry: {entry}" for entry in scope.unclassified(tracked)]
    manifest = json.loads((root / "tools/promotion-unit-manifest.json").read_text(encoding="utf-8"))
    names = [*manifest["sources"], *manifest["coverage_targets"],
             *(group[0] for group in manifest["node_groups"])]
    found += [f"promotion unit source is not released to Main: {name}"
              for name in names if not scope.released(name)]
    return found + unreleased_links(root, scope, pages)


def main() -> int:
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True,
    ).stdout.decode("utf-8")
    pages: tuple[str, ...] = _docs().ADOPTER_PAGES
    found = problems(ROOT, [path for path in listing.split("\0") if path], pages)
    for problem in found:
        print(f"  {problem}", file=sys.stderr)
    if found:
        print(f"check-promotion-release-scope: FAILED; see {scope_io.POLICY_PATH}", file=sys.stderr)
        return 1
    print("check-promotion-release-scope: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())