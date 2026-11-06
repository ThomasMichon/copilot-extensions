"""Canonical PR association reads do not observe or mutate a forge."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import asdict
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import companion_pr_associations as owner
from agent_worktrees import config, git_ops, pr_related_binding, repos, tracking
from agent_worktrees import pr_snapshot_payload, session_tracking_cli, worktree_status_daemon
from agent_worktrees.pr_snapshot import PRSnapshot


def _record():
    return tracking.WorktreeRecord(
        worktree_id="sample-work",
        branch="worktree/sample-work",
        worktree_path="/example/work",
        repo="sample",
        machine="host",
        platform="linux",
        started_at="2026-01-01",
        last_resumed_at="2026-01-01",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        creation_nonce="incarnation-one",
    )


def _pr(**changes):
    return {
        "pr_id": "a" * 32,
        "pr_revision": 1,
        "provider": "github",
        "repo": "example/repository",
        "number": 7,
        "url": "https://github.com/example/repository/pull/7",
        "state": "open",
        **changes,
    }


@pytest.fixture
def source(monkeypatch, tmp_path):
    record = _record()
    path = tmp_path / "sample-work.yaml"
    config_calls = []

    def load(**kwargs):
        config_calls.append(kwargs)
        pr_related_binding.control_plane_pr_map()
        repo = config.RepoConfig(
            anchor="",
            worktree_root="",
            pr=config.PRConfig(provider="github"),
        )
        return SimpleNamespace(repo_name="sample", default_repo=repo, repos={})

    monkeypatch.setattr(config, "load_config", load)
    monkeypatch.setattr(config, "project_dir", lambda project: tmp_path)
    monkeypatch.setattr(
        config,
        "_control_plane_related_pr_map",
        lambda: {
            "target": {"provider": "github", "repository": "example/repository"},
        },
    )
    monkeypatch.setattr(repos, "list_repos", lambda: [])
    monkeypatch.setenv("GH_HOST", "github.com")
    monkeypatch.setattr(git_ops, "git", lambda *a, **k: pytest.fail("Unexpected Git observation"))
    from agent_worktrees import providers

    monkeypatch.setattr(
        providers, "account_token_for_slug", lambda *a: pytest.fail("Credential acquisition")
    )
    monkeypatch.setattr(tracking, "save_record", lambda *a, **k: pytest.fail("Canonical write"))

    def read(entries, *, selected=None, extra=None, bundle=None):
        data = asdict(record)
        data["prs"] = entries
        data.update(extra or {})
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        before = path.read_bytes()
        original = bundle or {
            "project": "sample",
            "worktree_id": "sample-work",
            "as_of": 1.0,
            "facts": {"claims": {"value": {"resources": []}, "confirmed": True}},
        }
        frozen = copy.deepcopy(original)
        result = owner.extend_bundle(original, "sample", path, selected or record)
        assert original == frozen and path.read_bytes() == before
        if result.get("availability", {}).get("reason") != "association-byte-budget":
            assert (
                result["as_of"] == 1.0 and result["facts"]["claims"] == frozen["facts"]["claims"]
            )
        else:
            assert len(json.dumps(result).encode("ascii")) <= owner.MAX_BYTES
        return result["facts"]["pr_associations"]["value"]

    return SimpleNamespace(read=read, path=path, record=record, loads=config_calls)


def test_canonical_list_is_not_claim_state_and_empty_needs_no_configuration(source):
    value = source.read([])
    assert value["available"] and value["complete"] and value["associations"] == []
    assert source.loads == []
    value = source.read([_pr(state="merged")])
    entry = value["associations"][0]
    assert entry["recorded_state"] == "merged"
    assert "pr_state" not in entry and "merged" not in entry
    assert entry["binding"] == {
        "available": True,
        "provider": "github",
        "repo": "example/repository",
        "api_base": "",
        "endpoint": "github.com",
    }


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"pr_id": "", "pr_revision": 0}, "legacy-association-identity"),
        ({"number": None, "state": "creating"}, "incomplete-association"),
        ({"provider": ""}, "incomplete-association"),
        ({"repo": ""}, "incomplete-association"),
        ({"url": ""}, "incomplete-association"),
        ({"provider": "gitea"}, "provider-mismatch"),
        ({"repo": "other/repository"}, "target-binding-unavailable"),
        ({"repo": "not/a/repository"}, "target-binding-unavailable"),
        ({"url": "https://foreign.example/example/repository/pull/7"}, "url-binding-mismatch"),
        ({"url": "https://github.com/example/repository/pull/8"}, "url-binding-mismatch"),
    ],
)
def test_unavailable_binding_retains_exact_record_not_empty(source, changes, reason):
    value = source.read([_pr(**changes)])
    assert value["available"] and value["complete"]
    assert len(value["associations"]) == 1
    assert value["associations"][0]["binding"] == {"available": False, "reason": reason}


def test_bounded_projection_and_duplicate_outside_projection(source):
    entries = [
        _pr(
            pr_id=f"{index:032x}",
            number=index + 1,
            url=f"https://github.com/example/repository/pull/{index + 1}",
        )
        for index in range(25)
    ]
    entries[24]["pr_id"] = entries[0]["pr_id"]
    value = source.read(entries)
    assert value["available"] and not value["complete"] and value["omitted"] == 1
    assert len(value["associations"]) == 24
    assert value["associations"][0]["binding"]["reason"] == "duplicate-association-id"
    assert value["associations"][23]["binding"]["available"]
    assert len(source.loads) == 1


@pytest.mark.parametrize(
    "extra,reason",
    [
        ({"creation_nonce": ""}, "record-selection-changed"),
        ({"worktree_id": "different"}, "record-selection-changed"),
        ({"repo": "different"}, "record-selection-changed"),
        ({"worktree_path": "/other/work"}, "record-selection-changed"),
        ({"prs": {}}, "invalid-association-ledger"),
    ],
)
def test_wrong_owner_or_incarnation_and_malformed_ledger_are_unknown(source, extra, reason):
    value = source.read([], extra=extra)
    assert not value["available"] and not value["complete"]
    assert value["reason"] == reason
    assert source.loads == []


def test_legacy_record_is_not_backfilled(source):
    source.record.creation_nonce = ""
    value = source.read([_pr()])
    assert value["reason"] == "legacy-worktree-incarnation"
    assert source.loads == []


@pytest.mark.parametrize("changes", [{"number": True}, {"pr_revision": True}, {"url": 4}])
def test_malformed_typed_fields_are_not_normalized_to_success(source, changes):
    value = source.read([_pr(**changes)])
    assert not value["available"] and value["reason"] == "invalid-association-fields"


def test_configuration_failure_does_not_erase_canonical_association(source, monkeypatch):
    monkeypatch.setattr(
        config, "load_config", lambda **kwargs: (_ for _ in ()).throw(ValueError("private error"))
    )
    value = source.read([_pr()])
    assert value["available"] and len(value["associations"]) == 1
    assert value["associations"][0]["binding"]["reason"] == "target-binding-unavailable"
    assert "private error" not in json.dumps(value)


def test_ambiguous_registered_target_is_not_guessed(source, monkeypatch):
    monkeypatch.setattr(
        repos,
        "list_repos",
        lambda: [
            SimpleNamespace(name=name, remote="https://github.com/example/repository.git")
            for name in ("first", "second")
        ],
    )
    entry = source.read([_pr()])["associations"][0]
    assert entry["binding"] == {"available": False, "reason": "target-binding-unavailable"}


def test_ambiguous_related_target_is_not_guessed(source, monkeypatch):
    monkeypatch.setattr(
        config,
        "_control_plane_related_pr_map",
        lambda: {
            name: {"provider": "github", "repository": "example/repository"}
            for name in ("first", "second")
        },
    )
    entry = source.read([_pr()])["associations"][0]
    assert entry["binding"] == {"available": False, "reason": "target-binding-unavailable"}


def test_registered_remote_uses_own_binding_without_git(source, monkeypatch):
    monkeypatch.setattr(
        repos,
        "list_repos",
        lambda: [
            SimpleNamespace(name="sample", remote="https://github.com/example/repository.git"),
        ],
    )
    assert source.read([_pr()])["associations"][0]["binding"]["available"]


def test_replacement_during_configuration_is_rejected(source, monkeypatch):
    def load(**kwargs):
        data = yaml.safe_load(source.path.read_text())
        data["creation_nonce"] = "replacement"
        source.path.write_text(yaml.safe_dump(data))
        return SimpleNamespace(repo_name="sample", default_repo=object(), repos={})

    monkeypatch.setattr(config, "load_config", load)
    source.path.write_text(yaml.safe_dump({**asdict(source.record), "prs": [_pr()]}))
    result = owner.extend_bundle(
        {"project": "sample", "worktree_id": "sample-work"},
        "sample",
        source.path,
        source.record,
    )["facts"]["pr_associations"]["value"]
    assert not result["available"] and result["reason"] == "record-changed"


def test_shared_discovery_is_fresh_per_read(source, monkeypatch):
    calls = []
    endpoint = "first.example"

    def discover():
        calls.append(endpoint)
        return {
            "target": {
                "provider": "github",
                "repository": "example/repository",
                "api_base": endpoint,
            }
        }

    monkeypatch.setattr(config, "_control_plane_related_pr_map", discover)
    for endpoint in ("first.example", "second.example"):
        value = source.read(
            [
                _pr(url=f"https://{endpoint}/example/repository/pull/7"),
                _pr(pr_id="b" * 32, number=8, url=f"https://{endpoint}/example/repository/pull/8"),
            ]
        )
        assert {entry["binding"]["endpoint"] for entry in value["associations"]} == {endpoint}
    assert calls == ["first.example", "second.example"]


def test_reassignment_read_retains_stable_id_and_new_revision(source):
    first = source.read([_pr()])["associations"][0]
    second = source.read(
        [
            _pr(pr_revision=2, number=8, url="https://github.com/example/repository/pull/8"),
        ]
    )["associations"][0]
    assert first["pr_id"] == second["pr_id"]
    assert (first["pr_revision"], first["number"]) == (1, 7)
    assert (second["pr_revision"], second["number"]) == (2, 8)


def test_body_budget_does_not_expand_cached_fact_budget(source):
    original = {
        "project": "sample",
        "worktree_id": "sample-work",
        "as_of": 1.0,
        "facts": {"claims": {"value": {"resources": [""]}, "confirmed": True}},
    }
    overhead = len(json.dumps(original, ensure_ascii=True).encode("ascii"))
    original["facts"]["claims"]["value"]["resources"] = ["x" * (owner.MAX_BYTES - overhead - 100)]
    assert len(json.dumps(original).encode("ascii")) < owner.MAX_BYTES
    value = source.read([], bundle=original)
    assert not value["available"]
    assert value["reason"] == "association-byte-budget"


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:secret@forge.example",
        "https://forge.example?token=secret",
    ],
)
def test_credential_shaped_endpoint_is_not_exposed(source, monkeypatch, endpoint):
    monkeypatch.setattr(
        config,
        "_control_plane_related_pr_map",
        lambda: {
            "target": {
                "provider": "github",
                "repository": "example/repository",
                "api_base": endpoint,
            },
        },
    )
    value = source.read([_pr()])
    assert value["associations"][0]["binding"]["available"] is False
    assert "secret" not in json.dumps(value)


@pytest.mark.parametrize(
    "provider,endpoint,url",
    [
        ("gitea", "https://forge.example", "https://forge.example/example/repository/pulls/7"),
        (
            "azure-devops",
            "https://dev.azure.com/example",
            "https://dev.azure.com/example/example/_git/repository/pullrequest/7",
        ),
    ],
)
def test_non_github_endpoint_is_explicit_and_bound(source, monkeypatch, provider, endpoint, url):
    monkeypatch.setattr(
        config,
        "_control_plane_related_pr_map",
        lambda: {
            "target": {
                "provider": provider,
                "repository": "example/repository",
                "api_base": endpoint,
            },
        },
    )
    value = source.read([_pr(provider=provider, url=url)])
    assert value["associations"][0]["binding"]["available"]
    assert value["associations"][0]["binding"]["endpoint"] == endpoint


def test_snapshot_retains_effective_endpoint_and_separate_provider_state(monkeypatch):
    monkeypatch.setenv("GH_HOST", "enterprise.example")
    value = pr_snapshot_payload.snapshot_payload(
        PRSnapshot(pr_state="open", merged=False, head_sha="a" * 40),
        "example/repository",
        7,
        "github",
        "",
    )
    assert value["endpoint"] == "enterprise.example" and value["api_base"] == ""
    assert value["pr_state"] == "open" and value["merged"] is False


def test_default_status_advertises_without_discovery_or_additional_fact(
    monkeypatch, tmp_path, capfd
):
    record = _record()
    monkeypatch.setattr(
        session_tracking_cli, "_find_tracking_file", lambda wid: tmp_path / "sample-work.yaml"
    )
    monkeypatch.setattr(session_tracking_cli, "_project_for_tracking_file", lambda path: "sample")
    monkeypatch.setattr(tracking, "load_record", lambda path: record)
    monkeypatch.setattr(
        worktree_status_daemon,
        "status_with_boot",
        lambda **kwargs: {
            "project": "sample",
            "worktree_id": "sample-work",
            "facts": {"old": {"value": 3}},
        },
    )
    monkeypatch.setattr(
        owner, "extend_bundle", lambda *args: pytest.fail("Unrequested association read")
    )
    assert (
        session_tracking_cli.cmd_worktree_status_bundle(
            argparse.Namespace(worktree_id="sample-work", force_refresh=False),
        )
        == 0
    )
    value = json.loads(capfd.readouterr().out)
    assert value["facts"] == {"old": {"value": 3}}
    assert value["companion_pr_observations"]["option"] == "--pr-associations"


@pytest.mark.parametrize("optional", [False, True])
def test_actual_parser_process_preserves_old_form_and_admits_declared_optional_fact(
    tmp_path, optional
):
    from agent_procutil.owned_process import run_owned

    data = {**asdict(_record()), "prs": [_pr()]}
    path = tmp_path / "sample-work.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = path.read_bytes()
    script = """
