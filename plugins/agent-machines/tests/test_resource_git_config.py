"""Declared Git keys use one union-wide, consent-bound file transaction."""

from __future__ import annotations

import json

import pytest

from agent_machines.manifest import ManifestError, load_package
from agent_machines.reconcile import RestoreValidationError, plan, restore, restore_result_to_dict
from agent_machines.resources import resolve_resources

from ._helpers import base_package, write_package

KEY = "credential.https://github.example.com.helper"


@pytest.fixture(autouse=True)
def isolated_global_selection(monkeypatch):
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)


def package(tmp_path, *, name="example/git-policy", authority=0, entries=None, **overrides):
    data = base_package(
        name=name,
        schema_version=4,
        authority=authority,
        manage={},
        resources=[
            {
                "type": "git-config",
                "entries": [{"key": KEY, "values": ["", "!example-helper"]}]
                if entries is None
                else entries,
                **overrides,
            }
        ],
    )
    return load_package(write_package(tmp_path, name.replace("/", "-") + ".yaml", data))


def test_restore_requires_the_exact_preview_and_returns_a_rollback_receipt(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    target = home / ".gitconfig"
    original = b"# retained\r\n[user]\r\n\tname = Example\r\n"
    target.write_bytes(original)
    pkg = package(tmp_path)
    preview = restore([pkg], "box-1", home=home, only=["git-config"])
    item = restore_result_to_dict(preview)["resources"][0]
    assert item["status"] == "changed" and item["dry_run"]
    assert item["change_plan"]["schema"] == "agent-machines.git-config-plan/v1"
    assert target.read_bytes() == original

    refused = restore([pkg], "box-1", home=home, only=["git-config"], dry_run=False)
    assert not refused.ok and refused.resource_results[0].status == "blocked"
    assert target.read_bytes() == original
    applied = restore(
        [pkg],
        "box-1",
        home=home,
        only=["git-config"],
        dry_run=False,
        git_config_plan_sha256=item["change_plan"]["plan_sha256"],
    )
    assert applied.ok and applied.resource_results[0].changed
    assert applied.resource_results[0].backup_path
    assert not restore([pkg], "box-1", home=home, only=["git-config"]).resource_results[0].changed


def test_authority_resolves_each_key_without_dropping_unrelated_declarations(tmp_path):
    lower = package(
        tmp_path,
        name="lower",
        entries=[
            {"key": KEY, "values": ["", "!old-helper"]},
            {"key": "fetch.prune", "values": ["true"]},
        ],
    )
    higher = package(tmp_path, name="higher", authority=1)
    resolved, findings = resolve_resources([lower, higher], "box-1", "windows")
    assert not [finding for finding in findings if finding.level == "error"]
    assert len(resolved) == 1
    assert resolved[0].desired["entries"] == {
        KEY: ["", "!example-helper"],
        "fetch.prune": ["true"],
    }
    assert len(resolved[0].authority_decisions) == 1
    assert plan([lower, higher], "box-1").resources[0]["summary"] == "consent-required"


@pytest.mark.parametrize(
    "overrides",
    [
        {"scope": "system"},
        {"path": "/etc/gitconfig"},
        {"maintenance_safe": True},
        {"entries": [{"key": KEY, "values": []}]},
        {"entries": [{"key": KEY, "state": "absent", "values": ["ignored"]}]},
        {"entries": [{"key": "http.extraHeader", "values": ["sensitive"]}]},
    ],
)
def test_unsupported_scope_and_unsafe_declarations_fail_at_load(tmp_path, overrides):
    with pytest.raises(ManifestError):
        package(tmp_path, **overrides)


def test_equal_authority_conflict_is_visible_and_restore_is_refused(tmp_path):
    first = package(tmp_path, name="first")
    second = package(tmp_path, name="second", entries=[{"key": KEY, "state": "absent"}])
    _, findings = resolve_resources([first, second], "box-1", "windows")
    assert any(
        finding.level == "error" and finding.code == "resource-conflict" for finding in findings
    )
    with pytest.raises(RestoreValidationError, match="conflict"):
        restore([first, second], "box-1", home=tmp_path, only=["git-config"])


def test_absent_consent_is_not_supplied_by_maintenance_restore(tmp_path):
    pkg = package(tmp_path)
    result = restore(
        [pkg],
        "box-1",
        home=tmp_path,
        only=["git-config"],
        dry_run=False,
        maintenance_safe=True,
    )
    assert not result.resource_results[0].changed
    assert not (tmp_path / ".gitconfig").exists()


def test_restore_parser_passes_consent_and_rollback_uses_the_same_transaction(
    tmp_path, monkeypatch, capsys
):
    from agent_machines import __main__ as cli

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda _cls: home))
    pkg = package(tmp_path)
    monkeypatch.setattr(cli, "_collect_reconcile_packages", lambda *_args: ([pkg], "fixture"))
    argv = ["restore", "--machine", "box-1", "--only", "git-config", "--json"]
    assert cli.main(argv) == 0
    preview = json.loads(capsys.readouterr().out)["resources"][0]["change_plan"]
    assert cli.main([*argv, "--apply", "--git-config-plan-sha", preview["plan_sha256"]]) == 0
    applied = json.loads(capsys.readouterr().out)["resources"][0]
    undo = ["git-config", "rollback", "--receipt", applied["backup_path"], "--json"]
    assert cli.main(undo) == 0
    rollback = json.loads(capsys.readouterr().out)["plan"]
    assert cli.main([*undo, "--apply", "--plan-sha", rollback["plan_sha256"]]) == 0
    assert json.loads(capsys.readouterr().out)["changed"]
    assert not (home / ".gitconfig").exists()


@pytest.mark.parametrize("path", ["$HOME/.gitconfig", "$HOME/./.gitconfig", "absolute"])
def test_file_owners_cannot_compete_with_the_git_transaction(tmp_path, path):
    from agent_machines import resources as resources

    git = package(tmp_path)
    target = tmp_path / ".gitconfig"
    spec = str(target) if path == "absolute" else path
    data = base_package(
        name="other/file",
        manage={},
        resources=[{"type": "file", "path": spec, "content": "would replace"}],
    )
    other = load_package(write_package(tmp_path, "other.yaml", data))
    if path == "$HOME/.gitconfig":
        assert any(
            finding.level == "error"
            for finding in resolve_resources([git, other], "box-1", "windows")[1]
        )
    ctx = resources.ResourceContext(tmp_path, {}, "windows")
    results = resources.apply_resources([git, other], "box-1", "windows", ctx, dry_run=False)
    assert len(results) == 2 and all(item.status == "blocked" for item in results)
    assert not target.exists()


def test_new_parser_rejects_unknown_flags_before_rollback(monkeypatch):
    from agent_machines import __main__ as cli

    calls = []
    monkeypatch.setattr(
        cli.cli_git_config.transaction, "rollback_preview", lambda *_args: calls.append(True)
    )
    with pytest.raises(SystemExit) as error:
        cli.main(["git-config", "rollback", "--receipt", "unused", "--unknown-option"])
    assert error.value.code == 2 and calls == []


def test_ambient_global_selection_cannot_be_mistaken_for_convergence(tmp_path, monkeypatch):
    from agent_machines import resource_git_config

    calls = []
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "different.gitconfig"))
    monkeypatch.setattr(
        resource_git_config.transaction, "preview", lambda *_args: calls.append(True)
    )
    result = restore([package(tmp_path)], "box-1", home=tmp_path, only=["git-config"])
    assert not result.ok and result.resource_results[0].status == "blocked"
    assert not result.resource_results[0].changed and calls == []
    assert not (tmp_path / ".gitconfig").exists()
