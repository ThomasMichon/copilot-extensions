"""The bootstrap request completes only after its full update participant."""

from __future__ import annotations

from contextlib import nullcontext
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

from update_driver import core
from worktree_manager import self_install, update_driver_cli
from worktree_manager.engine_acquisition_budget import TIMEOUT_ENV

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_update.py"
sys.path.insert(0, str(SCRIPT.parent))


def _legacy_authority(**_kwargs):
    return core.authority.UpdateAuthority()


@pytest.fixture(autouse=True)
def retained_inputs(tmp_path, monkeypatch):
    import bootstrap_inputs
    import runtime_install
    from worktree_manager import update_python_path

    monkeypatch.delenv(TIMEOUT_ENV, raising=False)
    monkeypatch.delenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_UPDATE_CONTEXT", raising=False)
    monkeypatch.setattr(bootstrap_inputs, "retain", lambda payload, root, **kwargs: (payload, True))
    monkeypatch.setattr(runtime_install, "_source_digest", lambda payload: "a" * 64)
    monkeypatch.setattr(update_python_path, "finish_update", lambda command, code: code)


@pytest.mark.parametrize("exit_code", [0, 23])
@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("materialize", [False, True])
def test_bootstrap_update_journals_scope_and_waits_for_the_whole_result(
    tmp_path, monkeypatch, capsys, exit_code, automatic, materialize,
):
    import bootstrap_inputs
    import runtime_install

    execute = runpy.run_path(str(SCRIPT))["execute"]
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})
    payload, root = tmp_path / "source", tmp_path / "manager"
    payload.mkdir()
    seen = {}
    if materialize:
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "prepare_runtime_source.py").write_text("value = 1\n")
        (payload / "packaged-assets.json").write_text(json.dumps({
            "schema": "copilot-extensions.packaged-assets", "version": 1,
            "canonicalDir": "tools", "payloadDir": "scripts/runtime-source-tools",
            "files": ["prepare_runtime_source.py"],
        }))
        monkeypatch.setattr(runtime_install, "_source_digest", lambda path: "b" * 64)

    def retain(payload, root, **kwargs):
        seen["retention"] = kwargs
        return payload, True

    monkeypatch.setattr(bootstrap_inputs, "retain", retain)
    events = []
    command = [
        "--", "update", "--supervised-debug", "--project", "sample",
        *(["--auto"] if automatic else []),
    ]
    request = SimpleNamespace(id="fixture", log_path=tmp_path / "update.log")

    def enqueue(spec, *, runner, coalesce_running):
        assert coalesce_running is False
        seen.update(spec=spec, runner=runner)
        return request

    def drain():
        events.extend(["manager-built", "suite-finished"])

    def wait(identifier, *, output, worker):
        assert identifier == request.id
        assert worker is None
        assert events == ["manager-built", "suite-finished"]
        return SimpleNamespace(state="completed", exit_code=exit_code)

    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        current_authority=_legacy_authority,
        enqueue=enqueue, drain=drain, wait=wait, default_root=lambda: tmp_path,
    )
    args = SimpleNamespace(
        payload=payload, root=root, command=command, repair_update=True, install_source=None,
    )
    assert execute(args, driver, source_digest="a" * 64) == exit_code
    spec, runner = seen["spec"], seen["runner"]
    assert spec.backend == "manager"
    participant = ["--", "update", "--project", "sample", *(["--auto"] if automatic else [])]
    assert spec.argv == tuple(participant[2:])
    assert spec.source == "configured-source"
    assert spec.repair is not automatic
    assert dict(spec.context)["WORKTREE_MANAGER_ROOT"] == str(root)
    assert runner.argv[-len(participant):] == tuple(participant)
    assert "--update-participant" in runner.argv
    assert seen["retention"]["expected_payload_digest"] == ("a" * 64 if materialize else None)
    assert runner.argv[runner.argv.index("--source-digest") + 1] == (
        "b" * 64 if materialize else "a" * 64
    )
    assert "--install-only" not in runner.argv
    assert not capsys.readouterr().out


