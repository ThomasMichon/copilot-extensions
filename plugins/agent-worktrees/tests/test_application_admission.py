"""Required applications are observed before New admission, never installed."""

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess

import pytest

from agent_worktrees import application_admission as admission
from agent_worktrees import config as cfg
from agent_worktrees import launch_preflight
from test_launch_preflight import _config, _forbid_create_mutations


def _required(config):
    repo = replace(config.default_repo, require_application_readiness=True)
    return replace(config, repos={config.repo_name: repo})


def _observation(source):
    return {
        "schema": "copilot-extensions.installation-repair", "version": 1,
        "projectRoot": str(Path(source).resolve()), "requiredSelection": True,
        "callerOwnedReadiness": True,
        "apply": False, "status": "observed", "requiredSatisfied": True,
        "interrupted": False, "selectionRefusal": None,
        "selected": [], "outcomes": [], "refusedOwners": {}, "discoveryFindings": [],
        "restarts": {"pending": []},
    }


def test_unselected_repository_does_not_resolve_or_run_machines(tmp_path, monkeypatch):
    monkeypatch.setattr(admission, "_selected_runtime", lambda **k: pytest.fail("unselected owner"))
    assert admission.new_application_error(_config(tmp_path), source=str(tmp_path)) is None


@pytest.mark.parametrize("stage,timeout", [
    ("profile selection", True), ("runtime selection", False),
    ("runtime selection", True), ("observation", True),
])
def test_refusal_identifies_the_failed_stage_without_raw_diagnostics(
    tmp_path, monkeypatch, stage, timeout,
):
    from agent_worktrees import copilot_plugin_profile as tool

    config = _required(_config(tmp_path))

    def fail(*args, **kwargs):
        if timeout:
            raise subprocess.TimeoutExpired(["private-argument"], 55, stderr="private-output")
        raise ValueError("private-output")

    monkeypatch.setattr(tool, "observe_for_admission", lambda *a, **k: "profile")
    monkeypatch.setattr(admission, "_selected_runtime", lambda **k: (["owner"], {}))
    monkeypatch.setattr(admission, "run_caller_owned", lambda *a, **k: pytest.fail("unexpected observation"))
    if stage == "profile selection":
        monkeypatch.setattr(tool, "observe_for_admission", fail)
    elif stage == "runtime selection":
        monkeypatch.setattr(admission, "_selected_runtime", fail)
    else:
        monkeypatch.setattr(admission, "run_caller_owned", fail)
    error = admission.new_application_error(config, source=config.default_repo.anchor)
    assert stage in error
    assert ("exceeded the launch admission budget" in error) is timeout
    assert "private-" not in error
    assert "before allocation" in error


@pytest.mark.parametrize("change", [
    {"requiredSatisfied": False}, {"interrupted": True},
    {"selectionRefusal": "required-inventory-changed"},
    {"restarts": {"pending": [{"unexpected": "module"}]}},
    {"projectRoot": "another-repository"}, {"requiredSelection": False},
    {"version": True}, {"apply": True}, {"status": "discovered"},
    {"callerOwnedReadiness": False},
    {"refusedOwners": {"example": "unavailable"}}, {"discoveryFindings": ["unknown"]},
    {"selected": ["example::app/core"]},
])
def test_unsatisfied_or_unqualified_result_refuses_new(tmp_path, monkeypatch, change):
    config = _required(_config(tmp_path))
    data = {**_observation(config.default_repo.anchor), **change}
    monkeypatch.setattr(admission, "_selected_runtime", lambda **k: (["owner"], {}))
    monkeypatch.setattr(admission, "run_caller_owned", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, json.dumps(data), "",
    ))
    error = admission.new_application_error(config, source=config.default_repo.anchor)
    assert error and "Application Readiness" in error


def test_qualified_empty_required_set_is_not_an_unobserved_pass(tmp_path, monkeypatch):
    config = _required(_config(tmp_path))
    calls = []
    monkeypatch.setattr(admission, "_selected_runtime", lambda **k: (["selected-owner"], {}))

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(_observation(config.default_repo.anchor)), "")

    monkeypatch.setattr(admission, "run_caller_owned", run)
    assert admission.new_application_error(config, source=config.default_repo.anchor) is None
    assert calls[0][0] == [
        "selected-owner", "installation-repair", "--project-root", config.default_repo.anchor,
        "--required", "--caller-owned-readiness",
    ]
    assert "--apply" not in calls[0][0]


