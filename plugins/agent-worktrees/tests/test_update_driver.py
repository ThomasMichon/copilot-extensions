"""The public update boundary queues instead of running an installer in its caller."""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from agent_worktrees import update_cli, update_driver_cli as boundary


def _args(**values):
    fields = dict(
        force=False, no_anchor_sync=False, no_prune_pivots=False,
        recreate_venv=False, skip_modules=None, no_manager=False,
        detach=False, wait=False, supervised_debug=False, request_status=None,
        auth_report=False, json=False, force_runtime=None,
    )
    return argparse.Namespace(**{**fields, **values})


@pytest.fixture(autouse=True)
def _prepared_fixture_import_graph(monkeypatch):
    import json
    import sys
    from pathlib import Path

    monkeypatch.setenv("UPDATE_DRIVER_TEST_IMPORT_PATH", json.dumps([
        str(Path(path).resolve()) for path in sys.path if path
    ]))


def _run_runtime_owned(monkeypatch, argv, environment, *, timeout=20):

    with monkeypatch.context() as selected:
        for key, value in environment.items():
            selected.setenv(key, value)
        from agent_procutil.owned_process import run_owned

        return run_owned(list(argv), timeout=timeout)


def _write_fixture_project(scope):
    import json

    config = scope / ".example" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({
        "repos": {"example": {"anchor": str(scope / "anchor")}},
    }), encoding="utf-8")


def _enqueue_fixture_update(monkeypatch, scope, **values):
    import json
    import os
    from pathlib import Path
    import update_driver
    from agent_worktrees import __main__ as engine, project_update_controller

    fixture = Path(__file__).parent / "fixtures" / "update_driver_context.py"
    original = update_driver.runtime_command

    def command(module, args=()):
        argv, environment = original(module, args)
        if module == "agent_worktrees":
            if os.environ.get("UPDATE_DRIVER_TEST_RUNTIME_MODE", "source") == "prepared":
                prepared = json.loads(Path(os.environ["UPDATE_DRIVER_TEST_PREPARED_RUNTIME"]).read_text(encoding="utf-8"))
                prefix, environment = prepared["engine"]["script"]
            else:
                prefix = [argv[0], str(fixture.resolve())]
            argv = (*prefix, "execute", *args)
        return argv, environment

    with monkeypatch.context() as selected:
        selected.setattr(update_driver, "runtime_command", command)
        selected.setattr(update_driver, "ensure_worker", lambda *a, **kw: None)
        selected.setattr(boundary.cfg, "active_project", lambda: "example")
        selected.setattr(boundary.cfg, "install_dir", lambda: scope / ".agent-worktrees")
        selected.setattr(project_update_controller, "probe_for_submission", lambda _args, **_kwargs: False)
        selected.setattr(engine, "_INVOCATION_CWD", scope / "invocation")
        return boundary.cmd_update_request(_args(detach=True, **values))


