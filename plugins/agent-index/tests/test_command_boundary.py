"""Host lifecycle commands are standalone and keep the engine split intact."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_index import __main__ as cli

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "arguments,passive",
    [
        (["start"], False),
        (["serve"], False),
        (["__cell-start"], False),
        (["start", "--passive"], True),
    ],
)
def test_public_cli_runs_local_service_shell(monkeypatch, arguments, passive):
    calls = []
    monkeypatch.setattr(
        cli,
        "serve",
        lambda config, passive=False: calls.append((config.host, config.port, passive)),
    )

    assert cli.main(arguments) == 0
    assert calls == [("127.0.0.1", 0, passive)]


def test_restart_delegates_to_graceful_deploy(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli,
        "cmd_deploy",
        lambda args: calls.append(
            (args.health_timeout, args.drain_timeout, args.force, args.recover, args.json)
        )
        or 0,
    )

    assert cli.main(["restart"]) == 0
    assert calls == [(60.0, 300.0, False, False, False)]


def test_installers_are_base_only_and_never_implicitly_start_engine():
    """The [store] extra (numpy/pyarrow/lancedb/tree-sitter*) is the light,
    torch-free vector-store stack a HOST's own versioned service runtime
    needs to search/index locally -- distinct from the durable, heavy
    [engine] (torch) extra, which is provisioned exclusively by the
    `engine`/`engine-update` verbs (durable-vs-versioned-runtime.md). The
    installers may select [store] for a host role, but must never pull in
    [engine] or implicitly start the engine daemon as a side effect of a
    routine install/update.
    """
    ps = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert '"$PluginDir[store]"' in ps
    assert '"${PLUGIN_DIR}[store]"' in sh
    assert '[store,engine]' not in ps.split("function Install-Runtime {", 1)[1].split(
        "function Install-Engine {", 1
    )[0]
    assert '[store,engine]' not in sh.split("_ensure_runtime() {", 1)[1].split(
        "_install_engine() {", 1
    )[0]
    ps_actions = ps.split("switch ($Action) {", 1)[1]
    sh_actions = sh.split('case "$ACTION" in', 1)[1]
    for action, following in (("install", "update"), ("update", "ensure")):
        ps_branch = ps_actions.split(f"'{action}' {{", 1)[1].split(
            f"'{following}'", 1
        )[0]
        sh_branch = sh_actions.split(f"{action})", 1)[1].split(
            f"{following})", 1
        )[0]
        assert "Invoke-ServiceCutover" in ps_branch
        assert "Install-LogonAutostart" in ps_branch
        assert "Install-Engine" not in ps_branch
        assert "_service_cutover || _ensure_running" in sh_branch
        assert "_install_logon_autostart" in sh_branch
        assert "_install_engine" not in sh_branch
        assert "_ensure_engine" not in sh_branch

    ps_ensure = ps_actions.split("'ensure' {", 1)[1].split("'register-tasks'", 1)[0]
    sh_ensure = sh_actions.split("ensure)", 1)[1].split("stamp)", 1)[0]
    assert "Ensure-Running; Install-LogonAutostart" in ps_ensure
    assert "_ensure_running; _install_logon_autostart" in sh_ensure


def test_installers_preserve_two_step_cuda_engine_swap():
    ps = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert 'AGENT_INDEX_TORCH_INDEX' in ps
    assert '--no-deps --reinstall-package torch torch' in ps
    assert '"$PluginDir[store,engine]"' in ps
    assert "'engine-update' { if (Install-Engine -Upgrade) { Restart-EngineDaemon } }" in ps

    assert 'AGENT_INDEX_TORCH_INDEX' in sh
    assert '--no-deps --reinstall-package torch torch' in sh
    assert '"$PLUGIN_DIR[store,engine]"' in sh
    assert 'engine-update)                                                  # rebuild durable engine venv + restart daemon (decoupled from service update)' in sh


def test_versioned_activate_legacy_stop_guard_never_fires_on_the_build_target():
    """`Invoke-VersionedActivate`'s one-time legacy-migration guard must gate on
    the REAL historical `.venv` path, never on the variable the versioned-
    runtime block repoints at the freshly-built `versions/<v>` slot -- that
    slot is always a real, non-link directory, so gating on it fires on
    EVERY routine update and force-stops the service *and* the durable engine
    each time (the "kill the detached child" / "guard on the real link path,
    not the built slot" gotchas in service-lifecycle-supervision.md; this
    exact class of bug previously regressed agent-dispatch). Caught live on
    tmichon-cloud1: a routine `install.ps1 update` stopped the already-warm,
    healthy durable engine daemon even though nothing about the engine or its
    torch/model stack had changed.
    """
    ps = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    assert "$LegacyVenvDir = $VenvDir" in ps
    activate_fn = ps.split("function Invoke-VersionedActivate {", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "Test-Path $LegacyVenvDir" in activate_fn
    assert "Test-VenvIsLink $LegacyVenvDir" in activate_fn
    assert "$LinkDir" not in activate_fn


def test_stop_and_uninstall_also_stop_the_durable_engine_daemon():
    """The durable engine daemon (daemon.py) is a separate detached process
    from the light service -- stopping only the service, or only the engine's
    scheduled task, can leave it running indefinitely (the "kill the detached
    child, not just the task" gotcha in service-lifecycle-supervision.md,
    since the daemon can be started directly via `engine start` /
    Ensure-Running, outside any task-tracked process tree). `Invoke-Stop` /
    `_stop` -- which `Invoke-Uninstall` / `_uninstall` both call first -- must
    explicitly stop it via the CLI's own `engine stop` (pid-file-based),
    not just tear down a scheduled task/systemd unit that may not even own it.
    """
    ps = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")

    ps_stop = ps.split("function Invoke-Stop {", 1)[1].split(
        "\n}\n", 1
    )[0]
    assert "-m agent_index engine stop" in ps_stop

    sh_stop = sh.split("_stop() {", 1)[1].split("\n}\n", 1)[0]
    assert "-m agent_index engine stop" in sh_stop


def test_default_durable_autostart_stays_unprivileged_and_tasks_remain_opt_in():
    ps = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")

    start_fn = ps.split("function Start-LauncherDetached {", 1)[1].split(
        "\nfunction Install-LogonAutostartEntry {", 1
    )[0]
    autostart_fn = ps.split("function Install-LogonAutostartEntry {", 1)[1].split(
        "\nfunction Install-LogonAutostart {", 1
    )[0]
    assert "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" in ps
    assert "Start-Process -FilePath 'conhost.exe'" in start_fn
    assert "Invoke-ScopedElevation" not in autostart_fn
    assert "Get-ScheduledTask -TaskName $ScheduledTaskName" in autostart_fn
    assert "Remove-LogonAutostart -Name $Name" in autostart_fn

    register_tasks = ps.split("function Invoke-RegisterTasks {", 1)[1].split(
        "\nfunction Register-EngineDaemon {", 1
    )[0]
    assert "Invoke-ScopedElevation -ElevAction 'register-tasks'" in register_tasks
    assert "Remove-LogonAutostart -Name $TaskName" in register_tasks
    assert "Remove-LogonAutostart -Name $EngineTaskName" in register_tasks
    assert "Register-EngineDaemon" in register_tasks
    assert "Install-Service" in register_tasks
    assert "Install-LogonAutostart" not in register_tasks

    assert 'SERVICE_LAUNCHER="$INSTALL_DIR/service.sh"' in sh
    assert 'ENGINE_LAUNCHER="$ENGINE_HOME/engine.sh"' in sh
    assert "ExecStart=$SERVICE_LAUNCHER" in sh
    assert "ExecStart=$ENGINE_LAUNCHER" in sh
    assert "_install_service --no-restart" in sh

def test_lightweight_mcp_reports_unavailable_without_installing(monkeypatch, capsys):
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
    assert cli.main(["mcp"]) == 2
    assert "will not install host dependencies" in capsys.readouterr().err


@pytest.mark.parametrize("managed", [False, True])
def test_workers_preserve_immutable_python_and_managed_containment(
    tmp_path, monkeypatch, managed
):
    from agent_index.indexing import runner

    if managed:
        monkeypatch.setenv("AGENT_INDEX_MANAGED_PYTHON", sys.executable)
    else:
        monkeypatch.delenv("AGENT_INDEX_MANAGED_PYTHON", raising=False)
    monkeypatch.setattr(runner, "detached_kwargs", lambda: {"legacy_detach": True})
    monkeypatch.setattr(runner, "no_window_kwargs", lambda: {"managed_containment": True})
    calls = []
    instance = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    with monkeypatch.context() as process_patch:
        process_patch.setattr(
            runner.subprocess, "Popen",
            lambda argv, **kwargs: calls.append((argv, kwargs)),
        )
        runner.TaskRunner._spawn_worker(instance, "synthetic-task")
    argv, kwargs = calls[0]
    assert argv[1:5] == ["-I", "-B", "-X", "utf8"]
    assert kwargs.get("managed_containment", False) == managed
    assert kwargs.get("legacy_detach", False) != managed
    # Exercise the worker's exact Python flags, without running an indexing job.
    probe = runner.subprocess.run(
        [sys.executable, *argv[1:5], "-c", "import sys; assert sys.dont_write_bytecode"],
        check=False, capture_output=True, text=True,
    )
    assert probe.returncode == 0, probe.stderr
