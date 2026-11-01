import argparse
import copy
import json
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
    import shutil
    from pathlib import Path
    import configuration_context

    home = tmp_path / "home"
    scripts = home / ".agent-machines" / "scripts"
    scripts.mkdir(parents=True)
    for source in (Path(configuration.__file__).parents[2] / "scripts").glob("versioned_runtime*.py"):
        shutil.copy2(source, scripts / source.name)
    peer = home / ".agent-worktrees"
    fake_python = peer / "versions" / "1.0.0" / "Scripts" / "python.exe"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("not a completed runtime", encoding="utf-8")
    (peer / "current-version").write_text("1.0.0", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    for name in ("AGENT_RT_ROOT", "COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT"):
        monkeypatch.delenv(name, raising=False)

    def no_process(*args, **kwargs):
        pytest.fail("an interpreter-shaped unready slot must not launch or provision")

    monkeypatch.setattr(configuration_context.subprocess, "run", no_process)
    assert configuration.prepare_defaults([], "logical-worker") == []
    assert (scripts / "versioned_runtime.py").is_file()
    assert configuration._legacy_report(Path.cwd()) is None


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
