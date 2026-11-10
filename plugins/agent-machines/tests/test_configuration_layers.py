import argparse
import copy
import json
from pathlib import Path
import pytest
import yaml
from configuration_layers import ConfigurationError

from agent_machines import __main__ as cli, configuration, discover, reconcile
from agent_machines.manifest import ManifestError
from agent_machines.surfaces import apply_surfaces


def write(root, name, values, *, extra=None):
    path = root / ".copilot-extensions" / "agent-machines" / "all" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = {
        "schema_version": 4, "package": name,
        "manage": {"copilot.settings": {"disposition": "ensure-present", "values": values}},
        **(extra or {}),
    }
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return raw


def setup(tmp_path, monkeypatch, binding="bound"):
    base, knowledge, local, home = (tmp_path / part for part in ("base", "knowledge", "local", "home"))
    for root in (base, knowledge, local, home):
        root.mkdir()
    monkeypatch.setattr(discover, "home", lambda: home)
    monkeypatch.setattr(cli._layout, "resolve_cwd_repo", lambda: ("base-native", base, base))
    candidates = [discover.RepoCandidate(name="base-native", path=base)]
    if binding == "bound":
        candidates.append(discover.RepoCandidate(name="knowledge-native", path=knowledge, required_by=("base-native",)))
    monkeypatch.setattr(discover, "project_scope_repos", lambda *args, **kwargs: candidates)
    context = {
        "repository_id": "repo-qualified", "execution_machine": "logical-worker",
        "installation_cell": "cell-one", "binding": binding, "base_root": str(base),
    }
    if binding == "bound":
        context["knowledge_root"] = str(knowledge)
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": context, "repository_machine_root": str(local),
    }
    monkeypatch.setattr(configuration, "owner_report", lambda root: report)
    return base, knowledge, local, home, report


def collect():
    packages, scope = cli._collect_reconcile_packages(
        argparse.Namespace(all_projects=False, repo=None), "logical-worker",
    )
    return packages


def effective(packages, home, live=None, *, apply=False):
    home.mkdir(exist_ok=True)
    if live is not None:
        (home / "settings.json").write_text(json.dumps(live), encoding="utf-8")
    resolved = reconcile.resolve_union(packages, "logical-worker")
    results = apply_surfaces(resolved, home=home, dry_run=not apply, only=["settings"])
    changes = results[0].changes if results else []
    return {item["key"]: item["after"] for item in changes}, resolved


def test_real_default_collector_to_native_surface_all_tiers(tmp_path, monkeypatch):
    base, knowledge, local, home, report = setup(tmp_path, monkeypatch)
    original = write(base, "base-package", {"model": "base", "effortLevel": "low", "list": ["base"]},
                     extra={"resources": [{"type": "file", "path": "$HOME/protected", "content": "native"}]})
    write(knowledge, "knowledge-package", {"model": "knowledge", "effortLevel": "high", "list": ["knowledge"]})
    values, resolved = effective(collect(), tmp_path / "live")
    assert values["model"] == "knowledge"
    assert values["list"] == ["base", "knowledge"]
    user_root = discover.user_package_root()
    user_path = user_root / "all" / "machine.yaml"
    user_path.parent.mkdir(parents=True)
    user_path.write_text(yaml.safe_dump({
        "schema_version": 4, "package": "machine-defaults",
        "manage": {"copilot.settings": {"disposition": "ensure-present", "values": {"model": "machine"}}},
        "resources": [{"type": "file", "path": "$HOME/not-applied", "content": "protected"}],
    }), encoding="utf-8")
    assert effective(collect(), tmp_path / "live")[0]["model"] == "machine"
    write(local, "local-defaults", {"model": "local"})
    packages = collect()
    values, resolved = effective(packages, tmp_path / "live")
    assert values["model"] == "local"
    assert values["effortLevel"] == "high"
    assert {package.name for package in packages} == {"base-package", "knowledge-package"}
    assert resolved[0].resources == original["resources"]
    assert packages[0].ordinary_defaults["provenance"]["/model"]["installation_cell"] == "cell-one"
    first_touch = tmp_path / "first-touch"
    effective(packages, first_touch, apply=True)
    assert json.loads((first_touch / "settings.json").read_text())["model"] == "local"
    plan = reconcile.plan_to_dict(reconcile.plan(packages, "logical-worker"))
    assert plan["ordinary_defaults"]["provenance"]["/model"]["source"]["root"] == str(local)
    values, _ = effective(packages, tmp_path / "live", {"model": "existing-user", "unmanaged": True}, apply=True)
    assert "model" not in values
    persisted = json.loads((tmp_path / "live" / "settings.json").read_text())
    assert persisted["model"] == "existing-user"
    assert persisted["unmanaged"] is True
    assert not (home / "not-applied").exists()