@pytest.fixture
def queued(monkeypatch, tmp_path):
    import update_driver
    from agent_worktrees import project_update_controller
    from update_driver.authority import UpdateAuthority

    seen = {}
    request = SimpleNamespace(
        id="synthetic-request", state="pending", exit_code=None, error=None,
        log_path=tmp_path / "update.log",
    )

    def enqueue(spec, *, runner):
        seen["spec"], seen["runner"] = spec, runner
        request.spec = spec
        return request

    def wait(request_id, **kwargs):
        seen["wait"] = request_id
        return SimpleNamespace(**{**vars(request), "state": "completed", "exit_code": 4})

    def forbidden(*args, **kwargs):
        pytest.fail("ordinary caller entered the update executor")

    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(update_driver, "current_authority", lambda **_kwargs: UpdateAuthority())
    monkeypatch.setattr(update_driver, "enqueue", enqueue)
    monkeypatch.setattr(update_driver, "runtime_command", lambda module, args=(): (
        (str(tmp_path / "python.exe"), "-m", module, *args), {},
    ))
    monkeypatch.setattr(update_driver, "ensure_worker", lambda *args, **kwargs: seen.update(worker=args))
    monkeypatch.setattr(update_driver, "wait", wait)
    monkeypatch.setattr(boundary.cfg, "active_project", lambda: "example")
    monkeypatch.setattr(boundary.cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(project_update_controller, "probe_for_submission", lambda _args, **_kwargs: False)
    from agent_worktrees import __main__ as core
    monkeypatch.setattr(core, "_INVOCATION_CWD", None)
    monkeypatch.setattr(update_cli, "cmd_update", forbidden)
    return seen, request


def test_fresh_source_recovery_uses_existing_custody_without_requeue(tmp_path, monkeypatch):
    import update_driver

    calls = []

    class Custody:
        request_id = "source-request"
        root = tmp_path

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def assert_owned(self):
            calls.append("owned")

    monkeypatch.setattr(update_driver, "borrowed_custody", Custody)
    monkeypatch.setattr(update_driver, "status", lambda *args, **kw: SimpleNamespace(
        state="running", spec=SimpleNamespace(backend="source-recovery", context=(), cwd=str(tmp_path)),
    ))
    monkeypatch.setattr(update_driver, "enqueue", lambda *args, **kw: pytest.fail("no second request"))
    monkeypatch.setattr(update_cli, "cmd_update", lambda args: calls.append("native-suite") or 17)
    assert boundary.cmd_update_request(_args(no_manager=True)) == 17
    assert calls == ["owned", "native-suite"]


def test_ordinary_update_queues_before_manager_discovery(queued):
    seen, request = queued
    assert boundary.cmd_update_request(_args(force=True)) == 4
    assert seen["spec"].argv == ("--force",)
    assert seen["spec"].project == "example"
    assert seen["runner"].argv[-4:] == ("--project", "example", "update", "--force")
    assert seen["wait"] == request.id


def test_project_directory_relative_home_survives_queue_and_borrowed_reentry(
    tmp_path, monkeypatch,
):
    from dataclasses import replace
    from pathlib import Path
    import sys
    import update_driver
    from update_driver.authority import UpdateAuthority
    from agent_worktrees import __main__ as engine, project_update_controller

    caller, project = tmp_path / "caller", tmp_path / "project"
    caller.mkdir()
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("AGENT_HOME", "state")
    monkeypatch.setattr(engine, "_INVOCATION_CWD", caller)
    monkeypatch.setattr(boundary.cfg, "active_project", lambda: "example")
    monkeypatch.setattr(update_driver, "current_authority", lambda **_kw: UpdateAuthority())
    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(update_driver, "ensure_worker", lambda *_a, **_kw: None)
    monkeypatch.setattr(project_update_controller, "probe_for_submission", lambda *_a, **_kw: False)
    monkeypatch.setattr(update_driver, "runtime_command", lambda module, args=(): (
        (sys.executable, "-m", module, *args), {},
    ))
    enqueue = update_driver.enqueue
    rows = []

    def capture(spec, *, runner):
        row = enqueue(spec, runner=runner, root=tmp_path / "queue")
        rows.append(row)
        return row

    monkeypatch.setattr(update_driver, "enqueue", capture)
    source = str(boundary.cfg.install_dir().resolve())
    assert boundary.cmd_update_request(_args(detach=True)) == 0
    row, = rows
    assert dict(row.spec.context)["AGENT_HOME"] == str(project / "state")
    assert row.spec.source == source
    assert Path(row.spec.cwd) == caller

    class Custody:
        request_id, root = row.id, tmp_path / "queue"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def assert_owned(self):
            pass

    monkeypatch.setattr(update_driver, "borrowed_custody", Custody)
    monkeypatch.setattr(update_driver, "status", lambda *_a, **_kw: replace(row, state="running"))
    monkeypatch.setattr(update_cli, "cmd_update", lambda _args: (
        17 if boundary.cfg.install_dir().resolve() == Path(source) else 99
    ))
    for directory in (project, caller):
        monkeypatch.chdir(directory)
        assert boundary.cmd_update_request(_args()) == 17


def test_downlevel_driver_refuses_cleanly_without_queueing(queued, monkeypatch, capsys):
    import update_driver

    seen, _request = queued
    monkeypatch.delattr(update_driver, "current_authority")
    assert boundary.cmd_update_request(_args()) == 1
    assert "spec" not in seen
    assert "lacks authority support" in capsys.readouterr().err


def test_explicit_standalone_authority_bypasses_the_controller_through_manager(
    queued, monkeypatch,
):
    import update_driver
    from agent_worktrees import project_update_controller
    from update_driver.authority import selection

    seen, _request = queued
    monkeypatch.setattr(update_driver, "current_authority", lambda **_kwargs: selection("standalone"))
    monkeypatch.setattr(
        project_update_controller, "probe_for_submission",
        lambda *_args, **_kwargs: pytest.fail("standalone mode probed a project controller"),
    )
    monkeypatch.setenv("AGENT_WORKTREES_INTERNAL_PROJECT_CONTROLLER", "1")
    assert boundary.cmd_update_request(_args()) == 4
    assert seen["spec"].authority == "standalone"
    assert "--no-project-controller" in seen["spec"].argv
    assert "--no-project-controller" in seen["runner"].argv


def test_project_authority_routes_to_the_selected_project(queued, monkeypatch):
    import update_driver
    from agent_worktrees import project_update_controller
    from update_driver.authority import selection

    seen, _request = queued
    probes = []
    monkeypatch.setattr(
        update_driver, "current_authority", lambda **_kwargs: selection("project", "harness"),
    )
    monkeypatch.setattr(boundary.cfg, "active_project", lambda: "different")
    monkeypatch.setattr(boundary, "_request_context", lambda **_kwargs: ())
    monkeypatch.setattr(
        project_update_controller, "probe_for_submission",
        lambda _args, **kwargs: probes.append(kwargs) or True,
    )
    assert boundary.cmd_update_request(_args()) == 4
    assert probes[0]["project"] == "harness"
    assert seen["spec"].project == "harness"
    assert seen["spec"].authority == "project"
    assert seen["runner"].argv[-3:] == ("--project", "harness", "update")


def test_explicit_runtime_backend_is_bound_to_the_project_request(queued, monkeypatch):
    import update_driver
    from agent_worktrees import project_update_controller
    from update_driver.authority import selection

    seen, _request = queued
    monkeypatch.setattr(
        update_driver, "current_authority", lambda **_kwargs: selection("project", "harness"),
    )
    monkeypatch.setattr(boundary, "_request_context", lambda **_kwargs: ())
    monkeypatch.setattr(project_update_controller, "probe_for_submission", lambda *_a, **_kw: True)
    monkeypatch.setattr(project_update_controller, "supports_runtime_backend", lambda _project: True)
    assert boundary.cmd_update_request(_args(runtime_backend=["agent-worktrees=cpu"])) == 4
    assert seen["spec"].argv == ("--runtime-backend", "agent-worktrees=cpu")
    assert seen["runner"].argv[-2:] == ("--runtime-backend", "agent-worktrees=cpu")


def test_explicit_runtime_backend_is_not_queued_for_an_unadvertised_controller(queued, monkeypatch):
    from agent_worktrees import project_update_controller

    seen, _request = queued
    monkeypatch.setattr(project_update_controller, "probe_for_submission", lambda *_a, **_kw: True)
    monkeypatch.setattr(project_update_controller, "supports_runtime_backend", lambda _project: False)
    assert boundary.cmd_update_request(_args(runtime_backend=["agent-worktrees=cpu"])) == 1
    assert "spec" not in seen


@pytest.mark.parametrize("internal_hint", [False, True])
def test_explicit_runtime_backend_is_not_queued_without_a_project_controller(queued, monkeypatch, internal_hint):
    seen, _request = queued
    if internal_hint:
        monkeypatch.setenv("AGENT_WORKTREES_INTERNAL_PROJECT_CONTROLLER", "1")
    assert boundary.cmd_update_request(_args(runtime_backend=["agent-worktrees=cpu"])) == 1
    assert "spec" not in seen


@pytest.mark.parametrize("options", [{}, {"no_project_controller": True}, {"target": "agent-bridge"},
                                      {"phase": "runtimes"}])
def test_project_authority_never_queues_a_standalone_fallback(queued, monkeypatch, options):
    import update_driver
    from agent_worktrees import project_update_controller
    from update_driver.authority import selection

    seen, _request = queued
    monkeypatch.setattr(
        update_driver, "current_authority", lambda **_kwargs: selection("project", "example"),
    )
    monkeypatch.setattr(project_update_controller, "probe_for_submission", lambda *_a, **_kw: False)
    assert boundary.cmd_update_request(_args(**options)) == 1
    assert "spec" not in seen


@pytest.mark.parametrize("route", ["shortcut", "no-manager", "direct"])
@pytest.mark.parametrize("root_form", ["default", "relative", "registered"])
def test_controller_shortcut_keeps_automatic_manager_policy(
    queued, monkeypatch, tmp_path, root_form, route,
):
    from pathlib import Path
    from agent_worktrees import control_plane_providers, manager_launch_cli, project_update_controller
    from update_driver.core import automatic_update_refusal

    seen, _request = queued
    home, caller = tmp_path / "home", tmp_path / "caller"
    home.mkdir()
    caller.mkdir()
    root = {
        "default": home / ".worktree-manager",
        "relative": caller / "manager",
        "registered": tmp_path / "registered-manager",
    }[root_form]
    root.mkdir()
    (root / "config.toml").write_text(
        "[maintenance]\nautomatic = false\n", encoding="utf-8",
    )
    monkeypatch.chdir(caller)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(tmp_path / "cell.json"))
    if route == "shortcut":
        monkeypatch.setenv("AGENT_WORKTREES_INTERNAL_PROJECT_CONTROLLER", "1")
    else:
        monkeypatch.delenv("AGENT_WORKTREES_INTERNAL_PROJECT_CONTROLLER", raising=False)
        monkeypatch.setattr(
            project_update_controller, "probe_for_submission",
            lambda _args, **_kwargs: True,
        )
    if root_form == "relative":
        monkeypatch.setenv("WORKTREE_MANAGER_ROOT", "manager")
    else:
        monkeypatch.delenv("WORKTREE_MANAGER_ROOT", raising=False)
    manifest = (
        SimpleNamespace(provider="worktree-manager", provider_root=str(root))
        if root_form == "registered" else None
    )
    monkeypatch.setattr(
        manager_launch_cli, "_control_plane_provider_selection",
        lambda **_kwargs: (manifest, ""),
    )
    monkeypatch.setattr(
        control_plane_providers, "_machine_control_plane_providers_dir",
        lambda: home / "providers",
    )
    assert boundary.cmd_update_request(_args(auto=True, no_manager=route == "no-manager")) == 4
    request = seen["spec"]
    assert dict(request.context)["WORKTREE_MANAGER_ROOT"] == str(root.resolve())
    assert "maintenance.automatic is false" in automatic_update_refusal(request)
    assert "AGENT_WORKTREES_INTERNAL_PROJECT_CONTROLLER" not in boundary.os.environ


