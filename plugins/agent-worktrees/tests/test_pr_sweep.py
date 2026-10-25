"""Original-root attribution, provider truth and one-shot CLI behavior."""

from __future__ import annotations

import json
import argparse
import subprocess
from types import SimpleNamespace as NS

import pytest

from agent_worktrees import claims_cli, config as cfg, obligations, pr_sweep, pr_sweep_cli, tracking
from agent_worktrees import pr_sweep_transport as transport

REPO = "example/widgets"
CELL = "worker-a/linux"


def config(tmp_path):
    return cfg.Config(
        srcroot=str(tmp_path), machine="worker-a", platform="linux", repo_name="host",
        repos={"host": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path / "worktrees"))},
    )


def claim(number=1, **changes):
    return {
        "project": "host", "worktree_id": "root", "machine": "worker-a", "environment": "linux",
        "authority": "github.com", "repo": REPO, "number": number,
        "url": f"https://github.com/{REPO}/pull/{number}", "path": ["root", "child"],
        "evidence": "unsettled-transitive-claim", "checked_utc": "2026-01-01T00:00:00Z",
        **changes,
    }


def provider(rows=None, **changes):
    rows = rows if rows is not None else [{
        "number": 1, "url": f"https://github.com/{REPO}/pull/1", "author": "author-a",
        "head_sha": "a" * 40, "created_at": "2026-01-01T00:00:00Z", "title": "First",
    }]
    return {"status": "ok", "complete": True, "authority": "github.com",
            "checked_utc": "2026-01-01T00:00:00Z", "prs": rows, **changes}


def cell(claims=None, errors=None):
    return {"cell": CELL, "status": "ok", "checked_utc": "2026-01-01T00:00:00Z",
            "observation": {"claims": claims or [], "errors": errors or []}}


def test_join_preserves_unclaimed_provider_pr_and_retained_nonopen_claim():
    result = pr_sweep.join_inventory(REPO, provider(), [cell([claim(2)])])
    assert result["summary"]["open_count"] == 1
    assert result["summary"]["unclaimed_open_count"] == 1
    assert result["prs"][0]["claimants"] == []
    assert result["associations"][0]["provider_state"] == "not-in-open-census"
    assert "cleared" not in json.dumps(result)


def test_provider_open_overrides_stale_closed_tracking_and_deduplicates():
    item = claim(recorded_state="closed")
    result = pr_sweep.join_inventory(REPO, provider(), [cell([item, item])])
    assert len(result["associations"]) == 1
    assert result["prs"][0]["claimants"][0]["path"] == ["root", "child"]
    assert result["associations"][0]["provider_state"] == "open"


def test_direct_tracking_candidate_is_not_promoted_to_held_claim():
    candidate = claim(evidence="tracked-candidate", recorded_state="closed", path=["root"])
    result = pr_sweep.join_inventory(REPO, provider(), [cell([candidate])])
    assert result["summary"]["unclaimed_open_count"] == 1
    assert result["prs"][0]["claimants"] == []
    assert result["prs"][0]["candidates"][0]["recorded_state"] == "closed"


def test_foreign_authority_cannot_claim_same_number():
    result = pr_sweep.join_inventory(REPO, provider(), [cell([claim(authority="foreign.example")])])
    assert result["prs"][0]["claimants"] == []
    assert result["associations"][0]["provider_state"] == "unknown"


@pytest.mark.parametrize("slug", [
    "example/pull", "pull/widgets", "pull/123", "pulls/pull-requests",
    "example/_git", "_git/widgets",
])
def test_keyword_named_repository_claims_keep_terminal_identity(slug):
    url = f"https://github.com/{slug}/pull/7"
    assert pr_sweep.pr_identity(url) == ("github.com", slug, 7)
    record = NS(repo="", url=url, number=None)
    assert pr_sweep.claims_find_cli._pr_repo(record) == slug
    assert pr_sweep.claims_find_cli._pr_number(record) == 7


def test_repository_keyword_under_provider_prefix_does_not_become_authority():
    assert pr_sweep.pr_identity("https://forge.example/prefix/example/pull/pulls/7") == (
        "forge.example/prefix", "example/pull", 7,
    )


def test_azure_pr_suffix_keeps_organization_authority_and_project_repository():
    assert pr_sweep.pr_identity("https://dev.azure.com/org/project/_git/widgets/pullrequest/7") == (
        "dev.azure.com/org", "project/widgets", 7,
    )