@pytest.mark.parametrize("mode", ["standalone", "project"])
def test_bootstrap_repair_obeys_configured_authority(tmp_path, monkeypatch, mode):
    import bootstrap_inputs

    tmp_path = tmp_path.resolve()
    payload, queue = tmp_path / "source", tmp_path / "driver"
    payload.mkdir()
    core.change_authority(
        mode, project="example" if mode == "project" else "", root=queue, timeout=5,
        verify_capabilities=lambda _deadline: True,
    )
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})
    captured = []
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        DriverError=core.DriverError, StateError=core.StateError, InterruptedError=core.InterruptedError,
        default_root=lambda: queue,
        current_authority=lambda **kwargs: core.current_authority(root=queue, **kwargs),
        enqueue=lambda spec, **kwargs: core.enqueue(spec, root=queue, **kwargs),
        drain=lambda: core.drain(root=queue, executor=lambda transaction: captured.append(transaction.request) or 0),
        wait=lambda request_id, **kwargs: core.wait(request_id, root=queue, **kwargs),
    )
    args = SimpleNamespace(
        payload=payload, root=tmp_path / "manager",
        command=["update", "--supervised-debug", "--repair-engine", str(payload), "--project", "sample"],
        repair_update=True, install_source=None,
    )
    if mode == "project":
        monkeypatch.setattr(bootstrap_inputs, "retain", lambda *_a, **_kw: pytest.fail("unselected bootstrap ran"))
        assert runpy.run_path(str(SCRIPT))["execute"](args, driver, source_digest="a" * 64) == 1
        assert not core.status(root=queue)
    else:
        assert runpy.run_path(str(SCRIPT))["execute"](args, driver, source_digest="a" * 64) == 0
        assert len(captured) == 1
        request = captured[0]
        assert request.spec.authority == "standalone"
        assert "--no-project-controller" in request.spec.argv
        assert "--no-project-controller" in request.runner.argv


@pytest.mark.parametrize("state", ["interrupted", "blocked"])
def test_bootstrap_update_does_not_report_unfinished_work_as_success(tmp_path, monkeypatch, state):
    execute = runpy.run_path(str(SCRIPT))["execute"]
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        current_authority=_legacy_authority,
        default_root=lambda: tmp_path,
        enqueue=lambda *a, **k: SimpleNamespace(id="fixture", log_path=tmp_path / "log"),
        drain=lambda: None,
        wait=lambda *a, **k: SimpleNamespace(state=state, exit_code=None),
    )
    args = SimpleNamespace(
        payload=tmp_path, root=tmp_path, command=["update", "--supervised-debug"],
        repair_update=True, install_source=None,
    )
    with pytest.raises(RuntimeError, match=f"did not complete: {state}"):
        execute(args, driver, source_digest="a" * 64)


@pytest.mark.parametrize("detach", [False, True])
def test_default_update_observes_a_retained_worker_instead_of_running_installers(
    tmp_path, monkeypatch, detach,
):
    import bootstrap_update
    import runtime_install

    execute = bootstrap_update.execute
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})
    package = tmp_path / "retained-procutil"
    monkeypatch.setattr(runtime_install.engine, "_package_roots", lambda payload: (package,))
    monkeypatch.setattr(runtime_install.engine, "_package_name", lambda path: "agent-procutil")
    request = SimpleNamespace(
        id="fixture", log_path=tmp_path / "log", state="pending", exit_code=None, error=None,
    )
    seen = []

    def worker(command, **kwargs):
        assert Path(command[-1]) == tmp_path / "scripts" / "bootstrap_update.py"
        assert kwargs["cwd"] == tmp_path
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert kwargs["python_bootstrap_root"] == package / "src"
        seen.append("worker")
        return "worker-handle"

    def wait(identifier, **kwargs):
        assert kwargs["worker"] == "worker-handle"
        seen.append("wait")
        return SimpleNamespace(state="completed", exit_code=23)

    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        current_authority=_legacy_authority,
        default_root=lambda: tmp_path,
        enqueue=lambda *a, **k: request, ensure_worker=worker, wait=wait,
        drain=lambda: pytest.fail("ordinary observer ran installation"),
    )
    args = SimpleNamespace(
        payload=tmp_path, root=tmp_path / "manager",
        command=["update", *(["--detach"] if detach else [])],
        repair_update=True, install_source=None,
    )
    assert execute(args, driver, source_digest="a" * 64) == (0 if detach else 23)
    assert seen == (["worker"] if detach else ["worker", "wait"])


