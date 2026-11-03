"""A new session does not detach a resident from a systemd cleanup unit."""

from pathlib import Path
import os
import subprocess
from unittest.mock import Mock

import pytest

import agent_procutil as pu
from agent_procutil import linux_daemon_scope as scope
from agent_procutil.daemon_instances import DaemonInstanceSpec, inspect_instances


@pytest.mark.parametrize("text,expected", [
    ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/caller.service\n", "caller.service"),
    ("1:name=systemd:/user.slice/session-7.scope\n", "session-7.scope"),
    ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/outer.scope/child\n", "outer.scope"),
    ("0::/user.slice/user-1000.slice/user@1000.service/app.slice\n", None),
    ("0::/docker/fixture\n", None),
    ("7:cpu:/caller.service\n", None),
], ids=["unified-service", "legacy-systemd", "nested-scope", "user-manager", "container", "cpu-only"])
def test_native_cleanup_boundary(monkeypatch, text, expected):
    monkeypatch.setattr(scope.sys, "platform", "linux")
    monkeypatch.setattr(
        scope.Path, "read_text",
        lambda path, **k: text if str(path) == "/proc/self/cgroup" else "0::/\n1:name=systemd:/\n",
    )
    assert scope.caller_unit() == expected


@pytest.mark.parametrize("hierarchy", ["0:", "1:name=systemd"], ids=["unified", "legacy"])
@pytest.mark.parametrize("local", [
    "",
    "/user.slice/user-1000.slice/user@1000.service/app.slice/caller.service",
    "/system.slice/caller.scope/child",
], ids=["container-only", "local-service", "local-scope"])
def test_host_visible_container_boundary(monkeypatch, hierarchy, local):
    boundary = "/system.slice/docker-fixture.scope"
    monkeypatch.setattr(scope.sys, "platform", "linux")
    monkeypatch.setattr(
        scope.Path, "read_text",
        lambda path, **k: hierarchy + ":" + boundary + (
            local if str(path) == "/proc/self/cgroup" else ""
        ) + "\n",
    )
    expected = "caller.service" if local.endswith(".service") else "caller.scope" if local else None
    assert scope.caller_unit() == expected
    if not local:
        monkeypatch.setattr(
            scope.shutil, "which", Mock(side_effect=AssertionError("host scope needs no local runner")),
        )
        command = ["/runtime/python", "-m", "resident"]
        assert scope.scope_command(command, "a" * 32, {}, cwd="/runtime") is command
    else:
        monkeypatch.setattr(scope.shutil, "which", lambda *a, **k: None)
        with pytest.raises(RuntimeError, match="requires systemd-run"):
            scope.scope_command(["/runtime/python"], "a" * 32, {}, cwd="/runtime")


def test_unrelated_pid_one_scope_does_not_hide_local_unit(monkeypatch):
    monkeypatch.setattr(scope.sys, "platform", "linux")
    monkeypatch.setattr(
        scope.Path, "read_text",
        lambda path, **k: "0::/system.slice/" + (
            "caller.service" if str(path) == "/proc/self/cgroup" else "init.scope"
        ) + "\n",
    )
    monkeypatch.setattr(scope.shutil, "which", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="requires systemd-run"):
        scope.scope_command(["/runtime/python"], "a" * 32, {}, cwd="/runtime")


@pytest.mark.parametrize("version,expand", [(253, False), (254, True), (255, True)])
def test_scope_preserves_literal_command_and_child_environment(tmp_path, monkeypatch, version, expand):
    command = ["/runtime/python", "-c", "print('$VALUE')", "$VALUE", ""]
    environment = {"PATH": "/native", "VALUE": "do not substitute"}
    monkeypatch.setattr(scope, "caller_unit", lambda: "caller.service")
    monkeypatch.setattr(scope.shutil, "which", lambda *a, **k: "/native/systemd-run")
    run = Mock(return_value=subprocess.CompletedProcess([], 0, f"systemd {version}\n".encode()))
    monkeypatch.setattr(scope.subprocess, "run", run)
    result = scope.scope_command(command, "a" * 32, environment, cwd=str(tmp_path))
    assert result[result.index("--") + 1:] == command
    assert ("--expand-environment=no" in result) is expand
    assert "--unit=agent-daemon-" + "a" * 32 + ".scope" in result
    assert "--no-ask-password" in result
    assert run.call_args.kwargs["env"] == environment
    assert run.call_args.kwargs["cwd"] == str(tmp_path)
    assert run.call_args.kwargs["timeout"] == 3


def test_no_native_cleanup_boundary_keeps_plain_posix_launch(monkeypatch):
    monkeypatch.setattr(scope, "caller_unit", lambda: None)
    monkeypatch.setattr(scope.shutil, "which", Mock(side_effect=AssertionError("unexpected native lookup")))
    command = ["/runtime/python", "-m", "resident"]
    assert scope.scope_command(command, "a" * 32, {}, cwd="/runtime") is command


