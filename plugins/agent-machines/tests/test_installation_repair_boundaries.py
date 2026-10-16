from __future__ import annotations

import json
import os
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


@pytest.mark.skipif(os.name == "nt", reason="native POSIX SIGTERM cleanup")
def test_sigterm_during_admission_restores_handler_and_releases_apply_lock(
    repair_fixture, monkeypatch,
):
    home, _, _, _, _ = repair_fixture
    plan = invoke(repair_fixture)
    previous = signal.getsignal(signal.SIGTERM)

    def terminated(*args, **kwargs):
        assert signal.getsignal(signal.SIGTERM) is not previous
        os.kill(os.getpid(), signal.SIGTERM)
        pytest.fail("termination was ignored")

    monkeypatch.setattr(repair_api, "admit_host", terminated)
    with pytest.raises(KeyboardInterrupt):
        invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert signal.getsignal(signal.SIGTERM) is previous
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
         "print(signal.SIGTERM in signal.pthread_sigmask(signal.SIG_BLOCK, []))"],
        cwd=tmp_path, environment=os.environ, timeout=5, readonly=False,
    )
    assert result.status == "exited" and result.exit_code == 0
    assert result.stdout.strip() == b"False"


@pytest.mark.skipif(os.name == "nt", reason="native POSIX spawn/cancellation race")
def test_sigterm_during_spawn_defers_until_process_ownership_is_available(tmp_path, monkeypatch):
    original = process_api.spawn_sync_in_kill_on_close_job
    children = []
    previous = signal.getsignal(signal.SIGTERM)

    def interrupted_spawn(*args, **kwargs):
        process, job = original(*args, **kwargs)
        children.append(process)
        os.kill(os.getpid(), signal.SIGTERM)
        return process, job

    monkeypatch.setattr(process_api, "spawn_sync_in_kill_on_close_job", interrupted_spawn)
    with pytest.raises(KeyboardInterrupt):
        run_action(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=tmp_path, environment=os.environ, timeout=5, readonly=True,
        )
    assert len(children) == 1 and children[0].poll() is not None
    assert signal.getsignal(signal.SIGTERM) is previous


@pytest.mark.skipif(os.name == "nt", reason="native POSIX SIGTERM cleanup and lock ordering")
@pytest.mark.parametrize("first_during_cleanup", [False, True])
def test_terminated_conductor_reaps_installer_before_unlocking(
    repair_fixture, tmp_path, first_during_cleanup,
):
    home, project, payload, _, identity = repair_fixture
    descendant = tmp_path / "descendant.pid"
    cleanup = tmp_path / "cleanup.started"
    release = tmp_path / "cleanup.release"
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
        if first_during_cleanup else
        f"  '{sys.executable}' '{sleeper}' &\n  wait\n  exit 0"
    )
    script.write_text(script.read_text().replace(
        '  printf ready > "$state"; exit 0', installer,
    ), encoding="utf-8")
    plan = invoke(repair_fixture, selection=("base",))
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
        if not first_during_cleanup:
            os.kill(process.pid, signal.SIGTERM)
        _wait_for(cleanup, process)
        if first_during_cleanup:
            os.kill(process.pid, signal.SIGTERM)
        with pytest.raises(RepairAdmissionError, match="already active"):
            with RestartJournal(home).locked():
                pytest.fail("lock released while the installer was still alive")
        os.kill(int(descendant.read_text()), 0)
        os.kill(process.pid, signal.SIGTERM)
        release.touch()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr.decode()
        assert json.loads(stdout)["interrupted"]
        with RestartJournal(home).locked():
            pass
        stat = Path("/proc") / descendant.read_text() / "stat"
        assert not stat.exists() or stat.read_text().split()[2] == "Z"
    finally:
        release.touch()
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