@pytest.mark.parametrize("phase,already_completed,as_json", [
    ("launch", False, False), ("launch", False, True),
    ("launch", True, False), ("launch", True, True), ("wait", False, False),
])
def test_worker_start_failure_retains_the_admitted_request(
    tmp_path, monkeypatch, capsys, as_json, phase, already_completed,
):
    import agent_procutil
    import bootstrap_update
    import runtime_install

    queue = tmp_path / "queue"
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})
    monkeypatch.setattr(update_driver_cli, "request_source_anchor", lambda: str(tmp_path))
    monkeypatch.setattr(runtime_install.engine, "_package_roots", lambda payload: (tmp_path,))
    monkeypatch.setattr(runtime_install.engine, "_package_name", lambda path: "agent-procutil")
    launches = []

    def refuse_spawn(*args, **kwargs):
        launches.append(args)
        raise agent_procutil.DaemonSpawnError("fixture caller Job forbids breakaway")

    monkeypatch.setattr(agent_procutil, "spawn_daemon", refuse_spawn)

    def enqueue(spec, **kwargs):
        request = core.enqueue(spec, root=queue, **kwargs)
        if already_completed:
            core.drain(root=queue, executor=lambda _transaction: 23)
            return core.status(request.id, root=queue)
        return request

    def worker(command, **kwargs):
        if phase == "wait":
            launches.append(("acknowledged-worker",))
            return "acknowledged-worker"
        return core.ensure_worker(command, root=queue, **kwargs)

    def wait(identifier, **kwargs):
        assert phase == "wait" and kwargs["worker"] == "acknowledged-worker"
        raise core.SpawnError("fixture acknowledged worker exited before claim")

    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        current_authority=_legacy_authority, StateError=core.StateError,
        default_root=lambda: queue,
        enqueue=enqueue,
        ensure_worker=worker,
        drain=lambda: pytest.fail("worker refusal became foreground execution"),
        wait=wait,
    )
    args = SimpleNamespace(
        payload=tmp_path, root=tmp_path / "manager",
        command=["update", *(["--detach"] if phase == "launch" else []),
                 *(["--json"] if as_json else [])],
        repair_update=False, install_source=None,
    )
    message = "DaemonSpawnError" if phase == "launch" else "exited before claim"
    with pytest.raises(core.SpawnError, match=message):
        bootstrap_update.execute(args, driver, source_digest="a" * 64)

    request, = core.status(root=queue)
    expected_state, expected_exit = ("completed", 23) if already_completed else ("pending", None)
    assert request.state == expected_state and request.exit_code == expected_exit
    if already_completed:
        assert request.started_at is not None and request.finished_at is not None
    else:
        assert request.started_at is None and request.finished_at is None
        if phase == "launch":
            assert "DaemonSpawnError" in request.error
    assert len(launches) == 1
    output = capsys.readouterr()
    if as_json:
        receipt = json.loads(output.out)
        assert receipt["request_id"] == request.id
        assert receipt["log_path"] == str(request.log_path)
        assert receipt["state"] == expected_state and receipt["exit_code"] == expected_exit
        assert message in receipt["error"]
    else:
        assert output.out == ""
    assert f"Update request {request.id}; log: {request.log_path}" in output.err


def test_foreground_json_refuses_before_bootstrap_admission(tmp_path, capsys):
    import bootstrap_update

    args = SimpleNamespace(
        payload=tmp_path, root=tmp_path, command=["update", "--wait", "--json"],
        repair_update=False, install_source=None,
    )
    with pytest.raises(SystemExit) as failure:
        bootstrap_update.execute(args, SimpleNamespace(), source_digest="a" * 64)
    assert failure.value.code == 2
    assert capsys.readouterr().out == ""