def test_enforce_remains_independent_and_conflicting_enforce_still_refuses(tmp_path, monkeypatch):
    base, knowledge, local, home, report = setup(tmp_path, monkeypatch)
    write(base, "base-default", {"model": "default"})
    write(knowledge, "knowledge-default", {"model": "knowledge"})
    protected = write(base, "enforce-one", {"model": "enforced"})
    protected["manage"]["copilot.settings"]["disposition"] = "enforce"
    path = base / ".copilot-extensions" / "agent-machines" / "all" / "enforce-one.yaml"
    path.write_text(yaml.safe_dump(protected))
    values, _ = effective(collect(), tmp_path / "live")
    assert values["model"] == "enforced"
    conflict = copy.deepcopy(protected)
    conflict["package"] = "enforce-two"
    conflict["manage"]["copilot.settings"]["values"]["model"] = "conflicting"
    path.with_name("enforce-two.yaml").write_text(yaml.safe_dump(conflict))
    with pytest.raises(reconcile.RestoreValidationError, match="enforce-conflict"):
        reconcile.restore(collect(), "logical-worker", dry_run=True, only=["settings"])


def test_unbound_unresolved_and_native_per_machine_schema(tmp_path, monkeypatch):
    base, knowledge, local, home, report = setup(tmp_path, monkeypatch, "unbound")
    write(base, "base-default", {"model": "base"}, extra={
        "per-machine": {"logical-worker": {"manage": {"copilot.settings": {"values": {"model": "per-machine"}}}}},
    })
    write(knowledge, "excluded", {"model": "knowledge"})
    assert effective(collect(), tmp_path / "live")[0]["model"] == "per-machine"
    report["context"]["binding"] = "unresolved"
    with pytest.raises(ConfigurationError, match="unresolved"):
        collect()


def test_repository_machine_defaults_cannot_supply_hard_constraints(tmp_path, monkeypatch):
    base, knowledge, local, home, report = setup(tmp_path, monkeypatch)
    write(base, "base-default", {"model": "base"})
    write(local, "invalid-local", {"model": "local"}, extra={"authority": 10})
    with pytest.raises(ManifestError, match="hard constraints"):
        collect()


def test_actual_absent_owner_leaves_native_defaults_without_bootstrap(tmp_path, monkeypatch):
    import configuration_context

    for name in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT"):
        monkeypatch.delenv(name, raising=False)

    def no_process(*args, **kwargs):
        pytest.fail("an absent optional owner must not bootstrap or launch")

    monkeypatch.setattr(configuration_context.subprocess, "run", no_process)
    assert configuration.prepare_defaults([], "explicit-logical-selector") == []
    assert list(tmp_path.iterdir()) == []


