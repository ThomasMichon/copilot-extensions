"""Tests for `agent-worktrees claims find pr` (ThomasMichon/copilot-extensions
#4086): fleet-wide lookup of which locally tracked worktrees, across every
registered project, hold a claim on a PR in a given repo."""

from __future__ import annotations

import argparse
import json

import agent_worktrees.__main__ as m
from agent_worktrees import claims_find_cli, tracking


def _seed_pr_record(
    tmp_path, monkeypatch, project, worktree_id, *, repo, pr_state, number=1,
    url=None, owner_ref=None, codename=None,
):
    root = tmp_path / project
    tdir = root / "worktrees"
    tdir.mkdir(parents=True, exist_ok=True)
    wdir = root / worktree_id
    wdir.mkdir(exist_ok=True)
    rec = tracking.create_new_record(
        worktree_id, f"worktree/{worktree_id}", str(wdir), project,
        "example-machine", "wsl", tdir,
    )
    rec.owner_ref = owner_ref
    rec.codename = codename
    rec.prs.append(tracking.PRRecord(
        state=pr_state, repo=repo, number=number,
        url=url or f"https://github.com/{repo}/pull/{number}",
        branch=f"pr/{worktree_id}",
    ))
    tracking.save_record(rec, tdir / f"{worktree_id}.yaml")
    monkeypatch.setattr(
        "agent_worktrees.installer.read_projects_registry",
        lambda: {"projects": {"proj-a": {}, "proj-b": {}}},
    )
    monkeypatch.setattr(
        "agent_worktrees.config.project_dir",
        lambda name=None: tmp_path / (name or project),
    )
    return rec


def test_find_matches_open_pr_in_target_repo(monkeypatch, tmp_path):
    _seed_pr_record(
        tmp_path, monkeypatch, "proj-a", "wt-open", repo="acme/widgets",
        pr_state="open", number=42, owner_ref="m/owner/wt-root",
        codename="patient-blueprint",
    )
    matches = claims_find_cli._candidate_prs("acme/widgets", "open")
    assert len(matches) == 1
    m = matches[0]
    assert m["project"] == "proj-a"
    assert m["worktree_id"] == "wt-open"
    assert m["owner_ref"] == "m/owner/wt-root"
    assert m["codename"] == "patient-blueprint"
    assert m["pr"]["number"] == 42
    assert m["pr"]["state"] == "open"


def test_find_excludes_other_repos_and_stale_states(monkeypatch, tmp_path):
    _seed_pr_record(
        tmp_path, monkeypatch, "proj-a", "wt-other-repo", repo="acme/other",
        pr_state="open",
    )
    _seed_pr_record(
        tmp_path, monkeypatch, "proj-b", "wt-merged", repo="acme/widgets",
        pr_state="merged",
    )
    assert claims_find_cli._candidate_prs("acme/widgets", "open") == []
    merged = claims_find_cli._candidate_prs("acme/widgets", "merged")
    assert [m["worktree_id"] for m in merged] == ["wt-merged"]


def test_find_state_all_returns_every_local_state(monkeypatch, tmp_path):
    _seed_pr_record(
        tmp_path, monkeypatch, "proj-a", "wt-a", repo="acme/widgets",
        pr_state="open",
    )
    _seed_pr_record(
        tmp_path, monkeypatch, "proj-b", "wt-b", repo="acme/widgets",
        pr_state="closed",
    )
    matches = claims_find_cli._candidate_prs("acme/widgets", "all")
    assert {m["worktree_id"] for m in matches} == {"wt-a", "wt-b"}


def test_find_repo_match_is_case_insensitive(monkeypatch, tmp_path):
    _seed_pr_record(
        tmp_path, monkeypatch, "proj-a", "wt-a", repo="Acme/Widgets",
        pr_state="open",
    )
    matches = claims_find_cli._candidate_prs("acme/widgets", "open")
    assert len(matches) == 1


def test_cmd_claims_find_requires_repo(capfd):
    rc = claims_find_cli.cmd_claims_find(
        argparse.Namespace(json=True, claim_repo=None, claim_state="open", claim_live=False),
        ["pr"],
    )
    assert rc == 2
    out = json.loads(capfd.readouterr().out)
    assert "error" in out


def test_cmd_claims_find_requires_pr_kind(capfd):
    rc = claims_find_cli.cmd_claims_find(
        argparse.Namespace(json=True, claim_repo="acme/widgets", claim_state="open",
                          claim_live=False),
        [],
    )
    assert rc == 2