import json, sys
from pathlib import Path
from agent_worktrees import __main__ as main, config, session_tracking_cli as cli, worktree_status_daemon
path = Path(sys.argv[1])
cli._find_tracking_file = lambda wid: path
cli._project_for_tracking_file = lambda path: "sample"
config.project_dir = lambda project: path.parent
config.load_config = lambda **kwargs: (_ for _ in ()).throw(ValueError("unavailable"))
worktree_status_daemon.status_with_boot = lambda **kwargs: {"project": "sample", "worktree_id": "sample-work", "facts": {}}
main.front_door_cli.resolve_invocation_project = lambda *args: True
raise SystemExit(main.main(sys.argv[2:]))
"""
    args = ["worktree-status-bundle", "--worktree", "sample-work", "--json"]
    if optional:
        args.append("--pr-associations")
    settled = []
    result = run_owned(
        [sys.executable, "-c", script, str(path), *args],
        timeout=30,
        on_settled=lambda: settled.append(True),
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["companion_pr_observations"]["version"] == 1
    assert ("pr_associations" in value["facts"]) is optional
    if optional:
        assert (
            value["facts"]["pr_associations"]["value"]["associations"][0]["repo"]
            == "example/repository"
        )
    assert path.read_bytes() == before and settled == [True]


def test_strict_current_parser_rejects_misspelled_optional_flag():
    parser = argparse.ArgumentParser()
    session_tracking_cli.add_parsers(parser.add_subparsers())
    with pytest.raises(SystemExit) as error:
        parser.parse_args(
            ["worktree-status-bundle", "--worktree", "sample-work", "--pr-associationz"]
        )
    assert error.value.code == 2


def test_frozen_prior_status_parser_process_has_no_optional_declaration():
    from agent_procutil.owned_process import run_owned

    # The prior status syntax subset; unrelated lifecycle commands are not simulated.
    script = """
import argparse, json
parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest="command")
status = sub.add_parser("worktree-status-bundle")
status.add_argument("--worktree", "--worktree-id", dest="worktree_id", required=True)
status.add_argument("--force-refresh", action="store_true")
status.add_argument("--json", action="store_true")
args = parser.parse_args()
print(json.dumps({"worktree_id": args.worktree_id, "facts": {}}))
"""
    args = ["worktree-status-bundle", "--worktree", "sample-work", "--json"]
    settled = []
    result = run_owned(
        [sys.executable, "-c", script, *args],
        timeout=30,
        on_settled=lambda: settled.append(True),
    )
    assert result.returncode == 0 and "companion_pr_observations" not in json.loads(result.stdout)
    rejected = run_owned(
        [sys.executable, "-c", script, *args, "--pr-associations"],
        timeout=30,
        on_settled=lambda: settled.append(True),
    )
    assert rejected.returncode == 2 and "unrecognized arguments" in rejected.stderr
    assert settled == [True, True]
