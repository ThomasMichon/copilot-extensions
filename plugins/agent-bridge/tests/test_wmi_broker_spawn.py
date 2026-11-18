"""Bridge service and cutover delegate the canonical native lifetime policy."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

import agent_procutil
from agent_procutil import daemon
from agent_bridge import __main__ as core
from agent_bridge import service_process_cli as m

if TYPE_CHECKING:
    from subprocess import Popen

    from agent_procutil.daemon_broker import WindowsDaemonProcess


@pytest.fixture()
def native_launch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Mock:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_daemon_launch_argv", lambda: ["py", "-m", "agent_bridge", "start"])
    launch = Mock(return_value=Mock(pid=1234))
    monkeypatch.setattr(core, "spawn_daemon", launch)
    return launch


def test_spawn_detached_uses_shared_native_policy(native_launch: Mock, tmp_path: Path) -> None:
    assert m._spawn_detached_daemon() is native_launch.return_value

    native_launch.assert_called_once()
    kwargs = native_launch.call_args.kwargs
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["log_path"] == str(tmp_path / "agent-bridge.log")
    assert kwargs["stderr_log_path"] == str(tmp_path / "agent-bridge-err.log")
    assert kwargs["console_children"] is True
    assert kwargs["contained_fallback"] is False


def test_shared_native_failure_is_not_replayed_by_service_adapter(native_launch: Mock) -> None:
    native_launch.side_effect = agent_procutil.DaemonSpawnError("unconfirmed native cleanup")
    with pytest.raises(agent_procutil.DaemonSpawnError):
        m._spawn_detached_daemon()

    native_launch.assert_called_once()


@pytest.mark.parametrize("entry", ["service", "watchdog"])
def test_wmi_refusal_never_creates_a_caller_owned_bridge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, entry: str,
) -> None:
    import subprocess

    from agent_procutil.daemon_instances import daemon_test_lifecycle, inspect_instances
    from agent_bridge import service_process_cli, watchdog

    monkeypatch.setattr(core, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(
        service_process_cli, "_daemon_launch_argv", lambda: [sys.executable, "-c", "pass"],
    )
    monkeypatch.setattr(core, "spawn_daemon", agent_procutil.spawn_daemon)
    monkeypatch.setattr(agent_procutil, "_is_windows", lambda: True)
    monkeypatch.setattr(
        agent_procutil, "_daemon_popen_kwargs",
        lambda **_kwargs: {"creationflags": agent_procutil._CREATE_BREAKAWAY_FROM_JOB},
    )
    failure = agent_procutil.DaemonSpawnError(
        "Windows lifetime broker failed (child-adoption)",
        cleanup_confirmed=True, process_created=False, admission_sent=False,
    )
    native = Mock(side_effect=failure)
    monkeypatch.setattr(daemon, "_durable_windows_launch", native)
    contained = Mock(side_effect=OSError("caller-owned replacement refused by fixture"))
    monkeypatch.setattr(subprocess, "Popen", contained)
    events: list[tuple[str, str]] = []
    exits: list[str] = []
    monkeypatch.setattr(watchdog, "_record_event", lambda event, detail: events.append((event, detail)))
    monkeypatch.setattr(watchdog, "_force_exit", exits.append)
    registry = tmp_path / "instances"
    with daemon_test_lifecycle(
        owner="bridge-wmi-refusal", registry_root=registry,
    ) as environment:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        if entry == "watchdog":
            assert m._watchdog_dead("listener stopped") is False
            assert events == [
                ("replacement-requested", "listener stopped"),
                ("replacement-failed", "DaemonSpawnError; continuing to serve"),
            ]
            assert exits == []
        else:
            with pytest.raises(agent_procutil.DaemonSpawnError) as caught:
                m._spawn_detached_daemon()
            assert caught.value.__cause__ is failure
            assert caught.value.cleanup_confirmed is True
            assert caught.value.admission_sent is False
        assert inspect_instances(registry) == []
    native.assert_called_once()
    contained.assert_not_called()
    assert "contained_fallback" not in (tmp_path / "agent-bridge-err.log").read_text()
    assert "reason=Windows lifetime broker failed (child-adoption);" in (
        tmp_path / "agent-bridge-err.log"
    ).read_text()


def test_passive_generation_never_falls_back_to_contained(native_launch: Mock) -> None:
    native_launch.side_effect = agent_procutil.DaemonSpawnError("native admission refused")
    with pytest.raises(agent_procutil.DaemonSpawnError):
        m._launch_bridge_daemon(
            ["py", "-m", "agent_bridge", "start", "--passive"],
            display="agent-bridge passive generation", contained_last_resort=False,
        )
    native_launch.assert_called_once()
    assert native_launch.call_args.kwargs["contained_fallback"] is False


def test_passive_generation_preserves_actual_native_handle(native_launch: Mock) -> None:
    result = m._launch_bridge_daemon(
        ["py"], display="agent-bridge passive generation", contained_last_resort=False,
    )
    assert result is native_launch.return_value
    assert native_launch.call_args.kwargs["contained_fallback"] is False


def test_other_spawn_errors_are_not_masked_by_service_adapter(native_launch: Mock) -> None:
    native_launch.side_effect = FileNotFoundError("missing executable")
    with pytest.raises(FileNotFoundError):
        m._spawn_detached_argv(["missing.exe"])
    native_launch.assert_called_once()


def test_shared_native_policy_receives_anchored_roots_and_unmodified_argv(
    monkeypatch: pytest.MonkeyPatch, native_launch: Mock, tmp_path: Path,
) -> None:
    from agent_bridge.install_paths import CONFIG_DIR_ENV, INSTALL_DIR_ENV

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core, "_INSTALL_DIR", "root")
    monkeypatch.setenv(INSTALL_DIR_ENV, "root")
    monkeypatch.setenv(CONFIG_DIR_ENV, "configuration")
    argv = ["py", "spaces & 'quotes'", "unicode-\u65e5", ""]
    m._spawn_detached_argv(argv)

    assert native_launch.call_args.args[0] == argv
    kwargs = native_launch.call_args.kwargs
    assert kwargs["env_overrides"][INSTALL_DIR_ENV] == str(tmp_path / "root")
    assert kwargs["env_overrides"][CONFIG_DIR_ENV] == str(tmp_path / "configuration")
    assert kwargs["cwd"] == str(tmp_path / "root")


def test_spawn_detached_happy_path_never_calls_broker(
    monkeypatch: pytest.MonkeyPatch, native_launch: Mock,
) -> None:
    legacy = Mock(side_effect=AssertionError("automatic legacy activation"))
    monkeypatch.setattr(daemon, "spawn_brokered_daemon", legacy)

    m._spawn_detached_argv(["py"])

    legacy.assert_not_called()
    native_launch.assert_called_once()


@pytest.mark.parametrize("passive", [False, True])
def test_python_daemon_bypasses_redirector_and_preserves_venv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, passive: bool,
) -> None:
    from agent_bridge import service_process_cli

    monkeypatch.setattr(
        service_process_cli, "os",
        SimpleNamespace(name="nt", path=os.path, makedirs=os.makedirs, environ=os.environ),
    )
    executable = str(tmp_path / "venv" / "Scripts" / "python.exe")
    base = str(tmp_path / "base" / "python.exe")
    select = Mock(return_value=(base, {"__PYVENV_LAUNCHER__": executable}))
    monkeypatch.setattr(agent_procutil, "direct_python", select, raising=False)
    monkeypatch.setattr(core, "_INSTALL_DIR", str(tmp_path))
    launch = Mock()
    monkeypatch.setattr(core, "spawn_daemon", launch)
    argv = [executable, "-m", "agent_bridge", "start"]
    if passive:
        argv += ["--passive"]
        m._launch_bridge_daemon(argv, display="passive", contained_last_resort=False)
    else:
        m._spawn_detached_argv(argv)
    select.assert_called_once_with(executable)
    assert launch.call_args.args[0] == [base, *argv[1:]]
    assert launch.call_args.kwargs["env_overrides"]["__PYVENV_LAUNCHER__"] == executable
    assert argv[0] == executable, "normalization must not mutate the caller's argv"


def test_watchdog_replacement_uses_delayed_versioned_start(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(sys, "executable", r"C:\venv\python.exe")
    monkeypatch.setattr(
        core, "_spawn_detached_argv", lambda argv: captured.setdefault("argv", argv),
    )
    m._spawn_watchdog_replacement(
        delay=1.5, start_args=["start", "--passive", "--idle-shutdown", "30"],
    )
    argv = captured["argv"]
    assert argv[0] == r"C:\venv\python.exe"
    assert argv[1] == "-c"
    assert "agent_bridge" in argv[2]
    assert argv[3] == "1.5"
    assert argv[4:] == ["start", "--passive", "--idle-shutdown", "30"]


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_watchdog_replacement_executes_platform_handoff(
    monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    import runpy
    from agent_bridge import service_process_cli

    captured: list[list[str]] = []
    calls: list[object] = []
    monkeypatch.setattr(service_process_cli, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(core, "_spawn_detached_argv", captured.append)
    m._spawn_watchdog_replacement(delay=1.5, start_args=["start", "--idle-shutdown", "30"])
    argv = captured[0]
    monkeypatch.setattr(sys, "argv", ["-c", *argv[3:]])
    monkeypatch.setattr(time, "sleep", lambda delay: calls.append(("sleep", delay)))
    monkeypatch.setattr(os, "execv", lambda exe, args: calls.append(("exec", exe, args)))
    monkeypatch.setattr(
        runpy, "run_module",
        lambda module, **kwargs: calls.append(("run", module, kwargs, sys.argv.copy())),
    )
    exec(argv[2], {})
    assert calls == [
        ("sleep", 1.5),
        ("run", "agent_bridge", {"run_name": "__main__", "alter_sys": True},
         ["agent_bridge", "start", "--idle-shutdown", "30"])
        if platform == "nt" else
        ("exec", sys.executable,
         [sys.executable, "-m", "agent_bridge", "start", "--idle-shutdown", "30"]),
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CRT exec changes the admitted PID")
@pytest.mark.portfolio_tier("T2")
def test_watchdog_replacement_retains_native_instance_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil.daemon_instances import (
        InstanceCleanupResult, daemon_test_lifecycle, inspect_instances,
    )
    from agent_bridge import service_process_cli

    package = tmp_path / "agent_bridge"
    package.mkdir()
    source = str(Path(service_process_cli.__file__).parent)
    (package / "__init__.py").write_text(f"__path__.append({source!r})\n", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import argparse,json,os,sys\n"
        "from pathlib import Path\n"
        "from agent_bridge import service_start_cli\n"
        "def record(args, **kwargs):\n"
        " Path('admitted.json').write_text(json.dumps({"
        "'pid':os.getpid(),'argv':sys.argv,'main':sys.modules['__main__'].__file__}))\n"
        "service_start_cli._run_start=record\n"
        "service_start_cli._cmd_start(argparse.Namespace())\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(core, "_INSTALL_DIR", str(tmp_path))
    spawned: list[Popen[bytes] | WindowsDaemonProcess] = []
    launch = m._spawn_detached_argv

    def observe(argv: list[str]) -> Popen[bytes] | WindowsDaemonProcess:
        process = launch(argv)
        spawned.append(process)
        return process

    monkeypatch.setattr(core, "_spawn_detached_argv", observe)
    registry = tmp_path / "instances"
    cleanup: list[InstanceCleanupResult] = []
    with daemon_test_lifecycle(
        owner="bridge-watchdog-admission", registry_root=registry, cleanup_results=cleanup,
    ) as environment:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        started = time.monotonic()
        m._spawn_watchdog_replacement(
            delay=.2, start_args=["start", "--idle-shutdown", "30"],
        )
        assert len(spawned) == 1
        process = spawned[0]
        assert process.wait(timeout=15) == 0, (tmp_path / "agent-bridge-err.log").read_text()
        assert time.monotonic() - started >= .2
        proof = tmp_path / "admitted.json"
        assert proof.is_file(), (tmp_path / "agent-bridge-err.log").read_text()
        admitted = json.loads(proof.read_text())
        assert admitted["pid"] == process.pid
        assert admitted["argv"] == [
            str(package / "__main__.py"), "start", "--idle-shutdown", "30",
        ]
        assert admitted["main"] == str(package / "__main__.py")
        records = inspect_instances(registry)
        assert len(records) == 1
        assert records[0]["state"] == "finished"
        assert records[0]["purpose"] == "unattended-test"
    assert cleanup and all(result.status == "settled" for result in cleanup)


def test_watchdog_replacement_promotes_active_passive_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(sys, "executable", r"C:\venv\python.exe")
    monkeypatch.setattr(
        core, "_spawn_detached_argv", lambda argv: captured.setdefault("argv", argv),
    )
    m._spawn_watchdog_replacement(
        start_args=["start", "--port", "51103", "--passive"], active_port=51103,
    )
    assert captured["argv"][4:] == ["start", "--port", "51103"]


@pytest.mark.parametrize("active_port", [51103, None])
@pytest.mark.parametrize("launch_args", [
    ["--serving-launch-id", "ab" * 16],
    ["--serving-launch-id=" + "ab" * 16],
])
def test_watchdog_replacement_discards_one_shot_worker_receipt(
    monkeypatch: pytest.MonkeyPatch, active_port: int | None, launch_args: list[str],
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(
        core, "_spawn_detached_argv", lambda argv: captured.setdefault("argv", argv),
    )
    m._spawn_watchdog_replacement(
        start_args=["start", "--port", "51103", "--passive", *launch_args],
        active_port=active_port,
    )
    assert captured["argv"][4:] == [
        "start", "--port", "51103", *(["--passive"] if active_port is None else []),
    ]
    import argparse
    from agent_bridge.service_start_cli import register_service_start_commands

    parser = argparse.ArgumentParser()
    register_service_start_commands(parser.add_subparsers())
    parsed = parser.parse_args(captured["argv"][4:])
    assert parsed.serving_launch_id is None
    assert parsed.passive is (active_port is None)


def test_watchdog_dead_schedules_replacement_before_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_bridge import watchdog

    calls: list[object] = []
    monkeypatch.setattr(core, "_spawn_watchdog_replacement", lambda **_k: calls.append("replacement"))
    monkeypatch.setattr(watchdog, "_force_exit", lambda reason: calls.append(("exit", reason)))
    m._watchdog_dead("listener stopped")
    assert calls == ["replacement", ("exit", "listener stopped")]


def test_watchdog_dead_keeps_serving_when_replacement_scheduling_crashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import watchdog

    calls: list[tuple[str, str]] = []
    diagnostics: list[tuple[str, str]] = []

    def crash(**_kwargs: object) -> None:
        raise RuntimeError("unexpected helper failure")

    def forbidden_log(*_args: object, **_kwargs: object) -> None:
        pytest.fail("replacement failure synchronously logged")

    monkeypatch.setattr(core, "_spawn_watchdog_replacement", crash)
    monkeypatch.setattr(watchdog, "_record_event", lambda event, detail: diagnostics.append(
        (event, detail),
    ))
    monkeypatch.setattr(watchdog.log, "error", forbidden_log)
    monkeypatch.setattr(watchdog, "_force_exit", lambda reason: calls.append(("exit", reason)))
    assert m._watchdog_dead("listener stopped") is False
    assert calls == []
    assert diagnostics == [
        ("replacement-requested", "listener stopped"),
        ("replacement-failed", "RuntimeError; continuing to serve"),
    ]