@pytest.mark.parametrize("provider", ["other", "worktree-manager"])
def test_controller_shortcut_rejects_a_conflicting_manager(
    queued, monkeypatch, tmp_path, provider,
):
    from agent_worktrees import control_plane_providers, manager_launch_cli

    seen, _request = queued
    monkeypatch.setenv("AGENT_WORKTREES_INTERNAL_PROJECT_CONTROLLER", "1")
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "selected"))
    monkeypatch.setattr(
        control_plane_providers, "_machine_control_plane_providers_dir",
        lambda: tmp_path / "providers",
    )
    monkeypatch.setattr(
        manager_launch_cli, "_control_plane_provider_selection",
        lambda **_kwargs: (
            SimpleNamespace(provider=provider, provider_root=str(tmp_path / "other")), "",
        ),
    )
    assert boundary.cmd_update_request(_args()) == 1
    assert "spec" not in seen


@pytest.mark.parametrize("no_manager", [False, True])
def test_uncontrolled_cell_does_not_inherit_manager_policy(queued, monkeypatch, tmp_path, no_manager):
    seen, _request = queued
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(tmp_path / "cell.json"))
    monkeypatch.delenv("WORKTREE_MANAGER_ROOT", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_INTERNAL_PROJECT_CONTROLLER", raising=False)
    assert boundary.cmd_update_request(_args(auto=True, no_manager=no_manager)) == 4
    assert "WORKTREE_MANAGER_ROOT" not in dict(seen["spec"].context)


@pytest.mark.parametrize("values,repair", [
    ({}, True), ({"auto": True}, False), ({"auto": True, "force": True}, False),
    ({"force": True}, True),
    ({"force_runtime": ["agent-bridge"]}, True), ({"recreate_venv": True}, True),
])
def test_force_options_record_explicit_repair_request(queued, values, repair):
    seen, _request = queued
    assert boundary.cmd_update_request(_args(**values)) == 4
    assert seen["spec"].repair is repair
    assert ("--auto" in seen["runner"].argv) is bool(values.get("auto"))
    assert ("--force" in seen["runner"].argv) is bool(values.get("force"))


@pytest.mark.parametrize("flags,automatic", [([], False), (["--auto"], True)])
def test_repository_parser_carries_mode_to_the_real_request_adapter(queued, flags, automatic):
    from agent_worktrees import __main__ as engine

    args = engine.build_parser().parse_args(["update", *flags])
    assert args.auto is automatic
    assert not args.force and not args.recreate_venv and not args.force_runtime
    assert boundary.cmd_update_request(args) == 4
    seen, _request = queued
    assert seen["spec"].repair is not automatic
    assert seen["spec"].argv == tuple(flags)
    with pytest.raises(SystemExit) as error:
        engine.build_parser().parse_args(["update", "--aut"])
    assert error.value.code == 2
    for command in (["stage-update"], ["reconcile-plugins"], ["launch-update", "automatic"]):
        assert engine.build_parser().parse_args([*command, "--auto"]).auto
        with pytest.raises(SystemExit) as error:
            engine.build_parser().parse_args([*command, "--aut"])
        assert error.value.code == 2


def test_detached_adapter_binds_relative_killswitch_to_original_invocation(tmp_path, monkeypatch):
    from pathlib import Path
    import update_driver

    home = tmp_path / "home"
    home.mkdir()
    scope = tmp_path / "scope"
    invocation = scope / "invocation"
    invocation.mkdir(parents=True)
    (invocation / "pause.json").write_text('{"active":true}', encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", "./pause.json")
    assert _enqueue_fixture_update(monkeypatch, scope, no_manager=True, auto=True) == 0
    request, = update_driver.status()
    assert Path(request.spec.killswitch_path).samefile(invocation / "pause.json")
    monkeypatch.chdir(home)
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(home / "unrelated.json"))
    assert update_driver.drain(executor=lambda tx: pytest.fail("inhibited runner started")) == 0
    assert update_driver.wait(request.id).exit_code == 3


def test_selected_phase_queues_exact_scope_and_labels_partial_receipt(queued, capsys):
    import json

    seen, _request = queued
    assert boundary.cmd_update_request(_args(
        phase="runtimes", plugin=["agent-example"], detach=True, json=True,
    )) == 0
    assert seen["spec"].argv == ("--phase", "runtimes", "--plugin", "agent-example")
    assert seen["runner"].argv[-5:] == ("update", "--phase", "runtimes", "--plugin", "agent-example")
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["phase"] == "runtimes"
    assert receipt["plugins"] == ["agent-example"]
    assert receipt["full_update"] is False


@pytest.mark.parametrize("values", [
    {"plugin": ["agent-example"]},
    {"phase": "anchors", "plugin": ["agent-example"]},
    {"phase": "anchors", "no_anchor_sync": True},
    {"phase": "prune", "no_prune_pivots": True},
    {"phase": "payloads", "force": True},
    {"phase": "runtimes", "auth_report": True},
    {"phase": "runtimes", "recreate_venv": True},
    {"phase": "runtimes", "plugin": ["agent-example"], "force_runtime": ["agent-other"]},
    {"phase": "runtimes", "target": "agent-example"},
    {"phase": "preflight", "inventory": True},
    {"phase": "runtimes", "no_manager": True, "runtime_sources": "{}"},
])
def test_invalid_phase_selection_does_not_enqueue(queued, values):
    seen, _request = queued
    with pytest.raises(SystemExit) as failure:
        boundary.cmd_update_request(_args(**values))
    assert failure.value.code == 2
    assert seen == {}


def test_phase_execution_does_not_probe_or_self_update_manager(monkeypatch):
    monkeypatch.setattr(update_cli, "_usable_worktree_manager",
                        lambda: pytest.fail("selected phase probed Manager"))
    assert update_cli.manager_handoff(_args(phase="preflight")) is None


@pytest.mark.parametrize("target_status", [None, "OK", "SKIPPED (current)", "timed out (2s)"])
def test_partial_completion_does_not_report_full_verified_current(monkeypatch, tmp_path, target_status):
    import json
    import update_telemetry as ut
    from agent_worktrees import update_events

    run_ids = iter(("20260914T090000Z-1-ffff", "20260914T090000Z-1-0000"))
    monkeypatch.setattr("update_telemetry.run.new_run_id", lambda: next(run_ids))
    monkeypatch.setattr("update_telemetry.run.utc_now", lambda: "2026-09-14T09:00:00.000Z")
    environment = {ut.ENV_HOME: str(tmp_path / "telemetry")}
    previous = ut.Run.from_env_or_new("engine", environ=environment)
    previous.target("runtime_reconcile", "agent-example@copilot-extensions", ut.VERIFIED_CURRENT)
    assert previous.finish() == 0
    path = tmp_path / "telemetry" / "update-status.json"
    history = json.loads(path.read_text(encoding="utf-8"))
    # This tests partial folding, not the random-ID tie-breaker for equal start times.
    monkeypatch.setattr("update_telemetry.run.utc_now", lambda: "2026-09-14T09:00:00.001Z")
    run = ut.Run.from_env_or_new("engine", environ=environment)
    monkeypatch.setattr(update_events, "_ut", ut)
    monkeypatch.setattr(update_events, "_run", run)
    monkeypatch.setattr(update_events, "_required_failures", set())
    monkeypatch.setattr(update_events, "_human_phase", None)
    if target_status:
        update_events.record(
            "payload_refresh", "agent-example@copilot-extensions", target_status,
            changed=target_status == "OK",
        )
    failed = target_status == "timed out (2s)"
    assert update_events.finish(0, partial=True) == (6 if failed else 0)
    status = json.loads(path.read_text(encoding="utf-8"))
    assert status["run"]["id"] == run.run_id
    assert status["run"]["outcome"] == (ut.TIMEOUT if failed else ut.SKIPPED)
    assert status["run"]["exit_code"] == (6 if failed else 0)
    assert status["last_success"] == history["last_success"]
    if failed:
        assert status["last_failure"]["run_id"] == run.run_id
    else:
        assert status["last_failure"] == history["last_failure"]


def test_no_manager_does_not_bypass_the_queue(queued):
    seen, _request = queued
    assert boundary.cmd_update_request(_args(no_manager=True)) == 4
    assert seen["spec"].argv == ("--no-manager",)
    assert seen["runner"].argv[-2:] == ("update", "--no-manager")


def test_prepared_source_packet_is_durable_request_input_not_ambient_state(queued):
    seen, _request = queued
    raw = '{"schema":"copilot-extensions.prepared-runtime-sources","version":1,"sources":{}}'
    assert boundary.cmd_update_request(_args(no_manager=True, runtime_sources=raw)) == 4
    assert seen["spec"].argv == ("--no-manager", "--runtime-sources", raw)
    assert seen["runner"].argv[-4:] == ("update", "--no-manager", "--runtime-sources", raw)


def test_actual_queue_retains_sources_after_submitting_environment_disappears(tmp_path, monkeypatch):
    import json
    import os
    import sqlite3
    import sys
    from pathlib import Path

    from agent_procutil import direct_python
    from test_update_runtime_sources import packet
    import update_driver

    home, scope = tmp_path / "home", tmp_path / "scope"
    home.mkdir()
    for name in ("anchor", "invocation"):
        settings = scope / name / ".github" / "copilot" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}")
    _write_fixture_project(scope)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for key in boundary._CONTEXT_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("UPDATE_DRIVER_TEST_MANAGER", raising=False)
    monkeypatch.setenv("AGENT_HOME", str(scope))
    raw = json.dumps(packet(tmp_path))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_SOURCES", raw)
    monkeypatch.chdir(scope / "invocation")
    fixture = Path(__file__).parent / "fixtures" / "update_driver_context.py"
    python, environment = direct_python(sys.executable)
    submitted = _run_runtime_owned(
        monkeypatch, [python, str(fixture), "submit"], environment,
    )
    assert submitted.returncode == 0, submitted.stdout + submitted.stderr
    database = home / ".copilot-extensions" / "update-driver" / "queue.sqlite3"
    with sqlite3.connect(database) as connection:
        document = json.loads(connection.execute("select spec from requests").fetchone()[0])
    assert document["argv"] == ["--no-manager", "--runtime-sources", raw]
    monkeypatch.delenv("UPDATE_DRIVER_TEST_SOURCES")
    worker, environment = update_driver.runtime_command("update_driver", ["worker"])
    result = _run_runtime_owned(monkeypatch, worker, environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((scope / "result.json").read_text())["runtime_sources"] == raw
    assert "UPDATE_DRIVER_TEST_SOURCES" not in os.environ


def test_native_selected_preflight_crosses_queue_without_other_update_pieces(tmp_path, monkeypatch, capsys):
    import json
    from pathlib import Path
    import update_driver

    home, scope = tmp_path / "home", tmp_path / "selected"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    for key in boundary._CONTEXT_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AGENT_HOME", str(scope))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_MANAGER", "1")
    for name in ("anchor", "invocation"):
        settings = scope / name / ".github" / "copilot" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}")
    monkeypatch.chdir(scope / "invocation")
    assert _enqueue_fixture_update(
        monkeypatch, scope, phase="preflight", plugin=["agent-example"], json=True,
    ) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["phase"] == "preflight"
    assert receipt["plugins"] == ["agent-example"]
    assert receipt["full_update"] is False
    assert update_driver.drain() == 0
    request = update_driver.status(receipt["request_id"])
    assert request.state == "completed", request.error
    assert request.exit_code == 0, request.log_path.read_text(encoding="utf-8")
    recorded = json.loads((scope / "result.json").read_text())
    assert recorded["phase"] == "preflight"
    assert recorded["plugins"] == ["agent-example"]
    assert recorded["full_update"] is False
    assert recorded["exit_code"] == 0
    assert not (scope / "manager-entered").exists()


