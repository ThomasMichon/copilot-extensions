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
from dropin_registry import ScanAuthority

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
    real_list_repos = repos.list_repos

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
    monkeypatch.setattr(repos, "list_repos", lambda **kwargs: [])
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

    return SimpleNamespace(
        read=read,
        path=path,
        record=record,
        loads=config_calls,
        real_list_repos=real_list_repos,
    )


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


_OPENNESS_KEYS = {"pr_state", "state", "open", "merged", "mergeable", "provider_state"}


@pytest.mark.parametrize("state", ["creating", "open", "active", "merged", "closed", "abandoned"])
@pytest.mark.parametrize("claimed", [True, False])
def test_recorded_lifecycle_and_claims_cannot_qualify_provider_openness(source, state, claimed):
    resources = [{"kind": "pr", "id": "example/repository#7", "status": "active"}]
    bundle = {
        "project": "sample",
        "worktree_id": "sample-work",
        "as_of": 1.0,
        "facts": {
            "claims": {"value": {"resources": resources if claimed else []}, "confirmed": True}
        },
    }
    value = source.read([_pr(state=state)], bundle=bundle)
    assert value["available"] and value["complete"]
    (entry,) = value["associations"]
    assert entry["recorded_state"] == state
    assert entry["binding"]["available"]
    assert not _OPENNESS_KEYS & set(entry) and not _OPENNESS_KEYS & set(entry["binding"])


def test_claim_without_ledger_entry_is_not_an_association(source):
    bundle = {
        "project": "sample",
        "worktree_id": "sample-work",
        "as_of": 1.0,
        "facts": {
            "claims": {
                "value": {"resources": [{"kind": "pr", "id": "example/repository#7"}]},
                "confirmed": True,
            }
        },
    }
    value = source.read([], bundle=bundle)
    assert value["available"] and value["complete"] and value["associations"] == []
    assert source.loads == []


def test_unrecognized_recorded_lifecycle_is_incomplete_not_open(source):
    value = source.read([_pr(state="draft")])
    (entry,) = value["associations"]
    assert entry["recorded_state"] == "draft"
    assert entry["binding"] == {"available": False, "reason": "incomplete-association"}


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
        lambda **kwargs: [
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
        lambda **kwargs: [
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
        PRSnapshot(pr_state="open", merged=False, head_sha="a" * 40, endpoint="observed.example"),
        "example/repository",
        7,
        "github",
        "",
    )
    assert value["endpoint"] == "observed.example" and value["api_base"] == ""
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


@pytest.mark.parametrize("duplicate", ["ledger", "association"])
def test_duplicate_yaml_keys_are_unavailable_not_complete_empty(source, duplicate):
    raw = yaml.safe_dump({**asdict(source.record), "prs": [_pr()]})
    if duplicate == "ledger":
        raw += "prs: []\n"
    else:
        assert "  provider: github\n" in raw
        raw = raw.replace("  provider: github\n", "  provider: github\n  provider: gitea\n")
    source.path.write_text(raw)
    before = source.path.read_bytes()
    result = owner.extend_bundle(
        {"project": "sample", "worktree_id": "sample-work"},
        "sample",
        source.path,
        source.record,
    )["facts"]["pr_associations"]["value"]
    assert not result["available"] and not result["complete"]
    assert source.path.read_bytes() == before and source.loads == []


@pytest.mark.parametrize(
    "raw",
    [
        "repos: [\n",
        "repos:\n  target:\n    remote: https://github.com/other/repository.git\n    remote: https://github.com/example/repository.git\n",
        "repos:\n  target:\n    remote: https://github.com/other/repository.git\nrepos: {}\n",
    ],
)
def test_file_backed_registry_failure_cannot_fall_through_to_related_binding(
    source, monkeypatch, raw
):
    registry = source.path.parent / "repos.yaml"
    registry.write_text(raw)
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: registry)
    monkeypatch.setattr(repos, "list_repos", source.real_list_repos)
    entry = source.read([_pr()])["associations"][0]
    assert entry["binding"] == {"available": False, "reason": "target-binding-unavailable"}
    assert registry.read_text() == raw


