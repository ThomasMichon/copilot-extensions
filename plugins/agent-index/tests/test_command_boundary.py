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
    ps = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert '"$PluginDir[store]"' not in ps
    assert '"$PLUGIN_DIR[store]"' not in sh
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
        assert "Install-Engine" not in ps_branch
        assert "_service_cutover || _ensure_running" in sh_branch
        assert "_install_engine" not in sh_branch
        assert "_ensure_engine" not in sh_branch


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