def portable(tmp_path, monkeypatch):
    from agent_machines import preferences

    base, knowledge, local, home, report = setup(tmp_path, monkeypatch)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    write(base, "native", {"logLevel": "native-only"}, extra={
        "resources": [{"type": "file", "path": "$HOME/not-applied", "content": "protected"}],
    })
    state = tmp_path / "accepted-state"
    state.mkdir()
    policy = {
        "schema": "agent-machines.preferences.policy", "version": 1,
        "publisher": "example-publisher", "participant": "example-receiver", "domains": ["copilot"],
    }
    accepted = {
        "schema": preferences.SCHEMA, "version": 1, "publisher": "example-publisher",
        "revision": 1, "parent": None,
        "domains": {"copilot": {"model": "shared-model", "effortLevel": "low"}},
        "machines": {"logical-worker": {"copilot": {"model": "machine-model", "effortLevel": "high"}}},
    }
    (state / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
    (state / "accepted.json").write_text(json.dumps(accepted), encoding="utf-8")
    target = home / ".copilot" / "settings.json"
    return base, home, report, state, accepted, target


def test_portable_floor_normal_cli_preview_apply_and_withdrawal(tmp_path, monkeypatch, capsys):
    from agent_machines import preferences

    base, home, report, state, accepted, target = portable(tmp_path, monkeypatch)
    local = tmp_path / "local.json"
    local.write_text(json.dumps({
        "schema": preferences.LOCAL_SCHEMA, "version": 1, "machine": "logical-worker",
        "domains": {"copilot": {"model": "local-model"}},
    }), encoding="utf-8")
    monkeypatch.setattr(reconcile, "restore", lambda *_args, **_kwargs: pytest.fail("unscoped restore"))
    args = ["preferences", "copilot", "--state-dir", str(state), "--local-overrides", str(local)]
    assert cli.main(args) == 0
    proposed = json.loads(capsys.readouterr().out)
    assert proposed["floors"] == {"model": "local-model", "effortLevel": "high"}
    assert proposed["provenance"]["model"] == "local-machine"
    assert proposed["provenance"]["effortLevel"] == "named-machine"
    assert proposed["scope"]["machine"] == "logical-worker"
    assert not target.exists() and not (state / "exchange.lock").exists()
    assert cli.main([*args, "--apply", "--expect", proposed["preview_token"]]) == 0
    assert json.loads(capsys.readouterr().out)["configuration_applied"]
    assert json.loads(target.read_text()) == {"model": "local-model", "effortLevel": "high"}
    assert not (home / "not-applied").exists()
    accepted["domains"], accepted["machines"] = {}, {}
    (state / "accepted.json").write_text(json.dumps(accepted), encoding="utf-8")
    assert cli.main(["preferences", "copilot", "--state-dir", str(state)]) == 0
    withdrawn = json.loads(capsys.readouterr().out)
    assert withdrawn["floors"] == {} and withdrawn["changes"] == []
    assert json.loads(target.read_text())["model"] == "local-model"


def test_portable_floors_retain_live_native_defaults_and_enforce(tmp_path, monkeypatch):
    from agent_machines.preference_copilot import adopt

    base, home, report, state, accepted, target = portable(tmp_path, monkeypatch)
    target.parent.mkdir()
    target.write_text(json.dumps({"model": "live-model", "unmanaged": {"kept": True}}), encoding="utf-8")
    native = write(base, "native-model", {"effortLevel": "medium"})
    proposed = adopt(state)
    assert proposed["overridden"] == ["effortLevel", "model"]
    assert proposed["changes"] == [{"key": "effortLevel", "before": None, "after": "medium"}]
    native["manage"]["copilot.settings"]["disposition"] = "enforce"
    native["manage"]["copilot.settings"]["values"] = {"model": "enforced-model"}
    path = base / ".copilot-extensions" / "agent-machines" / "all" / "native-model.yaml"
    path.write_text(yaml.safe_dump(native), encoding="utf-8")
    proposed = adopt(state)
    adopt(state, apply=True, expected=proposed["preview_token"])
    assert json.loads(target.read_text()) == {
        "model": "enforced-model", "effortLevel": "high", "unmanaged": {"kept": True},
    }


def test_portable_confirmation_is_stable_across_scalar_field_iteration(tmp_path, monkeypatch):
    from agent_machines.preference_copilot import adopt

    base, home, report, state, accepted, target = portable(tmp_path, monkeypatch)
    write(base, "native-defaults", {"model": "native-model", "effortLevel": "medium"})
    monkeypatch.setattr(configuration, "FIELDS", ("model", "effortLevel"))
    proposed = adopt(state)
    monkeypatch.setattr(configuration, "FIELDS", ("effortLevel", "model"))
    assert adopt(state)["preview_token"] == proposed["preview_token"]
    adopt(state, apply=True, expected=proposed["preview_token"])
    assert json.loads(target.read_text()) == {"model": "native-model", "effortLevel": "medium"}


@pytest.mark.parametrize("change", ["accepted", "local", "native", "live", "context", "policy"])
def test_portable_apply_refuses_changed_preview_inputs(tmp_path, monkeypatch, change):
    from agent_machines import preferences
    from agent_machines.preference_copilot import adopt

    base, home, report, state, accepted, target = portable(tmp_path, monkeypatch)
    local = tmp_path / "local.json"
    local.write_text(json.dumps({
        "schema": preferences.LOCAL_SCHEMA, "version": 1, "machine": "logical-worker", "domains": {},
    }), encoding="utf-8")
    proposed = adopt(state, local=local)
    if change == "accepted":
        accepted["revision"], accepted["parent"] = 2, 1
        (state / "accepted.json").write_text(json.dumps(accepted), encoding="utf-8")
    elif change == "local":
        local.write_text(local.read_text().replace('"domains": {}', '"domains": {"copilot": {"model": "changed"}}'))
    elif change == "native":
        write(base, "changed", {"model": "changed-native"})
    elif change == "live":
        target.parent.mkdir()
        target.write_text('{"unmanaged":true}', encoding="utf-8")
    elif change == "context":
        report["context"]["installation_cell"] = "changed-cell"
    else:
        policy = json.loads((state / "policy.json").read_text())
        policy["participant"] = "changed-participant"
        (state / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
    before = target.read_bytes() if target.exists() else None
    with pytest.raises(preferences.PreferenceError, match="preview-changed"):
        adopt(state, local=local, apply=True, expected=proposed["preview_token"])
    assert (target.read_bytes() if target.exists() else None) == before


def test_portable_apply_rechecks_at_native_settings_write(tmp_path, monkeypatch):
    from agent_machines import preferences
    from agent_machines.preference_copilot import adopt
    from agent_machines.surfaces import settings

    base, home, report, state, accepted, target = portable(tmp_path, monkeypatch)
    proposed = adopt(state)
    original = settings.apply

    def changed(*args, **kwargs):
        if not kwargs.get("dry_run", True):
            report["context"]["execution_machine"] = "changed-worker"
        return original(*args, **kwargs)

    monkeypatch.setattr(settings, "apply", changed)
    with pytest.raises(preferences.PreferenceError, match="preview-changed"):
        adopt(state, apply=True, expected=proposed["preview_token"])
    assert not target.exists()


@pytest.mark.parametrize("invalid", ["{", "[]", '{"model":"first","model":"second"}'])
def test_portable_apply_never_repairs_invalid_live_settings(tmp_path, monkeypatch, invalid):
    from agent_machines import preferences
    from agent_machines.preference_copilot import adopt

    base, home, report, state, accepted, target = portable(tmp_path, monkeypatch)
    target.parent.mkdir()
    target.write_text(invalid, encoding="utf-8")
    with pytest.raises(preferences.PreferenceError, match="native-settings-unavailable"):
        adopt(state)
    assert target.read_text() == invalid


@pytest.mark.parametrize(("missing", "reason"), [
    ("context", "native-context-unavailable"), ("accepted", "input-unavailable"),
    ("policy", "domain-not-permitted"),
])
def test_portable_requires_accepted_policy_and_native_context(tmp_path, monkeypatch, missing, reason):
    from agent_machines import preferences
    from agent_machines.preference_copilot import adopt

    base, home, report, state, accepted, target = portable(tmp_path, monkeypatch)
    if missing == "context":
        monkeypatch.setattr(configuration, "owner_report", lambda _root: None)
    elif missing == "accepted":
        (state / "accepted.json").unlink()
    else:
        policy = json.loads((state / "policy.json").read_text())
        policy["domains"] = ["repositories"]
        (state / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(preferences.PreferenceError, match=reason):
        adopt(state)
    assert not target.exists()


def test_portable_conflicting_native_enforce_still_refuses(tmp_path, monkeypatch):
    from agent_machines import preferences
    from agent_machines.preference_copilot import adopt

    base, home, report, state, accepted, target = portable(tmp_path, monkeypatch)
    for model in ("first", "second"):
        native = write(base, model, {"model": model})
        native["manage"]["copilot.settings"]["disposition"] = "enforce"
        path = base / ".copilot-extensions" / "agent-machines" / "all" / f"{model}.yaml"
        path.write_text(yaml.safe_dump(native), encoding="utf-8")
    with pytest.raises(preferences.PreferenceError, match="native-validation-failed"):
        adopt(state)
    assert not target.exists()


def test_portable_cli_sanitizes_native_owner_refusal(tmp_path, monkeypatch, capsys):
    from agent_machines._peer_launch import ContextRefused

    base, home, report, state, accepted, target = portable(tmp_path, monkeypatch)

    def refused(_root):
        raise ContextRefused("private native owner detail")

    monkeypatch.setattr(configuration, "owner_report", refused)
    assert cli.main(["preferences", "copilot", "--state-dir", str(state)]) == 2
    assert json.loads(capsys.readouterr().err) == {
        "ok": False, "error": "native-configuration-unavailable",
    }
    assert not target.exists()


@pytest.mark.parametrize("source", ["user", "repository-machine"])
def test_portable_empty_native_union_refuses_instead_of_skipping_machine_defaults(
    tmp_path, monkeypatch, source,
):
    from agent_machines import preferences
    from agent_machines.preference_copilot import adopt

    base, home, report, state, accepted, target = portable(tmp_path, monkeypatch)
    (base / ".copilot-extensions" / "agent-machines" / "all" / "native.yaml").unlink()
    if source == "user":
        path = discover.user_package_root() / "all" / "native-machine.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump({
            "schema_version": 4, "package": "native-machine",
            "manage": {"copilot.settings": {
                "disposition": "ensure-present", "values": {"model": "native-machine"},
            }},
        }), encoding="utf-8")
    else:
        write(Path(report["repository_machine_root"]), "native-machine", {
            "model": "native-machine",
        })
    with pytest.raises(preferences.PreferenceError, match="native-defaults-unavailable"):
        adopt(state)
    assert not target.exists()