def test_actual_indented_stdout_budget_includes_core_formatting(source, capfd):
    from agent_worktrees import output

    source.path.write_text(yaml.safe_dump({**asdict(source.record), "prs": []}))
    core = {
        "project": "sample",
        "worktree_id": "sample-work",
        "facts": {"claims": {"value": {"resources": ["x"] * 70000}, "confirmed": True}},
    }
    before = copy.deepcopy(core)
    assert len(json.dumps(core).encode()) < owner.MAX_BYTES
    assert len(output.json_output_text(core).encode()) > owner.MAX_BYTES
    result = owner.extend_bundle(core, "sample", source.path, source.record)
    output._json_output(result)
    emitted = capfd.readouterr().out
    assert len(emitted.encode("utf-8")) <= owner.MAX_BYTES
    assert json.loads(emitted)["availability"]["reason"] == "association-byte-budget"
    assert core == before


def test_identity_metadata_overflow_is_bounded_without_truncating_subject(source, capfd):
    from agent_worktrees import output

    source.record.creation_nonce = "\u00e9" * 200000
    raw = yaml.safe_dump({**asdict(source.record), "prs": []}, allow_unicode=True)
    assert len(raw.encode("utf-8")) < owner.MAX_BYTES
    source.path.write_text(raw)
    result = owner.extend_bundle(
        {"project": "sample", "worktree_id": "sample-work"},
        "sample",
        source.path,
        source.record,
    )
    output._json_output(result)
    emitted = capfd.readouterr().out
    assert len(emitted.encode("utf-8")) <= owner.MAX_BYTES
    assert json.loads(emitted)["availability"]["reason"] == "association-metadata-overflow"
    assert "creation_nonce" not in emitted and "worktree_id" not in emitted
    assert source.path.read_text() == raw


@pytest.mark.parametrize("selected_credentials", [False, True])
def test_snapshot_endpoint_is_captured_by_actual_fetch_not_formatting_environment(
    monkeypatch,
    selected_credentials,
):
    from agent_worktrees import pr_watch
    from agent_worktrees.providers import github
    from test_pr_observation_credentials import Source, github_transport

    monkeypatch.setenv("GH_HOST", "ambient.example")
    if selected_credentials:
        calls = github_transport(monkeypatch)
    else:
        import subprocess

        calls = []

        def run(argv, *, env):
            calls.append((argv, env))
            value = (
                {"state": "open", "head": {}, "base": {}} if argv[-1].endswith("/pulls/7") else []
            )
            return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

        monkeypatch.setattr(github, "run_cli", run)
    kwargs = (
        {"credential_source": Source()} if selected_credentials else {"token": "synthetic-control"}
    )
    snapshot = pr_watch.build_fetch(
        config.PRConfig(provider="github"),
        "owner/repository",
        7,
        **kwargs,
    )()
    monkeypatch.setenv("GH_HOST", "changed.example")
    wire = pr_snapshot_payload.snapshot_payload(snapshot, "owner/repository", 7, "github", "")
    expected = "github.com" if selected_credentials else "ambient.example"
    assert wire["endpoint"] == expected
    assert all(argv[argv.index("--hostname") + 1] == expected for argv, env in calls)


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
@pytest.mark.parametrize("padding", [" ", "\t", "\n"])
def test_whitespace_binding_matches_actual_provider_fetch(
    source, monkeypatch, provider, endpoint, url, padding
):
    import subprocess
    from agent_worktrees import pr_watch
    from agent_worktrees.providers import azure_devops, gitea

    padded = padding + endpoint + padding
    monkeypatch.setattr(
        config,
        "_control_plane_related_pr_map",
        lambda: {
            "target": {
                "provider": provider,
                "repository": "example/repository",
                "api_base": padded,
            },
        },
    )
    binding = source.read([_pr(provider=provider, url=url)])["associations"][0]["binding"]
    assert (
        binding["available"] and binding["endpoint"] == endpoint and binding["api_base"] == padded
    )
    prcfg = config._parse_pr(
        {
            "provider": provider,
            "api_base": padded,
            "observation": {"enabled": False},
        }
    )
    if provider == "gitea":

        def curl(self, method, target, token, **kwargs):
            value = (
                {"state": "open", "head": {}, "base": {}} if target.endswith("/pulls/7") else []
            )
            return 200, json.dumps(value)

        monkeypatch.setattr(gitea.GiteaProvider, "_curl", curl)
    else:
        monkeypatch.setattr(
            azure_devops,
            "run_cli",
            lambda argv, **kwargs: subprocess.CompletedProcess(
                argv,
                0,
                json.dumps({"status": "active", "lastMergeSourceCommit": {}}),
                "",
            ),
        )
    snapshot = pr_watch.build_fetch(prcfg, "example/repository", 7, token="synthetic-control")()
    wire = pr_snapshot_payload.snapshot_payload(
        snapshot, "example/repository", 7, provider, padded
    )
    assert wire["endpoint"] == binding["endpoint"]


