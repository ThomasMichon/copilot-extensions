"""Composed Git cohorts qualify historical scope, not ASGI/native execution."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from test_http48_capture_guards import (
    capture_case as capture_case, planned, repository as repository,
)


@pytest.mark.parametrize("tree_available", [False, True])
def test_retained_historical_scope_survives_a_future_generation(
    repository, capture_case, monkeypatch, tree_available,
) -> None:
    evidence, _ = capture_case
    root, commit = repository
    registry, _, loaded = planned(root, commit, evidence)
    current = evidence.contract(registry)
    current["declared_range"]["current"] = evidence.GENERATION + 1
    protocol = evidence.previous.PREFIX + "protocol.py"
    row = next(source for source in current["source_paths"] if source["path"] == protocol)
    row["sha256"] = "f" * 64
    original_git = evidence.previous.git_evidence.git

    def git(*args):
        if not tree_available and args[:2] == ("ls-tree", "-r"):
            return SimpleNamespace(returncode=1)
        return original_git(*args)

    monkeypatch.setattr(evidence.previous.git_evidence, "git", git)
    errors = []
    evidence.validate(root, registry, loaded, errors)
    assert errors == []
    broken = copy.deepcopy(loaded)
    child_path = evidence.source_fixture_path(protocol)
    child = broken[child_path][1]
    child["captured_from"]["source_sha256"] = row["sha256"]
    broken[child_path] = (evidence.digest(evidence.encode(child)), child)
    evidence.validate(root, registry, broken, errors)
    assert errors and "shared source provenance mismatch" in errors[0]