def test_queued_update_keeps_original_context_after_project_chdir(queued, tmp_path, monkeypatch):
    from agent_worktrees import __main__ as core

    caller, anchor = tmp_path / "caller", tmp_path / "anchor"
    caller.mkdir()
    anchor.mkdir()
    monkeypatch.chdir(anchor)
    monkeypatch.setattr(core, "_INVOCATION_CWD", caller)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "selected-installation"))
    seen, _request = queued
    assert boundary.cmd_update_request(_args()) == 4
    assert seen["spec"].cwd == str(caller)
    assert seen["runner"].cwd == str(caller)
    assert dict(seen["spec"].context)["AGENT_HOME"] == str(tmp_path / "selected-installation")
    assert seen["runner"].context_keys == boundary._CONTEXT_KEYS


@pytest.mark.parametrize("with_manager", [False, True])
# Two independently bounded 20-second workers plus fixture setup and cleanup.
@pytest.mark.timeout(60)
def test_actual_queued_engine_keeps_each_scope_and_original_invocation(tmp_path, monkeypatch, with_manager):
    import json
    import importlib.util
    import os
    import subprocess
    import shutil
    import time
    from contextlib import nullcontext
    from pathlib import Path
    import update_driver

    mode = os.environ.get("UPDATE_DRIVER_TEST_RUNTIME_MODE", "source")
    assert mode in {"source", "prepared"}
    prepared = (
        json.loads(Path(os.environ["UPDATE_DRIVER_TEST_PREPARED_RUNTIME"]).read_text(encoding="utf-8"))
        if mode == "prepared" else None
    )
    print(f"Routing runtime mode: {mode}; cold-first-touch proof not exercised")
    phases = tmp_path / "fixture-phases.jsonl"
    monkeypatch.setenv("UPDATE_DRIVER_TEST_PHASE_LOG", str(phases))

    def worker_phase(phase):
        with phases.open("a", encoding="utf-8") as logfile:
            logfile.write(json.dumps({
                "time": time.monotonic(), "role": "worker-owner",
                "phase": phase, "pid": os.getpid(),
            }) + "\n")

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for key in boundary._CONTEXT_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("UPDATE_DRIVER_TEST_MANAGER", "1" if with_manager else "0")
    scopes = ("first",) if with_manager else ("first", "second")
    for name in scopes:
        scope = tmp_path / name
        (scope / "anchor").mkdir(parents=True)
        anchor_settings = scope / "anchor" / ".github" / "copilot" / "settings.json"
        anchor_settings.parent.mkdir(parents=True)
        anchor_settings.write_text("{}")
        invocation = scope / "invocation"
        settings = invocation / ".github" / "copilot" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}")
        _write_fixture_project(scope)
    observer = nullcontext()
    if with_manager and os.name == "nt":
        helper_path = Path(__file__).resolve().parents[3] / "libs/update-driver/tests/test_native_lifetime.py"
        specification = importlib.util.spec_from_file_location("_queued_engine_window_probe", helper_path)
        helper = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(helper)
        observer = helper.window_observer()
    with observer as windows:
        for _cycle in range(2 if with_manager else 1):
            for name in scopes:
                scope = tmp_path / name
                monkeypatch.setenv("AGENT_HOME", str(scope))
                monkeypatch.chdir(scope / "invocation")
                assert _enqueue_fixture_update(
                    monkeypatch, scope, no_manager=not with_manager,
                ) == 0
            monkeypatch.setenv("AGENT_HOME", str(tmp_path / "first"))
            worker, environment = update_driver.runtime_command("update_driver", ["worker"])
            if prepared is not None:
                from agent_procutil import python_cache_args

                worker, environment = prepared["engine"]["worker"]
                queue_root = home / ".copilot-extensions" / "update-driver"
                cache = python_cache_args(queue_root, runtime=prepared["engine"]["slot"])
                target_cache = Path(cache[1].split("=", 1)[1])
                if not target_cache.exists():
                    worker_phase("owned-bytecode-copy-start")
                    shutil.copytree(
                        Path(prepared["engine"]["cache"][1].split("=", 1)[1]), target_cache,
                    )
                    worker_phase("owned-bytecode-copy-completed")
            worker_phase("starting")
            try:
                result = _run_runtime_owned(monkeypatch, worker, environment, timeout=40)
            except subprocess.TimeoutExpired as error:
                worker_phase("timeout")
                logs = home / ".copilot-extensions" / "update-driver"
                pytest.fail(
                    f"worker timeout: {[p.read_text() for p in logs.glob('*.log')]}; "
                    f"phases: {phases.read_text(encoding='utf-8')}: {error}"
                )
            worker_phase("completed")
            print("Fixture phases:\n" + phases.read_text(encoding="utf-8"))
            assert result.returncode == 0, result.stderr
            rows = update_driver.status(root=home / ".copilot-extensions" / "update-driver")
            assert rows and all(row.state == "completed" and row.exit_code == 0 for row in rows), [
                (row.state, row.exit_code, row.error, row.log_path.read_text()) for row in rows
            ]
            assert all(
                row.spec.cwd == os.path.normcase(os.path.normpath(
                    str(Path(dict(row.spec.context)["AGENT_HOME"]) / "invocation")
                ))
                for row in rows
            )
    if windows is not None:
        assert windows.surfaced == {}, windows.surfaced
        assert windows.focus_changes == {}, windows.focus_changes
    entries = [
        json.loads(line) for line in phases.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["phase"] == "manager-entry-cwd"
    ]
    assert [entry["cwd"] for entry in entries] == (
        [str(tmp_path / "first" / "anchor")] * 2 if with_manager else []
    )
    for name in scopes:
        scope = tmp_path / name
        # Native update re-entry uses captured settings context; project routing
        # still selects the anchor. Manager entry is observed separately above.
        assert json.loads((scope / "result.json").read_text()) == {
            "anchor": str(scope / "anchor"),
            "invocation": str(scope / "invocation"),
            "update_context": str(scope / "invocation"),
        }
        if with_manager:
            assert (scope / "manager-entered").read_text() == str(scope / "invocation")