def test_provider_failure_is_unknown_not_zero_or_claim_closeout():
    result = pr_sweep.join_inventory(REPO, provider(complete=False, prs=None), [cell([claim()])])
    assert result["prs"] is None
    assert result["summary"]["open_count"] is None
    assert result["associations"][0]["provider_state"] == "unknown"
    assert not result["complete"]


def test_human_output_escapes_terminal_controls_without_changing_json(capsys):
    title = "Ordinary\x1b[2J\rspoof\nrow\u202ehidden"
    inventory = provider([{**provider()["prs"][0], "title": title}])
    result = pr_sweep.join_inventory(REPO, inventory, [cell()])
    pr_sweep_cli.emit_human(result)
    text = capsys.readouterr().out
    assert "\\x1b[2J\\rspoof\\nrow\\u202ehidden" in text
    assert "\x1b" not in text and "\r" not in text and "\u202e" not in text
    assert json.loads(json.dumps(result))["prs"][0]["title"] == title


def test_author_filter_keeps_all_author_census():
    first = provider()["prs"][0]
    second = {**first, "number": 2, "author": "author-b", "created_at": "2025-12-31T23:00:00Z"}
    result = pr_sweep.join_inventory(REPO, provider([first, second]), [cell()], "AUTHOR-A")
    assert len(result["prs"]) == 2
    assert result["summary"]["all_author_open_count"] == 2
    assert result["summary"]["open_count"] == 1
    assert result["summary"]["oldest"]["number"] == 1


def test_missing_author_preserves_total_but_cannot_prove_scoped_zero():
    unknown = {**provider()["prs"][0], "author": None}
    result = pr_sweep.join_inventory(REPO, provider([unknown]), [cell()], "author-a")
    assert result["summary"]["all_author_open_count"] == 1
    assert result["summary"]["open_count"] is None
    assert result["summary"]["known_matching_open_count"] == 0
    assert not result["summary"]["author_filter_complete"] and not result["complete"]


def test_unknown_author_prevents_claiming_a_filtered_oldest_pr(capsys):
    known = provider()["prs"][0]
    unknown = {**known, "number": 2, "author": None, "created_at": "2025-01-01T00:00:00Z"}
    result = pr_sweep.join_inventory(REPO, provider([known, unknown]), [cell()], "author-a")
    assert result["summary"]["oldest"] is None
    pr_sweep_cli.emit_human(result)
    assert "Oldest:" not in capsys.readouterr().out


@pytest.mark.parametrize("repo", ["widgets", "example/..", "example/widgets?x=1", "example\\widgets", "example/widgets\n"])
def test_invalid_repository_is_rejected_before_reading_config(monkeypatch, repo):
    monkeypatch.setattr(cfg, "load_config", lambda: pytest.fail("configuration read"))
    with pytest.raises(SystemExit) as error:
        pr_sweep_cli.run_pr_sweep([repo, "--json"])
    assert error.value.code == 2


def test_missing_cells_and_unresolved_edges_make_coverage_partial():
    assert not pr_sweep.join_inventory(REPO, provider(), [cell(errors=[{"reason": "cycle"}])])["complete"]
    assert not pr_sweep.join_inventory(REPO, provider(), [{"cell": CELL, "status": "excluded"}])["complete"]


@pytest.fixture
def local_records(tmp_path, monkeypatch):
    (tmp_path / "host" / "worktrees").mkdir(parents=True)
    (tmp_path / "source" / "worktrees").mkdir(parents=True)
    monkeypatch.setattr(pr_sweep.installer, "read_projects_registry",
                        lambda **_k: {"projects": {"host": {}, "source": {}}})
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / (name or "host"))
    monkeypatch.setattr(pr_sweep.machine_identity, "is_local_machine", lambda name, _config: name == "worker-a")
    monkeypatch.setattr(pr_sweep.claims_find_cli, "_authority_resolver",
                        lambda project: lambda slug, provider: "github.com")
    monkeypatch.setattr(cfg, "load_project_config", lambda *_a, **_k: config(tmp_path))

    def save(project, identity, resources=(), prs=()):
        directory = tmp_path / project / "worktrees"
        record = tracking.create_new_record(identity, f"worktree/{identity}", str(tmp_path / identity),
                                            project, "worker-a", "linux", directory)
        record.resources = list(resources)
        record.prs = list(prs)
        tracking.save_record(record, directory / f"{identity}.yaml")
        return record

    return save, config(tmp_path)


