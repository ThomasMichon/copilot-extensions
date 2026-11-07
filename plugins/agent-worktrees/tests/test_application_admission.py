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
        "apply": False, "status": "observed", "requiredSatisfied": True,
        "interrupted": False, "selectionRefusal": None,
        "selected": [], "outcomes": [], "refusedOwners": {}, "discoveryFindings": [],
        "restarts": {"pending": []},
    }


def test_unselected_repository_does_not_resolve_or_run_machines(tmp_path, monkeypatch):
    monkeypatch.setattr(admission, "_selected_runtime", lambda: pytest.fail("unselected owner"))
    assert admission.new_application_error(_config(tmp_path), source=str(tmp_path)) is None


@pytest.mark.parametrize("change", [
    {"requiredSatisfied": False}, {"interrupted": True},
    {"selectionRefusal": "required-inventory-changed"},
    {"restarts": {"pending": [{"unexpected": "module"}]}},
    {"projectRoot": "another-repository"}, {"requiredSelection": False},
    {"version": True}, {"apply": True}, {"status": "discovered"},
    {"refusedOwners": {"example": "unavailable"}}, {"discoveryFindings": ["unknown"]},
    {"selected": ["example::app/core"]},
])
def test_unsatisfied_or_unqualified_result_refuses_new(tmp_path, monkeypatch, change):
    config = _required(_config(tmp_path))
    data = {**_observation(config.default_repo.anchor), **change}
    monkeypatch.setattr(admission, "_selected_runtime", lambda: (["owner"], {}))
    monkeypatch.setattr(admission, "run_caller_owned", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, json.dumps(data), "",
    ))
    error = admission.new_application_error(config, source=config.default_repo.anchor)
    assert error and "Application Readiness" in error


def test_qualified_empty_required_set_is_not_an_unobserved_pass(tmp_path, monkeypatch):
    config = _required(_config(tmp_path))
    calls = []
    monkeypatch.setattr(admission, "_selected_runtime", lambda: (["selected-owner"], {}))

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(_observation(config.default_repo.anchor)), "")

    monkeypatch.setattr(admission, "run_caller_owned", run)
    assert admission.new_application_error(config, source=config.default_repo.anchor) is None
    assert calls[0][0] == [
        "selected-owner", "installation-repair", "--project-root", config.default_repo.anchor, "--required",
    ]
    assert "--apply" not in calls[0][0]


def test_unselected_pending_restart_does_not_create_a_new_requirement(tmp_path):
    data = _observation(str(tmp_path))
    data["restarts"]["pending"] = [{"module": "unselected::optional/app"}]
    assert admission._ready(subprocess.CompletedProcess([], 0, json.dumps(data), ""), str(tmp_path))


def test_knowledge_refusal_does_not_probe_applications(tmp_path, monkeypatch):
    monkeypatch.setattr(launch_preflight, "new_knowledge_error", lambda *a, **k: "knowledge unavailable")
    monkeypatch.setattr(admission, "new_application_error", lambda *a, **k: pytest.fail("later gate"))
    assert launch_preflight.new_authoring_error(
        _required(_config(tmp_path)), source=str(tmp_path),
    ) == "knowledge unavailable"


@pytest.mark.parametrize("value", ["false", 1, None, {}])
def test_application_requirement_rejects_nonboolean_configuration(tmp_path, value):
    with pytest.raises(ValueError, match="require_application_readiness must be a boolean"):
        cfg._build_repo_config(
            {"require_application_readiness": value}, str(tmp_path), str(tmp_path / "trees"),
        )


@pytest.mark.parametrize("version", [None, True, 2, 1])
def test_support_comes_from_the_selected_runtime_not_a_payload(tmp_path, version):
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
            "read_only": True, "target_identity": "project-root",
        }), encoding="utf-8")
    assert admission._supports_required_read(python) is (type(version) is int and version == 1)


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


def test_legacy_observation_uses_owner_selection_and_retained_build(tmp_path, monkeypatch):
    from agent_worktrees import _peer_launch, reconcile
    from types import SimpleNamespace

    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    payload, root = tmp_path / "payload", tmp_path / "runtime"
    python = root / "versions" / "selected" / ".venv" / "Scripts" / "python.exe"
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: payload)
    monkeypatch.setattr(reconcile, "resolve_runtime_installation", lambda name, path: SimpleNamespace(
        actual_mode="legacy", status="ready", context=None, runtime_root=root,
    ))
    selected = []

    def select(context):
        selected.append(context)
        return python

    monkeypatch.setattr(_peer_launch, "_peer_python_inprocess", select)
    monkeypatch.setattr(_peer_launch, "retained_runtime_identity", lambda *a: "retained-build")
    pinned = []

    def prefix(actual_root, actual_python, module, *, runtime_identity):
        pinned.append((actual_root, actual_python, module, runtime_identity))
        return ["retained-legacy-owner"]

    monkeypatch.setattr(_peer_launch, "retained_runtime_prefix", prefix)
    monkeypatch.setattr(admission, "_supports_required_read", lambda actual: actual == python)
    command, environment = admission._selected_runtime()
    assert command == ["retained-legacy-owner"]
    assert selected[0]["payloadRoot"] == str(payload)
    assert selected[0]["versionsRoot"] == str(root / "versions")
    assert pinned == [(root, python, "agent_machines", "retained-build")]
    assert "COPILOT_PLUGIN_ROOT" not in environment


def test_direct_new_refuses_before_create_mutations(tmp_path, monkeypatch, capfd):
    from agent_worktrees import __main__ as main

    config = _required(_config(tmp_path))
    monkeypatch.setattr(main.cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(launch_preflight, "new_knowledge_error", lambda *a, **k: None)
    monkeypatch.setattr(admission, "_selected_runtime", lambda: (_ for _ in ()).throw(
        ValueError("selected runtime is unsupported"),
    ))
    _forbid_create_mutations(monkeypatch)
    assert main.cmd_resolve(main.build_parser().parse_args(["resolve", "--json", "--new"])) != 0
    assert "Application Readiness" in json.loads(capfd.readouterr().out)["error"]
    assert not (tmp_path / "worktrees").exists()


@pytest.mark.timeout(60)
@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.contract("agent_worktrees.required_application_admission")
def test_native_machines_read_is_consumed_without_repair(tmp_path, monkeypatch):
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
    monkeypatch.setattr(admission, "_selected_runtime", lambda: (
        [owner_python, "-m", "agent_machines"], environment,
    ))
    config = _required(_config(tmp_path))
    assert admission.new_application_error(config, source=str(project))
    assert not list(home.glob("*.ready"))
    for name in ("base", "child"):
        (home / f"agent-example-{name}.ready").write_text("ready", encoding="utf-8")
    assert admission.new_application_error(config, source=str(project)) is None
