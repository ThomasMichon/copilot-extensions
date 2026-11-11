"""Portable account projection preserves explicit choices and credential boundaries."""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

from agent_procutil import no_window_kwargs
import pytest
import yaml

from agent_worktrees import account_reconciliation as recovery, config, config_sources, repos


@pytest.fixture
def local(tmp_path, monkeypatch):
    registry = tmp_path / "registry" / "repos.yaml"
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: registry)
    state = repos.ReposRegistry(repos={
        "harness": repos.RepoEntry("harness", remote="https://github.com/WorkOrg/harness"),
        "upstream": repos.RepoEntry("upstream", remote="https://github.com/Publisher/tool"),
        "sibling": repos.RepoEntry("sibling", remote="https://github.com/Publisher/other"),
    }, account_map={"WorkOrg": "operator-work"})
    repos.write_registry(state)
    policy = tmp_path / "knowledge" / recovery.POLICY_FILE
    policy.parent.mkdir(parents=True)
    return registry, policy


def run(policy, **changes):
    options = dict(
        default_slot="work", identity_source="WorkOrg/harness",
        repository_slots=[("Publisher/tool", "public")], path=policy,
    )
    return recovery.reconcile(**{**options, **changes})


def test_preview_apply_and_warm_read_preserve_exact_exception(local):
    registry, policy = local
    policy.write_text(json.dumps({
        "version": 1, "accounts": {"work": "operator-work", "public": "operator-public"},
    }), encoding="utf-8")
    before = registry.read_bytes()
    preview = run(policy)
    assert preview["changes_pending"] and not preview["applied"]
    assert registry.read_bytes() == before
    assert run(policy, apply=True)["applied"]
    assert repos.account_for_github_slug("Publisher/tool") == "operator-public"
    assert repos.account_for_github_slug("Publisher/other") == "operator-work"
    installed = registry.read_bytes(), registry.stat().st_mtime_ns
    assert run(policy, apply=True)["applied"] is False
    assert (registry.read_bytes(), registry.stat().st_mtime_ns) == installed


def test_absent_slots_use_only_existing_configured_repository_identity(local):
    _registry, policy = local
    result = run(policy, apply=True)
    assert result["default"]["account"] == "operator-work"
    assert result["default"]["source"] == "configured-repository"
    assert result["missing_slots"] == ["public"]
    assert repos.account_for_github_slug("Publisher/tool") == "operator-work"


def test_changed_and_withdrawn_portable_slot_refreshes_only_managed_choices(local):
    _registry, policy = local
    value = {"version": 1, "accounts": {"work": "operator-work", "public": "first-public"}}
    policy.write_text(json.dumps(value), encoding="utf-8")
    assert run(policy, apply=True)["applied"]
    value["accounts"]["public"] = "later-public"
    policy.write_text(json.dumps(value), encoding="utf-8")
    assert run(policy, apply=True)["applied"]
    assert repos.account_for_github_slug("Publisher/tool") == "later-public"
    del value["accounts"]["public"]
    policy.write_text(json.dumps(value), encoding="utf-8")
    assert run(policy, apply=True)["missing_slots"] == ["public"]
    assert repos.read_registry(strict=True).repos["upstream"].account == ""
    assert repos.account_for_github_slug("Publisher/tool") == "operator-work"


def test_explicit_native_choices_take_over_even_when_equal_to_a_projection(local, monkeypatch):
    from agent_worktrees import repos_cli

    _registry, policy = local
    value = {"version": 1, "accounts": {"work": "operator-work", "public": "first-public"}}
    policy.write_text(json.dumps(value), encoding="utf-8")
    run(policy, apply=True)
    assert repos_cli.cmd_repos_dispatch(["account", "default", "operator-work"]) == 0
    monkeypatch.setattr(repos, "_best_effort_pin_credential", lambda *_args: None)
    repos.add_repo("upstream", str(policy.parent), account="first-public")
    value["accounts"] = {"work": "changed-work", "public": "changed-public"}
    policy.write_text(json.dumps(value), encoding="utf-8")
    result = run(policy, apply=True)
    assert result["applied"] is False
    assert repos.read_registry(strict=True).default_account == "operator-work"
    assert repos.account_for_github_slug("Publisher/tool") == "first-public"


