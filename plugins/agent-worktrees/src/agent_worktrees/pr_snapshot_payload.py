"""Versioned one-shot snapshot boundary for the shared PR observation owner."""

from __future__ import annotations

from dataclasses import asdict

from .pr_contract import Baseline
from .pr_snapshot import PRSnapshot


def snapshot_payload(snapshot: PRSnapshot, repo: str, number: int, provider: str, api_base: str) -> dict:
    return {
        "schema": "agent-worktrees.pr-snapshot", "version": 1,
        "provider": provider, "api_base": api_base, "repo": repo, "pr": number,
        "pr_state": snapshot.pr_state, "merged": snapshot.merged,
        "head_sha": snapshot.head_sha, "base_ref": snapshot.base_ref,
        "mergeable": snapshot.mergeable, "checks_state": snapshot.checks_state,
        "checks_complete": snapshot.checks_complete,
        "check_failures": [asdict(failure) for failure in snapshot.check_failures],
        "title": snapshot.title, "draft": snapshot.draft, "author": snapshot.author,
        "reviews": [asdict(review) for review in snapshot.reviews],
        "cursor": Baseline.from_snapshot(snapshot).to_cursor(),
        "observation": snapshot.observation.to_dict() if snapshot.observation is not None else None,
    }