@pytest.mark.parametrize(
    "provider,endpoint,url",
    [
        (
            "gitea",
            "https://forge.example/CaseRoot",
            "https://forge.example/caseroot/example/repository/pulls/7",
        ),
        (
            "azure-devops",
            "https://forge.example/CaseRoot",
            "https://forge.example/caseroot/example/_git/repository/pullrequest/7",
        ),
    ],
)
def test_deployment_prefix_case_is_not_repository_case(
    source, monkeypatch, provider, endpoint, url
):
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
    entry = source.read([_pr(provider=provider, url=url)])["associations"][0]
    assert entry["binding"]["reason"] == "url-binding-mismatch"
    same_prefix = url.replace("/caseroot/", "/CaseRoot/")
    assert source.read([_pr(provider=provider, url=same_prefix)])["associations"][0]["binding"][
        "available"
    ]


@pytest.mark.parametrize(
    "path",
    [
        "Project%20Name/_git/Repo%20Name/pullrequest/7",
        "Project Name/_git/Repo Name/pullrequest/7",
    ],
)
def test_ado_components_support_encoded_and_legacy_names(source, monkeypatch, path):
    monkeypatch.setattr(
        config,
        "_control_plane_related_pr_map",
        lambda: {
            "target": {
                "provider": "azure-devops",
                "repository": "Project Name/Repo Name",
                "api_base": "https://dev.azure.com/example",
            },
        },
    )
    entry = source.read(
        [
            _pr(
                provider="azure-devops",
                repo="Project Name/Repo Name",
                url="https://dev.azure.com/example/" + path,
            )
        ]
    )["associations"][0]
    assert entry["binding"]["available"]


def test_encoded_separator_does_not_create_a_new_component(source, monkeypatch):
    monkeypatch.setattr(
        config,
        "_control_plane_related_pr_map",
        lambda: {
            "target": {
                "provider": "azure-devops",
                "repository": "Project Name/Repo Name",
                "api_base": "https://dev.azure.com/example",
            },
        },
    )
    entry = source.read(
        [
            _pr(
                provider="azure-devops",
                repo="Project Name/Repo Name",
                url="https://dev.azure.com/example/Project%2FName/_git/Repo%20Name/pullrequest/7",
            )
        ]
    )["associations"][0]
    assert entry["binding"]["reason"] == "url-binding-mismatch"


def test_malformed_initial_config_cannot_qualify_its_defaults(source, monkeypatch):
    from agent_worktrees import config_acquisition

    path = source.path.parent / "config.yaml"
    path.write_text("repos: [\n")

    def load(**kwargs):
        config_acquisition.yaml_value(path)
        return SimpleNamespace(repo_name="sample", default_repo=object(), repos={})

    monkeypatch.setattr(config, "load_config", load)
    entries = source.read([_pr(), _pr(pr_id="b" * 32)])["associations"]
    assert all(entry["binding"]["available"] is False for entry in entries)
    assert path.read_text() == "repos: [\n"