def test_changed_local_projection_is_preserved_as_an_explicit_choice(local):
    _registry, policy = local
    value = {"version": 1, "accounts": {"work": "operator-work", "public": "first-public"}}
    policy.write_text(json.dumps(value), encoding="utf-8")
    run(policy, apply=True)
    state = repos.read_registry(strict=True)
    state.repos["upstream"].account = "local-override"
    repos.write_registry(state)
    value["accounts"]["public"] = "changed-public"
    policy.write_text(json.dumps(value), encoding="utf-8")
    result = run(policy, apply=True)
    assert result["conflicts"] == [{
        "target": "publisher/tool", "slot": "public", "state": "local-choice-preserved",
    }]
    assert repos.account_for_github_slug("Publisher/tool") == "local-override"
    assert "repo:upstream" not in repos.read_registry(strict=True).account_projection


def test_alias_bootstrap_preserves_exact_name_and_first_slug_match(local):
    _registry, policy = local
    state = repos.read_registry(strict=True)
    state.repos["upstream"].account = "first-account"
    state.repos["zz-alias"] = repos.RepoEntry(
        "zz-alias", remote=state.repos["upstream"].remote, account="alias-account",
    )
    repos.write_registry(state)
    assert run(policy, identity_source="zz-alias")["default"]["account"] == "alias-account"
    assert run(policy, identity_source="Publisher/tool")["default"]["account"] == "first-account"
    state = repos.read_registry(strict=True)
    state.repos["upstream"].account = ""
    repos.write_registry(state)
    assert run(policy, identity_source="Publisher/tool")["default"]["state"] == "missing"


def test_repository_slot_projects_each_missing_alias_without_replacing_explicit_accounts(local):
    _registry, policy = local
    state = repos.read_registry(strict=True)
    state.repos["zz-alias"] = repos.RepoEntry(
        "zz-alias", remote=state.repos["upstream"].remote, account="operator-public",
    )
    repos.write_registry(state)
    policy.write_text(json.dumps({
        "version": 1, "accounts": {"work": "operator-work", "public": "operator-public"},
    }), encoding="utf-8")
    result = run(policy, apply=True)
    assert result["status"] == "ready"
    assert repos.account_for_github_slug("Publisher/tool") == "operator-public"
    state = repos.read_registry(strict=True)
    assert state.repos["upstream"].account == "operator-public"
    assert "repo:upstream" in state.account_projection
    assert "repo:zz-alias" not in state.account_projection


def test_invalid_projection_owner_never_persists_unreadable_metadata(local):
    registry, policy = local
    before = registry.read_bytes()
    with pytest.raises(ValueError):
        run(policy, owner="x" * 65, apply=True)
    assert registry.read_bytes() == before


def test_large_alias_projection_refuses_before_persisting_an_unreadable_record_count(local, monkeypatch):
    from copy import deepcopy

    registry, policy = local
    state = repos.read_registry(strict=True)
    state.repos.update({
        f"alias-{number}": repos.RepoEntry(
            f"alias-{number}", remote="https://github.com/Publisher/tool",
        ) for number in range(1023)
    })
    policy.write_text(json.dumps({
        "version": 1, "accounts": {"work": "operator-work", "public": "operator-public"},
    }), encoding="utf-8")
    before = registry.read_bytes()
    monkeypatch.setattr(repos, "read_registry", lambda **_kwargs: deepcopy(state))
    with pytest.raises(ValueError, match="record limit exceeded"):
        run(policy, apply=True)
    assert registry.read_bytes() == before


def test_portable_and_argument_slot_conflict_refuses_before_writing(local):
    registry, policy = local
    policy.write_text(json.dumps({
        "version": 1, "accounts": {"work": "operator-work", "public": "operator-public"},
        "repositories": {"Publisher/tool": "work"},
    }), encoding="utf-8")
    before = registry.read_bytes()
    with pytest.raises(ValueError, match="conflicts with portable policy"):
        run(policy, apply=True)
    assert registry.read_bytes() == before


