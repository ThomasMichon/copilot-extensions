"""Light tests for the agent-pull-requests CLI."""

from __future__ import annotations

import json

from agent_pull_requests.__main__ import _parse_json_tail, build_parser, main


def test_build_parser_accepts_status_flags():
    args = build_parser().parse_args(
        ["status", "--repo", "octo/example", "--number", "17", "--json"]
    )

    assert args.command == "status"
    assert args.repo == "octo/example"
    assert args.number == 17
    assert args.json is True


def test_status_json_smoke(monkeypatch, capsys):
    monkeypatch.setattr(
        "agent_pull_requests.__main__._github_status",
        lambda repo, number: {
            "repo": repo,
            "number": number,
            "title": "Demo PR",
            "url": "https://github.com/octo/example/pull/17",
            "state": "OPEN",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
            "transport": "agent-worktrees repos gh",
        },
    )

    rc = main(["status", "--repo", "octo/example", "--number", "17", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repo"] == "octo/example"
    assert payload["number"] == 17
    assert payload["mergeable"] == "MERGEABLE"
    assert payload["reviewDecision"] == "APPROVED"


def test_planned_command_exits_cleanly(capsys):
    rc = main(["merge"])

    assert rc == 2
    assert "not implemented yet" in capsys.readouterr().err


def test_parse_json_tail_ignores_leading_warning():
    stdout = (
        "could not mint a gh token for owner/repo; using ambient auth\n"
        '{"data": {"repository": {"pullRequest": {"number": 17}}}}'
    )

    payload = _parse_json_tail(stdout)

    assert payload["data"]["repository"]["pullRequest"]["number"] == 17


def test_parse_json_tail_raises_on_no_json():
    try:
        _parse_json_tail("just a warning, no JSON anywhere")
    except RuntimeError as exc:
        assert "non-JSON output" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
