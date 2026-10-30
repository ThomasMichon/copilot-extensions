"""Fixed native phases retain admission, exact results and durable submission."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

import pytest

from agent_worktrees import update_launch_cli as launch
from agent_worktrees import update_driver_json_cli as boundary


def _args(**values):
    return argparse.Namespace(
        **dict(
            phase="bootstrap", status=None, snapshot=None, detach=False, defer_worktrees=False,
            no_update=False, with_reconcile=False,
        )
        | values,
    )


def test_fixed_phase_has_no_external_action_protocol(monkeypatch):
    seen = {}

    def dispatch(verb, flags, args, execute):
        seen.update(verb=verb, flags=flags, execute=execute)
        return 7

    monkeypatch.setattr(boundary, "_run_request", dispatch)
    assert launch.cmd_launch_update(_args(defer_worktrees=True)) == 7
    assert seen == dict(
        verb="launch-update", flags=["bootstrap", "--defer-worktrees"], execute=launch._execute,
    )


def test_automatic_submission_uses_the_existing_execution_owner(monkeypatch):
    seen = []
    monkeypatch.setattr(boundary, "_run_request", lambda verb, flags, args, execute: (
        seen.append((verb, flags, args.detach, execute)) or 0
    ))
    assert launch.cmd_launch_update(_args(
        phase="automatic", auto=True, detach=True, no_update=True, with_reconcile=True,
    )) == 0
    assert seen == [(
        "launch-update", ["automatic", "--auto", "--no-update", "--with-reconcile"], True, launch._execute,
    )]


@pytest.mark.parametrize("no_update,with_reconcile,changed,expected", [
    (False, True, True, ["bootstrap", "reconcile", "staged"]),
    (False, False, True, ["bootstrap", "staged"]),
    (False, True, False, ["bootstrap", "reconcile"]),
    (True, True, False, ["reconcile"]),
    (True, False, False, []),
])
def test_automatic_work_runs_only_after_dispatch_under_one_custody(
    monkeypatch, tmp_path, capsys, no_update, with_reconcile, changed, expected,
):
    acquired, executed = [], []
    status = tmp_path / "owned-stage.json"
    monkeypatch.setattr(launch.update_stage, "status_path", lambda: status)
    monkeypatch.setattr(launch.update_stage, "stage", lambda: (
        acquired.append(True) or dict(stage_done=True, acquisition_ok=True, plugin_changed=changed)
    ))
    monkeypatch.setattr(launch, "_staged_snapshot", lambda path: ({}, "fresh-digest"))
    monkeypatch.setattr(boundary, "_run_request", lambda *a: pytest.fail("recursive submission"))

    def execute(args):
        executed.append(args)
        print(json.dumps({"phase": args.phase, "executed": []}))
        return 0

    monkeypatch.setattr(launch, "_execute_phase", execute)
    assert launch._execute(_args(
        phase="automatic", no_update=no_update, with_reconcile=with_reconcile,
    )) == 0
    assert acquired == ([] if no_update else [True])
    assert [args.phase for args in executed] == expected
    if changed and not no_update:
        assert executed[0].defer_worktrees is True
        assert executed[-1].status == str(status)
        assert executed[-1].snapshot == "fresh-digest"
    result = json.loads(capsys.readouterr().out)
    assert [item["phase"] for item in result["executed"]] == expected


@pytest.mark.parametrize("stage", [
    {"stage_done": False},
    {"stage_done": True, "skipped": "locked"},
    {"stage_done": True, "acquisition_ok": False},
])
def test_automatic_acquisition_refusal_never_starts_installers(monkeypatch, capsys, stage):
    monkeypatch.setattr(launch.update_stage, "stage", lambda: stage)
    monkeypatch.setattr(launch, "_execute_phase", lambda *a: pytest.fail("installer started"))
    assert launch._execute(_args(phase="automatic", with_reconcile=True)) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "launch-phase-invalid"


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("include_diagnostic", [False, True])
def test_managed_worktrees_retains_refusal_but_runs_independent_bootstrap_and_reconcile(
    monkeypatch, capsys, changed, include_diagnostic,
):
    diagnostic = dict(
        service="agent-worktrees", phase="runtime", reason="deployment-managed",
        message="Worktrees runtime is externally managed",
    )
    stage = dict(
        stage_done=True, acquisition_ok=True, plugin_changed=changed,
        runtime_apply_blocked="deployment-managed",
    )
    if include_diagnostic:
        stage["runtime_apply_diagnostic"] = diagnostic
    monkeypatch.setattr(launch.update_stage, "stage", lambda: stage)
    plans = iter([
        dict(action="self-update", diagnostics=[diagnostic], updates=[
            dict(service="repository-bootstrap", argv=["repository-installer"]),
        ]),
        dict(action="continue", diagnostics=[diagnostic]),
    ])
    monkeypatch.setattr(launch.update_cli, "_core_helper", lambda *a: lambda: next(plans))
    monkeypatch.setattr(launch, "_staged_snapshot", lambda *a: pytest.fail("denied staged apply"))
    monkeypatch.setattr(launch, "execution_diagnostic", lambda *a, **kw: None)
    observed = []
    monkeypatch.setattr(launch.reconcile, "_run_plan_step", lambda argv, update, log, **kw: (
        observed.append(update["service"]) or 0
    ))

    def reconcile(args):
        observed.append("reconcile")
        print(json.dumps({"executed": []}))
        return 0

    monkeypatch.setattr(launch.update_cli, "cmd_reconcile_plugins", reconcile)
    assert launch._execute(_args(phase="automatic", with_reconcile=True)) == 1
    assert observed == ["repository-bootstrap", "reconcile"]
    result = json.loads(capsys.readouterr().out)
    assert [item["phase"] for item in result["executed"]] == ["bootstrap", "reconcile"]
    assert all(item["returncode"] == 0 for item in result["executed"])
    assert result["diagnostics"][0]["reason"] == "deployment-managed"
    if include_diagnostic:
        assert result["diagnostics"] == [diagnostic]


@pytest.mark.parametrize("failed", ["bootstrap", "reconcile", "staged"])
def test_automatic_phase_failure_is_not_a_successful_completion(monkeypatch, capsys, failed):
    monkeypatch.setattr(launch.update_stage, "stage", lambda: dict(
        stage_done=True, acquisition_ok=True, plugin_changed=True,
    ))
    monkeypatch.setattr(launch, "_staged_snapshot", lambda path: ({}, "digest"))
    executed = []

    def execute(args):
        executed.append(args.phase)
        print(json.dumps({"executed": []}))
        return 7 if args.phase == failed else 0

    monkeypatch.setattr(launch, "_execute_phase", execute)
    assert launch._execute(_args(phase="automatic", with_reconcile=True)) == 1
    assert executed == ["bootstrap", "reconcile", "staged"]
    result = json.loads(capsys.readouterr().out)
    assert next(item for item in result["executed"] if item["phase"] == failed)["returncode"] == 7


@pytest.mark.parametrize("lost_custody", [False, True])
def test_automatic_real_reconcile_error_preserves_results_or_stops_on_custody_loss(
    monkeypatch, tmp_path, capsys, lost_custody,
):
    from update_driver import ParticipantError

    monkeypatch.setattr(launch.update_stage, "stage", lambda: dict(
        stage_done=True, acquisition_ok=True, plugin_changed=True,
    ))
    monkeypatch.setattr(launch.update_cli.worktree_identity, "_find_repo_dir", lambda: tmp_path)
    monkeypatch.setattr(launch, "_staged_snapshot", lambda path: ({}, "digest"))
    actual_execute = launch._execute_phase
    observed = []

    def execute(args):
        observed.append(args.phase)
        if args.phase == "reconcile":
            return actual_execute(args)
        print(json.dumps({"phase": args.phase, "executed": []}))
        return 0

    def reconcile(*args, **kwargs):
        if lost_custody:
            raise ParticipantError("synthetic custody loss")
        raise ValueError("synthetic adopted-plugin failure")

    monkeypatch.setattr(launch, "_execute_phase", execute)
    monkeypatch.setattr(launch.reconcile, "apply_plan", reconcile)
    if lost_custody:
        with pytest.raises(ParticipantError, match="custody loss"):
            launch._execute(_args(phase="automatic", with_reconcile=True))
        assert observed == ["bootstrap", "reconcile"]
    else:
        assert launch._execute(_args(phase="automatic", with_reconcile=True)) == 1
        assert observed == ["bootstrap", "reconcile", "staged"]
        result = json.loads(capsys.readouterr().out)
        assert [item["phase"] for item in result["executed"]] == observed
        assert result["executed"][0]["returncode"] == 0
        failed = result["executed"][1]
        assert failed["returncode"] == 1 and failed["result"]["ok"] is False
        assert failed["result"]["reason"] == "synthetic adopted-plugin failure"
        assert result["executed"][2]["returncode"] == 0


def test_reconcile_phase_calls_canonical_executor_only_under_request_custody(monkeypatch):
    seen = []
    monkeypatch.setattr(launch.update_cli, "cmd_reconcile_plugins", lambda args: (
        seen.append(args.apply) or 7
    ))
    monkeypatch.setattr(boundary, "_run_request", lambda verb, flags, args, execute: (
        pytest.fail("unexpected recursive enqueue")
    ))
    assert launch._execute(_args(phase="reconcile")) == 7
    assert seen == [True]


@pytest.mark.parametrize("phase", ["bootstrap", "reconcile"])
@pytest.mark.parametrize("adopted", [False, True])
def test_fixed_cli_discovers_optional_project_from_cwd(tmp_path, monkeypatch, phase, adopted):
    from agent_worktrees import front_door_cli, project_routing

    directory = tmp_path / ("adopted" if adopted else "neutral")
    directory.mkdir()
    monkeypatch.chdir(directory)
    seen = []

    def resolve(override):
        assert override is None
        assert launch.Path.cwd() == directory
        seen.append("resolved")
        return ("fixture-project" if adopted else None), None

    launch.update_cli.cfg.set_active_project(None)
    try:
        assert front_door_cli.resolve_invocation_project(
            ["launch-update", phase], None, resolve,
            lambda path: True, project_routing._is_no_project_invocation,
        ) is adopted
        assert seen == ["resolved"]
        assert launch.update_cli.cfg.active_project() == ("fixture-project" if adopted else None)
    finally:
        launch.update_cli.cfg.set_active_project(None)


@pytest.mark.parametrize("phase,options", [
    ("bootstrap", {"status": "untrusted.json"}),
    ("staged", {"defer_worktrees": True}),
    ("bootstrap", {"no_update": True}),
    ("reconcile", {"with_reconcile": True}),
    ("automatic", {"status": "untrusted.json"}),
    ("automatic", {"snapshot": "foreign"}),
    ("automatic", {"defer_worktrees": True}),
])
def test_invalid_phase_options_never_submit(tmp_path, monkeypatch, capsys, phase, options):
    monkeypatch.setattr(launch, "_staged_snapshot", lambda path: ({}, "digest"))
    monkeypatch.setattr(boundary, "_run_request", lambda *a: pytest.fail("invalid request submitted"))
    assert launch.cmd_launch_update(_args(phase=phase, **options)) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def _stage(tmp_path, monkeypatch):
    payload = tmp_path / "payload"
    (payload / "scripts").mkdir(parents=True)
    installer = payload / "scripts" / ("install.ps1" if sys.platform == "win32" else "install.sh")
    installer.write_text("fixture")
    root = tmp_path / "runtime"
    path = tmp_path / "stage.json"
    status = dict(stage_done=True, plugin_changed=True, plugin_dir=str(payload), runtime_root=str(root))
    path.write_text(json.dumps(status))
    monkeypatch.setattr(launch.update_stage, "discover_plugin_dir", lambda: (payload, "marketplace"))
    monkeypatch.setattr(launch.update_stage, "fingerprint", lambda p: installer.read_text())
    monkeypatch.setattr(launch.reconcile, "runtime_installer_environment", lambda *a, **kw: (
        {"FIXTURE": "canonical"}, root,
    ))
    monkeypatch.setattr(launch.update_cli.cfg, "active_project", lambda: "demo")
    return path, installer, status


def test_staged_request_binds_status_and_payload_before_enqueue(tmp_path, monkeypatch):
    path, installer, status = _stage(tmp_path, monkeypatch)
    seen = {}
    monkeypatch.setattr(boundary, "_run_request", lambda verb, flags, args, execute: (
        seen.update(flags=flags, detach=args.detach) or 0
    ))
    assert launch.cmd_launch_update(_args(phase="staged", status=str(path), detach=True)) == 0
    digest = seen["flags"][-1]
    assert seen["flags"] == ["staged", "--status", str(path), "--snapshot", digest]
    assert seen["detach"] is True
    installer.write_text("changed payload")
    assert launch._staged_snapshot(path)[1] != digest
    installer.write_text("fixture")
    status["plugin_changed"] = False
    path.write_text(json.dumps(status))
    assert launch._staged_snapshot(path)[1] != digest


@pytest.mark.parametrize("mutation", ["payload", "status", "root", "blocked"])
def test_staged_source_changes_refuse_execution(tmp_path, monkeypatch, capsys, mutation):
    path, installer, status = _stage(tmp_path, monkeypatch)
    digest = launch._staged_snapshot(path)[1]
    if mutation == "payload":
        installer.write_text("different")
    elif mutation == "status":
        status["argv"] = ["malicious"]
        path.write_text(json.dumps(status))
    elif mutation == "root":
        monkeypatch.setattr(launch.reconcile, "runtime_installer_environment", lambda *a, **kw: (
            {}, tmp_path / "changed-root",
        ))
    else:
        status["runtime_apply_blocked"] = "deployment-managed"
        path.write_text(json.dumps(status))
        digest = launch._staged_snapshot(path)[1]
    monkeypatch.setattr(launch.reconcile, "_run_plan_step", lambda *a: pytest.fail("installer executed"))
    assert launch._execute(_args(phase="staged", status=str(path), snapshot=digest)) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "launch-phase-invalid"


def test_staged_executor_reconstructs_argv_and_environment(tmp_path, monkeypatch, capsys):
    path, installer, status = _stage(tmp_path, monkeypatch)
    status.update(argv=["not-trusted"], environment={"FIXTURE": "not-trusted"})
    path.write_text(json.dumps(status))
    digest = launch._staged_snapshot(path)[1]
    observed = []
    monkeypatch.setattr(launch, "execution_diagnostic", lambda update, **kw: (
        observed.append((update.copy(), kw)) or None
    ))
    monkeypatch.setattr(launch.reconcile, "_run_plan_step", lambda argv, update, log, **kw: 7)
    assert launch._execute(_args(phase="staged", status=str(path), snapshot=digest)) == 1
    update, kwargs = observed[0]
    assert kwargs == {"staged": True}
    assert str(installer) in update["argv"]
    if installer.suffix == ".ps1":
        assert update["argv"][:3] == ["pwsh", "-NoProfile", "-File"]
    assert update["argv"][-1] == "demo"
    assert update["environment"] == {"FIXTURE": "canonical"}
    assert json.loads(capsys.readouterr().out)["executed"][0]["returncode"] == 7


@pytest.mark.parametrize("denied", [False, True])
def test_bootstrap_replans_and_retains_independent_services(tmp_path, monkeypatch, capsys, denied):
    updates = [
        dict(service="agent-worktrees", phase="runtime", argv=["owned-runtime"]),
        dict(service="repository-bootstrap", argv=["owned-service"]),
    ]
    plans = iter([dict(action="self-update", updates=updates), dict(action="continue")])
    monkeypatch.setattr(launch.update_cli, "_core_helper", lambda *a: lambda: next(plans))
    monkeypatch.setattr(launch, "execution_diagnostic", lambda update, **kw: (
        dict(reason="deployment-managed", message="policy changed")
        if denied and update.get("phase") == "runtime" else None
    ))
    seen = []
    monkeypatch.setattr(launch.reconcile, "_run_plan_step", lambda argv, update, log, **kw: (
        seen.append(update) or 0
    ))
    assert launch._execute(_args()) == (1 if denied else 0)
    assert seen[-1]["service"] == "repository-bootstrap"
    assert seen[-1]["unset_environment"] == []
    assert len(seen) == (1 if denied else 2)
    assert len(json.loads(capsys.readouterr().out)["executed"]) == 2


def test_bootstrap_defers_self_without_deferring_repository_actions(monkeypatch, capsys):
    plans = iter([
        dict(action="self-update", updates=[
            dict(service="agent-worktrees", argv=["not-now"]),
            dict(service="repository-bootstrap", argv=["now"]),
        ]), dict(action="continue"),
    ])
    monkeypatch.setattr(launch.update_cli, "_core_helper", lambda *a: lambda: next(plans))
    seen = []
    monkeypatch.setattr(launch, "_apply", lambda update: seen.append(update["service"]) or {"ok": True})
    assert launch._execute(_args(defer_worktrees=True)) == 0
    assert seen == ["repository-bootstrap"]
    assert "deferred to the staged request" in capsys.readouterr().err


def test_detach_returns_acceptance_without_waiting(tmp_path, monkeypatch, capsys):
    import update_driver as driver
    from types import SimpleNamespace

    monkeypatch.setattr(driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(boundary.cfg, "active_project", lambda: None)
    monkeypatch.setattr(boundary.cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(driver, "runtime_command", lambda *a: (("fixture",), {}))
    monkeypatch.setattr(driver, "enqueue", lambda *a, **kw: SimpleNamespace(
        id="fixture", log_path=tmp_path / "fixture.log",
    ))
    started = []
    monkeypatch.setattr(driver, "ensure_worker", lambda *a, **kw: started.append(True))
    monkeypatch.setattr(driver, "wait", lambda *a, **kw: pytest.fail("detached caller waited"))
    assert launch.cmd_launch_update(_args(detach=True)) == 0
    assert started == [True]
    assert json.loads(capsys.readouterr().out)["action"] == "accepted"


def test_automatic_worker_refusal_is_durable_without_foreground_fallback(
    tmp_path, monkeypatch, capsys,
):
    import agent_procutil
    import update_driver as driver
    from update_driver import core

    home = tmp_path / "home"
    home.mkdir()
    for key in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(key, str(home))
    queue_root = home / ".copilot-extensions" / "update-driver"
    monkeypatch.setattr(core, "default_root", lambda: queue_root)
    monkeypatch.setattr(driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(boundary.cfg, "active_project", lambda: "fixture-project")
    monkeypatch.setattr(boundary.cfg, "install_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(boundary.update_driver_cli, "_invocation_cwd", lambda: tmp_path)
    monkeypatch.setattr(boundary.update_driver_cli, "_request_context", lambda: ())
    monkeypatch.setattr(driver, "runtime_command", lambda *a: (
        (sys.executable, "-c", "raise AssertionError('no foreground execution')"), {},
    ))
    seen = []

    def refuse(*args, **kwargs):
        seen.append(kwargs)
        raise agent_procutil.DaemonSpawnError("independent lifetime unavailable")

    monkeypatch.setattr(agent_procutil, "spawn_daemon", refuse)
    monkeypatch.setattr(driver, "wait", lambda *a, **kw: pytest.fail("rejected caller waited"))
    monkeypatch.setattr(launch, "_execute", lambda *a: pytest.fail("foreground installer fallback"))
    assert launch.cmd_launch_update(_args(phase="automatic", detach=True)) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["reason"] == "update-driver"
    assert "accepted" not in output.out
    assert "worker launch failed" in output.err
    assert len(seen) == 1
    assert seen[0]["breakaway"] is True and seen[0]["contained_fallback"] is False
    request, = driver.status(root=queue_root)
    assert request.state == "pending" and request.exit_code is None
    assert request.error == "worker launch failed: DaemonSpawnError"
    assert request.spec.backend == "engine-launch-update"
    assert request.spec.argv == ("automatic",) and request.spec.project == "fixture-project"


@pytest.mark.parametrize("phase", ["bootstrap", "reconcile"])
def test_actual_fixed_request_has_durable_result_on_empty_inventory(tmp_path, monkeypatch, phase):
    from agent_procutil.owned_process import run_owned
    import update_driver as driver

    home = tmp_path / "home"
    home.mkdir()
    for key in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(key, str(home))
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_WORKTREES_PAYLOAD_ROOT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    # Blocking compatibility includes worker/executor startup, unlike detached handoff.
    completion_timeout = 30
    try:
        result = run_owned(
            [sys.executable, "-m", "agent_worktrees", "launch-update", phase],
            timeout=completion_timeout,
        )
    except subprocess.TimeoutExpired as error:
        root = home / ".copilot-extensions" / "update-driver"
        requests = driver.status(root=root)
        logs = [
            row.log_path.read_text(encoding="utf-8")
            for row in requests if row.log_path.exists()
        ]
        worker_log = root / "worker.log"
        if worker_log.exists():
            logs.append(worker_log.read_text(encoding="utf-8"))
        raise AssertionError(
            f"fixed {phase} request exceeded {completion_timeout}s; "
            f"requests={[(row.state, row.error) for row in requests]}; "
            f"stdout={error.stdout!r}; stderr={error.stderr!r}; logs={logs}"
        ) from error
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["executed"] == []
    # The public client must expose the same durable result it observed.
    request_id = result.stderr.split("Update request ", 1)[1].split(";", 1)[0]
    request = driver.status(request_id, root=home / ".copilot-extensions" / "update-driver")
    assert request.state == "completed" and request.exit_code == 0
    assert boundary._result_path(request).is_file()
    assert request.spec.backend == "engine-launch-update"


def test_actual_staged_install_survives_submitter_job_and_preserves_two_results(tmp_path, monkeypatch):
    import os
    import shutil
    import time
    from agent_procutil.owned_process import run_owned
    import update_driver as driver

    if (
        os.environ.get("UPDATE_DRIVER_NATIVE_LIFETIME") != "1"
        or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1"
    ):
        pytest.skip("Explicit production-lifetime qualification; retain contained-suite suppression")
    if os.name == "nt" and not shutil.which("pwsh"):
        pytest.skip("Native PowerShell unavailable")
    home = tmp_path / "home"
    payload = home / ".copilot" / "installed-plugins" / "copilot-extensions" / "agent-worktrees"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "plugin.json").write_text(json.dumps(dict(
        name="agent-worktrees", version="1.0.0", runtimeScope="universal",
    )))
    script = tmp_path / "installer.py"
    gate = tmp_path / "continue"
    started = tmp_path / "started"
    output = tmp_path / "outcome"
    script.write_text(
        "import os,sys,time\nfrom pathlib import Path\n"
        "if os.name=='nt':\n"
        " import ctypes\n"
        " k=ctypes.windll.kernel32; u=ctypes.windll.user32\n"
        " k.GetConsoleWindow.restype=ctypes.c_void_p; u.IsWindowVisible.argtypes=[ctypes.c_void_p]\n"
        " assert not u.IsWindowVisible(k.GetConsoleWindow()), 'visible installer console'\n"
        f"Path({str(started)!r}).write_text('started')\n"
        "print('staged-progress',flush=True)\n"
        "deadline=time.monotonic()+20\n"
        f"while not Path({str(gate)!r}).exists() and time.monotonic()<deadline: time.sleep(.02)\n"
        f"assert Path({str(gate)!r}).exists(), 'caller-owned fixture gate timed out'\n"
        f"code=int(Path({str(gate)!r}).read_text())\n"
        f"Path({str(output)!r}).write_text(str(code))\n"
        "print('staged-result',code,flush=True)\n"
        "sys.exit(code)\n",
    )
    if os.name == "nt":
        python = getattr(sys, "_base_executable", sys.executable)
        (scripts / "install.ps1").write_text(
            "param($Action,$InstallDir)\n"
            "if($Action -ne 'update'){throw 'not update'}\n"
            f"& '{python}' '{script}'\nexit $LASTEXITCODE\n",
        )
    else:
        import shlex
        (scripts / "install.sh").write_text(
            f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))}\n",
        )
    stage = tmp_path / "stage.json"
    stage.write_text(json.dumps(dict(
        stage_done=True, plugin_changed=True, plugin_dir=str(payload),
        runtime_root=str(home / ".agent-worktrees"),
    )))
    for key in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(key, str(home))
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_WORKTREES_PAYLOAD_ROOT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    root = home / ".copilot-extensions" / "update-driver"
    request_ids = []
    for code in (0, 7):
        gate.unlink(missing_ok=True)
        started.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        result = run_owned([
            sys.executable, "-m", "agent_worktrees", "launch-update",
            "staged", "--status", str(stage), "--detach",
        ], timeout=20)
        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        assert receipt["action"] == "accepted"
        request_ids.append(receipt["request_id"])
        request = driver.status(receipt["request_id"], root=root)
        try:
            deadline = time.monotonic() + 15
            while not started.exists() and time.monotonic() < deadline:
                request = driver.status(receipt["request_id"], root=root)
                assert request.state not in {"completed", "interrupted", "blocked"}, (
                    request, request.log_path.read_text() if request.log_path.exists() else "",
                )
                time.sleep(.02)
            assert started.exists(), (
                "worker did not survive submitting caller's Job",
                request.state,
                (root / "worker.log").read_text(),
                request.log_path.read_text() if request.log_path.exists() else "no product log",
            )
            deadline = time.monotonic() + 10
            while (
                (not request.log_path.exists() or "staged-progress" not in request.log_path.read_text())
                and time.monotonic() < deadline
            ):
                time.sleep(.02)
            assert "staged-progress" in request.log_path.read_text(), "live progress was buffered"
            assert not output.exists(), "detached caller waited for installation"
        finally:
            gate.write_text(str(code))
            request = driver.wait(receipt["request_id"], root=root, timeout=20)
        assert request.state == "completed" and request.exit_code == (1 if code else 0)
        summary = json.loads(boundary._result_path(request).read_text())
        assert summary["executed"][0]["returncode"] == code
        assert output.read_text() == str(code)
        assert f"staged-result {code}" in request.log_path.read_text()
    assert len(set(request_ids)) == 2


@pytest.mark.parametrize("phase", ["bootstrap", "reconcile"])
def test_native_phase_streams_real_installer_before_completion(tmp_path, monkeypatch, capfd, phase):
    import threading
    import time

    gate = tmp_path / "continue"
    producer = tmp_path / "producer.py"
    producer.write_text(
        "import sys,time\nfrom pathlib import Path\n"
        "print('live-installer-line',flush=True)\n"
        "deadline=time.monotonic()+10\n"
        f"while not Path({str(gate)!r}).exists() and time.monotonic()<deadline: time.sleep(.02)\n"
        f"assert Path({str(gate)!r}).exists(), 'progress was not observed before release'\n",
    )
    update = dict(
        service="fixture-service", argv=[sys.executable, str(producer)],
        environment={}, unset_environment=[],
    )
    if phase == "bootstrap":
        plans = iter([dict(action="self-update", updates=[update]), dict(action="continue")])
        monkeypatch.setattr(launch.update_cli, "_core_helper", lambda *a: lambda: next(plans))
    else:
        plans = iter([dict(action="reconcile", updates=[update]), dict(action="continue")])
        monkeypatch.setattr(launch.reconcile, "build_plan", lambda *a, **kw: next(plans))
        monkeypatch.setattr(launch.update_cli.worktree_identity, "_find_repo_dir", lambda: tmp_path)
    monkeypatch.setattr(launch, "execution_diagnostic", lambda *a, **kw: None)
    monkeypatch.setattr(launch.reconcile, "execution_diagnostic", lambda *a, **kw: None)
    results, failures = [], []

    def execute():
        try:
            results.append(launch._execute(_args(phase=phase)))
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=execute)
    worker.start()
    output = ""
    try:
        deadline = time.monotonic() + 10
        while "live-installer-line" not in output and time.monotonic() < deadline:
            captured = capfd.readouterr()
            output += captured.err
            assert captured.out == "", "protocol completion preceded installer completion"
            time.sleep(.02)
        assert "live-installer-line" in output
        assert worker.is_alive() and results == [], "progress arrived only after completion"
    finally:
        gate.write_text("continue")
        worker.join(timeout=15)
    assert not worker.is_alive() and failures == []
    assert results == [0]