@pytest.mark.parametrize("root_machine", ["example-host", "example-host-wsl"])
@pytest.mark.parametrize("child_machine,reference_machine", [
    ("example-host-wsl", "example-host-wsl"),
    ("example-host", "example-host-wsl"),
    ("example-host-wsl", "native-profile"),
])
def test_historical_wsl_record_uses_receiving_project_locality(
    tmp_path, monkeypatch, root_machine, child_machine, reference_machine,
):
    anchor = tmp_path / "source"
    anchor.mkdir()
    (anchor / "machines.yaml").write_text(
        "machines:\n  example-host:\n    ssh:\n      environments:\n"
        "      - {name: windows, alias: native-profile}\n"
        "      - {name: wsl, alias: guest-profile}\n",
    )
    conf = cfg.Config(
        srcroot=str(tmp_path), machine="example-host-wsl", platform="wsl", repo_name="host",
        repos={"host": cfg.RepoConfig(anchor=str(anchor), worktree_root=str(tmp_path / "trees"))},
    )
    directory = tmp_path / "state" / "worktrees"
    directory.mkdir(parents=True)
    record = tracking.create_new_record(
        "legacy", "worktree/legacy", str(tmp_path / "trees" / "legacy"),
        "host", root_machine, "wsl", directory,
    )
    record.resources = [
        tracking.ResourceClaim(kind="pr", ref=f"{REPO}#1", state=obligations.ACTIVE),
        tracking.ResourceClaim(
            kind="worktree", ref=tracking.format_claim_ref(reference_machine, "host", "child"),
            state=obligations.ACTIVE,
        ),
    ]
    tracking.save_record(record, directory / "legacy.yaml")
    child = tracking.create_new_record(
        "child", "worktree/child", str(tmp_path / "trees" / "child"),
        "host", child_machine, "wsl", directory,
    )
    child.resources = [tracking.ResourceClaim(kind="pr", ref=f"{REPO}#2", state=obligations.ACTIVE)]
    tracking.save_record(child, directory / "child.yaml")
    monkeypatch.setattr(cfg, "project_dir", lambda *_a: directory.parent)
    monkeypatch.setattr(cfg, "load_project_config", lambda *_a, **_k: conf)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(pr_sweep.installer, "read_projects_registry", lambda **_k: {"projects": {"host": {}}})
    before = {path: path.read_bytes() for path in directory.glob("*.yaml")}
    result = pr_sweep.collect_local(REPO, conf)
    root_claims = [claim for claim in result["claims"] if claim["worktree_id"] == "legacy"]
    assert all(claim["machine"] == root_machine for claim in root_claims)
    if reference_machine == "native-profile":
        assert [claim["number"] for claim in root_claims] == [1]
        assert result["errors"]
    else:
        assert [claim["number"] for claim in root_claims] == [1, 2]
        assert root_claims[1]["path"] == ["legacy", "child"]
        assert not result["errors"]
    assert before == {path: path.read_bytes() for path in directory.glob("*.yaml")}


def test_local_walk_keeps_original_project_root_and_full_transitive_path(local_records, tmp_path):
    save, conf = local_records
    save("host", "root", [tracking.ResourceClaim(
        kind="worktree", ref=tracking.format_claim_ref("worker-a", "source", "child"),
        state=obligations.ACTIVE,
    )])
    save("source", "child", [tracking.ResourceClaim(
        kind="pr", ref=f"{REPO}#1", state=obligations.ACTIVE,
    )], [tracking.PRRecord(repo=REPO, number=1, state="closed",
                          url=f"https://github.com/{REPO}/pull/1")])
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.yaml")}
    result = pr_sweep.collect_local(REPO, conf)
    root_claim = next(r for r in result["claims"] if r["worktree_id"] == "root")
    assert root_claim["project"] == "host" and root_claim["path"] == ["root", "child"]
    assert any(r["evidence"] == "tracked-candidate" and r["recorded_state"] == "closed"
               for r in result["claims"])
    assert result["errors"] == []
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.yaml")}


def test_unreadable_record_does_not_hide_valid_peer(local_records, tmp_path):
    save, conf = local_records
    save("host", "valid")
    (tmp_path / "source" / "worktrees" / "broken.yaml").write_text("[broken: {")
    result = pr_sweep.collect_local(REPO, conf)
    assert any(root["worktree_id"] == "valid" for root in result["roots"])
    assert result["errors"]


