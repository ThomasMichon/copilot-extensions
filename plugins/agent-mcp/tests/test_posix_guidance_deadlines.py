"""Actual Linux hook deadlines retain selector and writer group ownership."""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import shutil
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator

import pytest

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import run_caller_owned

from .managed_hook_fixture import packaged_hooks as packaged_hooks
from .managed_hook_fixture import prepared_helper as prepared_helper

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux native hook custody")


def _alive(pid: int) -> bool:
    try:
        return not (Path("/proc") / str(pid) / "stat").read_text().split(") ", 1)[1].startswith("Z")
    except FileNotFoundError:
        return False


@pytest.fixture
def multiline_control(
    prepared_helper: tuple[Path, Path, Path],
) -> Iterator[subprocess.Popen[bytes]]:
    code = (
        "import ctypes,time\n"
        "libc=ctypes.CDLL(None,use_errno=True)\n"
        "libc.prctl.argtypes=[ctypes.c_int,ctypes.c_char_p,"
        "ctypes.c_ulong,ctypes.c_ulong,ctypes.c_ulong]\n"
        "assert libc.prctl(15,b'native\\ncontrol',0,0,0)==0\n"
        "print('ready',flush=True)\ntime.sleep(300)\n"
    )
    process = subprocess.Popen(
        [str(prepared_helper[1]), "-I", "-B", "-c", code],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        assert process.stdout is not None
        assert select.select([process.stdout], [], [], 5)[0]
        assert process.stdout.readline() == b"ready\n"
        assert b"native\ncontrol" in (Path("/proc") / str(process.pid) / "stat").read_bytes()
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()


def test_bash_settlement_handles_unrelated_multiline_process_name(
    tmp_path: Path, packaged_hooks: Path, multiline_control: subprocess.Popen[bytes],
    record_property: Callable[[str, object], None],
) -> None:
    bash = shutil.which("bash")
    assert bash
    # This pinned child never leads a group, so its PID cannot name a live group.
    assert getattr(os, "getpgid")(multiline_control.pid) != multiline_control.pid
    started = time.monotonic()
    result = run_caller_owned(
        [bash, str(packaged_hooks / "scripts/_bootstrap_python/native_owned.sh"),
         "--wait-group-settlement", str(multiline_control.pid), "3"],
        cwd=tmp_path, timeout=5, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert result.returncode == 0 and not result.stdout and not result.stderr, result
    assert multiline_control.poll() is None
    record_property("mcp_native_settlement_execution", json.dumps({
        "multiline_unrelated_name": True, "unrelated_control_alive": True,
        "seconds": time.monotonic() - started,
    }))


def test_bash_settlement_tolerates_successful_empty_read_of_exited_process(
    tmp_path: Path, packaged_hooks: Path, multiline_control: subprocess.Popen[bytes],
    record_property: Callable[[str, object], None],
) -> None:
    bash = shutil.which("bash")
    assert bash
    process_entry = Path("/proc") / str(multiline_control.pid) / "stat"
    marker, released, proof = (tmp_path / name for name in ("opened", "released", "raced"))
    helper = tmp_path / "native_owned.sh"
    original = (packaged_hooks / "scripts/_bootstrap_python/native_owned.sh").read_text()
    needle = 'record="$(< "$entry")";'
    assert original.count(needle) == 1
    # Hold a real proc FD over reaping, then reproduce Bash's successful-empty
    # read outcome deterministically rather than depending on scheduler timing.
    replacement = '''record="$(
        if [[ "$entry" == "$MCP_RACE_ENTRY" && ! -e "$MCP_RACE_MARKER" ]]; then
            exec {race_fd}<"$entry" || exit 1
            printf opened > "$MCP_RACE_MARKER"
            for (( attempt=0; attempt<300; attempt++ )); do
                [[ ! -e "$MCP_RACE_RELEASED" ]] || break
                sleep .01
            done
            [[ -e "$MCP_RACE_RELEASED" && ! -e "$entry" ]] || exit 1
            printf raced > "$MCP_RACE_PROOF"
        else
            cat "$entry"
        fi
    )";'''
    helper.write_text(original.replace(needle, replacement), encoding="utf-8")
    failures: list[Exception] = []
    done = threading.Event()

    def reap_control() -> None:
        try:
            deadline = time.monotonic() + 5
            while not marker.exists():
                if time.monotonic() >= deadline or done.wait(.01):
                    raise TimeoutError("Observer did not reach the proc read barrier")
            multiline_control.kill()
            multiline_control.wait(timeout=5)
            assert not process_entry.exists()
            released.write_text("reaped", encoding="ascii")
        except Exception as error:
            failures.append(error)

    controller = threading.Thread(target=reap_control)
    controller.start()
    try:
        result = run_caller_owned(
            [bash, str(helper), "--wait-group-settlement", str(multiline_control.pid), "5"],
            cwd=tmp_path, env={
                **os.environ, "MCP_RACE_ENTRY": str(process_entry),
                "MCP_RACE_MARKER": str(marker), "MCP_RACE_RELEASED": str(released),
                "MCP_RACE_PROOF": str(proof),
            },
            timeout=8, max_stdout_bytes=65536, max_stderr_bytes=65536,
        )
    finally:
        done.set()
        controller.join(timeout=6)
    assert not controller.is_alive() and not failures, failures
    assert proof.read_text() == "raced" and not process_entry.exists()
    assert result.returncode == 0 and not result.stdout and not result.stderr, result
    record_property("mcp_native_disappearing_process_execution", json.dumps({
        "opened_before_exit": marker.exists(), "reaped_before_empty_read": True,
        "observer_succeeded": True,
    }))


@pytest.mark.parametrize("missing", ["native_owned.sh"])
def test_bash_missing_custody_refuses_before_selection(
    tmp_path: Path, packaged_hooks: Path, missing: str,
) -> None:
    bash = shutil.which("bash")
    assert bash
    payload = tmp_path / "agent-mcp"
    shutil.copytree(packaged_hooks, payload)
    support = payload / "scripts/_bootstrap_python"
    (support / missing).unlink()
    marker = tmp_path / "selector-ran"
    (support / "select.sh").write_text(
        'printf invoked > "$MCP_SELECTOR_MARKER"\n', encoding="utf-8",
    )
    result = run_caller_owned(
        [bash, str(payload / "scripts/write-session-guidance.sh")],
        cwd=tmp_path, env={
            **os.environ, "COPILOT_PLUGIN_ROOT": str(payload), "MCP_SELECTOR_MARKER": str(marker),
        },
        input_text='{"sessionId":"session-1"}', timeout=5,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert result.returncode == 0 and result.stdout == "{}" and not result.stderr, result
    assert not marker.exists()


@pytest.mark.parametrize("scenario", [
    "held-input", "slow-selection", "failed-selection", "unusable-selection",
    "failed-startup", "blocked-writer",
])
def test_bash_guidance_deadlines(
    tmp_path: Path, scenario: str, packaged_hooks: Path,
    prepared_helper: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    bash = shutil.which("bash")
    assert bash
    _, selected, managed_root = prepared_helper
    payload = tmp_path / "agent-mcp"
    shutil.copytree(packaged_hooks, payload)
    scripts = payload / "scripts"
    proofs = tmp_path / "processes"
    proofs.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "UV_OFFLINE": "1",
        "UV_PYTHON_INSTALL_DIR": str(managed_root), "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "MCP_DEADLINE_PROOFS": str(proofs),
        "COPILOT_EXTENSIONS_GUIDANCE_DEADLINE": "invalid-inherited-deadline",
    }
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "BASH_ENV", "ENV"):
        env.pop(name, None)
    if scenario == "slow-selection":
        selector = scripts / "_bootstrap_python/select.sh"
        selector.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s' \"$BASHPID\" > \"$MCP_DEADLINE_PROOFS/selector\"\n"
            "sleep 300 &\nprintf '%s' \"$!\" > \"$MCP_DEADLINE_PROOFS/descendant\"\n"
            "wait\n",
            encoding="utf-8",
        )
    elif scenario in {"failed-selection", "unusable-selection", "failed-startup"}:
        startup = tmp_path / "unusable-python"
        if scenario == "failed-startup":
            startup.write_text(
                f"#!{bash}\n"
                'printf reached > "$MCP_DEADLINE_PROOFS/startup"\nexit 17\n',
                encoding="utf-8",
            )
            startup.chmod(0o700)
        selector = scripts / "_bootstrap_python/select.sh"
        selector.write_text(
            "#!/usr/bin/env bash\ntrap '' TERM\n"
            "printf '%s' \"$BASHPID\" > \"$MCP_DEADLINE_PROOFS/selector\"\n"
            "sleep 300 &\nprintf '%s' \"$!\" > \"$MCP_DEADLINE_PROOFS/descendant\"\n"
            + ("exit 17\n" if scenario == "failed-selection" else
               "printf '%s\\n' " + (
                   shlex.quote(str(startup)) if scenario == "failed-startup"
                   else "/unavailable/python"
               ) + "\n"),
            encoding="utf-8",
        )
    elif scenario == "blocked-writer":
        (scripts / "write_session_guidance.py").write_text(
            "import os,subprocess,sys,time\nfrom pathlib import Path\n"
            "root=Path(os.environ['MCP_DEADLINE_PROOFS'])\n"
            "(root/'writer').write_text(str(os.getpid()))\n"
            "child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(300)'])\n"
            "(root/'descendant').write_text(str(child.pid))\n"
            "time.sleep(300)\n",
            encoding="utf-8",
        )
    argv = [bash, str(scripts / "write-session-guidance.sh")]
    if scenario == "held-input":
        worker = (
            "import json,subprocess,sys\n"
            "p=subprocess.Popen(json.loads(sys.argv[1]),stdin=subprocess.PIPE,"
            "stdout=subprocess.PIPE,stderr=subprocess.PIPE)\n"
            "try:\n"
            " p.stdin.write(b'{\"sessionId\":\"session-1\"}');p.stdin.flush()\n"
            " p.wait(timeout=45)\n"
            " print(json.dumps({'code':p.returncode,'out':p.stdout.read().decode(),"
            "'err':p.stderr.read().decode()}))\n"
            "finally:\n"
            " if p.poll() is None:p.kill();p.wait(timeout=5)\n"
            " p.stdin.close()\n"
        )
        argv = [str(selected), "-I", "-B", "-c", worker, json.dumps(argv)]
    started = time.monotonic()
    result = run_caller_owned(
        argv, env=env, cwd=tmp_path, input_text='{"sessionId":"session-1"}',
        timeout=50, max_stdout_bytes=65536, max_stderr_bytes=65536,
        budget=OwnedExecutionBudget.from_timeout(55, cleanup_reserve=5),
    )
    elapsed = time.monotonic() - started
    assert result.returncode == 0 and not result.stderr, result
    if scenario == "held-input":
        assert json.loads(result.stdout) == {"code": 0, "out": "{}", "err": ""}
        assert not list(proofs.iterdir())
    else:
        assert result.stdout == "{}"
        if scenario == "failed-startup":
            assert (proofs / "startup").read_text() == "reached"
        pids = [int(path.read_text()) for path in proofs.iterdir() if path.name != "startup"]
        assert len(pids) == 2
        assert not [pid for pid in pids if _alive(pid)]
    assert elapsed < 45, elapsed
    record_property("mcp_posix_deadline_execution", json.dumps({
        "scenario": scenario, "elapsed_seconds": elapsed,
        "hook_timeout_seconds": 45, "contributors_remaining": 0,
    }))


@pytest.mark.parametrize("phase", ["selection", "writer"])
def test_bash_guidance_parent_loss_retires_owned_contributors(
    tmp_path: Path, phase: str, packaged_hooks: Path,
    prepared_helper: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    import signal

    from agent_procutil._caller_owned import _pidfd_exited
    from agent_procutil._linux_pidfd import open_pidfd, send_pidfd_signal

    bash = shutil.which("bash")
    assert bash
    payload = tmp_path / "agent-mcp"
    shutil.copytree(packaged_hooks, payload)
    scripts = payload / "scripts"
    proofs = tmp_path / "processes"
    proofs.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    if phase == "selection":
        (scripts / "_bootstrap_python/select.sh").write_text(
            "#!/usr/bin/env bash\ntrap '' TERM\n"
            'printf "%s" "$BASHPID" > "$MCP_DEADLINE_PROOFS/selection"\n'
            'sleep 300 &\nprintf "%s" "$!" > "$MCP_DEADLINE_PROOFS/descendant"\nwait\n',
            encoding="utf-8",
        )
    else:
        (scripts / "write_session_guidance.py").write_text(
            "import os,signal,subprocess,sys,time\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
            "root=Path(os.environ['MCP_DEADLINE_PROOFS'])\n"
            "(root/'writer').write_text(str(os.getpid()))\n"
            "child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(300)'])\n"
            "(root/'descendant').write_text(str(child.pid))\ntime.sleep(300)\n",
            encoding="utf-8",
        )
    environment = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "UV_OFFLINE": "1",
        "UV_PYTHON_INSTALL_DIR": str(prepared_helper[2]),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"), "MCP_DEADLINE_PROOFS": str(proofs),
    }
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "BASH_ENV", "ENV"):
        environment.pop(name, None)
    entries = json.loads((payload / "hooks.json").read_text(encoding="utf-8"))["hooks"]["sessionStart"]
    commands = [entry["bash"] for entry in entries if "write-session-guidance.sh" in entry.get("bash", "")]
    assert len(commands) == 1
    hook = subprocess.Popen(
        [bash, "-c", commands[0]], env=environment, cwd=tmp_path,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    pins: dict[int, int] = {}
    group: int | None = None
    try:
        paths = [proofs / phase, proofs / "descendant"]
        deadline = time.monotonic() + 10
        while not all(path.exists() and path.read_text().isdigit() for path in paths):
            assert hook.poll() is None
            assert time.monotonic() < deadline, "Owned contributors did not reach readiness"
            time.sleep(.01)
        pids = [int(path.read_text()) for path in paths]
        group = int(getattr(os, "getpgid")(pids[0]))
        # The retained session leader identifies its actual native lifetime holder.
        identity = (Path("/proc") / str(group) / "stat").read_text()
        holder = int(identity.rsplit(") ", 1)[1].split()[1])
        assert hook.poll() is None
        holders: set[int] = set()
        while holder != hook.pid:
            assert holder > 1 and holder not in holders, "Native lifetime holder is not caller-owned"
            holders.add(holder)
            holder_identity = (Path("/proc") / str(holder) / "stat").read_text()
            holder = int(holder_identity.rsplit(") ", 1)[1].split()[1])
        retained = {*pids, group, *holders}
        for pid in retained:
            pins[pid] = open_pidfd(pid)
            assert not _pidfd_exited(pins[pid])
        started = time.monotonic()
        hook.kill()
        hook.wait(timeout=5)
        deadline = started + 3
        while not all(_pidfd_exited(fd) for fd in pins.values()) and time.monotonic() < deadline:
            time.sleep(.02)
        elapsed = time.monotonic() - started
        assert all(_pidfd_exited(fd) for fd in pins.values()), "Hook cancellation retained a contributor"
        assert elapsed < 3, elapsed
        record_property("mcp_posix_cancellation_execution", json.dumps({
            "phase": phase, "entry": "registered-bash", "parent_loss_seconds": elapsed,
            "cleanup_limit_seconds": 3, "contributors_remaining": 0,
        }))
    finally:
        if hook.poll() is None:
            hook.kill()
        hook.wait(timeout=5)
        try:
            for descriptor in pins.values():
                if not _pidfd_exited(descriptor):
                    send_pidfd_signal(descriptor, getattr(signal, "SIGKILL"))
            if group is not None:
                settled = run_caller_owned(
                    [bash, str(scripts / "_bootstrap_python/native_owned.sh"),
                     "--wait-group-settlement", str(group), "5"],
                    cwd=tmp_path, timeout=7, max_stdout_bytes=65536, max_stderr_bytes=65536,
                )
                assert settled.returncode == 0, settled
            assert all(_pidfd_exited(fd) for fd in pins.values())
        finally:
            for descriptor in pins.values():
                os.close(descriptor)