def test_malformed_foreign_config_remains_failed_after_cached_default_load(source, monkeypatch):
    from agent_worktrees import config_acquisition, config_cache

    path = source.path.parent / "foreign.yaml"
    path.write_text("pr: [\n")
    reads = []
    repo = config.RepoConfig(anchor="", worktree_root="", pr=config.PRConfig(provider="github"))

    def load(name):
        reads.append(name)
        config_acquisition.yaml_value(path)
        return SimpleNamespace(repos={name: repo})

    monkeypatch.setattr(
        config, "load_project_config", lambda name: config_cache.memoize_in_scope(load, name)
    )
    monkeypatch.setattr(
        repos,
        "list_repos",
        lambda **kwargs: [
            SimpleNamespace(name="foreign", remote="https://github.com/example/repository.git"),
        ],
    )
    entries = source.read([_pr(), _pr(pr_id="b" * 32)])["associations"]
    assert all(entry["binding"]["available"] is False for entry in entries)
    assert reads == ["foreign"] and path.read_text() == "pr: [\n"


def test_opaque_owner_provenance_is_not_a_configuration_value_failure(source, monkeypatch):
    from agent_worktrees import config_acquisition

    def load(**kwargs):
        config_acquisition.opaque_source("legacy-config-owner")
        return SimpleNamespace(repo_name="sample", default_repo=object(), repos={})

    monkeypatch.setattr(config, "load_config", load)
    assert source.read([_pr()])["associations"][0]["binding"]["available"]


def test_malformed_dropin_cannot_leave_a_fallback_binding_available(source, monkeypatch):
    from agent_worktrees import config_acquisition

    directory = source.path.parent / "config.d"
    directory.mkdir()
    path = directory / "override.yaml"
    path.write_text("repos: [\n")

    def load(**kwargs):
        config_acquisition.dropin_value(directory, project_name="sample")
        return SimpleNamespace(repo_name="sample", default_repo=object(), repos={})

    monkeypatch.setattr(config, "load_config", load)
    entry = source.read([_pr()])["associations"][0]
    assert entry["binding"]["available"] is False
    assert path.read_text() == "repos: [\n"


def test_indeterminate_dropin_registry_cannot_qualify_retained_values(source, monkeypatch):
    from pathlib import Path
    from agent_worktrees import config_acquisition

    directory = source.path.parent / "config.d"
    directory.mkdir()
    (directory / "valid.yaml").write_text("repos:\n  sample:\n    remote: origin\n")
    config_acquisition.dropin_value(directory, project_name="sample")
    original_iterdir = Path.iterdir

    def unreadable(path):
        if path == directory:
            raise OSError("fixture directory unavailable")
        return original_iterdir(path)

    def load(**kwargs):
        retained = config_acquisition.dropin_value(directory, project_name="sample")
        assert retained["repos"]["sample"]["remote"] == "origin"
        return SimpleNamespace(repo_name="sample", default_repo=object(), repos={})

    monkeypatch.setattr(Path, "iterdir", unreadable)
    monkeypatch.setattr(config, "load_config", load)
    assert source.read([_pr()])["associations"][0]["binding"]["available"] is False


def test_absent_optional_dropin_registry_keeps_a_known_binding(source, monkeypatch):
    from agent_worktrees import config_acquisition

    directory = source.path.parent / "absent-config.d"

    def load(**kwargs):
        assert config_acquisition.dropin_value(directory, project_name="sample") == {}
        return SimpleNamespace(repo_name="sample", default_repo=object(), repos={})

    monkeypatch.setattr(config, "load_config", load)
    assert source.read([_pr()])["associations"][0]["binding"]["available"]