def test_cmd_claims_find_json_roundtrip(monkeypatch, tmp_path, capfd):
    _seed_pr_record(
        tmp_path, monkeypatch, "proj-a", "wt-a", repo="acme/widgets",
        pr_state="open", number=7,
    )
    rc = claims_find_cli.cmd_claims_find(
        argparse.Namespace(json=True, claim_repo="acme/widgets", claim_state="open",
                          claim_live=False),
        ["pr"],
    )
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["repo"] == "acme/widgets"
    assert out["state"] == "open"
    assert out["live_checked"] is False
    assert [m["worktree_id"] for m in out["matches"]] == ["wt-a"]


def test_cmd_claims_find_no_matches_returns_1(monkeypatch, tmp_path, capfd):
    monkeypatch.setattr(
        "agent_worktrees.installer.read_projects_registry",
        lambda: {"projects": {}},
    )
    rc = claims_find_cli.cmd_claims_find(
        argparse.Namespace(json=True, claim_repo="acme/widgets", claim_state="open",
                          claim_live=False),
        ["pr"],
    )
    assert rc == 1


def test_live_check_keeps_unverified_candidate_when_provider_unreachable(monkeypatch):
    # `_live_pr_state` degrades to None (unreachable/unconfigured) rather than
    # raising -- `_apply_live_check` must keep such a candidate rather than
    # silently dropping it (a network hiccup must never hide a real claim).
    monkeypatch.setattr(
        claims_find_cli, "_live_pr_state", lambda repo, number, project, provider_name: None,
    )
    matches = [{"project": "proj-a", "pr": {"number": 1, "state": "open", "provider": ""}}]
    kept = claims_find_cli._apply_live_check(matches, "acme/widgets", "open")
    assert len(kept) == 1
    assert kept[0]["pr"]["live_state"] is None


def test_live_check_drops_candidate_whose_live_state_disagrees(monkeypatch):
    monkeypatch.setattr(
        claims_find_cli, "_live_pr_state", lambda repo, number, project, provider_name: "merged",
    )
    matches = [{"project": "proj-a", "pr": {"number": 1, "state": "open", "provider": ""}}]
    kept = claims_find_cli._apply_live_check(matches, "acme/widgets", "open")
    assert kept == []


def test_live_check_all_state_keeps_every_live_result(monkeypatch):
    monkeypatch.setattr(
        claims_find_cli, "_live_pr_state", lambda repo, number, project, provider_name: "closed",
    )
    matches = [{"project": "proj-a", "pr": {"number": 1, "state": "open", "provider": ""}}]
    kept = claims_find_cli._apply_live_check(matches, "acme/widgets", "all")
    assert len(kept) == 1
    assert kept[0]["pr"]["live_state"] == "closed"


def test_live_check_passes_each_candidates_own_project_and_provider(monkeypatch):
    # #4086 review: a fleet scan can surface a candidate from a project
    # configured for a different provider than the invoking project's own --
    # the live check must resolve using the CANDIDATE's project/provider,
    # not a single global default.
    seen = []

    def _fake_live(repo, number, project, provider_name):
        seen.append((project, provider_name))
        return "open"

    monkeypatch.setattr(claims_find_cli, "_live_pr_state", _fake_live)
    matches = [
        {"project": "proj-gitea", "pr": {"number": 1, "state": "open", "provider": "gitea"}},
        {"project": "proj-github", "pr": {"number": 2, "state": "open", "provider": ""}},
    ]
    claims_find_cli._apply_live_check(matches, "acme/widgets", "open")
    assert seen == [("proj-gitea", "gitea"), ("proj-github", "")]


def test_pr_number_falls_back_to_parsing_the_url():
    class _FakePR:
        number = None
        url = "https://github.com/acme/widgets/pull/4086"

    assert claims_find_cli._pr_number(_FakePR()) == 4086


def test_pr_number_parses_gitea_pulls_url():
    class _FakePR:
        number = None
        url = "https://gitea.example.com/acme/widgets/pulls/456"

    assert claims_find_cli._pr_number(_FakePR()) == 456


def test_pr_number_parses_azure_devops_pullrequest_url():
    class _FakePR:
        number = None
        url = "https://dev.azure.com/org/project/_git/widgets/pullrequest/789"

    assert claims_find_cli._pr_number(_FakePR()) == 789


def test_pr_number_parses_generic_pull_requests_url():
    class _FakePR:
        number = None
        url = "https://example.com/acme/widgets/pull-requests/321"

    assert claims_find_cli._pr_number(_FakePR()) == 321


def test_pr_number_prefers_persisted_number_over_url():
    class _FakePR:
        number = 7
        url = "https://github.com/acme/widgets/pull/999"

    assert claims_find_cli._pr_number(_FakePR()) == 7


