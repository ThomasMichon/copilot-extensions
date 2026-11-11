"""Refusal controls, not evidence of native suite installation."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time

import pytest

NATIVE = Path(__file__).parent / "native"
RUNTIMES = runpy.run_path(str(NATIVE / "manager_installed_suite_runtimes.py"))


@pytest.fixture
def fresh_fixture():
    return runpy.run_path(str(NATIVE / "manager_installed_suite_fixture.py"))


def test_fresh_suite_refuses_shared_host_before_creating_a_case(tmp_path, monkeypatch, fresh_fixture):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="disposable GitHub-hosted Windows VM"):
        fresh_fixture["prepare"](tmp_path / "never-created")
    assert not list(tmp_path.iterdir())


def test_fresh_environment_keeps_real_consumers_without_inherited_identity(
    tmp_path, monkeypatch, fresh_fixture,
):
    for key, value in {
        "AGENT_TEST_MODE": "1", "AGENT_WORKTREES_STATUS_MONITOR": "0",
        "AGENT_WORKTREES_NO_SELFPROVISION": "1", "WORKTREE_NO_UPDATE": "1",
        "COPILOT_OFFLINE": "true", "OPENAI_API_KEY": "foreign-provider",
        "TMUX": "foreign-mux", "SESSION_ID": "foreign-session",
    }.items():
        monkeypatch.setenv(key, value)
    env = fresh_fixture["_environment"](tmp_path, {})
    assert env["USERPROFILE"] == str(tmp_path / "h")
    assert env["COPILOT_HOME"] == str(tmp_path / "h" / ".copilot")
    assert not set(env) & {
        "AGENT_TEST_MODE", "AGENT_WORKTREES_STATUS_MONITOR", "AGENT_WORKTREES_NO_SELFPROVISION",
        "WORKTREE_NO_UPDATE", "COPILOT_OFFLINE", "OPENAI_API_KEY", "TMUX", "SESSION_ID",
    }


def test_fresh_acquisition_owns_its_git_settings_before_any_constructor(
    tmp_path, monkeypatch, fresh_fixture,
):
    import test_resume_pane_startup
    from plugin_test_containment import SANDBOX_ENV

    monkeypatch.setenv(SANDBOX_ENV, str(tmp_path))
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
    }

    def git(*args):
        return subprocess.run(
            ["git", *map(str, args)], check=True, capture_output=True, text=True,
            env=environment, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()

    git("init", tmp_path)
    outer_settings = tmp_path / ".github" / "copilot" / "settings.json"
    outer_settings.parent.mkdir(parents=True)
    outer_settings.write_text('{"extraKnownMarketplaces":{"outer-only":{}}}')
    for key in ("COPILOT_NATIVE_TEST_EXECUTABLE", "PSMUX_TEST_BIN"):
        executable = tmp_path / key
        executable.write_bytes(b"not executed")
        monkeypatch.setenv(key, str(executable))
    monkeypatch.setattr(fresh_fixture["suite"], "REPO", tmp_path)
    monkeypatch.setattr(fresh_fixture["suite"], "require_venue", lambda: None)
    monkeypatch.setattr(fresh_fixture["lifecycle"], "_require_mux_venue", lambda: None)
    monkeypatch.setattr(fresh_fixture["lifecycle"], "_identity", lambda: {"candidate_commit": "a" * 40})
    monkeypatch.setattr(test_resume_pane_startup, "_private_psmux", lambda *args: None)
    monkeypatch.setitem(fresh_fixture["payloads"]._PROVENANCE, "build", lambda *args, **kwargs: None)

    class BeforeConstruction(Exception):
        pass

    def before_construction(payload, root, env, project, diagnostics, label, run):
        assert Path(git("-C", project, "rev-parse", "--show-toplevel")).resolve() == project.resolve()
        assert git("-C", project, "symbolic-ref", "--short", "HEAD") == "main"
        assert json.loads((project.parent / "setup.json").read_text())["project_root"] == str(project)
        assert outer_settings.read_text() == '{"extraKnownMarketplaces":{"outer-only":{}}}'
        assert (diagnostics / "suite-project-init.stdout.txt").is_file()
        raise BeforeConstruction

    monkeypatch.setitem(
        fresh_fixture["prepare"].__globals__, "_install_engine", before_construction,
    )
    with pytest.raises(BeforeConstruction):
        fresh_fixture["prepare"](tmp_path / "qualification")


def test_fresh_adoption_registers_a_real_private_origin_and_publishes_main(
    tmp_path, fresh_fixture,
):
    import yaml

    profile, _portfolio, _payloads, _home = _selection(tmp_path)
    lifecycle = fresh_fixture["lifecycle"]
    project = tmp_path / lifecycle.PROJECT
    knowledge = tmp_path / lifecycle.KNOWLEDGE
    home = tmp_path / "h"
    binding = home / ("." + lifecycle.PROJECT) / "config.yaml"
    binding.parent.mkdir(parents=True)
    binding.write_text("{}")
    (tmp_path / "diagnostics").mkdir()
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
    }
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", USERPROFILE=str(home))
    registrations = []

    def run(argv, env, cwd, diagnostics, label, *, timeout):
        if argv[0] == "git":
            return subprocess.run(
                list(map(str, argv)), cwd=cwd, env=env, capture_output=True, text=True,
                check=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout.strip()
        if label == "register-project":
            origin = run(
                ["git", "-C", project, "remote", "get-url", "origin"],
                env, cwd, diagnostics, "origin", timeout=timeout,
            )
            assert Path(origin) == tmp_path / "project-origin.git"
            assert Path(origin).is_dir()
            registrations.append(origin)
        elif label == "knowledge-readiness":
            return json.dumps({"ready": True, "state_root": {"path": str(knowledge)}})
        elif label == "selected-native-launch":
            return json.dumps({
                "launch": yaml.safe_load(
                    (project / ".agent-worktrees" / "config.yaml").read_text(),
                )["launch"]["windows"],
                "agent": True,
            })
        else:
            assert label == "knowledge-register"
        return ""

    lifecycle._project(tmp_path, environment, "unexecuted-engine", "actual-cli", profile=profile, run=run)
    assert len(registrations) == 1
    clone = tmp_path / "consumer"
    run(
        ["git", "clone", "--branch", "main", registrations[0], clone],
        environment, tmp_path, tmp_path, "clone", timeout=15,
    )
    assert (clone / ".github" / "copilot" / "settings.json").read_bytes() == (
        project / ".github" / "copilot" / "settings.json"
    ).read_bytes()
    assert run(
        ["git", "-C", clone, "rev-parse", "HEAD"], environment, tmp_path, tmp_path, "head", timeout=15,
    ) == run(
        ["git", "-C", project, "rev-parse", "HEAD"],
        environment, tmp_path, tmp_path, "head", timeout=15,
    )


def test_harness_registration_preserves_the_full_profile_before_adoption(
    tmp_path, fresh_fixture,
):
    import yaml

    profile, _portfolio, _payloads, _home = _selection(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    arguments = fresh_fixture["installed"]._harness_registration(
        tmp_path / "installed-engine", project, tmp_path / "absent-core-fixture", profile=profile,
    )
    assert yaml.safe_load(
        (project / ".agent-worktrees" / "config.yaml").read_text(),
    ) == profile["repository_config"]
    assert json.loads(
        (project / ".github" / "copilot" / "settings.json").read_text(),
    ) == profile["settings"]
    assert "--no-agent" in arguments


def test_fresh_command_owner_has_a_finite_vm_lifetime_boundary(
    tmp_path, monkeypatch, fresh_fixture,
):
    from types import SimpleNamespace
    from agent_procutil import _caller_owned

    observed = []
    def run(argv, **options):
        observed.append((argv, options))
        return SimpleNamespace(returncode=0, stdout="constructed\n", stderr="")

    monkeypatch.setattr(_caller_owned, "run", run)
    owner = fresh_fixture["_run_owner"](time.monotonic() + 10)
    assert owner(["native-constructor"], {}, tmp_path, tmp_path, "construction", timeout=60) == (
        "constructed"
    )
    assert observed[0][1]["allow_daemon_breakaway"] is True
    assert 0 < observed[0][1]["timeout"] <= 10
    assert (tmp_path / "construction.stdout.txt").read_text() == "constructed\n"
    with pytest.raises(TimeoutError):
        fresh_fixture["_run_owner"](time.monotonic() - 1)(
            ["never-run"], {}, tmp_path, tmp_path, "expired",
        )
    assert len(observed) == 1


def test_fresh_command_failure_retains_the_original_constructor_diagnostic(
    tmp_path, monkeypatch, fresh_fixture,
):
    from types import SimpleNamespace
    from agent_procutil import _caller_owned

    monkeypatch.setattr(_caller_owned, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=7, stdout="constructor started\n",
        stderr="RuntimeError: installation requires live driver custody\n",
    ))
    with pytest.raises(RuntimeError, match="requires live driver custody") as failure:
        fresh_fixture["_run_owner"](time.monotonic() + 10)(
            ["native-constructor"], {}, tmp_path, tmp_path, "construction",
        )
    assert "construction exited 7" in str(failure.value)
    assert "constructor started" in str(failure.value)
    assert (tmp_path / "construction.stderr.txt").read_text().endswith("driver custody\n")


def test_fresh_engine_construction_enters_the_native_driver_for_each_private_root(
    tmp_path, monkeypatch, fresh_fixture,
):
    calls = []
    monkeypatch.setattr(fresh_fixture["shutil"], "which", lambda name: str(tmp_path / "pwsh.exe"))
    payload = tmp_path / "payload"
    environment = {"USERPROFILE": str(tmp_path / "home")}
    for root in (tmp_path / "bootstrap-engine", tmp_path / "home" / ".agent-worktrees"):
        fresh_fixture["_install_engine"](
            payload, root, environment, tmp_path, tmp_path, "engine",
            lambda *args: calls.append(args),
        )
    assert len(calls) == 2
    for arguments, root in zip(calls, (tmp_path / "bootstrap-engine",
                                       tmp_path / "home" / ".agent-worktrees")):
        argv, env, cwd, diagnostics, label = arguments
        assert argv[4] == payload / "scripts" / "runtime_install_driver.py"
        assert argv[argv.index("--root") + 1] == root
        assert argv[argv.index("--payload") + 1] == payload
        assert argv[argv.index("--action") + 1] == "provision"
        assert "--runtime-only" not in argv and "--bootstrap-python" in argv
        assert env is environment
        assert cwd == diagnostics == tmp_path and label == "engine"


@pytest.mark.parametrize(
    "defect", [None, "identity", "missing-runtime", "manager", "cli", "mux",
               "missing-action", "same-native", "missing-native"],
)
def test_fresh_result_requires_complete_candidate_bound_native_evidence(
    tmp_path, fresh_fixture, defect,
):
    profile, portfolio, _payloads, _home = _selection(tmp_path)
    setup = {
        "identity": {"candidate_commit": "a" * 40, "run_id": 7, "run_attempt": 2},
        "runtime_closure": {"runtimes": portfolio, "runtime_installation": "observed"},
        "closure": {"profile": profile, "copilot_sha256": "b" * 64},
        "mux_sha256": "c" * 64, "manager_origin": {"candidate_bytes_verified": True},
    }
    result = {
        "schema": fresh_fixture["RESULT_SCHEMA"], "mode": "fresh",
        "profile": "complete-installed-suite", "runtime_closure": deepcopy(setup["runtime_closure"]),
        "manager_origin": setup["manager_origin"], "identity": deepcopy(setup["identity"]),
        "copilot_sha256": "b" * 64, "mux_sha256": "c" * 64,
        "native_before_open": [100, 1000], "native_after_resume": [101, 1001],
        "conversation": "same-session", "worktree": "same-worktree",
        **{key: True for key in (
            "create", "open_existing", "detach_preserved", "normal_exit", "resume_continued",
        )},
    }
    if defect == "identity":
        result["identity"]["run_attempt"] += 1
    elif defect == "missing-runtime":
        del result["runtime_closure"]["runtimes"]["agent-machines"]
    elif defect == "manager":
        result["manager_origin"] = {}
    elif defect in {"cli", "mux"}:
        result[{"cli": "copilot", "mux": "mux"}[defect] + "_sha256"] = "d" * 64
    elif defect == "missing-action":
        del result["resume_continued"]
    elif defect == "same-native":
        result["native_after_resume"] = result["native_before_open"]
    elif defect == "missing-native":
        del result["native_before_open"]
    if defect is None:
        fresh_fixture["validate_evidence"](setup, result)
    else:
        with pytest.raises(ValueError):
            fresh_fixture["validate_evidence"](setup, result)


def _selection(tmp_path):
    home = tmp_path / "home"
    inventory = {
        "agent-worktrees": {"runtime_scope": "universal", "version": "1.0"},
        "agent-machines": {"runtime_scope": "universal", "version": "1.0"},
        "example-service": {"runtime_scope": "machine-gated", "version": "1.0"},
        "example-guidance": {"runtime_scope": "none", "version": "1.0"},
    }
    profile = {
        "repository_config": {"require_application_readiness": True},
        "inventory": inventory,
        "settings": {
            "extraKnownMarketplaces": {"example": {"source": {}}},
            "enabledPlugins": {name + "@example": True for name in inventory},
        },
    }
    payloads = {name: {"path": str(home / "payloads" / name)} for name in inventory}
    portfolio = {
        name: {
            "eligible": True, "status": "", "root": str(home / ("." + name)),
            "payload": payloads[name]["path"],
        }
        for name, row in inventory.items() if row["runtime_scope"] != "none"
    }
    return profile, portfolio, payloads, home


@pytest.mark.parametrize(
    "defect", [None, "missing", "extra", "gated", "failed", "foreign-root", "foreign-payload",
               "readiness-disabled", "reduced-profile", "payload-only", "payload-only-module",
               "payload-only-root", "payload-only-eligible", "payload-only-status",
               "payload-only-foreign", "payload-only-missing-runtime", "payload-only-unknown"],
)
def test_runtime_observation_requires_the_complete_eligible_private_portfolio(tmp_path, defect):
    profile, portfolio, payloads, home = _selection(tmp_path)
    if defect and defect.startswith("payload-only"):
        row = {
            "eligible": False, "status": "payload-only", "root": None,
            "payload": payloads["example-guidance"]["path"],
        }
        portfolio["example-guidance"] = row
        if defect == "payload-only-module":
            row["status"] = "SKIPPED (payload-only)"
        elif defect == "payload-only-root":
            row["root"] = str(home / ".example-guidance")
        elif defect == "payload-only-eligible":
            row["eligible"] = True
        elif defect == "payload-only-status":
            row["status"] = "source dir not found"
        elif defect == "payload-only-foreign":
            row["payload"] = str(tmp_path / "foreign-guidance")
        elif defect == "payload-only-missing-runtime":
            del portfolio["agent-machines"]
        elif defect == "payload-only-unknown":
            portfolio["unknown-guidance"] = portfolio.pop("example-guidance")
    if defect == "missing":
        del portfolio["agent-machines"]
    elif defect == "extra":
        portfolio["example-guidance"] = deepcopy(portfolio["agent-machines"])
    elif defect == "gated":
        portfolio["example-service"]["eligible"] = False
    elif defect == "failed":
        portfolio["agent-machines"]["status"] = "constructor failed"
    elif defect == "foreign-root":
        portfolio["agent-machines"]["root"] = str(tmp_path / "host-machines")
    elif defect == "foreign-payload":
        portfolio["agent-machines"]["payload"] = str(tmp_path / "other-payload")
    elif defect == "readiness-disabled":
        profile["repository_config"]["require_application_readiness"] = False
    elif defect == "reduced-profile":
        del profile["settings"]["enabledPlugins"]["example-guidance@example"]
    if defect in (None, "payload-only", "payload-only-module"):
        RUNTIMES["validate_portfolio"](profile, portfolio, payloads, home)
    else:
        with pytest.raises(ValueError):
            RUNTIMES["validate_portfolio"](profile, portfolio, payloads, home)


@pytest.mark.parametrize("defect", [None, "host-prefix", "source-import", "wrong-version"])
def test_runtime_observation_requires_the_actual_selected_import_and_version(tmp_path, defect):
    prefix = tmp_path / "versions" / "1.0.dev1" / ".venv"
    origin = {
        "prefix": str(prefix),
        "module": str(prefix / "Lib" / "site-packages" / "example" / "__init__.py"),
        "version": "1.0.dev1",
    }
    if defect == "host-prefix":
        origin["prefix"] = str(tmp_path / "host-venv")
    elif defect == "source-import":
        origin["module"] = str(tmp_path / "checkout" / "example" / "__init__.py")
    elif defect == "wrong-version":
        origin["version"] = "1.0.dev2"
    if defect is None:
        RUNTIMES["validate_origin"](origin, prefix, "1.0-dev1")
    else:
        with pytest.raises(ValueError):
            RUNTIMES["validate_origin"](origin, prefix, "1.0-dev1")


def test_runtime_construction_refuses_the_shared_host_before_reading_or_running(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(NATIVE))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    def forbidden(*_args, **_kwargs):
        pytest.fail("No source, constructor or update command may run on the shared host")

    with pytest.raises(RuntimeError, match="disposable GitHub-hosted Windows VM"):
        RUNTIMES["install_runtime_closure"](
            tmp_path / "absent-artifact", "a" * 40, payload_closure={},
            manager=tmp_path / "absent-manager", copilot=tmp_path / "absent-cli",
            environment={}, project=tmp_path / "project",
            diagnostics=tmp_path / "diagnostics", deadline=0, run=forbidden,
        )


def test_readiness_failure_details_reuse_only_the_admitted_readonly_owner(
    tmp_path, monkeypatch, capsys,
):
    from contextlib import contextmanager
    from types import ModuleType, SimpleNamespace

    calls = []
    selected = SimpleNamespace(qualified_id="fixture::example/runtime",
                               owner=SimpleNamespace(owner_id="fixture::example"))
    ignored = SimpleNamespace(qualified_id="fixture::other/runtime")
    owner = SimpleNamespace(refusal=None)
    bound_owner = SimpleNamespace(refusal=None, execution_bindings={"readiness": "admitted"})
    host = SimpleNamespace(report=SimpleNamespace(modules=[ignored, selected]),
                           bindings={"fixture::example": owner})

    @contextmanager
    def caller_owned(enabled):
        assert enabled is True
        yield

    def admit(project, *, readiness_only):
        assert project == tmp_path and readiness_only is True
        return host

    def invoke(module, binding, platform, *, readonly, timeout):
        assert module is selected and binding is bound_owner and platform == "windows"
        assert readonly is True and 0 < timeout <= 10
        calls.append(module.qualified_id)
        return SimpleNamespace(
            status="exited", exit_code=1,
            stdout=b'{"state":"failed","detail":"actual owner reason"}',
        )

    def bind(admitted, modules, platform):
        assert admitted is host and modules == [selected] and platform == "windows"
        calls.append("bound-readiness")
        return {}, {"fixture::example": bound_owner}

    definitions = {
        "agent_machines.installation_repair": {
            "_binding_inputs": bind, "_invoke": invoke, "current_platform": lambda: "windows",
        },
        "agent_machines.installation_repair_admission": {"admit_host": admit},
        "agent_machines.installation_repair_process": {"caller_owned_readiness": caller_owned},
    }
    for name, attributes in definitions.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys, "argv", ["diagnostic", str(tmp_path), json.dumps([selected.qualified_id])])
    exec(RUNTIMES["_READINESS_DETAILS"], {})
    assert calls == ["bound-readiness", selected.qualified_id]
    result = json.loads(capsys.readouterr().out)
    assert result["exitCode"] == 1
    assert json.loads(result["stdout"])["detail"] == "actual owner reason"


def test_fresh_index_roles_are_readable_without_runtime_yaml_dependencies(tmp_path, fresh_fixture):
    profile, _portfolio, _payloads, _home = _selection(tmp_path)
    (tmp_path / "h").mkdir()
    project = tmp_path / fresh_fixture["lifecycle"].PROJECT
    project.mkdir()
    fresh_fixture["_roles"](tmp_path, profile)
    reader = (
        fresh_fixture["suite"].REPO / "plugins" / "agent-index" / "scripts"
        / "resolve_effective_config.py"
    )
    configuration = project / ".copilot-extensions" / "agent-index" / "config.yaml"
    program = (
        "import importlib.util,json,runpy,sys;from pathlib import Path;"
        "assert importlib.util.find_spec('yaml') is None;"
        "reader=runpy.run_path(sys.argv[1]);"
        "state,data=reader['_load_yaml_mapping'](Path(sys.argv[2]));"
        "assert state=='ready',(state,data);"
        "assert len(data['indexers'])==2;"
        "print(json.dumps(data))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", program, str(reader), str(configuration)],
        check=True, capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert {"machine": "tty-fixture"} in json.loads(result.stdout)["indexers"]


@pytest.mark.parametrize(
    "defect", [None, "update-refused", "missing-runtime", "wrong-version", "foreign-machines",
               "readiness-refused", "diagnostic-refused", "diagnostic-custody",
               "changed-transport", "changed-cli", "changed-payload"],
)
def test_runtime_construction_keeps_update_discovery_and_readiness_in_one_owned_recipe(
    tmp_path, monkeypatch, defect,
):
    monkeypatch.syspath_prepend(str(NATIVE))
    import manager_suite_fixture

    monkeypatch.setattr(manager_suite_fixture, "require_venue", lambda: None)
    controls = runpy.run_path(str(Path(__file__).with_name("test_installed_suite_payloads.py")))
    artifact = controls["_artifact"](tmp_path)
    for name in ("agent-worktrees", "agent-machines"):
        payload = artifact / "plugins" / name
        (payload / "pyproject.toml").write_text(f'[project]\nname="{name}"\nversion="1.0"\n')
        (payload / "payload-invocation.json").write_text(json.dumps({"module": name.replace("-", "_")}))
    controls["_seal"](artifact)
    project = tmp_path / "project"
    home = tmp_path / "home"
    template = tmp_path / "suite-checkout-template"
    (template / "info").mkdir(parents=True)
    (template / "info" / "attributes").write_bytes(b"* -text\n")
    transport = {
        "repository": str(tmp_path / "suite-transport.git"),
        "url": "http://127.0.0.1:12345/suite-transport.git",
        "commit": "b" * 40, "tree": "c" * 40, "checkout_template": str(template),
    }
    profile = controls["_SUITE"]["selected_profile"](
        artifact, controls["_COMMIT"], {"require_application_readiness": True}, transport=transport,
    )
    settings = project / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps(profile["settings"]))
    payloads = {}
    for name in profile["inventory"]:
        payload = home / "payloads" / name
        shutil.copytree(artifact / "plugins" / name, payload)
        payloads[name] = {"path": str(payload)}
    cli = tmp_path / "copilot.exe"
    cli.write_bytes(b"selected actual executable")
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: str(cli))
    calls = []
    portfolio = {}
    for name in ("agent-worktrees", "agent-machines"):
        root = home / ("." + name)
        python = root / "versions" / "1.0" / ".venv" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.touch()
        (root / "current-version").write_text("1.0")
        portfolio[name] = {
            "root": str(root), "payload": payloads[name]["path"],
            "eligible": True, "status": "",
        }
    engine = home / ".agent-worktrees" / "versions" / "1.0" / ".venv" / "Scripts" / "python.exe"
    portfolio["example-guidance"] = {
        "eligible": False, "status": "payload-only", "root": None,
        "payload": payloads["example-guidance"]["path"],
    }
    from agent_procutil.owned_process import OwnedCleanupError

    custody = OwnedCleanupError(None)
    refused = defect in {"readiness-refused", "diagnostic-refused", "diagnostic-custody"}

    def run(argv, environment, cwd, diagnostics, label, *, timeout):
        assert 0 < timeout <= 120
        assert cwd == project
        calls.append(label)
        if label.startswith("suite-runtime-transport-"):
            commit = "d" * 40 if defect == "changed-transport" and label.endswith("-after") else transport["commit"]
            return commit + "\n" + transport["tree"]
        if label == "suite-manager-source":
            assert argv[-4:] == ["--repo", transport["url"], "--ref", "main"]
            return ""
        if label == "suite-runtime-update":
            assert argv[1:] == ["-I", "-B", "-m", "worktree_manager", "update", "--project", project.name]
            assert environment["GIT_TEMPLATE_DIR"] == str(template)
            if defect == "update-refused":
                raise RuntimeError("normal updater refused")
            return ""
        if label == "suite-current-engine":
            return json.dumps([str(engine), "-I", "-B", "-m", "agent_worktrees"])
        if label == "suite-runtime-portfolio":
            assert argv[0] == engine
            observed = deepcopy(portfolio)
            if defect == "missing-runtime":
                del observed["agent-machines"]
            return json.dumps(observed)
        if label.startswith("suite-runtime-origin-"):
            prefix = Path(argv[0]).parent.parent
            return json.dumps({
                "prefix": str(prefix), "module": str(prefix / "Lib" / "site-packages" / "example.py"),
                "version": "2.0" if defect == "wrong-version" else "1.0",
            })
        if label == "suite-readiness-failures":
            machines = home / ".agent-machines" / "versions" / "1.0" / ".venv" / "Scripts" / "python.exe"
            assert argv[0] == str(machines)
            assert argv[4] == RUNTIMES["_READINESS_DETAILS"]
            assert json.loads(argv[-1]) == ["fixture::agent-machines/runtime"]
            if defect == "diagnostic-refused":
                raise RuntimeError("controlled diagnostic failure")
            if defect == "diagnostic-custody":
                raise custody
            return ""
        assert label == "suite-machines-readiness"
        if defect == "changed-cli":
            cli.write_bytes(b"substituted executable")
        elif defect == "changed-payload":
            (Path(payloads["agent-machines"]["path"]) / "README.md").write_text("changed")
        machines = home / ".agent-machines" / "versions" / "1.0" / ".venv" / "Scripts" / "python.exe"
        return json.dumps({
            "prefix": [str(tmp_path / "foreign.exe" if defect == "foreign-machines" else machines)],
            "accepted": not refused, "readiness": {
                "status": "observed",
                "outcomes": [{"module": "fixture::agent-machines/runtime",
                              "observedBefore": {"state": "failed" if refused else "ready"}}],
            },
        })

    arguments = {
        "payload_closure": {
            "candidate_commit": controls["_COMMIT"], "profile": profile, "payloads": payloads,
            "transport": transport, "copilot_sha256": hashlib.sha256(cli.read_bytes()).hexdigest(),
        },
        "manager": home / ".worktree-manager" / "manager.exe", "copilot": cli,
        "environment": {"HOME": str(home), "USERPROFILE": str(home), "PATH": str(cli.parent)},
        "project": project, "diagnostics": tmp_path, "deadline": time.monotonic() + 120,
        "run": run,
    }
    if defect is None:
        result = RUNTIMES["install_runtime_closure"](artifact, controls["_COMMIT"], **arguments)
        assert result["runtime_installation"] == "observed"
        assert set(result["runtimes"]) == {"agent-worktrees", "agent-machines"}
        assert result["upgrade"] == "not-exercised"
        assert calls.index("suite-runtime-update") < calls.index("suite-runtime-portfolio")
        assert calls[-1] == "suite-runtime-transport-after"
    elif defect == "diagnostic-custody":
        with pytest.raises(OwnedCleanupError) as failure:
            RUNTIMES["install_runtime_closure"](artifact, controls["_COMMIT"], **arguments)
        assert failure.value is custody
    else:
        with pytest.raises((ValueError, RuntimeError)) as failure:
            RUNTIMES["install_runtime_closure"](artifact, controls["_COMMIT"], **arguments)
        if refused:
            assert "did not satisfy required application readiness" in str(failure.value)