@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize(
    ("body", "available"),
    [
        (b"pr: [\n", False),
        (b"marker: \xff\n", False),
        (b"marker: committed\n", True),
        (b"", True),
        (None, True),
    ],
)
def test_committed_value_failures_cannot_qualify_disk_fallback(
    source, monkeypatch, foreign, body, available
):
    from agent_worktrees import config_cache

    anchor = source.path.parent / "anchor"
    anchor.mkdir()
    disk = anchor / config.INREPO_CONFIG_FILENAME
    disk.write_text("marker: disk\n")
    reads = []
    values = []

    def git(*args, **kwargs):
        if args[0] == "symbolic-ref":
            return SimpleNamespace(returncode=0, stdout="refs/remotes/origin/main\n")
        assert args == ("cat-file", "--batch")
        packet = b""
        for spec in kwargs["input_data"].splitlines():
            if spec.endswith(b":" + config.INREPO_CONFIG_FILENAME.encode()) and body is not None:
                packet += b"a" * 40 + b" blob " + str(len(body)).encode() + b"\n" + body + b"\n"
            else:
                packet += spec + b" missing\n"
        return SimpleNamespace(returncode=0, stdout=packet)

    def load(name):
        reads.append(name)
        values.append(config._load_inrepo_config(str(anchor)))
        repo = config.RepoConfig(
            anchor="", worktree_root="", pr=config.PRConfig(provider="github")
        )
        return SimpleNamespace(repo_name=name, default_repo=repo, repos={name: repo})

    monkeypatch.setattr(git_ops, "git", git)
    if foreign:
        monkeypatch.setattr(config, "_control_plane_related_pr_map", lambda: {})
        monkeypatch.setattr(
            repos,
            "list_repos",
            lambda **kwargs: [
                SimpleNamespace(name="foreign", remote="https://github.com/example/repository.git")
            ],
        )
        monkeypatch.setattr(
            config, "load_project_config", lambda name: config_cache.memoize_in_scope(load, name)
        )
    else:
        monkeypatch.setattr(config, "load_config", lambda **kwargs: load("sample"))
    entries = source.read([_pr(), _pr(pr_id="b" * 32)])["associations"]
    assert all(entry["binding"]["available"] is available for entry in entries)
    assert reads == ["foreign" if foreign else "sample"]
    marker = (
        "committed"
        if body == b"marker: committed\n"
        else "\ufffd"
        if body == b"marker: \xff\n"
        else "disk"
    )
    assert values == [{"marker": marker}]
    assert disk.read_text() == "marker: disk\n"


def test_associations_share_one_strict_registry_read_across_targets(source, monkeypatch):
    reads = []

    def registry(**kwargs):
        reads.append(kwargs)
        return []

    monkeypatch.setattr(repos, "list_repos", registry)
    monkeypatch.setattr(
        config,
        "_control_plane_related_pr_map",
        lambda: {
            "first": {"provider": "github", "repository": "example/repository"},
            "second": {"provider": "github", "repository": "example/second"},
        },
    )
    entries = source.read(
        [
            _pr(),
            _pr(
                pr_id="b" * 32,
                repo="example/second",
                url="https://github.com/example/second/pull/7",
            ),
        ]
    )["associations"]
    assert all(entry["binding"]["available"] for entry in entries)
    assert reads == [{"strict": True}]


def test_suppressed_related_config_failure_cannot_qualify_active_defaults(source, monkeypatch):
    from agent_worktrees import related

    monkeypatch.setattr(config, "_control_plane_related_pr_map", config._CONTROL_PLANE_PR_OWNER)
    monkeypatch.setattr(related, "find_control_plane_anchor", lambda: None)
    monkeypatch.setattr(related, "installed_plugin_related_anchors", lambda: ["/example/overlay"])
    monkeypatch.setattr(
        related,
        "read_related_grafted",
        lambda *args: (_ for _ in ()).throw(OSError("fixture overlay unavailable")),
    )
    monkeypatch.setattr(
        repos,
        "list_repos",
        lambda **kwargs: [
            SimpleNamespace(name="sample", remote="https://github.com/example/repository.git")
        ],
    )
    entries = source.read([_pr(), _pr(pr_id="b" * 32)])["associations"]
    assert all(entry["binding"]["available"] is False for entry in entries)