def test_keyword_repository_held_claim_survives_collection(local_records):
    save, conf = local_records
    repo = "example/_git"
    url = f"https://github.com/{repo}/pull/7"
    save("host", "keyword-root", [tracking.ResourceClaim(
        kind="pr", ref=url, state=obligations.ACTIVE,
    )])
    result = pr_sweep.collect_local(repo, conf)
    assert not result["errors"]
    assert [(r["repo"], r["number"], r["authority"]) for r in result["claims"]] == [
        (repo, 7, "github.com"),
    ]


@pytest.mark.parametrize("repo", [REPO, ""])
def test_malformed_candidate_keeps_valid_same_root_and_peer_claims(local_records, repo):
    save, conf = local_records
    save("host", "mixed", prs=[
        tracking.PRRecord(repo=repo, number=None, url="https://["),
        tracking.PRRecord(repo=REPO, number=1, url=f"https://github.com/{REPO}/pull/1"),
    ])
    save("source", "peer", [tracking.ResourceClaim(
        kind="pr", ref=f"{REPO}#1", state=obligations.ACTIVE,
    )])
    result = pr_sweep.collect_local(REPO, conf)
    assert {r["worktree_id"] for r in result["claims"]} == {"mixed", "peer"}
    assert any(e["worktree_id"] == "mixed" and "could not be parsed" in e["error"]
               for e in result["errors"])
    joined = pr_sweep.join_inventory(REPO, provider(), [cell(result["claims"], result["errors"])])
    assert not joined["complete"]
    assert len(joined["prs"][0]["claimants"]) == 1
    assert len(joined["prs"][0]["candidates"]) == 1


@pytest.mark.parametrize("child", ["worker-a/source/missing", "worker-b/source/child", "worker-a/host/root"])
def test_missing_foreign_and_cyclic_subtrees_remain_unresolved(local_records, child):
    save, conf = local_records
    save("host", "root", [tracking.ResourceClaim(
        kind="worktree", ref=child, state=obligations.ACTIVE,
    )])
    result = pr_sweep.collect_local(REPO, conf)
    assert result["errors"]
    assert result["roots"][0]["unresolved"]


def test_transitive_child_in_another_environment_is_not_observed(local_records, tmp_path):
    save, conf = local_records
    save("host", "root", [tracking.ResourceClaim(
        kind="worktree", ref="worker-a/source/foreign", state=obligations.ACTIVE,
    )])
    foreign = save("source", "foreign", [tracking.ResourceClaim(
        kind="pr", ref=f"{REPO}#1", state=obligations.ACTIVE,
    )])
    foreign.platform = "wsl"
    tracking.save_record(foreign, tmp_path / "source" / "worktrees" / "foreign.yaml")
    result = pr_sweep.collect_local(REPO, conf)
    assert result["claims"] == []
    assert any("outside admitted snapshot" in error.get("reason", error.get("error", ""))
               for error in result["errors"])


def test_transitive_cache_miss_never_loads_foreign_machine_record(local_records, tmp_path):
    save, conf = local_records
    save("host", "root", [tracking.ResourceClaim(
        kind="worktree", ref="worker-a/source/foreign", state=obligations.ACTIVE,
    )])
    foreign = save("source", "foreign", [tracking.ResourceClaim(
        kind="pr", ref=f"{REPO}#1", state=obligations.ACTIVE,
    )])
    foreign.machine = "worker-b"
    tracking.save_record(foreign, tmp_path / "source" / "worktrees" / "foreign.yaml")
    result = pr_sweep.collect_local(REPO, conf)
    assert not result["claims"]
    assert any("outside admitted snapshot" in error.get("reason", "") for error in result["errors"])


def test_remote_observation_uses_its_own_registered_project(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cfg, "load_config", lambda: config(tmp_path))
    monkeypatch.setattr(pr_sweep, "provider_inventory", lambda *_a: provider())
    monkeypatch.setattr(transport, "targets", lambda *_a: [
        {"cell": "worker-b/linux", "local": False, "available": True},
    ])
    calls = []

    def read(cell, command, **kwargs):
        calls.append((cell, command, kwargs))
        return {"cell": cell["cell"], "status": "ok", "observation": {"claims": [], "errors": []}}

    monkeypatch.setattr(transport, "observe_remote", read)
    commands = tmp_path / "commands.json"
    commands.write_text(json.dumps({
        "worker-b/linux": {"command": "/catalog/agent-worktrees", "project": "different-remote-project"},
    }))
    assert pr_sweep_cli.run_pr_sweep([REPO, "--fleet", "--commands", str(commands), "--json"]) == 0
    assert calls[0][2]["project"] == "different-remote-project"
    assert calls[0][1] == "/catalog/agent-worktrees"
    assert json.loads(capsys.readouterr().out)["complete"]