@pytest.mark.parametrize("source", ["gate", "directory", "overlay"])
@pytest.mark.parametrize("guest", [False, True])
def test_portable_retains_alias_gated_native_enforce_without_host_guest_leak(
    tmp_path, monkeypatch, source, guest,
):
    from agent_machines.preference_copilot import adopt

    base, home, report, state, accepted, target = portable(tmp_path, monkeypatch)
    (base / "machines.yaml").write_text(yaml.safe_dump({"machines": {
        "logical-worker": {
            "hostname": "physical-example", "ssh": {"environments": [
                {"name": "windows", "alias": "native-profile"},
                {"name": "wsl", "alias": "guest-profile"},
            ]},
        },
    }}), encoding="utf-8")
    alias, foreign = ("guest-profile", "native-profile") if guest else ("native-profile", "guest-profile")
    if guest:
        report["context"]["execution_machine"] = "logical-worker-wsl"
    native = write(base, "alias-enforce", {"model": "alias-enforced"})
    spec = native["manage"]["copilot.settings"]
    spec["disposition"] = "enforce"
    path = base / ".copilot-extensions" / "agent-machines" / "all" / "alias-enforce.yaml"
    if source == "gate":
        native["gate"] = [alias]
    elif source == "directory":
        path.unlink()
        path = path.parent.parent / "machines" / alias / path.name
        path.parent.mkdir(parents=True)
    else:
        spec["values"] = {"model": "base-enforced"}
        native["per-machine"] = {alias: {"manage": {
            "copilot.settings": {"values": {"model": "alias-enforced"}},
        }}}
    path.write_text(yaml.safe_dump(native), encoding="utf-8")
    wrong = write(base, "foreign-enforce", {"model": "wrong-space"}, extra={"gate": [foreign]})
    wrong["manage"]["copilot.settings"]["disposition"] = "enforce"
    (base / ".copilot-extensions" / "agent-machines" / "all" / "foreign-enforce.yaml").write_text(
        yaml.safe_dump(wrong), encoding="utf-8",
    )
    proposed = adopt(state)
    adopt(state, apply=True, expected=proposed["preview_token"])
    assert json.loads(target.read_text())["model"] == "alias-enforced"