@pytest.mark.parametrize(
    ("raw", "available"),
    [
        ("related: [\n", False),
        ("[]\n", False),
        ("related: []\n", False),
        ("related:\n  sample: invalid\n", False),
        ("related:\n  sample:\n    pr: invalid\n", False),
        ("related:\n  sample:\n    pr: []\n", False),
        ("related:\n  sample:\n    pr: {}\n", True),
        ("related:\n  sample:\n    pr: null\n", True),
        ("related:\n  sample:\n", True),
        ("", True),
        (None, True),
    ],
)
def test_native_related_file_failure_cannot_qualify_active_defaults(
    source, monkeypatch, raw, available
):
    from agent_worktrees import related

    anchor = source.path.parent / "plugin"
    directory = anchor / ".agent-worktrees"
    directory.mkdir(parents=True)
    (anchor / "plugin.json").write_text('{"name": "fixture"}')
    path = directory / "related.yaml"
    if raw is not None:
        path.write_text(raw)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(config, "_control_plane_related_pr_map", config._CONTROL_PLANE_PR_OWNER)
    monkeypatch.setattr(related, "find_control_plane_anchor", lambda: None)
    monkeypatch.setattr(
        related,
        "installed_plugin_related_anchors",
        lambda: [related._PluginContributionAnchor(str(anchor), "fixture")],
    )
    monkeypatch.setattr(
        repos,
        "list_repos",
        lambda **kwargs: [
            SimpleNamespace(name="sample", remote="https://github.com/example/repository.git")
        ],
    )
    entries = source.read([_pr(), _pr(pr_id="b" * 32)])["associations"]
    assert all(entry["binding"]["available"] is available for entry in entries)
    assert path.read_text() == raw if raw is not None else not path.exists()


@pytest.mark.parametrize(
    ("context", "available"),
    [
        (None, True),
        ('{"marketplaceId": "fixture"}', True),
        ("{", False),
        ("[]", False),
        ('{"other": 1}', False),
        ("missing-context.json", False),
    ],
)
def test_native_installation_context_failure_cannot_qualify_base_related(
    source, monkeypatch, context, available
):
    from agent_worktrees import related

    anchor = source.path.parent / "repository"
    directory = anchor / related.CANONICAL_RELATED_DIR
    directory.mkdir(parents=True)
    (directory / related.RELATED_FILENAME).write_text("related:\n  sample:\n")
    if context is None:
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    elif context.endswith(".json"):
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(source.path.parent / context))
    else:
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", context)
    monkeypatch.setattr(config, "_control_plane_related_pr_map", config._CONTROL_PLANE_PR_OWNER)
    monkeypatch.setattr(related, "find_control_plane_anchor", lambda: str(anchor))
    monkeypatch.setattr(related, "installed_plugin_related_anchors", lambda: [])
    monkeypatch.setattr(
        repos,
        "list_repos",
        lambda **kwargs: [
            SimpleNamespace(
                name="sample",
                remote="https://github.com/example/repository.git",
                local_path=lambda: None,
            )
        ],
    )
    entries = source.read([_pr(), _pr(pr_id="b" * 32)])["associations"]
    assert all(entry["binding"]["available"] is available for entry in entries), [
        entry["binding"] for entry in entries
    ]


@pytest.mark.parametrize(
    ("activation", "available"),
    [("empty", True), ("indeterminate", False), ("entry", False), ("malformed", False)],
)
def test_native_activation_failure_cannot_qualify_base_related(
    source, monkeypatch, activation, available
):
    from agent_worktrees import related
    from dropin_registry import EntryDecision, Finding
    from plugin_activation import ActivationReport

    anchor = source.path.parent / "repository"
    directory = anchor / related.CANONICAL_RELATED_DIR
    directory.mkdir(parents=True)
    (directory / related.RELATED_FILENAME).write_text("related:\n  sample:\n")

    def resolve_active_plugins(**kwargs):
        if activation == "malformed":
            raise ValueError("malformed activation settings")
        if activation == "entry":
            finding = Finding(
                registry="plugin-activation", entry="uncertain@example-market",
                status="indeterminate", reason="unreadable-source", detail="fixture",
            )
            return ActivationReport(
                authority=ScanAuthority.COMPLETE,
                decisions={finding.entry: EntryDecision.indeterminate(finding)},
            )
        authority = (
            ScanAuthority.INDETERMINATE if activation == "indeterminate" else ScanAuthority.COMPLETE
        )
        return SimpleNamespace(
            authority=authority, findings=(), plugin_names=None, decisions={}, active={}
        )

    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv(related.INSTALLED_PLUGINS_ENV, raising=False)
    monkeypatch.setattr(config, "_control_plane_related_pr_map", config._CONTROL_PLANE_PR_OWNER)
    monkeypatch.setattr(related, "find_control_plane_anchor", lambda: str(anchor))
    monkeypatch.setattr(related, "resolve_active_plugins", resolve_active_plugins)
    monkeypatch.setattr(
        repos,
        "list_repos",
        lambda **kwargs: [
            SimpleNamespace(
                name="sample",
                remote="https://github.com/example/repository.git",
                local_path=lambda: None,
            )
        ],
    )
    entries = source.read([_pr(), _pr(pr_id="b" * 32)])["associations"]
    assert all(entry["binding"]["available"] is available for entry in entries), [
        entry["binding"] for entry in entries
    ]