def test_explicit_provider_token_still_passes_expected_account(monkeypatch, tmp_path):
    binding = NS(provider="github", api_base="", token_command="explicit", token_env="")
    monkeypatch.setattr(pr_sweep.pr_config, "resolve_repo_config_for_slug",
                        lambda *_a: NS(resolved=True, repo_config=NS(pr=binding)))
    monkeypatch.setattr(pr_sweep.repo_accounts, "account_for_remote", lambda *_a: "expected-account")
    monkeypatch.setattr(pr_sweep.providers, "resolve_token", lambda *_a: "configured-token")
    calls = []
    monkeypatch.setattr(pr_sweep.providers, "get_provider", lambda *_a: NS(
        name="github", authority_endpoint=lambda *_a: "github.com",
        list_open_pull_records=lambda *_a, **kwargs: calls.append(kwargs) or [],
    ))
    assert pr_sweep.provider_inventory(REPO, config(tmp_path))["complete"]
    assert calls[0]["expected_login"] == "expected-account"
    assert calls[0]["token"] == "configured-token"


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("actual", ["enterprise-reader", "another-account"])
def test_enterprise_inventory_uses_registered_host_account(monkeypatch, tmp_path, explicit, actual):
    from agent_worktrees import repos
    from agent_worktrees.providers import repository_inventory

    registry = repos.ReposRegistry(repos={
        "public": repos.RepoEntry("public", remote=f"https://github.com/{REPO}", account="public-reader"),
        "enterprise": repos.RepoEntry(
            "enterprise", remote=f"https://forge.example/{REPO}.git", account="enterprise-reader",
        ),
    })
    monkeypatch.setattr(repos, "read_registry", lambda **_k: registry)
    binding = NS(
        provider="github", api_base="https://forge.example/api/v3", token_command="",
        token_env="SWEEP_TEST_TOKEN" if explicit else "",
    )
    monkeypatch.setenv("SWEEP_TEST_TOKEN", "selected-repository-token")
    monkeypatch.setattr(pr_sweep.pr_config, "resolve_repo_config_for_slug",
                        lambda *_a: NS(resolved=True, repo_config=NS(pr=binding)))
    monkeypatch.setattr(pr_sweep.providers, "account_token_for_slug",
                        lambda *_a: pytest.fail("public-host token resolution"))
    calls = []

    def run(argv, *, env):
        calls.append(argv)
        assert argv[3] == "forge.example"
        assert argv[4].startswith("https://forge.example/api/v3/")
        if explicit:
            assert all(env[key] == "selected-repository-token" for key in (
                "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
            ))
        return subprocess.CompletedProcess(argv, 0, actual if argv[-1] == ".login" else "[]", "")

    monkeypatch.setattr(repository_inventory, "run_cli", run)
    if actual == "enterprise-reader":
        assert pr_sweep.provider_inventory(REPO, config(tmp_path))["complete"]
        assert len(calls) == 2
    else:
        with pytest.raises(pr_sweep.ProviderError, match="identity"):
            pr_sweep.provider_inventory(REPO, config(tmp_path))
        assert len(calls) == 1


def test_invalid_selector_executes_no_provider_or_cell(monkeypatch, tmp_path, capsys):
    conf = config(tmp_path)
    monkeypatch.setattr(cfg, "load_config", lambda: conf)
    monkeypatch.setattr(pr_sweep, "provider_inventory", lambda *_a: pytest.fail("provider executed"))
    monkeypatch.setattr(pr_sweep, "collect_local", lambda *_a: pytest.fail("cell executed"))
    assert pr_sweep_cli.run_pr_sweep([REPO, "--include", "typo/linux", "--json"]) == 2
    assert "Unconfigured" in json.loads(capsys.readouterr().out)["error"]