def test_prepared_old_source_is_data_not_the_bootstrap_controller(tmp_path, monkeypatch):
    execute = runpy.run_path(str(SCRIPT))["execute"]
    import bootstrap_inputs

    source, controller = tmp_path / "old-source", tmp_path / "retained-controller"
    source.mkdir()
    selected = tmp_path / "prepared"
    seen = {}

    def retain(payload, root, *, include_sdk):
        assert payload == SCRIPT.parents[1] and root == tmp_path
        assert include_sdk is False
        return controller, False

    def enqueue(spec, *, runner, coalesce_running):
        assert coalesce_running is False
        seen["runner"] = runner
        return SimpleNamespace(id="fixture", log_path=tmp_path / "log")

    monkeypatch.setattr(bootstrap_inputs, "retain", retain)
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        current_authority=_legacy_authority,
        enqueue=enqueue, default_root=lambda: tmp_path, drain=lambda: None,
        wait=lambda *a, **k: SimpleNamespace(state="completed", exit_code=23),
    )
    args = SimpleNamespace(
        payload=source, root=tmp_path / "manager", install_source=selected,
        command=["update", "--supervised-debug"], repair_update=False,
    )
    assert execute(args, driver, source_digest="a" * 64) == 23
    runner = seen["runner"]
    assert runner.cwd == str(controller)
    assert str(controller / "scripts" / "runtime_install.py") in runner.argv
    assert runner.argv[runner.argv.index("--payload") + 1] == str(source)
    assert runner.argv[runner.argv.index("--install-source") + 1] == str(selected)
    assert not list(source.iterdir())


@pytest.mark.parametrize("flags,expected", [
    ([], "600.0"),
    (["--engine-acquisition-timeout", "420"], "420.0"),
    (["--target", "agent-bridge"], None),
])
def test_bootstrap_retains_effective_budget_only_for_suite_execution(
    tmp_path, monkeypatch, flags, expected,
):
    execute = runpy.run_path(str(SCRIPT))["execute"]
    monkeypatch.setenv(TIMEOUT_ENV, "600")
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})
    captured = []
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        current_authority=_legacy_authority,
        enqueue=lambda spec, **kwargs: captured.append((spec, kwargs["runner"]))
        or SimpleNamespace(id="fixture", log_path=tmp_path / "log"),
        default_root=lambda: tmp_path, drain=lambda: None,
        wait=lambda *a, **k: SimpleNamespace(state="completed", exit_code=23),
    )
    command = ["update", *flags, "--supervised-debug"]
    args = SimpleNamespace(
        payload=tmp_path, root=tmp_path / "manager", command=command,
        repair_update=False, install_source=None,
    )
    assert execute(args, driver, source_digest="a" * 64) == 23
    spec, runner = captured[0]
    if expected is None:
        assert "--engine-acquisition-timeout" not in spec.argv
    else:
        index = spec.argv.index("--engine-acquisition-timeout")
        assert spec.argv[index + 1] == expected
        assert runner.argv[runner.argv.index("--engine-acquisition-timeout") + 1] == expected