@pytest.mark.parametrize(
    ("reason", "available"),
    [
        ("unclassified-acquisition-failure", False),
        ("owner-coverage-unavailable", True),
        ("presence-provenance-unavailable", True),
    ],
)
def test_unclassified_acquisition_failure_fails_closed(source, monkeypatch, reason, available):
    from agent_worktrees import config_acquisition, pr_config

    original = pr_config.resolve_repo_config_for_slug

    def resolve(*args, **kwargs):
        config_acquisition.failed(
            config_acquisition.current_observations(), "fixture", "fixture-stage", reason, "fixture"
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", resolve)
    monkeypatch.setattr(
        repos,
        "list_repos",
        lambda **kwargs: [
            SimpleNamespace(name="sample", remote="https://github.com/example/repository.git")
        ],
    )
    entries = source.read([_pr(), _pr(pr_id="b" * 32)])["associations"]
    assert all(entry["binding"]["available"] is available for entry in entries)


@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("failure", ["head", "batch", "absent"])
def test_committed_transport_failure_cannot_qualify_disk_fallback(
    source, monkeypatch, foreign, failure
):
    from agent_worktrees import config_cache

    anchor = source.path.parent / "anchor"
    anchor.mkdir()
    disk = anchor / config.INREPO_CONFIG_FILENAME
    disk.write_text("marker: disk\n")
    values = []
    calls = []

    def git(*args, **kwargs):
        calls.append(args[0])
        if args[0] == "symbolic-ref":
            return SimpleNamespace(
                returncode=1 if failure == "absent" else 128 if failure == "head" else 0,
                stdout="refs/remotes/origin/main\n",
            )
        if args == ("cat-file", "--batch"):
            return SimpleNamespace(returncode=128, stdout=b"")
        assert args[0] == "--literal-pathspecs"
        # A successful compatibility fallback does not repair the failed batch.
        return SimpleNamespace(returncode=0, stdout="")

    def load(name):
        values.append(config._load_inrepo_config(str(anchor)))
        repo = config.RepoConfig(
            anchor="", worktree_root="", pr=config.PRConfig(provider="github")
        )
        return SimpleNamespace(repo_name=name, default_repo=repo, repos={name: repo})

    monkeypatch.setattr(git_ops, "git", git)
    if foreign:
        monkeypatch.setattr(config, "_control_plane_related_pr_map", lambda: {})
        monkeypatch.setattr(
            repos,
            "list_repos",
            lambda **kwargs: [
                SimpleNamespace(name="foreign", remote="https://github.com/example/repository.git")
            ],
        )
        monkeypatch.setattr(
            config, "load_project_config", lambda name: config_cache.memoize_in_scope(load, name)
        )
    else:
        monkeypatch.setattr(config, "load_config", lambda **kwargs: load("sample"))
    entries = source.read([_pr(), _pr(pr_id="b" * 32)])["associations"]
    assert all(entry["binding"]["available"] is (failure == "absent") for entry in entries)
    assert values == [{"marker": "disk"}] and disk.read_text() == "marker: disk\n"
    assert calls == (
        ["symbolic-ref", "cat-file", "--literal-pathspecs"]
        if failure == "batch"
        else ["symbolic-ref"]
    )