def test_pr_number_none_when_neither_available():
    class _FakePR:
        number = None
        url = ""

    assert claims_find_cli._pr_number(_FakePR()) is None


def test_candidate_prs_recovers_number_from_url_when_persisted_number_is_absent(
    monkeypatch, tmp_path,
):
    _seed_pr_record(
        tmp_path, monkeypatch, "proj-a", "wt-numberless", repo="acme/widgets",
        pr_state="open", number=None, url="https://github.com/acme/widgets/pull/321",
    )
    matches = claims_find_cli._candidate_prs("acme/widgets", "open")
    assert len(matches) == 1
    assert matches[0]["pr"]["number"] == 321


def test_live_pr_state_uses_lightweight_get_pull_not_get_snapshot(monkeypatch):
    # #4086 review: `get_snapshot` pulls reviews/checks too (several requests
    # per candidate on some providers) -- a fleet-wide sweep only needs the
    # PR lifecycle state, so this must call the lighter `get_pull`.
    from agent_worktrees import config as cfg
    from agent_worktrees.providers import base as providers_base

    calls = {"get_pull": 0, "get_snapshot": 0}

    class _FakeProvider:
        def get_pull(self, repo, number, *, api_base="", token=None):
            calls["get_pull"] += 1
            return providers_base.PullResult(state="closed", merged=True)

        def get_snapshot(self, repo, number, *, api_base="", token=None):
            calls["get_snapshot"] += 1
            raise AssertionError("get_snapshot should not be called")

    monkeypatch.setattr(
        cfg, "load_project_config",
        lambda project: __import__("types").SimpleNamespace(
            default_repo=__import__("types").SimpleNamespace(
                pr=__import__("types").SimpleNamespace(provider="github", api_base=""),
            ),
        ),
    )
    monkeypatch.setattr(
        "agent_worktrees.providers.get_provider", lambda name: _FakeProvider(),
    )
    monkeypatch.setattr(
        "agent_worktrees.providers.account_token_for_slug", lambda repo, prcfg: "tok",
    )

    live_state = claims_find_cli._live_pr_state("acme/widgets", 1, "proj-a", "")
    assert live_state == "merged"
    assert calls == {"get_pull": 1, "get_snapshot": 0}


def test_live_pr_state_reports_open_when_not_merged(monkeypatch):
    from agent_worktrees import config as cfg
    from agent_worktrees.providers import base as providers_base

    class _FakeProvider:
        def get_pull(self, repo, number, *, api_base="", token=None):
            return providers_base.PullResult(state="open", merged=False)

    monkeypatch.setattr(
        cfg, "load_project_config",
        lambda project: __import__("types").SimpleNamespace(
            default_repo=__import__("types").SimpleNamespace(
                pr=__import__("types").SimpleNamespace(provider="github", api_base=""),
            ),
        ),
    )
    monkeypatch.setattr(
        "agent_worktrees.providers.get_provider", lambda name: _FakeProvider(),
    )
    monkeypatch.setattr(
        "agent_worktrees.providers.account_token_for_slug", lambda repo, prcfg: "tok",
    )

    assert claims_find_cli._live_pr_state("acme/widgets", 1, "proj-a", "") == "open"


def test_claims_find_pr_parser_registers_expected_flags():
    args = m.build_parser().parse_args(
        ["claims", "find", "pr", "--repo", "acme/widgets", "--state", "closed",
         "--live", "--json"])
    assert args.target == ["find", "pr"]
    assert args.claim_repo == "acme/widgets"
    assert args.claim_state == "closed"
    assert args.claim_live is True
    assert args.json is True


def test_claims_find_pr_parser_rejects_invalid_state():
    import pytest

    with pytest.raises(SystemExit):
        m.build_parser().parse_args(
            ["claims", "find", "pr", "--repo", "acme/widgets", "--state", "typo"])


def test_cmd_claims_dispatches_find_end_to_end(monkeypatch, tmp_path, capfd):
    # #4086 review: exercise the PUBLIC `cmd_claims` dispatcher (parser
    # registration + target slicing + --state choices), not just
    # `cmd_claims_find` directly -- a regression in the 'find' target
    # slicing or parser wiring would otherwise leave the advertised command
    # unusable while the lower-level unit tests above still pass.
    _seed_pr_record(
        tmp_path, monkeypatch, "proj-a", "wt-a", repo="acme/widgets",
        pr_state="open", number=7,
    )
    args = m.build_parser().parse_args(
        ["claims", "find", "pr", "--repo", "acme/widgets", "--json"])
    rc = m.cmd_claims(args)
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["repo"] == "acme/widgets"
    assert [match["worktree_id"] for match in out["matches"]] == ["wt-a"]
