"""Promotion ownership decisions using only in-memory provider responses."""

from __future__ import annotations

import copy

import pytest

import promotion_lifecycle as lifecycle

REPOSITORY = "example/project"
SHA = "a" * 40


def pr(number: int = 123, branch: str = "release/promote-456") -> dict:
    return {
        "number": number, "state": "open", "base": {"ref": "main"},
        "head": {"ref": branch, "sha": SHA, "repo": {"full_name": REPOSITORY}},
        "user": {"login": "example"},
    }


def page(rows: object, *, total: int | None = None, cursor: str | None = None) -> dict:
    nodes = rows
    if isinstance(rows, list):
        nodes = []
        for row in rows:
            if not isinstance(row, dict):
                nodes.append(row)
                continue
            head, base, user = row.get("head"), row.get("base"), row.get("user")
            source = head.get("repo") if isinstance(head, dict) else None
            state = row.get("state")
            nodes.append({
                "number": row.get("number"), "state": state.upper() if isinstance(state, str) else state,
                "merged": False, "baseRefName": base.get("ref") if isinstance(base, dict) else None,
                "headRefName": head.get("ref") if isinstance(head, dict) else None,
                "headRefOid": head.get("sha") if isinstance(head, dict) else None,
                "headRepository": {"nameWithOwner": source.get("full_name")} if isinstance(source, dict) else None,
                "author": user,
            })
    return {"data": {"repository": {"pullRequests": {
        "nodes": nodes, "totalCount": len(rows) if total is None and isinstance(rows, list) else total,
        "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor},
    }}}}


def inventory(rows: object) -> tuple[lifecycle.ActivePromotion, ...]:
    def api(path: str, *, timeout: float, body: dict) -> object:
        assert path == "graphql"
        assert "states:OPEN" in body["query"] and "direction:ASC" in body["query"]
        assert body["variables"] == {"owner": "example", "name": "project", "cursor": None}
        assert 0 < timeout <= 30
        return page(copy.deepcopy(rows))

    return lifecycle.active_promotions(REPOSITORY, api=api)


def test_complete_empty_inventory_is_the_only_new_generation_admission() -> None:
    assert inventory([]) == ()
    lifecycle.require_idle(())
    promotions = inventory([pr()])
    assert promotions == (lifecycle.ActivePromotion(123, "release/promote-456", SHA, 456),)
    with pytest.raises(lifecycle.PromotionAdmissionError, match="#123 remain active"):
        lifecycle.require_idle(promotions)


@pytest.mark.parametrize("run_state", ["failure", "cancelled", "timed_out", "completed", None])
def test_producing_run_terminal_state_cannot_release_an_open_pr(run_state: object) -> None:
    row = pr()
    row["producing_run_conclusion"] = run_state
    with pytest.raises(lifecycle.PromotionAdmissionError, match="remain active"):
        lifecycle.require_idle(inventory([row]))


def test_only_exact_existing_candidate_can_be_reused_after_qualification() -> None:
    promotions = inventory([pr()])
    lifecycle.require_idle(promotions, candidate_branch="release/promote-456", candidate_sha=SHA)
    for branch, sha in (("release/promote-457", SHA), ("release/promote-456", "b" * 40)):
        with pytest.raises(lifecycle.PromotionAdmissionError, match="remain active"):
            lifecycle.require_idle(promotions, candidate_branch=branch, candidate_sha=sha)
    with pytest.raises(lifecycle.PromotionAdmissionError, match="#123, #124"):
        lifecycle.require_idle(
            inventory([pr(), pr(124, "release/promote-457")]),
            candidate_branch="release/promote-456", candidate_sha=SHA,
        )


def test_nonpromotion_and_foreign_prs_cannot_claim_the_release_namespace() -> None:
    normal = pr(branch="release-pipeline/control-repair")
    fork = pr(125)
    fork["head"]["repo"]["full_name"] = "example/fork"
    assert inventory([normal, fork]) == ()


def test_unknown_same_repository_publisher_cannot_be_silently_ignored() -> None:
    row = pr()
    row["user"]["login"] = "unexpected-publisher"
    with pytest.raises(lifecycle.PromotionAdmissionError, match="unexpected publisher"):
        inventory([row])


@pytest.mark.parametrize("mutation", [
    "not-row", "number", "duplicate", "state", "base", "head", "user", "source", "branch", "sha",
])
def test_invalid_or_incomplete_inventory_never_defaults_to_idle(mutation: str) -> None:
    row = pr()
    if mutation == "not-row":
        rows = [None]
    elif mutation == "duplicate":
        rows = [row, row]
    else:
        if mutation == "number":
            row["number"] = True
        elif mutation == "state":
            row["state"] = "closed"
        elif mutation == "base":
            row["base"]["ref"] = "dev"
        elif mutation == "head":
            row["head"] = None
        elif mutation == "user":
            row["user"] = {}
        elif mutation == "source":
            row["head"]["repo"] = None
        elif mutation == "branch":
            row["head"]["ref"] = "release/promote-invalid"
        else:
            row["head"]["sha"] = "not-a-sha"
        rows = [row]
    with pytest.raises(lifecycle.PromotionAdmissionError):
        inventory(rows)