def test_unselected_pending_restart_does_not_create_a_new_requirement(tmp_path):
    data = _observation(str(tmp_path))
    data["restarts"]["pending"] = [{"module": "unselected::optional/app"}]
    assert admission._ready(subprocess.CompletedProcess([], 0, json.dumps(data), ""), str(tmp_path))


@pytest.mark.parametrize("selection_seconds,observation_seconds,ready", [
    (14, 58, False), (14, 10, True), (60, 0, False),
])
def test_selection_observation_and_settlement_share_one_deadline(
    tmp_path, monkeypatch, selection_seconds, observation_seconds, ready,
):
    config = _required(_config(tmp_path))
    clock = [100.0]
    monkeypatch.setattr(admission.time, "monotonic", lambda: clock[0])
    budgets = []

    def select(*, project_root, budget):
        assert project_root == Path(config.default_repo.anchor)
        budgets.append(budget)
        clock[0] += selection_seconds
        return ["selected-owner"], {}

    def observe(argv, *, timeout, budget, **kwargs):
        assert budget is budgets[0]
        assert timeout == 55 - selection_seconds
        clock[0] += observation_seconds
        return subprocess.CompletedProcess(argv, 0, json.dumps(_observation(config.default_repo.anchor)), "")

    monkeypatch.setattr(admission, "_selected_runtime", select)
    monkeypatch.setattr(admission, "run_caller_owned", observe)
    result = admission.new_application_error(config, source=config.default_repo.anchor)
    assert (result is None) is ready
    assert budgets[0].deadline == 160


def test_knowledge_refusal_does_not_probe_applications(tmp_path, monkeypatch):
    monkeypatch.setattr(launch_preflight, "new_knowledge_error", lambda *a, **k: "knowledge unavailable")
    monkeypatch.setattr(admission, "new_application_preflight", lambda *a, **k: pytest.fail("later gate"))
    assert launch_preflight.new_authoring_error(
        _required(_config(tmp_path)), source=str(tmp_path),
    ) == "knowledge unavailable"


@pytest.mark.parametrize("preflight", [None, launch_preflight.LaunchPreflight()])
def test_source_only_or_recovery_has_no_received_application_read(tmp_path, monkeypatch, preflight):
    monkeypatch.setattr(admission, "new_application_preflight", lambda *a, **kw: pytest.fail("new-only read"))
    admission.require_received_applications(_required(_config(tmp_path)), str(tmp_path), preflight)


@pytest.mark.parametrize("json_output", [False, True])
def test_received_cleanup_failure_preserves_custody_and_allocated_identity(tmp_path, monkeypatch, json_output):
    import time
    from agent_procutil.owned_process import OwnedCleanupError
    from agent_worktrees import __main__ as main, resolve_launch_cli, tracking
    from test_worktree_creation_seed import _create_config, _stub_create_worktree_core_internals

    config = _required(_create_config(tmp_path))
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    records = tmp_path / "tracking"
    monkeypatch.setattr(main.cfg, "tracking_dir", lambda: records)
    monkeypatch.setattr(admission, "_selected_runtime", lambda **kw: (["owner"], {}))
    process, job, resource = object(), object(), object()
    retried = []
    failure = OwnedCleanupError(
        process, job, cleanup_resources=(resource,), cleanup_retry=retried.append)

    def observe(argv, *, cwd, **kwargs):
        if cwd == config.default_repo.anchor:
            return subprocess.CompletedProcess(argv, 0, json.dumps(_observation(cwd)), "")
        raise failure

    monkeypatch.setattr(admission, "run_caller_owned", observe)
    monkeypatch.setattr(main, "_build_launch_cmd", lambda *a, **kw: pytest.fail("unconfirmed cleanup launch"))
    with pytest.raises(OwnedCleanupError) as caught:
        args = main.build_parser().parse_args(["resolve", "--new", *(["--json"] if json_output else [])])
        if json_output:
            main.cmd_resolve(args)
        else:
            resolve_launch_cli._resolve_new_context(resolve_launch_cli.ResolveLaunchContext(config=config, args=args))
    assert caught.value is failure
    assert (failure.process, failure.job, failure.cleanup_resources) == (process, job, (resource,))
    allocated = tracking.list_records(records)
    assert len(allocated) == 1
    record = allocated[0]
    assert failure.worktree == {
        "id": record.worktree_id, "path": record.worktree_path,
        "branch": record.branch, "repo": record.repo,
    }
    deadline = time.monotonic() + 1
    failure.retry_cleanup(deadline=deadline)
    assert retried == [deadline]


