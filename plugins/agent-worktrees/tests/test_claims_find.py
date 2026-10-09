"""Tests for `agent-worktrees claims find pr` (ThomasMichon/copilot-extensions
#4086): fleet-wide lookup of which locally tracked worktrees, across every
registered project, hold a claim on a PR in a given repo."""

from __future__ import annotations

import argparse
import json

import pytest

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


def test_cmd_claims_find_without_repo_lists_every_repo_per_project(monkeypatch, tmp_path, capfd):
    _seed_pr_record(tmp_path, monkeypatch, "proj-a", "wt-a", repo="acme/widgets", pr_state="open", number=7)
    _seed_pr_record(tmp_path, monkeypatch, "proj-b", "wt-b", repo="other/gadgets", pr_state="closed",
                    number=None, url="https://gitea.example.com/other/gadgets/pulls/9")
    monkeypatch.setattr(claims_find_cli, "_authority_resolver", lambda project: lambda slug, provider: "github.com")
    rc = claims_find_cli.cmd_claims_find(
        argparse.Namespace(json=True, claim_repo=None, claim_state="all", claim_live=False), ["pr"])
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert (out["schema"], out["repo"]) == (1, None)
    assert out["projects"] == [
        {"project": "proj-a", "status": "ok", "prs": [
            {"worktree_id": "wt-a", "authority": "github.com", "repo": "acme/widgets", "number": 7,
             "state": "open"}]},
        {"project": "proj-b", "status": "ok", "prs": [
            {"worktree_id": "wt-b", "authority": "github.com", "repo": "other/gadgets", "number": 9,
             "state": "closed"}]},
    ]


def test_the_authority_comes_from_the_slugs_own_binding_not_the_default_repo(monkeypatch):
    from types import SimpleNamespace as NS

    from agent_worktrees import config as cfg
    from agent_worktrees import pr_config

    bindings = {
        "acme/home": NS(resolved=True, repo_config=NS(pr=NS(provider="github", api_base=""))),
        "forge/tool": NS(resolved=True, repo_config=NS(pr=NS(provider="gitea",
                                                              api_base="https://forge.example.com/gitea/"))),
        "nobody/knows": NS(resolved=False, repo_config=None),
    }
    monkeypatch.setattr(cfg, "load_project_config", lambda project: NS(default_repo=NS(pr=NS(provider="github"))))
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda config, slug: bindings[slug])
    resolve = claims_find_cli._authority_resolver("proj-a")
    assert resolve("acme/home", "") == "github.com"
    assert resolve("forge/tool", "") == "forge.example.com/gitea"  # not the project's default github.com
    assert resolve("nobody/knows", "") is None  # pr bar can't read it either


def test_repo_filter_matches_a_bare_legacy_record_by_its_url(monkeypatch, tmp_path, capfd):
    _seed_pr_record(tmp_path, monkeypatch, "proj-a", "wt-legacy", repo="widgets", pr_state="open", number=8,
                    url="https://github.com/acme/widgets/pull/8")
    monkeypatch.setattr(claims_find_cli, "_authority_resolver", lambda project: lambda slug, provider: "github.com")
    rc = claims_find_cli.cmd_claims_find(
        argparse.Namespace(json=True, claim_repo="acme/widgets", claim_state="open", claim_live=False), ["pr"])
    out = json.loads(capfd.readouterr().out)
    assert rc == 0 and [m["worktree_id"] for m in out["matches"]] == ["wt-legacy"]
    assert [p["repo"] for e in out["projects"] for p in e["prs"]] == ["acme/widgets"]


