"""The stdlib guardian observes exit without reaping on older macOS Python."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import venv

import pytest
import agent_procutil
from agent_procutil import no_window_kwargs


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def guardian():
    source = Path(__file__).resolve().parents[3] / "libs" / "installer-engine" / "installer_engine_process.py"
    spec = importlib.util.spec_from_file_location("guardian_fixture", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("state,exited", [("S\n", False), ("Z\n", True), ("Z+\n", True)])
def test_missing_waitid_uses_nonreaping_process_state(guardian, monkeypatch, state, exited):
    monkeypatch.setattr(guardian, "os", SimpleNamespace())
    calls = []

    def observe(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=state)

    monkeypatch.setattr(guardian.subprocess, "run", observe)
    assert guardian._child_exited_unreaped(123) is exited
    assert calls[0][0] == ["ps", "-o", "stat=", "-p", "123"]
    assert calls[0][1]["check"] is True and calls[0][1]["timeout"] == 5


@pytest.mark.parametrize("state", ["", "Z\nS\n"])
def test_ambiguous_process_observation_is_not_exit(guardian, monkeypatch, state):
    monkeypatch.setattr(guardian, "os", SimpleNamespace())
    monkeypatch.setattr(guardian.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=state))
    with pytest.raises(RuntimeError, match="Cannot observe"):
        guardian._child_exited_unreaped(123)


@pytest.mark.parametrize("code,expected", [(23, 23), (-15, 143)])
def test_guardian_reaps_only_after_group_settlement(guardian, monkeypatch, code, expected):
    process = SimpleNamespace(pid=123, returncode=None)
    events = []
    observations = iter([False, True])
    monkeypatch.setattr(guardian, "os", SimpleNamespace(
        close=lambda fd: events.append(("closed", fd)),
    ))
    monkeypatch.setattr(guardian, "subprocess", SimpleNamespace(
        Popen=lambda *a, **k: process, DEVNULL=subprocess.DEVNULL,
    ))
    monkeypatch.setattr(guardian.select, "select", lambda *args: ([], [], []))

    def observe(pid):
        assert pid == 123 and process.returncode is None
        return next(observations)

    def settle(child):
        assert child is process and child.returncode is None
        events.append("settled")
        child.returncode = code

    monkeypatch.setattr(guardian, "_child_exited_unreaped", observe)
    monkeypatch.setattr(guardian, "_terminate_group", settle)
    assert guardian._supervise(17, ["fixture"]) == expected
    assert events == ["settled", ("closed", 17)]


@pytest.mark.parametrize("live_member", [False, True])
def test_zombie_only_group_eperm_settles_via_process_observation(guardian, monkeypatch, live_member):
    waits = []
    process = SimpleNamespace(pid=123, returncode=None, wait=lambda timeout: waits.append(timeout))

    def killpg(pgid, sig):
        raise PermissionError(1, "Operation not permitted")

    rows = "  123   123 Z\n" + ("  124   123 S\n" if live_member else "")
    clock = iter([0.0, 0.0, 11.0])
    monkeypatch.setattr(guardian, "os", SimpleNamespace(killpg=killpg))
    monkeypatch.setattr(guardian, "signal", SimpleNamespace(SIGKILL=9))
    monkeypatch.setattr(guardian, "subprocess", SimpleNamespace(
        run=lambda *a, **k: SimpleNamespace(stdout=rows), DEVNULL=subprocess.DEVNULL,
    ))
    monkeypatch.setattr(guardian, "time", SimpleNamespace(
        monotonic=lambda: next(clock), sleep=lambda seconds: None,
    ))
    if live_member:
        with pytest.raises(TimeoutError, match="live writers"):
            guardian._terminate_group(process)
        assert waits == []
    else:
        guardian._terminate_group(process)
        assert waits == [10]


@pytest.mark.skipif(os.name == "nt", reason="native POSIX ps/process-group guardian")
def test_real_guardian_without_waitid_preserves_child_exit(guardian, tmp_path):
    script = (
        "import importlib.util,os,sys;"
        "s=importlib.util.spec_from_file_location('guardian',sys.argv[1]);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "setattr(m.os,'waitid',None);r,w=os.pipe();"
        "result=m._supervise(r,[sys.executable,'-c','raise SystemExit(23)']);"
        "os.close(w);raise SystemExit(result)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, guardian.__file__],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
        **no_window_kwargs(),
    )
    assert result.returncode == 23, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native Windows retained Job guardian")
@pytest.mark.parametrize("outcome", ["success", "exit", "timeout", "absolute-timeout", "launch"])
@pytest.mark.timeout(30)
def test_retained_windows_child_preserves_outcome_and_releases_custody(
    guardian, tmp_path, outcome,
):
    runtime_path = (
        Path(__file__).resolve().parents[3]
        / "libs" / "versioned-runtime" / "versioned_runtime.py"
    )
    runtime = guardian._load_resource("retained_guardian_test_runtime", runtime_path)
    root = tmp_path / "runtime"
    root.mkdir()
    slot = runtime.slot(root, "probe")
    commands = {
        "success": [sys.executable, "-I", "-c", "print('owned')"],
        "exit": [sys.executable, "-I", "-c", "raise SystemExit(23)"],
        "timeout": [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
        "absolute-timeout": [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
        "launch": [str(tmp_path / "missing-child.exe")],
    }
    errors = {
        "exit": subprocess.CalledProcessError,
        "timeout": guardian.ChildTimeoutExpired,
        "absolute-timeout": guardian.ChildTimeoutExpired,
        "launch": guardian.ChildLaunchError,
    }
    with runtime.construction_lease(root, "probe") as descriptor:
        def run():
            return guardian.run_owned(
                commands[outcome], cwd=slot, env=dict(os.environ),
                timeout=3, procutil=agent_procutil, construction_fd=descriptor,
                runtime_root=root, runtime_version="probe", runtime_path=runtime_path,
                **({"deadline": time.monotonic() + 3} if outcome == "absolute-timeout" else {}),
            )

        if outcome == "success":
            assert run().stdout == "owned\n"
        else:
            with pytest.raises(errors[outcome]) as caught:
                run()
            if outcome == "exit":
                assert caught.value.returncode == 23
        with runtime._cleanup_lease(root, "probe", allow_construction_owner=True) as admitted:
            assert admitted


@pytest.mark.skipif(os.name != "nt", reason="native Windows retained Job guardian")
@pytest.mark.parametrize("outcome", ["success", "exit", "timeout", "launch", "unavailable"])
@pytest.mark.timeout(30)
def test_retained_guardian_archives_child_not_protocol_and_preserves_outcome(
    guardian, tmp_path, monkeypatch, outcome, capsys,
):
    helper_path = SCRIPTS / "installer_diagnostics.py"
    diagnostics = guardian._load_resource("guardian_capture_fixture", helper_path)
    environment = dict(os.environ, USERPROFILE=str(tmp_path), HOME=str(tmp_path))
    environment = diagnostics.reserve_launch(environment)
    directory = Path(environment["COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY"])
    runtime_path = (
        Path(__file__).resolve().parents[3]
        / "libs" / "versioned-runtime" / "versioned_runtime.py"
    )
    runtime = guardian._load_resource("capture_guardian_runtime", runtime_path)
    root = tmp_path / "runtime"
    root.mkdir()
    slot = runtime.slot(root, "probe")
    credential = "gh" + "p_" + "g" * 24
    child_error = credential + "\nchild-error\n"
    script = (
        "import os,sys,time;"
        "os.write(1,b'out\\r\\nprogress\\rcomplete\\n'+b'x'*300000+b'\\n');"
        f"os.write(2,{child_error.encode()!r}+b'y'*300000+b'\\n');"
        + ("time.sleep(60)" if outcome == "timeout" else
           "raise SystemExit(23)" if outcome == "exit" else "")
    )
    command = ([str(tmp_path / "missing.exe")] if outcome == "launch" else
               [sys.executable, "-I", "-c", script])
    if outcome == "unavailable":
        helper_path = tmp_path / "missing-diagnostics.py"
        for name in (
            "HOME", "USERPROFILE", "COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT",
            "COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY",
        ):
            monkeypatch.setenv(name, environment[name])
        monkeypatch.delenv("COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING", raising=False)
    errors = {
        "exit": subprocess.CalledProcessError,
        "timeout": guardian.ChildTimeoutExpired,
        "launch": guardian.ChildLaunchError,
    }
    with runtime.construction_lease(root, "probe") as descriptor:
        def run():
            return guardian.run_owned(
                command, cwd=slot, env=environment, timeout=3,
                procutil=agent_procutil, construction_fd=descriptor,
                runtime_root=root, runtime_version="probe", runtime_path=runtime_path,
                diagnostics_path=helper_path,
            )

        if outcome in errors:
            with pytest.raises(errors[outcome]) as caught:
                run()
            result = caught.value
            if outcome == "exit":
                assert result.returncode == 23
        else:
            result = run()
            assert result.returncode == 0
        if outcome != "launch":
            assert result.stdout == "out\nprogress\ncomplete\n" + "x" * 300000 + "\n"
            assert result.stderr == child_error + "y" * 300000 + "\n"
        with runtime._cleanup_lease(root, "probe", allow_construction_owner=True) as admitted:
            assert admitted
    if outcome == "unavailable":
        assert os.environ["COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING"] == "1"
        assert "WARNING: installer diagnostics unavailable" in capsys.readouterr().err
        assert (directory / "missing-diagnostics.json").exists()
        assert diagnostics.launch_reference(environment)["missing_diagnostics"]
        assert not list(directory.glob("*.raw"))
        return
    archives = list(directory.glob("*.log"))
    assert len(archives) == 1
    archive = archives[0].read_text(encoding="utf-8")
    assert '"returncode"' not in archive and '"launch_error"' not in archive
    assert credential not in archive
    assert len(list(directory.glob("*.pending"))) == 1
    if outcome != "launch":
        assert "child-error" in archive and "complete" in archive
        assert any(credential.encode() in path.read_bytes() for path in directory.glob("*.raw"))


@pytest.mark.skipif(os.name != "nt", reason="native Windows retained Job guardian")
@pytest.mark.parametrize("inherit_output", [True, False])
@pytest.mark.timeout(30)
def test_durable_guardian_keeps_existing_descendant_pipe_completion(
    guardian, tmp_path, inherit_output,
):
    runtime_path = (
        Path(__file__).resolve().parents[3]
        / "libs" / "versioned-runtime" / "versioned_runtime.py"
    )
    runtime = guardian._load_resource("pipe_contract_runtime", runtime_path)
    root = tmp_path / "runtime"
    root.mkdir()
    slot = runtime.slot(root, "probe")
    helper = SCRIPTS / "installer_diagnostics.py"
    diagnostics = guardian._load_resource("pipe_contract_diagnostics", helper)
    environment = diagnostics.reserve_launch(dict(os.environ, USERPROFILE=str(tmp_path)))
    redirection = (
        ",stdout=sys.stdout,stderr=sys.stderr" if inherit_output else
        ",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL"
    )
    script = (
        "import subprocess,sys;"
        "subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'],"
        f"creationflags=subprocess.CREATE_NO_WINDOW{redirection});"
        "print('root complete',flush=True)"
    )
    with runtime.construction_lease(root, "probe") as descriptor:
        def run():
            return guardian.run_owned(
                [sys.executable, "-I", "-c", script], cwd=slot, env=environment, timeout=3,
                procutil=agent_procutil, construction_fd=descriptor,
                runtime_root=root, runtime_version="probe", runtime_path=runtime_path,
                diagnostics_path=helper,
            )

        if inherit_output:
            with pytest.raises(guardian.ChildTimeoutExpired) as caught:
                run()
            assert caught.value.stdout == "root complete\n"
        else:
            assert run().stdout == "root complete\n"
        with runtime._cleanup_lease(root, "probe", allow_construction_owner=True) as admitted:
            assert admitted


@pytest.mark.skipif(os.name != "nt", reason="native Windows owned capture")
def test_capture_write_failure_still_drains_both_native_streams(guardian):
    class FailedCapture:
        writes = 0

        def write(self, data):
            self.writes += 1
            if self.writes == 1:
                return 1
            raise OSError("capture storage unavailable")

    capture = FailedCapture()
    warnings = []
    process, job = agent_procutil.spawn_sync_in_kill_on_close_job(
        [sys.executable, "-I", "-c",
         "import os;os.write(1,b'o'*300000);os.write(2,b'e'*300000)"],
        require_job=True, allow_breakaway=False, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    try:
        output = guardian._CapturedPipes(
            process, {"stdout": capture}, lambda: warnings.append("missing"),
        )
        stdout, stderr = output.communicate(timeout=10)
        assert process.returncode == 0
        assert stdout == "o" * 300000 and stderr == "e" * 300000
        assert warnings == ["missing"] and capture.writes == 2
    finally:
        job.terminate_and_wait(10)
        process.wait(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="native Windows executable-token limit")
@pytest.mark.timeout(30)
def test_retained_windows_guardian_accepts_a_long_owning_interpreter(guardian, tmp_path):
    environment = tmp_path / "owner"
    while len(str(environment / "Scripts" / "python.exe")) <= 270:
        environment /= "interpreter-" + "x" * 30
    venv.EnvBuilder(with_pip=False, symlinks=False).create(environment)
    python = environment / "Scripts" / "python.exe"
    repo = Path(__file__).resolve().parents[3]
    code = """