def test_engine_request_identity_keeps_explicit_provider_override(queued, monkeypatch):
    seen, _request = queued
    monkeypatch.setenv("WORKTREE_MANAGER_ENGINE_CMD", "synthetic-first-provider")
    assert boundary.cmd_update_request(_args()) == 4
    first = seen["spec"].context
    monkeypatch.setenv("WORKTREE_MANAGER_ENGINE_CMD", "synthetic-second-provider")
    assert boundary.cmd_update_request(_args()) == 4
    assert first != seen["spec"].context
    assert dict(first)["WORKTREE_MANAGER_ENGINE_CMD"] == "synthetic-first-provider"
    assert "WORKTREE_MANAGER_ENGINE_CMD" in seen["runner"].context_keys


def test_detach_receipt_is_not_a_successful_installation(queued, capsys):
    import json

    seen, request = queued
    assert boundary.cmd_update_request(_args(detach=True, json=True)) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["request_id"] == request.id
    assert receipt["state"] == "pending"
    assert receipt["exit_code"] is None
    assert "wait" not in seen


def test_spawn_failure_never_uses_foreground_fallback(queued, monkeypatch):
    import update_driver

    seen, _request = queued

    def fail(*args, **kwargs):
        raise update_driver.SpawnError("fixture spawn refused")

    monkeypatch.setattr(update_driver, "ensure_worker", fail)
    assert boundary.cmd_update_request(_args()) == 1
    assert "wait" not in seen