def test_independent_cells_keep_default_provenance_and_recompute_native_plan(tmp_path, monkeypatch):
    base, knowledge, local, home, report = setup(tmp_path, monkeypatch)
    write(base, "base-default", {"model": "base"})
    write(local, "local-default", {"model": "selected"})
    first_packages = collect()
    first_plan = reconcile.plan(first_packages, "logical-worker")
    second = tmp_path / "other-cell-local"
    second.mkdir()
    write(second, "local-default", {"model": "selected"})
    report["repository_machine_root"] = str(second)
    report["context"]["installation_cell"] = "cell-two"
    second_packages = configuration.prepare_defaults(first_packages, "logical-worker")
    second_plan = reconcile.plan(second_packages, "logical-worker")
    assert first_plan.ordinary_defaults["values"] == second_plan.ordinary_defaults["values"]
    assert first_plan.provenance_hash != second_plan.provenance_hash
    assert first_plan.ordinary_defaults["provenance"]["/model"]["installation_cell"] == "cell-one"
    assert second_plan.ordinary_defaults["provenance"]["/model"]["installation_cell"] == "cell-two"
    assert first_packages[0].ordinary_defaults["provenance"]["/model"]["source"]["root"] == str(local)


def test_native_unready_legacy_owner_never_bootstraps(tmp_path, monkeypatch):
    import os
    import shutil
    from pathlib import Path

    home = tmp_path / "home"
    scripts = home / ".agent-machines" / "bin"
    scripts.mkdir(parents=True)
    suffix = "ps1" if os.name == "nt" else "sh"
    resolver = Path(configuration.__file__).parents[2] / "scripts" / f"resolve-runtime.{suffix}"
    shutil.copy2(resolver, scripts / resolver.name)
    peer = home / ".agent-worktrees"
    fake_python = peer / "versions" / "1.0.0" / "Scripts" / "python.exe"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("not a completed runtime", encoding="utf-8")
    (peer / "current-version").write_text("1.0.0", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    for name in ("AGENT_RT_ROOT", "COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT"):
        monkeypatch.delenv(name, raising=False)

    assert configuration.prepare_defaults([], "logical-worker") == []
    assert not (home / ".agent-machines" / "scripts").exists()
    assert not (peer / "logs").exists()
    assert configuration._legacy_report(Path.cwd()) is None


@pytest.mark.parametrize("outcome", ["matching", "repository-mismatch", "cell-mismatch", "refused", "cell-owned"])
def test_deployed_legacy_resolver_requalifies_delivered_configuration(tmp_path, monkeypatch, outcome):
    import os
    import shlex
    import shutil
    import sys
    from pathlib import Path

    if os.name == "nt":
        pytest.skip("real POSIX installed-layout actor; Windows transport has its own contract test")
    home = tmp_path / "home"
    own = home / ".agent-machines"
    peer = home / ".agent-worktrees"
    (own / "bin").mkdir(parents=True)
    shutil.copyfile(
        Path(configuration.__file__).parents[2] / "scripts/resolve-runtime.sh",
        own / "bin/resolve-runtime.sh",
    )
    python = peer / "versions/1.2.3/bin/python"
    python.parent.mkdir(parents=True)
    (peer / "current-version").write_text("1.2.3")
    (python.parent.parent / ".install-complete.json").write_text(
        '{"version": "1.2.3", "completed_at": "2026-01-01", "pid": 0}',
    )
    report = {"schema": "agent-worktrees.configuration", "version": 1, "context": {
        "repository_id": "selected-repository", "execution_machine": "worker",
        "installation_cell": "selected-cell", "binding": "unbound", "base_root": str(tmp_path),
    }}
    fresh = copy.deepcopy(report)
    if outcome == "repository-mismatch":
        fresh["context"]["repository_id"] = "foreign-repository"
    if outcome == "cell-mismatch":
        fresh["context"]["installation_cell"] = "foreign-cell"
    actor = tmp_path / "owner.py"
    actor.write_text(
        "import json, os, sys\n"
        "assert sys.argv[1:] == ['-I', '-X', 'utf8', '-m', 'agent_worktrees', "
        "'state-root', '--configuration', '--json']\n"
        "assert not any(key in os.environ for key in "
        "['AGENT_CONFIGURATION_CONTEXT', 'AGENT_RT_ROOT', 'PYTHONPATH', 'GITHUB_TOKEN'])\n"
        f"sys.exit(3) if {outcome == 'refused'!r} else None\n"
        f"print(json.dumps({fresh!r}))\n",
    )
    python.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -I {shlex.quote(str(actor))} \"$@\"\n")
    python.chmod(0o700)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("AGENT_RT_ROOT", str(own))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-must-not-reach-peer")
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    if outcome == "cell-owned":
        (peer / ".installation-ownership.json").write_text("{}")
        with pytest.raises(configuration._peer_launch.ContextRefused, match="installation-cell ownership"):
            configuration.owner_report(tmp_path)
    elif outcome == "matching":
        assert configuration.owner_report(tmp_path) == fresh
    else:
        with pytest.raises(ConfigurationError, match="refused|matching qualified owner"):
            configuration.owner_report(tmp_path)
    assert not (peer / "logs").exists()
    assert not (own / "scripts").exists()