def test_claims_find_one_failing_project_does_not_hide_the_other(monkeypatch, tmp_path, capfd):
    _seed_pr_record(tmp_path, monkeypatch, "proj-a", "wt-a", repo="acme/widgets", pr_state="open")

    def project_dir(name=None):
        if name == "proj-b":
            raise OSError("proj-b's config is gone")
        return tmp_path / name

    monkeypatch.setattr("agent_worktrees.config.project_dir", project_dir)
    rc = claims_find_cli.cmd_claims_find(
        argparse.Namespace(json=True, claim_repo=None, claim_state="all", claim_live=False), ["pr"])
    assert rc == 0
    projects = {p["project"]: p for p in json.loads(capfd.readouterr().out)["projects"]}
    assert projects["proj-a"]["status"] == "ok" and len(projects["proj-a"]["prs"]) == 1
    assert projects["proj-b"]["status"] == "failed" and projects["proj-b"]["error"]
    assert projects["proj-b"]["prs"] == []


def test_claims_find_counts_a_tracking_record_it_cannot_load(monkeypatch, tmp_path):
    _seed_pr_record(tmp_path, monkeypatch, "proj-a", "wt-a", repo="acme/widgets", pr_state="open")
    (tmp_path / "proj-a" / "worktrees" / "broken.yaml").write_text("{not: [yaml", encoding="utf-8")
    entry = next(p for p in claims_find_cli.scan_projects(None, "all") if p["project"] == "proj-a")
    assert (entry["status"], entry["unreadable"], len(entry["prs"])) == ("ok", 1, 1)


def test_claims_find_empty_registry_is_an_empty_ok_envelope(monkeypatch, capfd):
    monkeypatch.setattr("agent_worktrees.installer.read_projects_registry", lambda: {"projects": {}})
    rc = claims_find_cli.cmd_claims_find(
        argparse.Namespace(json=True, claim_repo=None, claim_state="all", claim_live=False), ["pr"])
    assert rc == 0
    assert json.loads(capfd.readouterr().out)["projects"] == []


def test_claims_find_unreadable_registry_exits_non_zero(monkeypatch, capfd):
    def boom():
        raise OSError("projects.yaml: permission denied")

    monkeypatch.setattr("agent_worktrees.installer.read_projects_registry", boom)
    rc = claims_find_cli.cmd_claims_find(
        argparse.Namespace(json=True, claim_repo=None, claim_state="all", claim_live=False), ["pr"])
    assert rc == claims_find_cli.REGISTRY_UNREADABLE_EXIT
    assert "registry" in json.loads(capfd.readouterr().out)["error"]


def test_claims_find_live_needs_a_repo(capfd):
    rc = claims_find_cli.cmd_claims_find(
        argparse.Namespace(json=True, claim_repo=None, claim_state="open", claim_live=True), ["pr"])
    assert rc == 2


@pytest.mark.parametrize("repo, url, expected", [
    ("acme/widgets", "", "acme/widgets"),
    ("widgets", "https://github.com/acme/widgets/pull/4", "acme/widgets"),
    ("widgets", "https://gitea.example.com/acme/widgets/pulls/4", "acme/widgets"),
    ("widgets", "https://dev.azure.com/org/proj/_git/widgets/pullrequest/4", "proj/widgets"),
    ("widgets", "", ""),
])
def test_pr_repo_recovers_the_slug_from_the_url(repo, url, expected):
    pr = tracking.PRRecord(state="open", repo=repo, number=4, url=url, branch="b")
    assert claims_find_cli._pr_repo(pr) == expected


def test_claims_find_pr_needs_no_project_context():
    from agent_worktrees import front_door_cli

    assert front_door_cli._is_no_project_invocation(["claims", "find", "pr", "--json"])
    assert not front_door_cli._is_no_project_invocation(["claims", "owner", "x"])


@pytest.mark.parametrize("endpoint, expected", [
    ("github.com", "github.com"),
    ("https://GHES.Example.com:443/", "ghes.example.com"),
    ("https://user:pw@dev.azure.com/OrgA/", "dev.azure.com/OrgA"),
    ("http://gitea.example.com:8080/api/v1/", "gitea.example.com:8080/api/v1"),
    ("", None),
])
def test_canonical_authority(endpoint, expected):
    assert claims_find_cli.canonical_authority(endpoint) == expected


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