def test_invalid_participant_never_becomes_new_work(queued, monkeypatch):
    import update_driver

    seen, _request = queued

    def fail():
        raise update_driver.ParticipantError("fixture witness expired")

    monkeypatch.setattr(update_driver, "borrowed_custody", fail)
    assert boundary.cmd_update_request(_args(no_manager=True)) == 1
    assert "spec" not in seen


def test_supervised_debug_uses_same_queue_and_drain_authority(queued, monkeypatch):
    import update_driver

    seen, _request = queued
    monkeypatch.setattr(update_driver, "drain", lambda: seen.update(debug=True))
    assert boundary.cmd_update_request(_args(supervised_debug=True)) == 4
    assert seen["debug"]
    assert "worker" not in seen
    assert "spec" in seen


def test_status_does_not_resolve_installation_or_enqueue(queued, monkeypatch):
    import update_driver

    seen, request = queued
    monkeypatch.setattr(update_driver, "status", lambda identifier: request)
    assert boundary.cmd_update_request(_args(request_status=request.id, json=True)) == 0
    assert seen == {}


def test_status_exposes_settlement_kind_without_executing_installation(queued, monkeypatch, capsys):
    import json
    import update_driver

    seen, request = queued
    request.state, request.error = "interrupted", "native scope settled after failure"
    request.settled_at, request.settlement_kind = 1.0, "native_scope"
    monkeypatch.setattr(update_driver, "status", lambda identifier: request)
    assert boundary.cmd_update_request(_args(request_status=request.id, json=True)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "interrupted" and result["exit_code"] is None
    assert result["settlement_kind"] == "native_scope" and result["settled_at"] == 1.0
    assert seen == {}


def test_auth_report_stays_read_only_before_custody_resolution(queued, monkeypatch):
    seen, _request = queued
    monkeypatch.setattr(update_cli, "cmd_update", lambda args: 4)
    assert boundary.cmd_update_request(_args(auth_report=True, json=True)) == 4
    assert seen == {}


def test_driver_controls_are_not_forwarded_to_transaction_runner():
    args = _args(detach=True, json=True, force=True)
    assert boundary._flags(args) == ["--force"]


def test_request_status_does_not_require_a_project():
    from agent_worktrees.project_routing import _is_no_project_invocation

    assert _is_no_project_invocation(["update", "--request-status", "synthetic-request", "--json"])
    assert _is_no_project_invocation(["update", "--request-status=synthetic-request"])
    assert not _is_no_project_invocation(["update", "--wait"])


@pytest.mark.parametrize("mode", ["detach", "supervised_debug", "request_status"])
def test_authentication_report_cannot_select_installation_mode(mode):
    with pytest.raises(SystemExit) as error:
        boundary.cmd_update_request(_args(auth_report=True, **{mode: True}))
    assert error.value.code == 2


def test_recovery_controls_work_without_resolving_or_executing_installation(queued, monkeypatch, capsys):
    import json
    import update_driver

    seen, request = queued
    request.settled_at = 1.0
    monkeypatch.setattr(update_driver, "recovery_status", lambda: SimpleNamespace(
        eligible_ids=(request.id,), refused=(), error=None,
    ))
    assert boundary.cmd_update_request(_args(recovery_status=True, json=True)) == 0
    assert json.loads(capsys.readouterr().out)["eligible_requests"] == [request.id]
    monkeypatch.setattr(update_driver, "recover_after_restart", lambda: (request,))
    assert boundary.cmd_update_request(_args(recover_after_restart=True, json=True)) == 0
    assert json.loads(capsys.readouterr().out)["replayed"] is False
    assert seen == {}


def test_operator_repair_is_explicit_and_does_not_execute_installation(queued, monkeypatch, capsys):
    import json
    import update_driver

    seen, request = queued
    request.state = "interrupted"
    request.recovery_authority, request.released_at = "operator_repair", 2.0
    request.settled_at = request.settlement_kind = None
    def recover(*, authorized):
        assert authorized is True
        return (request,)

    monkeypatch.setattr(update_driver, "recover_for_repair", recover)
    assert boundary.cmd_update_request(_args(recover_for_repair=True, json=True)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["replayed"] is False
    assert result["released"][0]["recovery_authority"] == "operator_repair"
    assert result["released"][0]["settlement_kind"] is None
    assert seen == {}


@pytest.mark.parametrize("mode", ["auth_report", "force"])
def test_operator_repair_rejects_installation_and_auth_options(queued, mode):
    if mode == "auth_report":
        with pytest.raises(SystemExit):
            boundary.cmd_update_request(_args(recover_for_repair=True, auth_report=True))
    else:
        assert boundary.cmd_update_request(_args(recover_for_repair=True, force=True)) == 1
    assert queued[0] == {}


def test_operator_repair_routing_needs_no_project():
    from agent_worktrees.project_routing import _is_no_project_invocation

    assert _is_no_project_invocation(["update", "--recover-for-repair", "--json"])


def test_manager_update_handoff_carries_only_explicit_scoped_capability(monkeypatch):
    import agent_procutil
    import update_driver
    from agent_worktrees import manager_launch_cli

    seen = {}
    monkeypatch.delenv("UPDATE_DRIVER_WITNESS", raising=False)
    monkeypatch.setattr(update_driver, "participant_environment", lambda: {"UPDATE_DRIVER_WITNESS": "fixture"})
    monkeypatch.setattr(manager_launch_cli.platform, "system", lambda: "Windows")

    def spawn(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(wait=lambda: 0, kill=lambda: None), None

    monkeypatch.setattr(agent_procutil, "spawn_sync_in_kill_on_close_job", spawn)
    with pytest.raises(SystemExit) as result:
        manager_launch_cli._exec_worktree_manager(["fixture-manager"], None, subcommand=["update"])
    assert result.value.code == 0
    assert seen["env"]["UPDATE_DRIVER_WITNESS"] == "fixture"
    with pytest.raises(SystemExit):
        manager_launch_cli._exec_worktree_manager(["fixture-manager"], None, subcommand=["picker"])
    assert "UPDATE_DRIVER_WITNESS" not in seen["env"]