def test_profile_observation_uses_the_same_budget_and_cannot_start_a_late_owner(tmp_path, monkeypatch):
    from agent_worktrees import copilot_plugin_profile as tool

    config = _required(_config(tmp_path))
    clock = [100.0]
    monkeypatch.setattr(admission.time, "monotonic", lambda: clock[0])

    def observe(config, *, deadline):
        assert deadline == 155
        clock[0] = 155
        return "checked-input"

    monkeypatch.setattr(tool, "observe_for_admission", observe)
    monkeypatch.setattr(admission, "_selected_runtime", lambda **kw: pytest.fail("late owner selection"))
    assert admission.new_application_error(config, source=config.default_repo.anchor)


@pytest.mark.parametrize("value", ["false", 1, None, {}])
def test_application_requirement_rejects_nonboolean_configuration(tmp_path, value):
    with pytest.raises(ValueError, match="require_application_readiness must be a boolean"):
        cfg._build_repo_config(
            {"require_application_readiness": value}, str(tmp_path), str(tmp_path / "trees"),
        )


@pytest.mark.parametrize("version,scope", [(None, 1), (True, 1), (2, 1), (1, None), (1, True), (1, 1)])
def test_support_comes_from_the_selected_runtime_not_a_payload(tmp_path, version, scope):
    environment = tmp_path / "selected" / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    package = environment / (
        "Lib/site-packages/agent_machines" if os.name == "nt" else
        "lib/python3.11/site-packages/agent_machines"
    )
    package.mkdir(parents=True)
    if version is not None:
        (package / "installation-repair-v1.json").write_text(json.dumps({
            "schema": admission.SCHEMA, "version": version, "required_selection": 1,
            "caller_owned_readiness": scope,
            "read_only": True, "target_identity": "project-root",
        }), encoding="utf-8")
    assert admission._supports_required_read(python) is (
        type(version) is int and version == 1 and type(scope) is int and scope == 1
    )


def test_same_cell_observation_qualifies_and_keeps_the_pinned_runtime(tmp_path, monkeypatch):
    from agent_worktrees import _peer_launch, peer_launch_adapter

    root = tmp_path / "cell" / "plugins" / "agent-worktrees"
    selected = tmp_path / "machines" / ".venv" / "Scripts" / "python.exe"
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "selected-receipt")
    monkeypatch.setattr(peer_launch_adapter, "validate_context", lambda: {
        "pluginRoot": str(root), "installReceipt": "selected-receipt",
    })
    observed = []

    def pinned(owner, own_root, context, peer):
        observed.append((owner, own_root, context, peer))
        return ["retained-selected-runtime"], selected

    monkeypatch.setattr(_peer_launch, "pinned_launch_prefix", pinned)
    monkeypatch.setattr(admission, "_supports_required_read", lambda python: python == selected)
    assert admission._selected_runtime()[0] == ["retained-selected-runtime"]
    assert observed == [("agent-worktrees", root, "selected-receipt", "agent-machines")]


