from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from installer_readiness.model import Invocation, Platform
from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

from agent_machines import installation_repair as repair_api
from agent_machines import installation_repair_process as process_api
from agent_machines.installation_repair_admission import RepairAdmissionError
from agent_machines.installation_repair_process import run_action
from agent_machines.installation_repair_restarts import RestartJournal
from . import test_installation_repair as fixture_support
from .test_installation_repair import invoke, write_json

pytestmark = pytest.mark.guard
repair_fixture = fixture_support.repair_fixture


def test_transitive_identity_bound_refuses_before_probe_or_restart_state(repair_fixture, monkeypatch):
    home, _, payload, _, identity = repair_fixture
    declaration = payload / "installer-readiness.json"
    value = json.loads(declaration.read_text())
    long_local_id = "agent-example/" + "x" * (257 - len(identity + "::agent-example/"))
    value["modules"][0]["id"] = long_local_id
    value["modules"][0]["restart"] = "session"
    value["modules"][1]["dependsOn"] = [long_local_id]
    write_json(declaration, value)
    monkeypatch.setattr(repair_api, "_invoke", lambda *a, **k: pytest.fail("probe executed"))
    with pytest.raises(RepairAdmissionError, match="identity exceeds its bound"):
        invoke(repair_fixture)
    assert not RestartJournal(home).path.exists()
    with RestartJournal(home).locked() as journal:
        journal.require("fixture::valid/base", "session", "a" * 64)
    RestartJournal(home).load()


@pytest.mark.parametrize("failure", ["oversized", "unreadable"])
def test_unavailable_executable_never_launches_a_probe_or_installer(
    repair_fixture, tmp_path, monkeypatch, failure,
):
    executable = tmp_path / "unavailable-interpreter"
    if failure == "oversized":
        with executable.open("wb") as stream:
            stream.truncate(64 * 1024 * 1024 + 1)
    else:
        executable.write_bytes(b"unreadable")
        original_open = Path.open

        def unreadable(path, *args, **kwargs):
            if path == executable:
                raise PermissionError("synthetic denied executable read")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", unreadable)
    original_which = repair_api.shutil.which
    monkeypatch.setattr(
        repair_api.shutil, "which",
        lambda name: str(executable) if name in {"bash", "pwsh", "powershell"}
        else original_which(name),
    )
    monkeypatch.setattr(repair_api, "run_action", lambda *a, **k: pytest.fail("executed"))
    plan = invoke(repair_fixture)
    assert plan["outcomes"][0]["observedBefore"]["reason"] == "readiness-invocation-refused"
    if failure == "oversized":
        with executable.open("wb") as stream:
            stream.truncate(64 * 1024 * 1024 + 2)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert result["status"] == "incomplete" and not result["requiredSatisfied"]
    assert result["outcomes"][0]["installer"]["status"] == "invocation-refused"


def test_initially_ready_probe_cannot_skip_changed_owner_binding(repair_fixture, monkeypatch):
    from agent_machines.installation_repair_process import ActionResult

    _, _, payload, _, _ = repair_fixture
    plan = invoke(repair_fixture)

    def changed(module, owner, platform, *, readonly, timeout):
        assert readonly, "installer executed"
        (payload / "changed-during-ready-probe").write_text("changed", encoding="utf-8")
        return ActionResult("exited", 0, json.dumps({
            "schema": "copilot-extensions.module-readiness", "version": 1,
            "module": module.module_id, "state": "ready",
        }).encode())

    monkeypatch.setattr(repair_api, "_invoke", changed)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert result["status"] == "incomplete" and not result["requiredSatisfied"]
    assert result["outcomes"][0]["observedBefore"]["state"] == "ready"
    assert result["outcomes"][0]["installer"]["reason"] == "owner-binding-changed"
    assert not any(row["attempted"] for row in result["outcomes"])


