"""Focused tests for tools/check-effort-vision-structure.py.

Proves the guard fails a malformed effort/vision README and passes a valid
one, using the explicit-path mode (bypassing the git-diff scoping) so the
tests are independent of the working tree's actual diff.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
MODULE_PATH = TOOLS_DIR / "check-effort-vision-structure.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_effort_vision_structure", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


mod = _load_module()

VALID_EFFORT = """# Sample Effort

- **Slug:** `sample-effort`
- **Repo:** copilot-extensions
- **Branch(es):** `worktree/sample`
- **Created:** 2026-01-01
- **Status:** Draft

## Guiding Intent

Intent text.

## Context

Context text.

## Request

Request text.

## Plan

### Phase 1 - name
- [ ] item

## Validation Plan

- [ ] item

## Journal

### 2026-01-01 - Kickoff
- created
"""

INVALID_EFFORT = """# Sample Effort

- **Slug:** `sample-effort`
- **Repo:** copilot-extensions
- **Status:** Draft

## Guiding Intent

Intent text.

## Plan

### Phase 1 - name
- [ ] item

## Journal

### 2026-01-01 - Kickoff
- created
"""

VALID_VISION = """# Sample Vision

- **Subject:** sample subsystem
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-01-01

## Purpose & Intent

Purpose text.

## Concepts & Components

Concepts text.

## Features

### a-feature

Feature text.

## Behaviors

### a-behavior

Behavior text.

## Non-Goals / Boundaries

Boundaries text.

## See Also

- Parent vision: none
"""

INVALID_VISION = """# Sample Vision

- **Subject:** sample subsystem
- **Scope:** leaf
- **Status:** Active

## Purpose & Intent

Purpose text.

## See Also

- Parent vision: none
"""

NON_SCHEMA_README = """# Just an index page

No frontmatter bullet markers here at all.

## Some Heading

Text.
"""


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_valid_effort_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    path = _write(tmp_path, "efforts/active/sample-effort/README.md", VALID_EFFORT)
    assert mod.check_file(path) == []


def test_invalid_effort_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    path = _write(tmp_path, "efforts/active/sample-effort/README.md", INVALID_EFFORT)
    errors = mod.check_file(path)
    assert len(errors) == 1
    assert "## Context" in errors[0]
    assert "## Request" in errors[0]
    assert "## Validation Plan" in errors[0]


def test_valid_vision_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    path = _write(tmp_path, "visions/sample/README.md", VALID_VISION)
    assert mod.check_file(path) == []


def test_invalid_vision_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    path = _write(tmp_path, "visions/sample/README.md", INVALID_VISION)
    errors = mod.check_file(path)
    assert len(errors) == 1
    assert "## Concepts & Components" in errors[0]
    assert "## Non-Goals / Boundaries" in errors[0]


def test_non_schema_readme_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    path = _write(tmp_path, "efforts/README.md", NON_SCHEMA_README)
    assert mod.check_file(path) == []


def test_main_explicit_paths_reports_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    bad = _write(tmp_path, "efforts/active/sample-effort/README.md", INVALID_EFFORT)
    rc = mod.main([str(bad)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "malformed effort/vision doc" in captured.err


def test_main_explicit_paths_reports_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    good = _write(tmp_path, "efforts/active/sample-effort/README.md", VALID_EFFORT)
    rc = mod.main([str(good)])
    assert rc == 0


def _git(repo, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_changed_readmes_base_sharing_no_merge_base_degrades_to_soft_skip(
    tmp_path, monkeypatch, capsys,
):
    """A `base` that RESOLVES but shares no common ancestor with HEAD at
    all (the confirmed fallout of a deliberate `main` history rewrite --
    see docs/pipelines.md's "If main's history is force-rewritten") must
    degrade to a soft no-op (no README misreported as changed), never
    silently diff raw `base` directly against HEAD's current tree (which
    previously could misattribute an untouched effort/vision README as
    "changed by this branch" merely because it differs from `main`'s last
    promotion snapshot)."""
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "checkout", "-q", "-b", "dev")
    _write(tmp_path, "efforts/active/sample-effort/README.md", VALID_EFFORT)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")

    _git(tmp_path, "checkout", "-q", "--orphan", "rewritten-main")
    _write(tmp_path, "unrelated.txt", "rewritten history\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "unrelated root (simulates a rewritten main)")
    rewritten_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", rewritten_sha)
    _git(tmp_path, "checkout", "-q", "dev")
    _write(tmp_path, "efforts/active/sample-effort/README.md", INVALID_EFFORT)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "edit effort README (same branch, unrelated to origin/main)")

    paths = mod._changed_readmes("origin/main", "HEAD")
    assert paths == []
    assert "shares no common history" in capsys.readouterr().out