import importlib.util,json,sys
from pathlib import Path
s=importlib.util.spec_from_file_location("long_owner_guardian",sys.argv[1])
guardian=importlib.util.module_from_spec(s);s.loader.exec_module(guardian)
runtime=guardian._load_resource("long_owner_runtime",sys.argv[2])
procutil=guardian._load_resource("long_owner_procutil",sys.argv[3])
root=Path(sys.argv[4]);root.mkdir();slot=runtime.slot(root,"probe")
assert len(sys.executable)>260
with runtime.construction_lease(root,"probe") as descriptor:
    result=guardian.run_owned(
        [sys._base_executable,"-I","-c","print('owned')"],
        cwd=slot,env=dict(__import__("os").environ),timeout=3,
        procutil=procutil,construction_fd=descriptor,
        runtime_root=root,runtime_version="probe",runtime_path=Path(sys.argv[2]),
    )
    with runtime._cleanup_lease(root,"probe",allow_construction_owner=True) as admitted:
        assert admitted
print(json.dumps({"owner":sys.executable,"output":result.stdout}))
"""
    result = subprocess.run(
        [str(python), "-I", "-B", "-c", code, guardian.__file__,
         str(repo / "libs" / "versioned-runtime" / "versioned_runtime.py"),
         str(repo / "libs" / "agent-procutil" / "src" / "agent_procutil" / "__init__.py"),
         str(tmp_path / "runtime")],
        executable=str(python), cwd=tmp_path, capture_output=True, text=True, timeout=20,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert Path(report["owner"]) == python and report["output"] == "owned\n"
