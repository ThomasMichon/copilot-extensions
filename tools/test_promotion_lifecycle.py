"""An open promotion outlives its producing run and blocks another generation."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

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


@pytest.mark.parametrize("rows", [None, {}, "", [pr()] * 101])
def test_invalid_page_shape_is_not_empty_inventory(rows: object) -> None:
    with pytest.raises(lifecycle.PromotionAdmissionError):
        inventory(rows)


def test_all_pages_are_read_before_admission() -> None:
    calls = []
    first = [pr(index, "ordinary-control") for index in range(1, 101)]

    def api(path: str, *, timeout: float, body: dict) -> object:
        calls.append(body["variables"]["cursor"])
        assert 0 < timeout <= 30
        return page(first, total=101, cursor="after-100") if len(calls) == 1 else page([pr(101)], total=101)

    promotions = lifecycle.active_promotions(REPOSITORY, api=api)
    assert calls == [None, "after-100"]
    assert promotions[0].number == 101
    with pytest.raises(lifecycle.PromotionAdmissionError):
        lifecycle.require_idle(promotions)


def test_bounded_pagination_refuses_instead_of_silently_truncating() -> None:
    calls = []

    def api(path: str, *, timeout: float, body: dict) -> object:
        calls.append(path)
        rows = [pr(index, "ordinary-control") for index in range((len(calls) - 1) * 100 + 1, len(calls) * 100 + 1)]
        return page(rows, total=1001, cursor=f"after-{len(calls)}")

    with pytest.raises(lifecycle.PromotionAdmissionError, match="pagination limit"):
        lifecycle.active_promotions(REPOSITORY, api=api)
    assert len(calls) == 10


@pytest.mark.parametrize("repository", ["", "../project", "example/..", "example/project/extra"])
def test_invalid_repository_refuses_before_any_api_call(repository: str) -> None:
    def api(*args: object, **kwargs: object) -> object:
        pytest.fail("invalid repository reached the API")

    with pytest.raises(lifecycle.PromotionAdmissionError):
        lifecycle.active_promotions(repository, api=api)


def test_cli_status_blocks_dispatch_without_claiming_a_completed_promotion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(lifecycle, "_api", lambda *args, **kwargs: page([pr()]))
    output = tmp_path / "output"
    assert lifecycle.main(["--repository", REPOSITORY, "--github-output", str(output)]) == 0
    result = capsys.readouterr()
    assert json.loads(result.out)["idle"] is False
    assert "no new promotion is dispatched" in result.err
    assert output.read_bytes() == b"promotion_idle=false\n"
    assert lifecycle.main(["--repository", REPOSITORY, "--require-idle"]) == 1
    assert "resume their existing publication/merge job" in capsys.readouterr().err


def test_cli_existing_pr_opening_is_idempotent_but_never_makes_dispatch_idle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(lifecycle, "_api", lambda *args, **kwargs: page([pr()]))
    output = tmp_path / "output"
    assert lifecycle.main([
        "--repository", REPOSITORY, "--require-idle", "--github-output", str(output),
        "--candidate-branch", "release/promote-456", "--candidate-sha", SHA,
    ]) == 0
    assert json.loads(capsys.readouterr().out)["idle"] is False
    assert output.read_bytes() == b"promotion_idle=false\n"


def test_cli_transport_failure_is_visible_and_writes_no_success_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    def api(*args: object, **kwargs: object) -> object:
        raise subprocess.CalledProcessError(1, ["gh", "api", "synthetic"])

    monkeypatch.setattr(lifecycle, "_api", api)
    output = tmp_path / "output"
    assert lifecycle.main(["--repository", REPOSITORY, "--github-output", str(output)]) == 1
    assert not output.exists()
    assert "::error::promotion-admission:" in capsys.readouterr().err


def test_real_isolated_cli_bootstraps_its_own_read_only_helper() -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(Path(lifecycle.__file__)), "--help"],
        capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert "--require-idle" in result.stdout


def test_closing_an_earlier_pr_between_cursor_pages_cannot_hide_a_surviving_promotion() -> None:
    calls = []
    ordinary = [pr(number, "ordinary-control") for number in range(1, 101)]

    def api(path: str, *, timeout: float, body: dict) -> object:
        cursor = body["variables"]["cursor"]
        calls.append(cursor)
        if cursor is None:
            return page(ordinary, total=101, cursor="after-100")
        assert cursor == "after-100"
        # PR 1 closes after page one. Unlike offset page 2, the cursor still
        # returns the promotion after PR 100; changed totals also refuse idle.
        return page([pr(101)], total=100)

    with pytest.raises(lifecycle.PromotionAdmissionError, match="history changed"):
        lifecycle.active_promotions(REPOSITORY, api=api)
    assert calls == [None, "after-100"]