@pytest.mark.parametrize("shared", [True, False])
def test_observer_modes_share_the_actual_pending_installation_recipe(
    tmp_path, monkeypatch, capsys, shared,
):
    import bootstrap_inputs
    import bootstrap_update
    import runtime_install

    monkeypatch.setenv(TIMEOUT_ENV, "600")
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})
    monkeypatch.setattr(runtime_install.engine, "_package_roots", lambda payload: (tmp_path,))
    monkeypatch.setattr(runtime_install.engine, "_package_name", lambda path: "agent-procutil")
    monkeypatch.setattr(bootstrap_inputs, "retain", lambda payload, root, **kwargs: (payload, shared))
    queue = tmp_path / "queue"
    events = []

    def worker(*args, **kwargs):
        events.append("worker")
        return "worker-handle"

    def wait(identifier, **kwargs):
        events.append("wait")
        return SimpleNamespace(state="completed", exit_code=23)

    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        current_authority=_legacy_authority,
        default_root=lambda: queue,
        enqueue=lambda spec, **kwargs: core.enqueue(spec, root=queue, **kwargs),
        ensure_worker=worker, wait=wait, drain=lambda: events.append("drain"),
    )
    for mode, budget in (
        ([], ["--engine-acquisition-timeout", "600"]),
        (["--wait"], []),
        (["--detach", "--json"], ["--engine-acquisition-timeout", "600.0"]),
        (["--supervised-debug"], []),
    ):
        args = SimpleNamespace(
            payload=tmp_path, root=tmp_path / "manager",
            command=["update", "--project", "sample", *mode, *budget],
            repair_update=False, install_source=None,
        )
        assert bootstrap_update.execute(args, driver, source_digest="a" * 64) == (
            0 if "--detach" in mode else 23
        )
    requests = core.status(root=queue)
    assert len(requests) == (1 if shared else 4)
    assert [request.id for request in requests if request.state == "pending"] == [requests[-1].id]
    for request in requests:
        assert request.runner.cwd == str(tmp_path)
        if request != requests[-1]:
            assert request.exit_code == 125 and request.started_at is None
            assert "superseded by update request" in request.error
        assert request.spec.argv == (
            "--project", "sample", "--engine-acquisition-timeout", "600.0",
        )
    if not shared:
        attempts = [
            request.runner.argv[request.runner.argv.index("--bootstrap-attempt") + 1]
            for request in requests
        ]
        assert len(set(attempts)) == 4
    else:
        assert "--bootstrap-attempt" not in requests[0].runner.argv
    assert json.loads(capsys.readouterr().out)["request_id"] == requests[0 if shared else 2].id
    assert events == ["worker", "wait", "worker", "wait", "worker", "drain", "wait"]


@pytest.mark.parametrize("flags", [
    ["--auth-report", "--json"], ["--request-status", "existing", "--json"],
    ["--recovery-status", "--json"], ["--recover-for-repair", "--json"],
    ["--recover-after-restart", "--json"],
])
def test_request_controls_use_the_current_cli_without_installation(
    tmp_path, monkeypatch, capsys, flags,
):
    adapter = self_install._runtime_installer()
    import bootstrap_update

    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    monkeypatch.setenv(TIMEOUT_ENV, "nan")
    monkeypatch.setattr(adapter, "_execution_driver", lambda payload: object())
    monkeypatch.setattr(adapter, "_install", lambda *a: pytest.fail("inspection installed Manager"))
    monkeypatch.setattr(adapter, "_source_digest", lambda *a: pytest.fail("inspection retained source"))
    monkeypatch.setattr(bootstrap_update, "execute", lambda *a, **k: pytest.fail("inspection enqueued"))

    def control(arguments):
        assert arguments == flags
        print('{"fixture":true}')
        return 0

    monkeypatch.setattr(update_driver_cli, "cmd_update_request", control)
    assert adapter.main(["--root", str(tmp_path), "--", "update", *flags]) == 0
    assert capsys.readouterr().out == '{"fixture":true}\n'


@pytest.mark.parametrize("flags", [
    ["--auth-report"], ["--request-status", "existing"], ["--recovery-status"],
    ["--recover-for-repair"], ["--recover-after-restart"], ["--request-status", ""],
])
def test_request_controls_refuse_installation_submission(flags):
    execute = runpy.run_path(str(SCRIPT))["execute"]
    with pytest.raises(ValueError):
        execute(SimpleNamespace(command=["update", *flags]), object(), source_digest="a" * 64)


