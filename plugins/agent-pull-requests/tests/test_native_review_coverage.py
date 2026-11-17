"""Additive native review activity preserves unknown prior-provider coverage."""

from __future__ import annotations

from typing import Any

import pytest

from agent_pull_requests.watch_provider import decode_snapshot


def test_decoder_preserves_review_coverage_without_inventing_legacy_completeness() -> None:
    value: dict[str, Any] = {
        "schema": "agent-worktrees.pr-snapshot", "version": 1,
        "repo": "project/repo", "pr": 7, "provider": "gitea",
        "pr_state": "open", "merged": False, "head_sha": "a" * 40,
        "base_ref": "main", "mergeable": None, "checks_state": "",
        "title": "Synthetic", "draft": False,
    }
    legacy = decode_snapshot(value, "project/repo", 7).native_snapshot
    assert legacy is not None and "reviews_complete" not in legacy
    value.update(reviews_complete=True, updated_at="2026-01-01T00:00:00Z", reviews=[])
    decoded = decode_snapshot(value, "project/repo", 7).native_snapshot
    assert decoded is not None and decoded["reviews_complete"] is True and decoded["reviews"] == []
    assert decoded["updated_at"] == value["updated_at"]
    malformed_values: tuple[object, ...] = ("true", 1, {}, [])
    for malformed in malformed_values:
        value["reviews_complete"] = malformed
        with pytest.raises(ValueError, match="details"):
            decode_snapshot(value, "project/repo", 7)
    value.update(reviews_complete=None, updated_at=None)
    with pytest.raises(ValueError, match="details"):
        decode_snapshot(value, "project/repo", 7)