@pytest.mark.parametrize("failure", ["unsupported-runtime", "refused-context"])
def test_missing_support_or_refused_context_never_runs_the_read_or_falls_back(
    tmp_path, monkeypatch, failure,
):
    from agent_worktrees import _peer_launch, peer_launch_adapter, reconcile

    config = _required(_config(tmp_path))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "selected-receipt")

    def context():
        if failure == "refused-context":
            raise _peer_launch.ContextRefused("invalid context")
        return {"pluginRoot": str(tmp_path), "installReceipt": "selected-receipt"}

    monkeypatch.setattr(peer_launch_adapter, "validate_context", context)
    monkeypatch.setattr(_peer_launch, "pinned_launch_prefix", lambda *a: (["retained"], tmp_path))
    monkeypatch.setattr(admission, "_supports_required_read", lambda python: False)
    monkeypatch.setattr(admission, "run_caller_owned", lambda *a, **k: pytest.fail("unqualified read"))
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda *a: pytest.fail("legacy fallback"))
    assert admission.new_application_error(config, source=config.default_repo.anchor)


@pytest.mark.parametrize("project_source", [None, "undeclared", "remote"])
def test_legacy_observation_keeps_the_owners_exact_completed_interpreter(
    tmp_path, monkeypatch, project_source,
):
    from agent_worktrees import _peer_launch, reconcile
    from types import SimpleNamespace

    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(reconcile, "_home", lambda: tmp_path / "home")
    if project_source == "remote":
        settings = project / ".github" / "copilot" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "extraKnownMarketplaces": {"copilot-extensions": {
                "source": {"source": "github", "repo": "ThomasMichon/copilot-extensions"},
            }},
        }), encoding="utf-8")
    payload, root = tmp_path / "payload", tmp_path / "runtime"
    python = root / "versions" / "selected" / ".venv" / "Scripts" / "python.exe"
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: payload)
    seen_budgets = []

    def installation(name, path, *, budget):
        seen_budgets.append(budget)
        return SimpleNamespace(
            actual_mode="legacy", status="ready", context=None, runtime_root=root,
        )

    monkeypatch.setattr(reconcile, "resolve_runtime_installation", installation)
    selected = []

    def select(context):
        selected.append(context)
        return python

    monkeypatch.setattr(_peer_launch, "_peer_python_inprocess", select)
    monkeypatch.setattr(_peer_launch, "retained_runtime_identity", lambda *a: pytest.fail("unavailable legacy bootstrap"))
    monkeypatch.setattr(admission, "_supports_required_read", lambda actual: actual == python)
    budget = admission.OwnedExecutionBudget(160, 5)
    command, environment = admission._selected_runtime(
        project_root=project if project_source is not None else None, budget=budget,
    )
    assert command == [str(python), "-I", "-B", "-X", "utf8", "-m", "agent_machines"]
    assert selected[0]["payloadRoot"] == str(payload)
    assert selected[0]["versionsRoot"] == str(root / "versions")
    assert seen_budgets == [budget]
    assert "COPILOT_PLUGIN_ROOT" not in environment


def _directory_machines_source(tmp_path, *, enabled=True, copied=False):
    config = _required(_config(tmp_path))
    project = Path(config.default_repo.anchor)
    home = tmp_path / "home"
    marketplace = tmp_path / "marketplace"
    payload = marketplace / "machines"
    payload.mkdir(parents=True)
    (payload / "plugin.json").write_text(json.dumps({
        "name": "agent-machines", "version": "1.0.0", "runtimeScope": "universal",
    }), encoding="utf-8")
    catalog = marketplace / ".github" / "plugin" / "marketplace.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps({
        "name": "copilot-extensions",
        "plugins": [{"name": "agent-machines", "source": "./machines"}],
    }), encoding="utf-8")
    settings = project / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "enabledPlugins": {"agent-machines@copilot-extensions": enabled},
        "extraKnownMarketplaces": {"copilot-extensions": {
            "source": {"source": "github", "repo": "ThomasMichon/copilot-extensions"},
        }},
    }), encoding="utf-8")
    local = settings.with_name("settings.local.json")
    local.write_text(json.dumps({
        "extraKnownMarketplaces": {"copilot-extensions": {
            "source": {"source": "directory", "path": "../marketplace"},
        }},
    }), encoding="utf-8")
    if copied:
        installed = home / ".copilot" / "installed-plugins" / "copilot-extensions" / "agent-machines"
        installed.mkdir(parents=True)
        (installed / "plugin.json").write_bytes((payload / "plugin.json").read_bytes())
    return config, home, payload, settings, local