@pytest.mark.parametrize("value", ["nan", "0", "3601"])
def test_invalid_bootstrap_budget_refuses_before_retention_or_submission(tmp_path, monkeypatch, value):
    execute = runpy.run_path(str(SCRIPT))["execute"]
    import bootstrap_inputs

    monkeypatch.setenv(TIMEOUT_ENV, value)
    monkeypatch.setattr(bootstrap_inputs, "retain", lambda *a, **k: pytest.fail("invalid inputs retained"))
    args = SimpleNamespace(command=["update"], payload=tmp_path)
    with pytest.raises(ValueError, match=TIMEOUT_ENV):
        execute(args, object(), source_digest="a" * 64)


@pytest.mark.parametrize("context", ["relative", "missing"])
def test_invalid_bootstrap_context_refuses_before_retention(tmp_path, monkeypatch, context):
    execute = runpy.run_path(str(SCRIPT))["execute"]
    import bootstrap_inputs

    directory = "relative" if context == "relative" else str(tmp_path / "missing")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {
        "AGENT_WORKTREES_UPDATE_CONTEXT": directory,
    })
    monkeypatch.setattr(bootstrap_inputs, "retain", lambda *a, **k: pytest.fail("invalid inputs retained"))
    args = SimpleNamespace(command=["update"], root=tmp_path)
    with pytest.raises(ValueError, match="existing absolute directory"):
        execute(args, object(), source_digest="a" * 64)


def test_invalid_source_anchor_refuses_before_retention(monkeypatch):
    from worktree_manager.source_config import SOURCE_ANCHOR_ENV
    import bootstrap_inputs

    execute = runpy.run_path(str(SCRIPT))["execute"]
    monkeypatch.setenv(SOURCE_ANCHOR_ENV, "relative")
    monkeypatch.setattr(bootstrap_inputs, "retain", lambda *a, **k: pytest.fail("invalid inputs retained"))
    with pytest.raises(ValueError, match="source anchor must be absolute"):
        execute(SimpleNamespace(command=["update"]), object(), source_digest="a" * 64)


def test_bootstrap_source_identity_is_captured_before_retention(tmp_path, monkeypatch):
    execute = runpy.run_path(str(SCRIPT))["execute"]
    import bootstrap_inputs

    current = ["original-source"]
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: current[0])
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})

    def retain(payload, root, *, expected_payload_digest):
        assert expected_payload_digest is None
        current[0] = "changed-source"
        return payload, True

    def enqueue(spec, **kwargs):
        assert spec.source == "original-source"
        return SimpleNamespace(id="fixture", log_path=tmp_path / "log")

    monkeypatch.setattr(bootstrap_inputs, "retain", retain)
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        current_authority=_legacy_authority,
        enqueue=enqueue, default_root=lambda: tmp_path, drain=lambda: None,
        wait=lambda *a, **k: SimpleNamespace(state="completed", exit_code=23),
    )
    args = SimpleNamespace(
        command=["update", "--supervised-debug"], root=tmp_path / "manager",
        payload=tmp_path, install_source=None, repair_update=False,
    )
    assert execute(args, driver, source_digest="a" * 64) == 23


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("shell_handoff", [False, True])
def test_relative_source_keeps_its_admission_anchor_without_the_caller_directory(
    tmp_path, monkeypatch, changed, shell_handoff,
):
    from worktree_manager import source_config
    import bootstrap_inputs

    execute = runpy.run_path(str(SCRIPT))["execute"]
    adapter = self_install._runtime_installer()
    caller, retained = tmp_path / "caller", tmp_path / "retained-controller"
    caller.mkdir()
    retained.mkdir()
    root = tmp_path / "manager"
    source_config.set_source(repo="../repository", root=root)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    monkeypatch.delenv(source_config.SOURCE_ANCHOR_ENV, raising=False)
    monkeypatch.chdir(caller)
    if shell_handoff:
        monkeypatch.setenv(source_config.SOURCE_ANCHOR_ENV, str(caller))
        monkeypatch.chdir(retained)
    monkeypatch.setattr(core, "default_root", lambda: tmp_path / "driver")
    monkeypatch.setattr(bootstrap_inputs, "retain", lambda *a, **k: (retained, True))
    drain = core.drain
    acquired = []

    def participant(transaction):
        request = transaction.request
        assert Path(request.spec.cwd) == caller and Path(request.runner.cwd) == retained
        monkeypatch.chdir(retained)
        caller.rmdir()
        if changed:
            source_config.set_source(repo="../other-repository", root=root)
        with update_driver_cli._request_environment(
            request.spec.context, cwd=request.spec.cwd,
        ):
            adapter._validate_update_request(SimpleNamespace(admitted_source=request.spec.source))
            acquired.append(source_config.absolute_local_source(self_install.manager_repo(root)))
        assert adapter.os.environ.get(source_config.SOURCE_ANCHOR_ENV) == (
            str(caller) if shell_handoff else None
        )
        return 23

    monkeypatch.setattr(core, "drain", lambda: drain(executor=participant))
    args = SimpleNamespace(
        payload=tmp_path, root=root, install_source=None,
        command=["update", "--supervised-debug"], repair_update=False,
    )
    if changed:
        with pytest.raises(RuntimeError, match="Manager source changed"):
            execute(args, core, source_digest="a" * 64)
        assert acquired == []
    else:
        assert execute(args, core, source_digest="a" * 64) == 23
        assert acquired == [str(tmp_path / "repository")]
    assert not caller.exists()