@pytest.mark.parametrize("mode", ["contained-parent", "contained-child", "no-breakaway"])
@pytest.mark.skipif(scope.sys.platform == "win32", reason="real POSIX process containment")
def test_spawn_preserves_explicit_containment(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    if mode == "contained-parent":
        monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    else:
        monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    scope_command = Mock(side_effect=AssertionError("contained launch escaped its owner"))
    monkeypatch.setattr(scope, "scope_command", scope_command)
    overrides = {"COPILOT_EXTENSIONS_TEST_CONTAINED": "1"} if mode == "contained-child" else {}
    process = pu.spawn_daemon(
        ["/bin/true"], cwd=tmp_path, log_path=tmp_path / "resident.log",
        env_overrides=overrides, breakaway=mode != "no-breakaway",
        instance=DaemonInstanceSpec("fixture", tmp_path, Path(__file__).resolve()),
    )
    assert process.wait(timeout=5) == 0
    scope_command.assert_not_called()


def test_refused_scope_preparation_settles_without_creating_a_resident(tmp_path, monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(scope, "scope_command", Mock(side_effect=RuntimeError("native scope unavailable")))
    created = Mock(side_effect=AssertionError("scope refusal launched application"))
    monkeypatch.setattr(subprocess, "Popen", created)
    with pytest.raises(RuntimeError, match="native scope unavailable"):
        pu.spawn_daemon(
            ["/runtime/python"], cwd=tmp_path, log_path=tmp_path / "resident.log",
            instance=DaemonInstanceSpec("fixture", tmp_path, Path(__file__).resolve()),
        )
    created.assert_not_called()
    assert inspect_instances() == []


@pytest.mark.parametrize(
    "output,code", [(b"not systemd", 0), (b"systemd 255\n", 1)],
    ids=["malformed", "failed-version"],
)
def test_unknown_native_runner_is_not_inherited_lifetime(monkeypatch, output, code):
    monkeypatch.setattr(scope, "caller_unit", lambda: "caller.scope")
    monkeypatch.setattr(scope.shutil, "which", lambda *a, **k: "/native/systemd-run")
    monkeypatch.setattr(scope.subprocess, "run", Mock(
        return_value=subprocess.CompletedProcess([], code, output),
    ))
    with pytest.raises(RuntimeError, match="scope compatibility"):
        scope.scope_command(["/runtime/python"], "a" * 32, {}, cwd="/runtime")


def test_missing_native_runner_refuses(monkeypatch):
    monkeypatch.setattr(scope, "caller_unit", lambda: "caller.service")
    monkeypatch.setattr(scope.shutil, "which", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="requires systemd-run"):
        scope.scope_command(["/runtime/python"], "a" * 32, {}, cwd="/runtime")


def test_relative_runner_search_uses_daemon_cwd_for_probe_and_launch(tmp_path, monkeypatch):
    caller, target = tmp_path / "caller", tmp_path / "resident"
    caller.mkdir()
    (target / "bin").mkdir(parents=True)
    runner = target / "bin" / ("systemd-run.exe" if os.name == "nt" else "systemd-run")
    runner.write_bytes(b"fixture")
    runner.chmod(0o700)
    monkeypatch.chdir(caller)
    monkeypatch.setattr(scope, "caller_unit", lambda: "caller.service")
    probe = Mock(return_value=subprocess.CompletedProcess([], 0, b"systemd 255\n"))
    monkeypatch.setattr(scope.subprocess, "run", probe)
    result = scope.scope_command(["/runtime/python"], "a" * 32, {"PATH": "bin"}, cwd=str(target))
    assert result[0] == str(runner)
    assert probe.call_args.args[0] == [str(runner), "--version"]
    assert probe.call_args.kwargs["cwd"] == str(target)


def test_native_probe_timeout_is_an_admission_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr(scope, "caller_unit", lambda: "caller.service")
    monkeypatch.setattr(scope.shutil, "which", lambda *a, **k: "/native/systemd-run")
    timeout = subprocess.TimeoutExpired("systemd-run", 3)
    monkeypatch.setattr(scope.subprocess, "run", Mock(side_effect=timeout))
    with pytest.raises(RuntimeError, match="probe timed out") as raised:
        scope.scope_command(["/runtime/python"], "a" * 32, {}, cwd=str(tmp_path))
    assert raised.value.__cause__ is timeout


def test_other_platforms_do_not_read_linux_cgroups(monkeypatch):
    monkeypatch.setattr(scope.sys, "platform", "darwin")
    monkeypatch.setattr(scope.Path, "read_text", Mock(side_effect=AssertionError("Linux-only probe")))
    assert scope.caller_unit() is None


@pytest.mark.skipif(
    scope.sys.platform != "linux" or pu.contained_test_mode(),
    reason="native lifetime proof requires a separate uncontained user-systemd venue",
)
def test_native_resident_outlives_caller_unit(tmp_path):
    import os
    if os.environ.get("AGENT_PROCUTIL_TEST_SYSTEMD_LIFETIME") != "1":
        pytest.skip("explicit native lifetime qualification")
    from systemd_daemon_fixture import qualify
    assert qualify(tmp_path / "native")["cleanup_settled"]