def test_legacy_resolver_timeout_retires_its_actual_descendant(tmp_path, monkeypatch):
    import shlex
    import subprocess
    import sys
    from pathlib import Path
    from agent_procutil.owned_process import run_caller_owned

    if sys.platform != "linux":
        pytest.skip("Linux process-state witness; native Windows cleanup remains separately qualified")
    home = tmp_path / "home"
    own = home / ".agent-machines"
    (own / "bin").mkdir(parents=True)
    (home / ".agent-worktrees").mkdir()
    child_record = tmp_path / "resolver-child.pid"
    (own / "bin/resolve-runtime.sh").write_text(
        "# Trace contract: normal/read-only/ready-v1\n"
        f"/bin/sleep 60 &\nprintf '%s' \"$!\" > {shlex.quote(str(child_record))}\nwait\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("AGENT_RT_ROOT", str(own))
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT"):
        monkeypatch.delenv(key, raising=False)

    def short_owned(argv, **kwargs):
        assert kwargs["timeout"] == 15
        return run_caller_owned(argv, **{**kwargs, "timeout": 0.5})

    monkeypatch.setattr(configuration, "run_caller_owned", short_owned)
    with pytest.raises(ConfigurationError, match="read failed") as failure:
        configuration._legacy_report(tmp_path)
    cause = failure.value
    while cause.__cause__ is not None:
        cause = cause.__cause__
    assert isinstance(cause, subprocess.TimeoutExpired)
    pid = int(child_record.read_text())
    state_file = Path("/proc") / str(pid) / "stat"
    if state_file.exists():
        state = state_file.read_text().rsplit(")", 1)[1].split()[0]
        assert state in {"Z", "X"}, f"resolver descendant {pid} survived timeout"
    assert not (home / ".agent-worktrees/logs").exists()


@pytest.mark.parametrize("implicit", [False, True])
@pytest.mark.parametrize("matching_cell", [False, True])
def test_real_collector_checks_delivered_cell_against_actual_owner(tmp_path, monkeypatch, implicit, matching_cell):
    real_report = configuration.owner_report
    base, knowledge, local, home, report = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(configuration, "owner_report", real_report)
    write(base, "base-default", {"model": "base"})
    write(knowledge, "knowledge-default", {"model": "knowledge"})
    cell = tmp_path / "marketplaces" / "current-cell"
    own_root = cell / "plugins" / "agent-machines"
    own_root.mkdir(parents=True)
    report["context"]["installation_cell"] = str(cell if matching_cell else tmp_path / "foreign-cell")
    calls = []

    def validate(owner, root, raw):
        calls.append((owner, root, raw))
        assert root == own_root
        assert raw == str(own_root / "install.json")
        return {"cellRoot": str(cell), "pluginRoot": str(own_root)}

    monkeypatch.setattr(configuration._peer_launch, "validate_owner", validate)
    monkeypatch.setenv("AGENT_RT_ROOT", str(own_root))
    if implicit:
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    else:
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own_root / "install.json"))
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    monkeypatch.chdir(base)
    if matching_cell:
        assert effective(collect(), tmp_path / "live")[0]["model"] == "knowledge"
    else:
        with pytest.raises(ConfigurationError, match="another installation cell"):
            collect()
    assert calls