@pytest.mark.parametrize("automatic", [False, True])
def test_default_update_uses_supported_manual_repair_without_replaying_failure(
    tmp_path, monkeypatch, automatic,
):
    execute = runpy.run_path(str(SCRIPT))["execute"]
    root = tmp_path / "driver"
    monkeypatch.setattr(core, "default_root", lambda: root)
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})
    old = core.enqueue(
        core.RequestSpec("old-attempt"),
        runner=core.TrustedRunner((sys.executable,), str(tmp_path)),
    )
    drain = core.drain

    def interrupt(transaction):
        raise RuntimeError("original interrupted attempt")

    with pytest.raises(RuntimeError, match="original interrupted attempt"):
        drain(executor=interrupt)
    before = core.status(old.id)
    assert before.unresolved and before.state == "interrupted"
    executed = []
    monkeypatch.setattr(
        core, "drain",
        lambda: drain(executor=lambda transaction: executed.append(transaction.request.id) or 23),
    )
    args = SimpleNamespace(
        payload=tmp_path, root=tmp_path / "manager",
        command=["update", "--supervised-debug", *(["--auto"] if automatic else [])],
        repair_update=True, install_source=None,
    )
    if automatic:
        with pytest.raises(core.InterruptedError, match="queue fenced"):
            execute(args, core, source_digest="a" * 64)
        assert core.status() == (before,)
        assert not executed
        return
    assert execute(args, core, source_digest="a" * 64) == 23
    old, new = core.status()
    assert old.state == before.state and old.error == before.error
    assert old.spec == before.spec and old.runner == before.runner
    assert old.finished_at == before.finished_at and old.exit_code is None
    assert old.recovery_authority == "operator_repair"
    assert old.settled_at is None and old.settlement_kind is None
    assert old.released_at is not None
    assert new.id != old.id and executed == [new.id]
    assert new.state == "completed" and new.exit_code == 23


@pytest.mark.parametrize("relative", [False, True])
def test_automatic_whole_update_rechecks_its_saved_hold_before_construction(
    tmp_path, monkeypatch, relative,
):
    execute = runpy.run_path(str(SCRIPT))["execute"]
    monkeypatch.setattr(core, "default_root", lambda: tmp_path / "driver")
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})
    saved = tmp_path / "saved-hold.json"
    saved.write_text('{"active": false}', encoding="utf-8")
    other = tmp_path / "worker-hold.json"
    other.write_text('{"active": false}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", saved.name if relative else str(saved))
    import bootstrap_inputs

    retained = tmp_path / "retained-controller"
    retained.mkdir()
    monkeypatch.setattr(bootstrap_inputs, "retain", lambda *a, **k: (retained, True))
    drain = core.drain

    def owner():
        saved.write_text('{"active": true}', encoding="utf-8")
        monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(other))
        drain(executor=lambda transaction: pytest.fail("held update started construction"))

    monkeypatch.setattr(core, "drain", owner)
    args = SimpleNamespace(
        payload=tmp_path, root=tmp_path / "manager", install_source=None,
        command=["update", "--supervised-debug", "--auto"], repair_update=False,
    )
    assert execute(args, core, source_digest="a" * 64) == 3
    request, = core.status()
    assert request.spec.killswitch_path == str(saved)
    assert request.state == "completed" and request.started_at is None
    assert not request.log_path.exists()