def test_binding_refusal_propagates_optional_prerequisite_blockers(
    repair_fixture, monkeypatch,
):
    from agent_machines.installation_repair_process import ActionResult

    _, _, payload, configure, _ = repair_fixture
    configure(classification="optional", dependencies=("agent-example/base",))
    plan = invoke(repair_fixture)

    def changed(module, owner, platform, *, readonly, timeout):
        assert readonly, "installer executed"
        (payload / "changed-during-observation").write_text("changed", encoding="utf-8")
        return ActionResult("exited", 0, json.dumps({
            "schema": "copilot-extensions.module-readiness", "version": 1,
            "module": module.module_id, "state": "ready",
        }).encode())

    monkeypatch.setattr(repair_api, "_invoke", changed)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    base, child = result["outcomes"]
    assert base["classification"] == "optional" and child["classification"] == "required"
    assert base["installer"]["reason"] == "owner-binding-changed"
    assert child["observedBefore"]["state"] == "ready"
    assert child["blockedBy"] == [base["module"]]
    assert not any(row["attempted"] for row in result["outcomes"])
    assert not result["requiredSatisfied"] and result["status"] == "incomplete"


@pytest.mark.parametrize("initially_unavailable", [False, True])
def test_executable_changed_after_binding_cannot_run_a_probe(
    repair_fixture, tmp_path, monkeypatch, initially_unavailable,
):
    executable = tmp_path / "changed-interpreter"
    executable.write_bytes(b"first")
    if initially_unavailable:
        with executable.open("wb") as stream:
            stream.truncate(64 * 1024 * 1024 + 1)
    original_which = repair_api.shutil.which
    monkeypatch.setattr(
        repair_api.shutil, "which",
        lambda name: str(executable) if name in {"bash", "pwsh", "powershell"}
        else original_which(name),
    )
    original_binding = repair_api._binding_inputs

    def changed(*args, **kwargs):
        bound = original_binding(*args, **kwargs)
        executable.write_bytes(b"second")
        return bound

    monkeypatch.setattr(repair_api, "_binding_inputs", changed)
    monkeypatch.setattr(repair_api, "run_action", lambda *a, **k: pytest.fail("executed"))
    plan = invoke(repair_fixture)
    assert all(row["observedBefore"]["reason"] == "readiness-invocation-refused"
               for row in plan["outcomes"])


@pytest.mark.parametrize("platform", [Platform.WINDOWS, Platform.LINUX])
def test_admitted_executable_argv_is_absolute(tmp_path, monkeypatch, platform):
    conductor = tmp_path / "conductor"
    executable = conductor / "relative-bin" / "interpreter"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"admitted-interpreter")
    monkeypatch.chdir(conductor)
    monkeypatch.setattr(repair_api, "_argv", lambda *args: [
        str(Path("relative-bin") / "interpreter"), "unchanged-argument",
    ])
    action = Invocation(kind="payload-script", target=tmp_path / "action", arguments=())
    (tmp_path / "action").write_bytes(b"action")
    argv, admitted, _, _ = repair_api._admit_invocation(action, platform)
    assert argv == [str(executable), "unchanged-argument"]
    assert admitted == executable and Path(argv[0]).is_absolute()


@pytest.mark.skipif(os.name == "nt", reason="native POSIX relative PATH execution")
def test_relative_path_executes_admitted_interpreter_not_payload_twin(
    repair_fixture, tmp_path, monkeypatch,
):
    home, _, payload, _, _ = repair_fixture
    interpreter = shutil.which("bash")
    assert interpreter
    conductor = tmp_path / "conductor"
    admitted = conductor / "relative-bin" / "bash"
    admitted.parent.mkdir(parents=True)
    shutil.copyfile(interpreter, admitted)
    admitted.chmod(0o755)
    twin = payload / "relative-bin" / "bash"
    twin.parent.mkdir()
    twin.write_text("#!/bin/sh\nexit 96\n", encoding="utf-8")
    twin.chmod(0o755)
    monkeypatch.chdir(conductor)
    monkeypatch.setenv("PATH", "relative-bin")
    assert shutil.which("bash") == str(Path("relative-bin") / "bash")
    plan = invoke(repair_fixture, selection=("base",))
    assert plan["outcomes"][0]["observedBefore"]["state"] == "not-ready"
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"], selection=("base",))
    assert result["status"] == "ready" and result["requiredSatisfied"]
    assert result["outcomes"][0]["installer"] == {"status": "exited", "exitCode": 0}
    assert result["outcomes"][0]["observedAfter"]["state"] == "ready"
    assert (home / "agent-example-base.ready").read_text() == "ready"