def test_existing_default_map_writer_revokes_managed_ownership(local):
    _registry, policy = local
    value = {"version": 1, "accounts": {"work": "operator-work"}}
    policy.write_text(json.dumps(value), encoding="utf-8")
    run(policy, apply=True)
    repos.set_account_map("GITHUB.COM:*", "operator-work")
    value["accounts"]["work"] = "changed-work"
    policy.write_text(json.dumps(value), encoding="utf-8")
    run(policy, apply=True)
    state = repos.read_registry(strict=True)
    assert state.default_account == "operator-work"
    assert "default" not in state.account_projection


def test_remove_and_explicit_readd_cannot_inherit_projection_ownership(local, monkeypatch):
    _registry, policy = local
    value = {"version": 1, "accounts": {"work": "operator-work", "public": "first-public"}}
    policy.write_text(json.dumps(value), encoding="utf-8")
    run(policy, apply=True)
    assert repos.remove_repo("upstream")
    assert "repo:upstream" not in repos.read_registry(strict=True).account_projection
    state = repos.read_registry(strict=True)
    state.account_projection["repo:upstream"] = {"owner": "account-reconcile", "account": "first-public"}
    repos.write_registry(state)
    monkeypatch.setattr(repos, "_best_effort_pin_credential", lambda *_args: None)
    repos.add_repo(
        "upstream", str(policy.parent), remote="https://github.com/Publisher/tool",
        account="first-public",
    )
    value["accounts"]["public"] = "changed-public"
    policy.write_text(json.dumps(value), encoding="utf-8")
    run(policy, apply=True)
    assert repos.read_registry(strict=True).repos["upstream"].account == "first-public"


def test_namespace_and_ambient_login_are_not_bootstrap_evidence(local, monkeypatch):
    registry, policy = local
    monkeypatch.setenv("GH_USER", "Publisher")
    before = registry.read_bytes()
    result = run(policy, identity_source="Publisher/tool", apply=True)
    assert result["default"]["state"] == "missing"
    assert result["missing_slots"] == ["public", "work"]
    assert registry.read_bytes() == before


def test_existing_default_repository_and_owner_choices_remain_authoritative(local):
    _registry, policy = local
    state = repos.read_registry(strict=True)
    state.default_account = "preferred"
    state.repos["upstream"].account = "exact"
    state.account_map["Publisher"] = "owner-choice"
    repos.write_registry(state)
    policy.write_text(json.dumps({
        "version": 1, "accounts": {"work": "work-slot", "public": "public-slot"},
        "repositories": {"Publisher/other": "public"},
    }), encoding="utf-8")
    result = run(policy, apply=True)
    assert result["applied"] is False
    assert {row["target"] for row in result["conflicts"]} == {
        "default", "publisher/tool", "publisher/other",
    }
    assert repos.account_for_github_slug("Publisher/tool") == "exact"
    assert repos.account_for_github_slug("Publisher/other") == "owner-choice"


@pytest.mark.parametrize("document", [
    '{"version":1,"version":1}',
    '{"version":true}',
    '{"version":1,"accounts":{"public":"two accounts"}}',
    '{"version":1,"repositories":{"../tool":"public"}}',
    '{"version":1,"accounts":[]}',
    '{"version":1,"repositories":{"Publisher/tool":"public","publisher/tool":"work"}}',
])
def test_invalid_policy_never_changes_local_accounts(local, document):
    registry, policy = local
    policy.write_text(document, encoding="utf-8")
    before = registry.read_bytes()
    with pytest.raises(ValueError):
        run(policy, apply=True)
    assert registry.read_bytes() == before


def test_changed_policy_refuses_projection(local, monkeypatch):
    registry, policy = local
    policy.write_text('{"version":1,"accounts":{"work":"first"}}', encoding="utf-8")
    original = recovery._read
    calls = []

    def read(path):
        calls.append(path)
        if len(calls) == 2:
            path.write_text('{"version":1,"accounts":{"work":"later"}}', encoding="utf-8")
        return original(path)

    monkeypatch.setattr(recovery, "_read", read)
    before = registry.read_bytes()
    with pytest.raises(ValueError, match="changed before projection"):
        run(policy, apply=True)
    assert registry.read_bytes() == before