def test_outer_update_does_not_dispatch_a_second_suite_after_its_queued_result(tmp_path, monkeypatch):
    adapter = self_install._runtime_installer()
    import bootstrap_update

    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    monkeypatch.setattr(adapter, "_execution_driver", lambda payload: object())
    monkeypatch.setattr(adapter, "_automatic_bootstrap_refusal", lambda args: None)
    monkeypatch.setattr(adapter, "_installation_custody", lambda: nullcontext(None))
    monkeypatch.setattr(adapter, "_source_digest", lambda payload: "a" * 64)
    monkeypatch.setattr(bootstrap_update, "execute", lambda *a, **k: 23)
    monkeypatch.setattr(
        adapter, "_finish", lambda *a: pytest.fail("whole update dispatched twice"),
    )
    assert adapter.main(["--root", str(tmp_path), "--", "update"]) == 23


@pytest.mark.parametrize("failure", [None, "source", "context"])
def test_update_participant_retains_custody_through_manager_and_suite(
    tmp_path, monkeypatch, failure,
):
    adapter = self_install._runtime_installer()
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    events = []
    saved = tmp_path / "saved-hold.json"
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(tmp_path / "worker-hold.json"))

    class Custody:
        root = tmp_path
        request_id = "fixture"

        def __enter__(self):
            events.append("owned")
            return self

        def __exit__(self, *_args):
            events.append("released")

    caller = tmp_path / "caller"
    caller.mkdir()
    spec = core.RequestSpec(
        "manager", ("--project", "sample"), source="admitted-source",
        context=(
            ("WORKTREE_MANAGER_ROOT", str(tmp_path)),
            ("AGENT_WORKTREES_UPDATE_CONTEXT", str(caller)),
        ), killswitch_path=str(saved),
    )
    monkeypatch.setattr(adapter, "_execution_driver", lambda payload: SimpleNamespace(
        status=lambda identifier, **kwargs: SimpleNamespace(spec=spec),
    ))
    monkeypatch.setattr(
        update_driver_cli, "_source_key",
        lambda: "changed-source" if failure == "source" else spec.source,
    )

    def policy(args):
        assert events == ["owned"]
        assert adapter.os.environ["BOOTSTRAP_KILLSWITCH_STATE_FILE"] == str(saved)

    monkeypatch.setattr(adapter, "_automatic_bootstrap_refusal", policy)
    monkeypatch.setattr(adapter, "_installation_custody", Custody)
    monkeypatch.setattr(adapter, "_install", lambda args: events.append("manager-built") or tmp_path)

    def finish(args, slot, command):
        assert events == ["owned", "manager-built"]
        assert command == ["update", "--project", "sample"]
        events.append("suite-finished")
        return 23

    monkeypatch.setattr(adapter, "_finish", finish)
    if failure:
        if failure == "context":
            caller.rmdir()
        error = RuntimeError if failure == "source" else ValueError
        with pytest.raises(error):
            adapter.main([
                "--root", str(tmp_path), "--update-participant", "--source-digest", "a" * 64,
                "--", "update", "--project", "sample",
            ])
        assert events == ["owned", "released"]
        return
    assert adapter.main([
        "--root", str(tmp_path), "--update-participant", "--source-digest", "a" * 64,
        "--", "update", "--project", "sample",
    ]) == 23
    assert events == ["owned", "manager-built", "suite-finished", "released"]
    assert adapter.os.environ["BOOTSTRAP_KILLSWITCH_STATE_FILE"] == str(tmp_path / "worker-hold.json")