@pytest.mark.skipif(os.name == "nt", reason="native POSIX command interpreter execution")
@pytest.mark.parametrize("shebang", ["#!/usr/bin/env sh", "#!/usr/bin/env bash"])
def test_payload_command_binds_interpreter_before_changing_cwd(
    repair_fixture, tmp_path, monkeypatch, shebang,
):
    home, _, payload, _, _ = repair_fixture
    name = shebang.rsplit(" ", 1)[1]
    interpreter = shutil.which(name)
    assert interpreter
    conductor = tmp_path / "conductor"
    admitted = conductor / "relative-bin" / name
    admitted.parent.mkdir(parents=True)
    shutil.copyfile(interpreter, admitted)
    admitted.chmod(0o755)
    twin = payload / "relative-bin" / name
    twin.parent.mkdir()
    twin.write_text("#!/bin/sh\nexit 96\n", encoding="utf-8")
    twin.chmod(0o755)
    target = payload / "bin" / "agent-example"
    target.parent.mkdir()
    target.write_text(
        (payload / "scripts" / "action.sh").read_text().replace(
            "#!/usr/bin/env bash", shebang,
        ).replace('state="$HOME/${2//\\//-}.ready"',
                  'state="$HOME/agent-example-base.ready"'),
        encoding="utf-8",
    )
    target.chmod(0o755)
    declaration = payload / "installer-readiness.json"
    value = json.loads(declaration.read_text())
    for module in value["modules"]:
        for actions in (module["installer"], module["readiness"]["invocations"]):
            for platform in ("linux", "wsl", "macos"):
                actions[platform].pop("path")
                actions[platform].update(kind="payload-command", command="agent-example")
    write_json(declaration, value)
    monkeypatch.chdir(conductor)
    monkeypatch.setenv("PATH", "relative-bin")
    plan = invoke(repair_fixture, selection=("base",))
    assert plan["outcomes"][0]["observedBefore"]["state"] == "not-ready"
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"], selection=("base",))
    assert result["status"] == "ready" and result["requiredSatisfied"]
    assert result["outcomes"][0]["installer"] == {"status": "exited", "exitCode": 0}
    assert (home / "agent-example-base.ready").read_text() == "ready"


@pytest.mark.parametrize("shebang", [
    "#!/usr/bin/env sh -e", "#!/usr/bin/env -S bash", "#!relative/sh",
    "#!/usr/bin/python3", "not a script",
])
def test_payload_command_refuses_unsupported_interpreter_forms(tmp_path, shebang):
    target = tmp_path / "agent-example"
    target.write_text(shebang + "\n", encoding="utf-8")
    action = Invocation(kind="payload-command", target=target, arguments=())
    with pytest.raises(RepairAdmissionError, match="unsupported interpreter declaration"):
        repair_api._admit_invocation(action, Platform.LINUX)


@pytest.mark.skipif(os.name == "nt", reason="native POSIX interpreter symlink semantics")
def test_declared_sh_symlink_preserves_bash_sh_invocation_semantics(tmp_path, monkeypatch):
    bash = shutil.which("bash")
    assert bash
    shell = tmp_path / "sh"
    shell.symlink_to(bash)
    target = tmp_path / "agent-example"
    target.write_text(
        "#!/usr/bin/env sh\nset -e\nvalue=$(false; printf should-not-run)\n"
        "printf reached-end\n", encoding="utf-8",
    )
    action = Invocation(kind="payload-command", target=target, arguments=())
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    argv, executable, _, _ = repair_api._admit_invocation(action, Platform.LINUX)
    assert argv[0] == str(shell)
    assert executable == Path(bash).resolve()
    result = run_action(argv, cwd=tmp_path, environment=os.environ, timeout=5, readonly=True)
    assert result.status == "exited" and result.exit_code != 0 and result.stdout == b""