@pytest.mark.parametrize("code,diagnostic", [
    (2, "unrecognized arguments: --configuration"),
    (2, "unknown option: --configuration"),
    (1, "binding refused"), (3, "binding unresolved"), (126, "owner refused"),
    (2, "unrecognized arguments: --unrelated"), (0, "malformed"),
])
def test_optional_older_peer_preserves_native_plan_or_fails_real_refusal(tmp_path, monkeypatch, code, diagnostic):
    import subprocess
    real_report = configuration.owner_report
    base, knowledge, local, home, report = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(configuration, "owner_report", real_report)
    write(base, "base-default", {"model": "base"})
    cell = tmp_path / "marketplaces" / "current-cell"
    own = cell / "plugins" / "agent-machines"
    (cell / "plugins" / "agent-worktrees").mkdir(parents=True)
    monkeypatch.setenv("AGENT_RT_ROOT", str(own))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own / "install.json"))
    monkeypatch.delenv("AGENT_CONFIGURATION_CONTEXT", raising=False)
    monkeypatch.setattr(configuration._peer_launch, "validate_owner", lambda *args:
                        {"cellRoot": str(cell), "pluginRoot": str(own)})
    monkeypatch.setattr(configuration._peer_launch, "launch_prefix", lambda *args: ["qualified-peer"])
    monkeypatch.chdir(base)
    calls = []

    def run(argv, **kwargs):
        assert argv == ["qualified-peer", "state-root", "--configuration", "--json"]
        calls.append(argv)
        return subprocess.CompletedProcess(argv, code, b"{bad-json", diagnostic.encode())

    monkeypatch.setattr(subprocess, "run", run)
    if code == 2 and diagnostic.endswith("--configuration"):
        assert reconcile.plan(collect(), "logical-worker").ordinary_defaults is None
    else:
        with pytest.raises(ConfigurationError):
            collect()
    assert len(calls) == 1


def test_unqualified_delivery_cannot_be_admitted_by_optional_absence(tmp_path, monkeypatch):
    real_report = configuration.owner_report
    base, knowledge, local, home, report = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(configuration, "owner_report", real_report)
    write(base, "base-default", {"model": "base"})
    for name in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_RT_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    monkeypatch.setattr(configuration, "_legacy_report", lambda root: None)
    monkeypatch.chdir(base)
    with pytest.raises(ConfigurationError, match="matching qualified owner"):
        collect()
