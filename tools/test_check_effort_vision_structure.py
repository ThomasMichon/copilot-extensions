"""Focused tests for tools/check-effort-vision-structure.py.

Proves the guard fails a malformed effort/vision README and passes a valid
one, using the explicit-path mode (bypassing the git-diff scoping) so the
tests are independent of the working tree's actual diff.
"""
from __future__ import annotations

import importlib.util
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