@pytest.mark.parametrize("tree", ["__pycache__", ".pytest_cache", ".ruff_cache", ".git"])
def test_declared_action_in_excluded_tree_still_has_content_binding(
    repair_fixture, monkeypatch, tree,
):
    _, _, payload, _, _ = repair_fixture
    scripts = payload / "scripts"
    excluded = scripts / tree
    excluded.mkdir()
    for name in ("action.ps1", "action.sh"):
        (scripts / name).rename(excluded / name)
    declaration = payload / "installer-readiness.json"
    value = json.loads(declaration.read_text())
    for module in value["modules"]:
        for actions in (module["installer"], module["readiness"]["invocations"]):
            for action in actions.values():
                action["path"] = action["path"].replace("scripts/", f"scripts/{tree}/")
    write_json(declaration, value)
    plan = invoke(repair_fixture, selection=("base",))
    for target in excluded.iterdir():
        target.write_text(target.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    monkeypatch.setattr(repair_api, "run_action", lambda *a, **k: pytest.fail("executed"))
    with pytest.raises(RepairAdmissionError, match="plan"):
        invoke(repair_fixture, apply=True, digest=plan["planDigest"], selection=("base",))


@pytest.mark.parametrize("size", [8 * 1024 * 1024, 8 * 1024 * 1024 + 1])
def test_action_content_binding_enforces_exact_byte_bound(tmp_path, monkeypatch, size):
    target = tmp_path / "action"
    with target.open("wb") as stream:
        stream.truncate(size)
    monkeypatch.setattr(repair_api, "_argv", lambda *args: [sys.executable, str(target)])
    action = Invocation(kind="payload-script", target=target, arguments=())
    if size > 8 * 1024 * 1024:
        with pytest.raises(RepairAdmissionError, match="action exceeds its content bound"):
            repair_api._admit_invocation(action, Platform.LINUX)
    else:
        assert len(repair_api._admit_invocation(action, Platform.LINUX)[3]) == 64


@pytest.mark.skipif(os.name == "nt", reason="native POSIX descendant resource lifetime")
def test_posix_exited_installer_releases_redirected_descendant_lock_before_unlock(
    tmp_path,
):
    import fcntl

    resource = tmp_path / "descendant.lock"
    group_file = tmp_path / "descendant.pgid"
    child = (
        "import fcntl,os,time; from pathlib import Path; "
        f"lock=open({str(resource)!r},'w'); fcntl.flock(lock,fcntl.LOCK_EX); "
        f"Path({str(group_file)!r}).write_text(str(os.getpgrp())); time.sleep(60)"
    )
    parent = (
        "import subprocess,sys,time; from pathlib import Path; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}],stdin=subprocess.DEVNULL,"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        f"p=Path({str(group_file)!r}); deadline=time.monotonic()+8\n"
        "while not p.exists() and time.monotonic()<deadline: time.sleep(.02)\n"
        "assert p.exists()\n"
    )
    with RestartJournal(tmp_path).locked():
        result = run_action(
            [sys.executable, "-c", parent], cwd=tmp_path, environment=os.environ,
            timeout=10, readonly=True,
        )
        assert result.status == "exited" and result.exit_code == 0
        with resource.open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not process_api._posix_group_alive(int(group_file.read_text()))
    with RestartJournal(tmp_path).locked():
        pass


@pytest.mark.skipif(os.name == "nt", reason="native POSIX group cleanup deadline")
def test_unconfirmed_posix_group_cleanup_never_returns_success(tmp_path, monkeypatch):
    import types

    elapsed = 0
    real_clock = time.monotonic

    def alive(pgid):
        nonlocal elapsed
        elapsed += 6
        return True

    monkeypatch.setattr(process_api, "_posix_group_alive", alive)
    monkeypatch.setattr(process_api, "time", types.SimpleNamespace(
        monotonic=lambda: real_clock() + elapsed, sleep=time.sleep,
    ))
    with pytest.raises(TimeoutError, match="transient process group did not exit"):
        run_action(
            [sys.executable, "-c", "pass"], cwd=tmp_path, environment=os.environ,
            timeout=10, readonly=True,
        )


@pytest.mark.skipif(os.name != "nt", reason="native Windows CMD contract")
@pytest.mark.parametrize("readonly", [True, False])
def test_native_cmd_preserves_payload_path_and_argument_spaces(tmp_path, readonly):
    target = tmp_path / "payload with spaces" / "command with spaces.cmd"
    target.parent.mkdir()
    target.write_bytes(b"@echo off\r\necho [%~1]\r\necho [%~2]\r\nexit /b 0\r\n")
    action = Invocation(kind="payload-command", target=target, arguments=("first value", "last\\"))
    argv = repair_api._argv(action, Platform.WINDOWS)
    result = run_action(
        argv, cwd=target.parent, environment=os.environ, timeout=10, readonly=readonly, cmd=True,
    )
    assert result.status == "exited" and result.exit_code == 0
    assert result.stdout.decode().splitlines() == ["[first value]", "[last\\]"]


@pytest.mark.skipif(os.name != "nt", reason="native Windows Job teardown and lock ordering")
def test_windows_exited_installer_reaps_redirected_descendant_before_unlock(
    tmp_path, monkeypatch,
):
    import ctypes
    from ctypes import wintypes
    from agent_procutil import JobHandle

    pid_file = tmp_path / "descendant.pid"
    child = (
        "import os,time; from pathlib import Path; "
        f"Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    parent = (
        "import subprocess,sys,time; from pathlib import Path; "
        "from agent_procutil import no_window_kwargs; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}],stdin=subprocess.DEVNULL,"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**no_window_kwargs()); "
        f"p=Path({str(pid_file)!r}); deadline=time.monotonic()+8\n"
        "while not p.exists() and time.monotonic()<deadline: time.sleep(.02)\n"
        "assert p.exists()\n"
    )
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    original = JobHandle.terminate_and_wait
    original_spawn = process_api.spawn_sync_in_kill_on_close_job
    parents = []
    confirmed = []

    def capture_spawn(*args, **kwargs):
        process, job = original_spawn(*args, **kwargs)
        parents.append(process)
        return process, job

    def confirm_before_unlock(job, timeout_seconds):
        assert parents[0].poll() == 0
        handle = kernel.OpenProcess(0x100000, False, int(pid_file.read_text()))
        assert handle, ctypes.get_last_error()
        try:
            assert kernel.WaitForSingleObject(handle, 0) == 258
            with pytest.raises(RepairAdmissionError, match="already active"):
                with RestartJournal(tmp_path).locked():
                    pytest.fail("apply lock released before descendant teardown")
            original(job, timeout_seconds)
            assert job.handle is None
            # Zero active Job members can precede final process-object signaling.
            assert kernel.WaitForSingleObject(handle, 5000) == 0
            confirmed.append(True)
        finally:
            kernel.CloseHandle(handle)

    monkeypatch.setattr(process_api, "spawn_sync_in_kill_on_close_job", capture_spawn)
    monkeypatch.setattr(JobHandle, "terminate_and_wait", confirm_before_unlock)
    with RestartJournal(tmp_path).locked():
        result = run_action(
            [sys.executable, "-c", parent], cwd=tmp_path, environment=os.environ,
            timeout=10, readonly=True,
        )
        assert confirmed == [True]
    assert result.status == "exited" and result.exit_code == 0
    with RestartJournal(tmp_path).locked():
        pass