def test_exclusion_never_runs_cell_and_is_visible(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cfg, "load_config", lambda: config(tmp_path))
    monkeypatch.setattr(pr_sweep, "provider_inventory", lambda *_a: provider())
    monkeypatch.setattr(pr_sweep, "collect_local", lambda *_a: pytest.fail("excluded cell executed"))
    assert pr_sweep_cli.run_pr_sweep([REPO, "--exclude", CELL, "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["summary"]["open_count"] == 1 and result["cells"][0]["status"] == "excluded"


def test_collect_only_never_reads_provider(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cfg, "load_config", lambda: config(tmp_path))
    monkeypatch.setattr(pr_sweep, "provider_inventory", lambda *_a: pytest.fail("provider executed"))
    monkeypatch.setattr(pr_sweep, "collect_local", lambda *_a: {"claims": [], "errors": []})
    assert pr_sweep_cli.run_pr_sweep([REPO, "--collect-only", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"claims": [], "errors": []}


def test_provider_identity_failure_cannot_fall_back_to_ambient(monkeypatch, tmp_path):
    binding = NS(provider="github", api_base="", token_command="", token_env="")
    monkeypatch.setattr(pr_sweep.pr_config, "resolve_repo_config_for_slug",
                        lambda *_a: NS(resolved=True, repo_config=NS(pr=binding)))
    monkeypatch.setattr(pr_sweep.providers, "get_provider",
                        lambda *_a: NS(name="github", authority_endpoint=lambda *_a: "github.com",
                                       list_open_pull_records=lambda *_a, **_k: pytest.fail("read")))
    monkeypatch.setattr(pr_sweep.providers, "account_token_for_slug", lambda *_a: None)
    monkeypatch.setattr(pr_sweep.repo_accounts, "account_for_remote", lambda *_a: "expected-account")
    from agent_worktrees import git_ops
    monkeypatch.setattr(git_ops, "active_gh_account", lambda: "another-account")
    with pytest.raises(pr_sweep.ProviderError, match="credential is unavailable"):
        pr_sweep.provider_inventory(REPO, config(tmp_path))


def test_human_and_json_use_same_filtered_summary(capsys):
    result = pr_sweep.join_inventory(REPO, provider(), [cell([claim()])], "author-a")
    pr_sweep_cli.emit_human(result)
    text = capsys.readouterr().out
    assert f"{REPO}: 1 open PRs (complete" in text
    assert result["summary"]["oldest"]["url"] in text
    assert "host/root" in text and "root -> child" in text


def test_command_map_never_uses_path_or_unknown_cells():
    cells = [{"cell": CELL}]
    with pytest.raises(ValueError, match="absolute"):
        transport.validate_command_map({CELL: {"command": "agent-worktrees", "project": "host"}}, cells)
    with pytest.raises(ValueError, match="unconfigured"):
        transport.validate_command_map({"other/linux": "/commands/agent-worktrees"}, cells)


def test_duplicate_command_keys_refuse_before_provider_execution(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cfg, "load_config", lambda: config(tmp_path))
    monkeypatch.setattr(pr_sweep, "provider_inventory", lambda *_a: pytest.fail("provider executed"))
    path = tmp_path / "commands.json"
    path.write_text('{"worker-a/linux":"/owner/one","worker-a/linux":"/owner/two"}')
    assert pr_sweep_cli.run_pr_sweep([REPO, "--commands", str(path), "--json"]) == 2
    assert "Duplicate" in json.loads(capsys.readouterr().out)["error"]


def test_list_cells_is_only_configuration_inspection(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cfg, "load_config", lambda: config(tmp_path))
    monkeypatch.setattr(pr_sweep, "provider_inventory", lambda *_a: pytest.fail("provider executed"))
    monkeypatch.setattr(pr_sweep, "collect_local", lambda *_a: pytest.fail("cell executed"))
    assert pr_sweep_cli.run_pr_sweep([REPO, "--list-cells", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["operation"] == "list-cells"
    assert result["cells"][0]["cell"] == CELL


@pytest.mark.parametrize("target,apply", [(["add", "pr", "example/widgets#1"], False), (["find", "pr"], True)])
def test_claims_adapter_refuses_mutating_combinations_before_dispatch(monkeypatch, capfd, target, apply):
    monkeypatch.setattr(claims_cli, "_claims_add", lambda *_a: pytest.fail("mutation attempted"))
    args = argparse.Namespace(
        sweep_observation=True, target=target, json=True, claim_repo=REPO,
        claim_state="all", claim_live=False, apply=apply,
    )
    assert claims_cli.cmd_claims(args) == 2
    assert "read-only" in json.loads(capfd.readouterr().out)["error"]
