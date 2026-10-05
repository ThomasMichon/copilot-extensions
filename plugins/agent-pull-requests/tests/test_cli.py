"""Light tests for the agent-pull-requests CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_pull_requests.__main__ import (
    _parse_json_tail,
    _strip_leading_diagnostic,
    build_parser,
    main,
)


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


def test_version_subcommand_exits_zero_and_prints_version(capsys):
    from agent_pull_requests import __version__

    rc = main(["version"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == f"agent-pull-requests {__version__}"


def test_planned_command_exits_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(
        "agent_pull_requests.__main__._github_merge",
        lambda repo, number, method, auto, delete_branch: (_ for _ in ()).throw(
            RuntimeError("pull request #7 is not mergeable")
        ),
    )

    rc = main(["merge", "--repo", "octo/example", "--number", "7"])

    assert rc == 1
    assert "not mergeable" in capsys.readouterr().err


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


def test_strip_leading_diagnostic_drops_ambient_auth_warning():
    stdout = (
        "could not mint a gh token for owner/repo; using ambient auth\n"
        "https://github.com/octo/example/pull/42\n"
    )

    assert _strip_leading_diagnostic(stdout) == "https://github.com/octo/example/pull/42"


def test_build_parser_accepts_create_flags():
    args = build_parser().parse_args(
        [
            "create",
            "--repo",
            "octo/example",
            "--head",
            "feature/x",
            "--base",
            "main",
            "--title",
            "Add x",
            "--draft",
            "--json",
        ]
    )

    assert args.command == "create"
    assert args.head == "feature/x"
    assert args.base == "main"
    assert args.draft is True


def test_create_json_smoke(monkeypatch, capsys):
    monkeypatch.setattr(
        "agent_pull_requests.__main__._resolve_claimant_worktree_id",
        lambda: "caller-wt-1",
    )
    monkeypatch.setattr(
        "agent_pull_requests.__main__._journal_pr_claim",
        lambda worktree_id, url, *, note: None,
    )
    monkeypatch.setattr(
        "agent_pull_requests.__main__._github_create",
        lambda repo, head, base, title, body, draft: {
            "repo": repo,
            "number": 42,
            "title": title,
            "url": f"https://github.com/{repo}/pull/42",
            "head": head,
            "base": base,
            "isDraft": draft,
        },
    )

    rc = main(
        [
            "create",
            "--repo",
            "octo/example",
            "--head",
            "feature/x",
            "--title",
            "Add x",
            "--json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["number"] == 42
    assert payload["url"].endswith("/pull/42")
    assert payload["claimed_by"] == "caller-wt-1"


def test_create_refuses_when_cwd_has_no_claimant(monkeypatch, capsys):
    monkeypatch.setattr(
        "agent_pull_requests.__main__._resolve_claimant_worktree_id", lambda: None,
    )
    called = {"create": False}
    monkeypatch.setattr(
        "agent_pull_requests.__main__._github_create",
        lambda *a, **k: called.__setitem__("create", True) or {},
    )

    rc = main([
        "create", "--repo", "octo/example", "--head", "feature/x",
        "--title", "Add x", "--json",
    ])

    assert rc == 2
    assert called["create"] is False
    payload = json.loads(capsys.readouterr().out)
    assert "no claimant" in payload["error"] or "tracked agent-worktrees worktree" in payload["error"]


def test_create_surfaces_a_claim_journal_failure_as_a_warning_not_a_create_failure(
    monkeypatch, capsys,
):
    monkeypatch.setattr(
        "agent_pull_requests.__main__._resolve_claimant_worktree_id",
        lambda: "caller-wt-1",
    )
    monkeypatch.setattr(
        "agent_pull_requests.__main__._journal_pr_claim",
        lambda worktree_id, url, *, note: "worktree not found: caller-wt-1",
    )
    monkeypatch.setattr(
        "agent_pull_requests.__main__._github_create",
        lambda repo, head, base, title, body, draft: {
            "repo": repo, "number": 7, "title": title,
            "url": f"https://github.com/{repo}/pull/7",
            "head": head, "base": base, "isDraft": draft,
        },
    )

    rc = main([
        "create", "--repo", "octo/example", "--head", "feature/x",
        "--title", "Add x", "--json",
    ])

    # The PR itself was created successfully -- a claim-journal failure is
    # never fatal to that (it already exists and can't be un-created).
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["number"] == 7
    assert payload["claimed_by"] is None
    assert "claim_warning" in payload


def test_build_parser_merge_defaults_to_squash():
    args = build_parser().parse_args(["merge", "--repo", "octo/example", "--number", "5"])

    assert args.method == "squash"
    assert args.auto is False
    assert args.delete_branch is False


def test_build_parser_merge_accepts_rebase_and_auto():
    args = build_parser().parse_args(
        ["merge", "--repo", "octo/example", "--number", "5", "--rebase", "--auto", "--delete-branch"]
    )

    assert args.method == "rebase"
    assert args.auto is True
    assert args.delete_branch is True


def test_merge_json_smoke(monkeypatch, capsys):
    monkeypatch.setattr(
        "agent_pull_requests.__main__._github_merge",
        lambda repo, number, method, auto, delete_branch: {
            "repo": repo,
            "number": number,
            "method": method,
            "auto": auto,
            "message": "Squashed and merged pull request #5",
        },
    )

    rc = main(["merge", "--repo", "octo/example", "--number", "5", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["method"] == "squash"
    assert "merged" in payload["message"]


def test_wait_returns_zero_on_merged(monkeypatch, capsys):
    monkeypatch.setattr("agent_pull_requests.__main__.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "agent_pull_requests.__main__._github_status",
        lambda repo, number: {
            "repo": repo,
            "number": number,
            "title": "Demo PR",
            "url": "https://github.com/octo/example/pull/17",
            "state": "MERGED",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
            "transport": "agent-worktrees repos gh",
        },
    )

    rc = main(["wait", "--repo", "octo/example", "--number", "17", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "MERGED"
    assert payload["timedOut"] is False


def test_wait_times_out_on_open_pr(monkeypatch, capsys):
    monkeypatch.setattr("agent_pull_requests.__main__.time.sleep", lambda _: None)
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
            "reviewDecision": "",
            "transport": "agent-worktrees repos gh",
        },
    )

    rc = main(
        ["wait", "--repo", "octo/example", "--number", "17", "--interval", "0", "--timeout", "0", "--json"]
    )

    assert rc == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["timedOut"] is True