@pytest.mark.skipif(os.name == "nt", reason="native POSIX signal cleanup")
@pytest.mark.parametrize("signal_name", ["SIGINT", "SIGTERM"])
def test_signal_during_admission_restores_handler_and_releases_apply_lock(
    repair_fixture, monkeypatch, signal_name,
):
    home, _, _, _, _ = repair_fixture
    plan = invoke(repair_fixture)
    signum = getattr(signal, signal_name)
    original = signal.getsignal(signum)

    def caller_handler(*args):
        pytest.fail("caller handler was not protected during repair")

    signal.signal(signum, caller_handler)

    def terminated(*args, **kwargs):
        assert signal.getsignal(signum) is not caller_handler
        os.kill(os.getpid(), signum)
        pytest.fail("termination was ignored")

    monkeypatch.setattr(repair_api, "admit_host", terminated)
    try:
        with pytest.raises(KeyboardInterrupt):
            invoke(repair_fixture, apply=True, digest=plan["planDigest"])
        assert signal.getsignal(signum) is caller_handler
    finally:
        signal.signal(signum, original)
    with RestartJournal(home).locked():
        pass


def _wait_for(path: Path, process, timeout=8):
    deadline = time.monotonic() + timeout
    while not path.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert path.exists(), f"child did not reach {path.name}; exit={process.poll()}"


