"""Changed-path-only publication regression using the existing isolated fixture."""

from __future__ import annotations

import json
from pathlib import Path

import test_check_changefile_presence as fixtures
from test_check_changefile_presence import _git, _run, _write

repo = fixtures.repo


def test_changefile_gate_does_not_charge_retired_artifact_only_consumer(repo: Path):
    _write(repo, ".gitignore", ".ruff_cache/\n*.egg-info/\n")
    _write(repo, "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py", "shared = 1\n")
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "library baseline")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(repo, "plugins/retired/libs/shared-lib/.ruff_cache/CACHEDIR.TAG", "cache")
    _write(repo, "plugins/retired/libs/shared-lib/src/shared.egg-info/PKG-INFO", "metadata")
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 2\n")
    _write(
        repo, ".changefiles/library.json",
        json.dumps({"comment": "library fix", "changes": [{"plugin": "alpha", "type": "patch"}]}),
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "library fix with changefile for real consumer")
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "retired" not in result.stdout + result.stderr