@pytest.mark.parametrize("copied", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_directory_source_observation_uses_the_addressed_checkout(
    tmp_path, monkeypatch, copied, enabled,
):
    from agent_worktrees import _peer_launch, copilot_plugin_profile as tool, reconcile

    config, home, payload, settings, local = _directory_machines_source(
        tmp_path, enabled=enabled, copied=copied,
    )
    project = Path(config.default_repo.anchor)
    before = settings.read_bytes(), local.read_bytes()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(reconcile, "_home", lambda: home)
    selected = home / ".agent-machines" / "versions" / "selected" / ".venv" / "Scripts" / "python.exe"
    contexts = []

    def select(context):
        contexts.append(context)
        assert Path(context["payloadRoot"]) == payload
        assert Path(context["pluginRoot"]) == home / ".agent-machines"
        return selected

    monkeypatch.setattr(_peer_launch, "_peer_python_inprocess", select)
    monkeypatch.setattr(admission, "_supports_required_read", lambda python: python == selected)
    monkeypatch.setattr(tool, "observe_for_admission", lambda *a, **k: "profile")
    calls = []

    def observe(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(_observation(project)), "")

    monkeypatch.setattr(admission, "run_caller_owned", observe)
    assert admission.new_application_error(config, source=str(project)) is None
    assert contexts and calls[0][0][0] == str(selected)
    assert calls[0][1]["cwd"] == str(project)
    assert (settings.read_bytes(), local.read_bytes()) == before


@pytest.mark.parametrize("change", [
    "missing-source", "missing-plugin", "plugin-identity", "catalog-identity",
    "invalid-settings", "invalid-source",
])
def test_invalid_directory_source_never_falls_back_to_a_copied_payload(tmp_path, monkeypatch, change):
    from agent_worktrees import _peer_launch, copilot_plugin_profile as tool, reconcile

    config, home, payload, _settings, local = _directory_machines_source(tmp_path, copied=True)
    if change in {"missing-source", "invalid-source"}:
        local.write_text(json.dumps({
            "extraKnownMarketplaces": {"copilot-extensions": {"source": {
                "source": "directory" if change == "missing-source" else "unsupported",
                "path": "../missing",
            }}},
        }), encoding="utf-8")
    elif change == "missing-plugin":
        (payload / "plugin.json").unlink()
    elif change == "plugin-identity":
        (payload / "plugin.json").write_text('{"name":"another-plugin"}', encoding="utf-8")
    elif change == "catalog-identity":
        catalog = payload.parent / ".github" / "plugin" / "marketplace.json"
        catalog.write_text('{"name":"another-marketplace","plugins":[]}', encoding="utf-8")
    else:
        local.write_text("{", encoding="utf-8")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(reconcile, "_home", lambda: home)
    monkeypatch.setattr(tool, "observe_for_admission", lambda *a, **k: "profile")
    monkeypatch.setattr(_peer_launch, "_peer_python_inprocess", lambda *a: pytest.fail("stale copied source"))
    monkeypatch.setattr(admission, "run_caller_owned", lambda *a, **k: pytest.fail("unqualified observation"))
    error = admission.new_application_error(config, source=config.default_repo.anchor)
    assert error and "runtime selection" in error


def test_direct_new_refuses_before_create_mutations(tmp_path, monkeypatch, capfd):
    from agent_worktrees import __main__ as main

    config = _required(_config(tmp_path))
    monkeypatch.setattr(main.cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(launch_preflight, "new_knowledge_error", lambda *a, **k: None)
    monkeypatch.setattr(admission, "_selected_runtime", lambda **k: (_ for _ in ()).throw(
        ValueError("selected runtime is unsupported"),
    ))
    _forbid_create_mutations(monkeypatch)
    assert main.cmd_resolve(main.build_parser().parse_args(["resolve", "--json", "--new"])) != 0
    assert "Application Readiness" in json.loads(capfd.readouterr().out)["error"]
    assert not (tmp_path / "worktrees").exists()


@pytest.mark.parametrize("selector", ["--new", "--auto"])
def test_base_repo_new_refuses_before_launch_effects(tmp_path, monkeypatch, capfd, selector):
    from agent_worktrees import __main__ as main
    from agent_worktrees import state_root

    base = _required(_config(tmp_path))
    config = replace(base, repos={base.repo_name: replace(base.default_repo, base_repo=True)})
    monkeypatch.setattr(main.cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(state_root, "resolve_config_root", lambda *a, **k: state_root.ConfigRoot(
        str(tmp_path / "configuration"), "machine_local", config.repo_name, False, True,
    ))
    monkeypatch.setattr(launch_preflight, "new_knowledge_error", lambda *a, **k: None)
    calls = []

    def incomplete(config, *, source):
        calls.append(source)
        return launch_preflight.LaunchPreflight(policy_error="Application Readiness is incomplete")

    monkeypatch.setattr(admission, "new_application_preflight", incomplete)
    monkeypatch.setattr(main, "_build_launch_cmd", lambda *a, **k: pytest.fail("base New bypassed readiness"))
    assert main.cmd_resolve(main.build_parser().parse_args(["resolve", "--json", selector])) != 0
    assert calls == [config.default_repo.anchor]
    assert "Application Readiness" in json.loads(capfd.readouterr().out)["error"]


@pytest.mark.parametrize("arguments", [
    ["resolve", "--base", "--dry-run"], ["resolve", "--new", "--recovery", "--dry-run"],
])
def test_base_repo_existing_and_recovery_do_not_acquire_new_admission(
    tmp_path, monkeypatch, capfd, arguments,
):
    from agent_worktrees import __main__ as main
    from agent_worktrees import state_root

    base = _required(_config(tmp_path))
    config = replace(base, repos={base.repo_name: replace(base.default_repo, base_repo=True)})
    monkeypatch.setattr(state_root, "resolve_config_root", lambda *a, **k: state_root.ConfigRoot(
        str(tmp_path / "configuration"), "machine_local", config.repo_name, False, True,
    ))
    monkeypatch.setattr(admission, "new_application_preflight", lambda *a, **k: pytest.fail("existing New gate"))
    monkeypatch.setattr(main, "_build_launch_cmd", lambda *a, **k: ["copilot"])
    monkeypatch.setattr(main, "_build_env", lambda *a, **k: {})
    with main.output.stdout_to_stderr():
        result = main._resolve_base_repo(config, main.build_parser().parse_args(arguments))
    assert result == 0
    assert json.loads(capfd.readouterr().out)["action"] == "none"


@pytest.mark.parametrize("selector", ["--new", "--auto"])
def test_base_new_retains_checked_profile_for_launch_staging(tmp_path, monkeypatch, capfd, selector):
    from agent_worktrees import __main__ as main, local_cache_refresh, state_root

    base = _required(_config(tmp_path))
    config = replace(base, repos={base.repo_name: replace(base.default_repo, base_repo=True)})
    monkeypatch.setattr(main.cfg, "_ACTIVE_PROJECT", config.repo_name)
    monkeypatch.setattr(main.cfg, "load_config", lambda *a, **kw: config)
    monkeypatch.setattr(state_root, "resolve_config_root", lambda *a, **kw: state_root.ConfigRoot(
        str(tmp_path / "configuration"), "machine_local", config.repo_name, False, True))
    monkeypatch.setattr(launch_preflight, "new_knowledge_error", lambda *a, **kw: None)
    monkeypatch.setattr(admission, "new_application_preflight", lambda *a, **kw:
                        launch_preflight.LaunchPreflight(checked_profile="checked-input"))
    monkeypatch.setattr(main, "_build_launch_cmd", lambda *a, **kw: ["copilot"])
    monkeypatch.setattr(main, "_build_env", lambda *a, **kw: {})
    staged = []
    monkeypatch.setattr(local_cache_refresh, "prepare_admitted_launch", lambda *a, **kw:
                        staged.append((a, kw)))
    assert main.cmd_resolve(main.build_parser().parse_args(["resolve", "--json", selector])) == 0
    assert staged == [((config.default_repo.anchor,), {
        "defer_projections": False, "config": config, "checked_profile": "checked-input"})]
    assert json.loads(capfd.readouterr().out)["action"] == "exec"


def _native_machines_fixture(tmp_path, monkeypatch):
    owner_python = os.environ.get("APPLICATION_READINESS_TEST_PYTHON")
    if not owner_python:
        pytest.skip("select the contained Machines test interpreter for native owner consumption")
    from agent_procutil import no_window_kwargs

    root = Path(__file__).resolve().parents[3]
    prepared = subprocess.run(
        [owner_python, "-c",
         "import importlib.util,json,sys\nfrom pathlib import Path\n"
         "p=Path(sys.argv[1])/'plugins'/'agent-machines'/'tests'/'test_installation_repair.py'\n"
         "s=importlib.util.spec_from_file_location('fixture_support',p)\n"
         "m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
         "f=m.repair_fixture.__wrapped__(Path(sys.argv[2]))\n"
         "print(json.dumps([str(v) for v in f[:3]]))\n",
         str(root), str(tmp_path)],
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert prepared.returncode == 0, prepared.stderr
    home, project, _payload = map(Path, json.loads(prepared.stdout))
    environment = dict(os.environ)
    for key in ("HOME", "USERPROFILE", "AGENT_HOME"):
        environment[key] = str(home)
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    monkeypatch.setattr(admission, "_selected_runtime", lambda **k: (
        [owner_python, "-m", "agent_machines"], environment,
    ))
    return home, project


@pytest.mark.timeout(60)
@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.contract("agent_worktrees.required_application_admission")
def test_native_machines_read_is_consumed_without_repair(tmp_path, monkeypatch):
    home, project = _native_machines_fixture(tmp_path, monkeypatch)
    config = _required(_config(tmp_path))
    assert admission.new_application_error(config, source=str(project))
    assert not list(home.glob("*.ready"))
    for name in ("base", "child"):
        (home / f"agent-example-{name}.ready").write_text("ready", encoding="utf-8")
    assert admission.new_application_error(config, source=str(project)) is None


@pytest.mark.timeout(60)
@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.contract("agent_worktrees.required_application_admission")
@pytest.mark.parametrize("ready", [False, True])
@pytest.mark.parametrize("defer", [False, True])
def test_native_new_checks_the_prepared_worktree_not_only_the_anchor(tmp_path, monkeypatch, ready, defer):
    import shutil
    from agent_worktrees import __main__ as main, tracking
    from agent_worktrees.worktree_creation import LaunchSeedStagingFailure
    from test_worktree_creation_seed import _create_config, _stub_create_worktree_core_internals

    home, project = _native_machines_fixture(tmp_path, monkeypatch)
    settings = project / ".github" / "copilot" / "settings.json"
    target_settings = json.loads(settings.read_text(encoding="utf-8"))
    settings.write_text(json.dumps({
        **target_settings, "enabledPlugins": {"agent-example@fixture": False},
    }), encoding="utf-8")
    base = _create_config(tmp_path)
    config = replace(base, repos={base.repo_name: replace(
        base.default_repo, anchor=str(project), require_application_readiness=True)})
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    records = tmp_path / "tracking"
    monkeypatch.setattr(main.cfg, "tracking_dir", lambda: records)

    def create(_anchor, path, *args, **kwargs):
        shutil.copytree(project, path)
        (Path(path) / ".github" / "copilot" / "settings.json").write_text(
            json.dumps(target_settings), encoding="utf-8")

    monkeypatch.setattr(main.git_ops, "create_worktree", create)
    if ready:
        for name in ("base", "child"):
            (home / f"agent-example-{name}.ready").write_text("ready", encoding="utf-8")
        result = main._create_worktree_core(config, defer_projections=defer)
        assert result["launch"]["work_dir"] == result["worktree"]["path"]
    else:
        monkeypatch.setattr(main, "_build_launch_cmd", lambda *a, **kw: pytest.fail("unobserved target launch"))
        with pytest.raises(LaunchSeedStagingFailure, match="Application Readiness"):
            main._create_worktree_core(config, defer_projections=defer)
    assert len(tracking.list_records(records)) == 1
    assert len(list(home.glob("*.ready"))) == (2 if ready else 0)