@pytest.mark.skipif(os.name == "nt", reason="native POSIX signal inheritance")
def test_owner_action_does_not_inherit_a_blocked_termination_signal(tmp_path):
    result = run_action(
        [sys.executable, "-c", "import signal; "
         "print(bool({signal.SIGINT,signal.SIGTERM} & "
         "signal.pthread_sigmask(signal.SIG_BLOCK, [])))"],
        cwd=tmp_path, environment=os.environ, timeout=5, readonly=False,
    )
    assert result.status == "exited" and result.exit_code == 0
    assert result.stdout.strip() == b"False"


@pytest.mark.skipif(os.name == "nt", reason="native POSIX ignored signal inheritance")
def test_owner_action_retains_caller_ignored_signal_dispositions(tmp_path):
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        for signum in previous:
            signal.signal(signum, signal.SIG_IGN)
        result = run_action(
            [sys.executable, "-c", "import signal; print([int(signal.getsignal(s)) "
             "for s in (signal.SIGINT,signal.SIGTERM)])"],
            cwd=tmp_path, environment=os.environ, timeout=5, readonly=True,
        )
        assert result.status == "exited" and result.exit_code == 0
        assert result.stdout.strip() == b"[1, 1]"
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


@pytest.mark.parametrize("signal_name", ["SIGINT", "SIGTERM"])
def test_signal_during_spawn_defers_until_process_ownership_is_available(
    tmp_path, monkeypatch, signal_name,
):
    if os.name == "nt" and signal_name == "SIGTERM":
        pytest.skip("Windows cancellation uses SIGINT, not POSIX SIGTERM")
    signum = getattr(signal, signal_name)
    original = process_api.spawn_sync_in_kill_on_close_job
    children = []
    previous = signal.getsignal(signum)

    def interrupted_spawn(*args, **kwargs):
        process, job = original(*args, **kwargs)
        children.append(process)
        signal.raise_signal(signum)
        return process, job

    monkeypatch.setattr(process_api, "spawn_sync_in_kill_on_close_job", interrupted_spawn)
    with pytest.raises(KeyboardInterrupt):
        run_action(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=tmp_path, environment=os.environ, timeout=5, readonly=True,
        )
    assert len(children) == 1 and children[0].poll() is not None
    assert signal.getsignal(signum) is previous


