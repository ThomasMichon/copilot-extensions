from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


_SCRIPT = Path(__file__).parents[1] / "scripts" / "reviewer_source.py"
_SPEC = importlib.util.spec_from_file_location("reviewer_source", _SCRIPT)
assert _SPEC and _SPEC.loader
reviewer_source = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(reviewer_source)


def _policy(tmp_path, **over):
    guidance = tmp_path / "guidance.md"
    guidance.write_text("trusted guidance", encoding="utf-8")
    policy = {
        "repository": "ThomasMichon/copilot-extensions",
        "lane": "github.com/ThomasMichon/copilot-extensions",
        "acting_identity": "ThomasMichon",
        "landing": "self",
        "allow_forks": True,
        "excluded_author_associations": ["OWNER", "MEMBER", "COLLABORATOR"],
        "excluded_authors": ["dependabot[bot]"],
        "task_label": "self-hosted-review",
        "guidance": "guidance.md",
        "_root": str(tmp_path),
    }
    policy.update(over)
    return policy


def _pr(**over):
    value = {
        "number": 42,
        "title": "Contribute feature",
        "url": "https://github.com/ThomasMichon/copilot-extensions/pull/42",
        "updatedAt": "2026-08-31T01:00:00Z",
        "isDraft": False,
        "isCrossRepository": True,
        "headRefOid": "abc123",
        "baseRefName": "main",
        "authorAssociation": "CONTRIBUTOR",
        "author": {"login": "external-user"},
        "state": "OPEN",
    }
    value.update(over)
    return value


def test_eligibility_excludes_owner_and_allows_external_fork(tmp_path):
    policy = _policy(tmp_path)
    assert reviewer_source.eligibility(_pr(), policy)[0] is True
    assert reviewer_source.eligibility(
        _pr(authorAssociation="OWNER"), policy
    )[0] is False


def test_render_task_uses_stock_recipe_and_untrusted_guidance(tmp_path):
    calls = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "title": "review ThomasMichon/copilot-extensions#42 to resolution",
                    "prompt": "stock charter",
                    "requires": [],
                    "labels": ["recipe:reviewer", "landing:self"],
                    "goal": "review",
                    "done_criteria": "merged",
                }
            ),
            stderr="",
        )

    task = reviewer_source.render_task(_pr(), _policy(tmp_path), runner=runner)
    assert calls[0][0:4] == ["agent-dispatch", "recipes", "render", "reviewer"]
    assert "trusted guidance" in task["prompt"]
    assert "Acting GitHub identity: ThomasMichon" in task["prompt"]
    assert "Reviewed head generation: abc123" in task["prompt"]
    assert "reviewer_source.py wait" in task["prompt"]
    assert task["payload_ref"].endswith("#42@abc123:base=main")
    assert task["dedup_key"].endswith("ThomasMichon/copilot-extensions#42")
    assert "self-hosted-review" in task["labels"]


def test_side_load_excluded_pull_request_emits_nothing(tmp_path):
    responses = [
        {
            "data": {
                "repository": {
                    "pullRequest": _pr(authorAssociation="COLLABORATOR")
                }
            }
        }
    ]

    def runner(_argv, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(responses.pop(0)),
            stderr="",
        )

    assert reviewer_source.side_load(
        "ThomasMichon/copilot-extensions#42",
        _policy(tmp_path),
        runner=runner,
    ) == []


def test_discovery_watermark_skips_completed_head(tmp_path):
    responses = [
        [
            {
                "payload_ref": (
                    "github-pr:ThomasMichon/copilot-extensions#42@abc123:base=main"
                )
            }
        ],
        {
            "data": {
                "repository": {
                    "pullRequests": {
                        "nodes": [_pr()],
                        "pageInfo": {
                            "hasNextPage": False,
                            "endCursor": None,
                        },
                    }
                }
            }
        },
    ]

    def runner(_argv, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(responses.pop(0)),
            stderr="",
        )

    assert reviewer_source.discover(_policy(tmp_path), runner=runner) == []


def test_terminal_watermark_queries_all_terminal_outcomes(tmp_path):
    calls = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    reviewer_source._completed_payload_refs(_policy(tmp_path), runner=runner)
    assert calls[0][calls[0].index("--status") + 1] == (
        "completed,abandoned,dead_letter"
    )
    assert calls[0][calls[0].index("--evaluator-ref") + 1] == (
        "copilot-extensions-review-lifecycle"
    )


def test_discovery_bootstrap_watermark_skips_old_open_pull_request(tmp_path):
    responses = [
        [],
        {
            "data": {
                "repository": {
                    "pullRequests": {
                        "nodes": [_pr(updatedAt="2026-08-01T00:00:00Z")],
                        "pageInfo": {
                            "hasNextPage": False,
                            "endCursor": None,
                        },
                    }
                }
            }
        },
    ]

    def runner(_argv, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(responses.pop(0)),
            stderr="",
        )

    assert reviewer_source.discover(
        _policy(tmp_path, discover_updated_since="2026-08-31T00:00:00Z"),
        runner=runner,
    ) == []


@pytest.mark.parametrize("value", ["x", "other/repo#42", "0"])
def test_change_ref_validation(value):
    with pytest.raises(reviewer_source.ReviewerSourceError):
        reviewer_source._parse_change_ref(
            value, "ThomasMichon/copilot-extensions"
        )


def test_wait_for_change_returns_on_new_head(tmp_path):
    responses = [
        {
            "data": {
                "repository": {
                    "pullRequest": _pr(headRefOid="old")
                }
            }
        },
        {
            "data": {
                "repository": {
                    "pullRequest": _pr(headRefOid="new")
                }
            }
        },
    ]

    def runner(_argv, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(responses.pop(0)),
            stderr="",
        )

    result = reviewer_source.wait_for_change(
        "42",
        "old",
        "main",
        _policy(tmp_path),
        interval=0,
        timeout=10,
        runner=runner,
        sleep=lambda _seconds: None,
    )
    assert result["event"] == "generation-changed"
    assert result["head"] == "new"


def test_wait_for_change_returns_on_base_retarget(tmp_path):
    responses = [
        {
            "data": {
                "repository": {
                    "pullRequest": _pr(
                        headRefOid="same", baseRefName="release"
                    )
                }
            }
        }
    ]

    def runner(_argv, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(responses.pop(0)),
            stderr="",
        )

    result = reviewer_source.wait_for_change(
        "42",
        "same",
        "main",
        _policy(tmp_path),
        interval=0,
        timeout=10,
        runner=runner,
        sleep=lambda _seconds: None,
    )
    assert result["event"] == "generation-changed"
    assert result["base"] == "release"


def test_discovery_paginates_all_open_pull_requests(tmp_path):
    responses = [
        [],
        {
            "data": {
                "repository": {
                    "pullRequests": {
                        "nodes": [],
                        "pageInfo": {
                            "hasNextPage": True,
                            "endCursor": "cursor-1",
                        },
                    }
                }
            }
        },
        {
            "data": {
                "repository": {
                    "pullRequests": {
                        "nodes": [],
                        "pageInfo": {
                            "hasNextPage": False,
                            "endCursor": None,
                        },
                    }
                }
            }
        },
    ]
    calls = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(responses.pop(0)),
            stderr="",
        )

    assert reviewer_source.discover(_policy(tmp_path), runner=runner) == []
    assert any("after=cursor-1" == value for value in calls[-1])