def test_registry_changed_during_projection_is_not_overwritten(local, monkeypatch):
    registry, policy = local
    policy.write_text('{"version":1,"accounts":{"work":"first"}}', encoding="utf-8")
    original = recovery._read

    def read(path):
        raw = original(path)
        registry.write_bytes(registry.read_bytes() + b"\n# concurrent owner\n")
        return raw

    monkeypatch.setattr(recovery, "_read", read)
    with pytest.raises(Exception, match="changed|baseline|concurrent"):
        run(policy, apply=True)
    assert b"# concurrent owner" in registry.read_bytes()
    assert repos.read_registry(strict=True).default_account == ""


def test_selected_knowledge_source_uses_native_binding_owner(local, monkeypatch):
    _registry, policy = local
    selected = SimpleNamespace(
        repo_name="selected-harness", default_repo=SimpleNamespace(anchor="/selected-harness"),
    )
    seen = []
    monkeypatch.setattr(config, "load_config", lambda *args, **kwargs: selected)
    monkeypatch.setattr(config, "project_dir", lambda name: policy.parent)

    def source(current, **kwargs):
        seen.append((current, kwargs))
        return SimpleNamespace(anchor=str(policy.parent.parent))

    monkeypatch.setattr(config_sources, "knowledge_config_source", source)
    assert recovery.knowledge_policy_path("selected-harness") == policy
    assert seen == [(selected, {"base_anchor": "/selected-harness"})]


@pytest.mark.timeout(60)
@pytest.mark.parametrize(("explicit_context", "bound"), [(True, True), (False, True), (False, False)])
def test_actual_cli_projects_bound_knowledge_policy_without_credentials(tmp_path, monkeypatch, explicit_context, bound):
    for key in ("AGENT_HOME", "HOME", "USERPROFILE"):
        monkeypatch.setenv(key, str(tmp_path))
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_WORKTREES_CONFIG_ROOT"):
        monkeypatch.delenv(key, raising=False)
    roots = {}
    for name in ("example-harness", "example-knowledge"):
        root = tmp_path / name
        result = subprocess.run(
            ["git", "-c", "init.defaultBranch=main", "init", "--quiet", str(root)],
            capture_output=True, timeout=5, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        roots[name] = root
    local_config = tmp_path / ".example-harness"
    local_config.mkdir()
    (local_config / "config.yaml").write_text(yaml.safe_dump({
        "repo_name": "example-harness", **({"knowledge_repo": "example-knowledge"} if bound else {}),
        "repos": {"example-harness": {"anchor": str(roots["example-harness"])}},
    }), encoding="utf-8")
    registry = repos._repos_yaml_path()
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(yaml.safe_dump({"repos": {
        name: {"class": "knowledge" if "knowledge" in name else "worktree",
               repos._current_platform(): str(root), "remote": f"https://github.com/WorkOrg/{name}",
               "account": "operator-work" if name == "example-harness" else ""}
        for name, root in roots.items()
    }}), encoding="utf-8")
    (registry.parent / "projects.yaml").write_text(yaml.safe_dump({
        "schema_version": 2, "projects": {"example-harness": {"config_dir": str(local_config)}},
    }), encoding="utf-8")
    policy = roots["example-knowledge"] / recovery.POLICY_FILE
    policy.parent.mkdir()
    policy.write_text('{"version":1,"accounts":{"work":"operator-work"}}', encoding="utf-8")
    arguments = [
        "repos", "account", "reconcile", "--default-slot", "work",
        "--bootstrap-from-context", "--apply", "--json",
    ]
    if explicit_context:
        arguments.extend(["--context-project", "example-harness"])
    for changed in (True, False):
        child = subprocess.run(
            [sys.executable, "-m", "agent_worktrees", *arguments],
            cwd=roots["example-harness"], capture_output=True, text=True, timeout=15,
            **no_window_kwargs(),
        )
        assert child.returncode == 0, child.stderr
        result = json.loads(child.stdout)
        assert result["applied"] is changed
        assert result["default"]["account"] == "operator-work"
    assert repos.read_registry(strict=True).default_account == "operator-work"
    if explicit_context:
        before = registry.read_bytes()
        (local_config / "config.yaml").write_text("knowledge_repo: [", encoding="utf-8")
        child = subprocess.run(
            [sys.executable, "-m", "agent_worktrees", *arguments],
            cwd=roots["example-harness"], capture_output=True, text=True, timeout=15,
            **no_window_kwargs(),
        )
        assert child.returncode == 2, child.stdout + child.stderr
        assert registry.read_bytes() == before