@pytest.mark.skipif(os.name == "nt", reason="native POSIX signal cleanup and lock ordering")
@pytest.mark.parametrize("stage", ["action", "spawn", "cleanup"])
@pytest.mark.parametrize("signal_name", ["SIGINT", "SIGTERM"])
def test_terminated_conductor_reaps_installer_before_unlocking(
    repair_fixture, tmp_path, stage, signal_name,
):
    signum = getattr(signal, signal_name)
    home, project, payload, _, identity = repair_fixture
    descendant = tmp_path / "descendant.pid"
    cleanup = tmp_path / "cleanup.started"
    release = tmp_path / "cleanup.release"
    spawning = tmp_path / "spawn.started"
    release_spawn = tmp_path / "spawn.release"
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text(
        "import os,time\nfrom pathlib import Path\n"
        f"Path({str(descendant)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n", encoding="utf-8",
    )
    script = payload / "scripts" / "action.sh"
    installer = (
        f"  '{sys.executable}' '{sleeper}' </dev/null >/dev/null 2>&1 &\n"
        f"  while [ ! -f '{descendant}' ]; do sleep .01; done\n  exit 0"
        if stage == "cleanup" else
        f"  '{sys.executable}' '{sleeper}' &\n  wait\n  exit 0"
    )
    script.write_text(script.read_text().replace(
        '  printf ready > "$state"; exit 0', installer,
    ), encoding="utf-8")
    plan = invoke(repair_fixture, selection=("base",))
    spawn_hook = "" if stage != "spawn" else (
        "original_spawn=runner.spawn_sync_in_kill_on_close_job\n"
        "def paused_spawn(*args, **kwargs):\n"
        "    process,job=original_spawn(*args,**kwargs)\n"
        "    if kwargs.get('allow_breakaway'):\n"
        f"        Path({str(spawning)!r}).touch()\n"
        "        deadline=time.monotonic()+8\n"
        f"        while not Path({str(release_spawn)!r}).exists() and time.monotonic()<deadline:\n"
        "            time.sleep(.02)\n"
        "    return process,job\n"
        "runner.spawn_sync_in_kill_on_close_job=paused_spawn\n"
    )
    conductor = tmp_path / "conductor.py"
    conductor.write_text(
        "import json,time\nfrom pathlib import Path\n"
        "from agent_machines.installation_repair import repair\n"
        "from agent_machines import installation_repair_process as runner\n"
        "original=runner.os.killpg\n"
        "def paused_cleanup(pid, sig):\n"
        f"    if Path({str(descendant)!r}).exists():\n"
        f"        Path({str(cleanup)!r}).touch()\n"
        "        deadline=time.monotonic()+8\n"
        f"        while not Path({str(release)!r}).exists() and time.monotonic()<deadline:\n"
        "            time.sleep(.02)\n"
        "    original(pid,sig)\n"
        "runner.os.killpg=paused_cleanup\n"
        + spawn_hook +
        f"result=repair(Path({str(project)!r}),home=Path({str(home)!r}),"
        f"modules={[identity + '::agent-example/base']!r},apply=True,"
        f"expected_plan_digest={plan['planDigest']!r})\n"
        "print(json.dumps(result),flush=True)\n", encoding="utf-8",
    )
    process, job = spawn_sync_in_kill_on_close_job(
        [sys.executable, str(conductor)], require_job=True, allow_breakaway=False,
        start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    try:
        _wait_for(descendant, process)
        if stage != "cleanup":
            if stage == "spawn":
                _wait_for(spawning, process)
            os.kill(process.pid, signum)
            if stage == "spawn":
                with pytest.raises(RepairAdmissionError, match="already active"):
                    with RestartJournal(home).locked():
                        pytest.fail("lock released during spawn ownership handoff")
                os.kill(int(descendant.read_text()), 0)
                release_spawn.touch()
        _wait_for(cleanup, process)
        if stage == "cleanup":
            os.kill(process.pid, signum)
        with pytest.raises(RepairAdmissionError, match="already active"):
            with RestartJournal(home).locked():
                pytest.fail("lock released while the installer was still alive")
        os.kill(int(descendant.read_text()), 0)
        os.kill(process.pid, signum)
        release.touch()
        release_spawn.touch()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr.decode()
        assert json.loads(stdout)["interrupted"]
        with RestartJournal(home).locked():
            pass
        stat = Path("/proc") / descendant.read_text() / "stat"
        assert not stat.exists() or stat.read_text().split()[2] == "Z"
    finally:
        release.touch()
        release_spawn.touch()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        if descendant.exists():
            try:
                os.killpg(os.getpgid(int(descendant.read_text())), signal.SIGKILL)
            except ProcessLookupError:
                pass
        if job is not None:
            job.close()
