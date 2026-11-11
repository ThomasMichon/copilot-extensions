"""Explicit hosted native Jobs and session ownership; no desktop observation."""

import asyncio
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import agent_procutil as pu
from agent_procutil._daemon_instance_identity import birth_identity, identity_state
from agent_procutil._daemon_instance_store import _test_context
from agent_procutil.daemon_instances import inspect_instances

from agent_mcp.ipc import request_via_socket
from agent_mcp.serve import flip_data_handle

_spec = importlib.util.spec_from_file_location(
    "custody_native_fixture", Path(__file__).with_name("generation_native_fixture.py"),
)
fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("AGENT_MCP_NATIVE_LIFETIME") != "1",
    reason="explicit disposable hosted Windows lifetime packet",
)


async def observed(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(.02)
    await asyncio.wait_for(poll(), 15)


async def record(path):
    async def read():
        return json.loads(path.read_text(encoding="utf-8"))
    return await fixture.eventually(read)


def pin(pid, birth):
    from agent_procutil._daemon_instance_identity import _windows_birth
    from agent_procutil.daemon_broker import WindowsDaemonProcess

    process = WindowsDaemonProcess(int(pid), ["native-mcp-fixture"])
    try:
        assert _windows_birth(process._handle) == birth
    except BaseException:
        process.close()
        raise
    return process


@pytest.mark.timeout(600)
async def test_native_caller_jobs_and_session_owner_lifetime(
    tmp_path, monkeypatch, record_testsuite_property,
):
    from windows_broker_fixture import close_job

    assert os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1"
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    assert os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    context = _test_context(os.environ)
    assert context is not None
    registry = Path(context["registry_root"])
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv(
        "UV_CACHE_DIR",
        str(Path(os.environ["COPILOT_EXTENSIONS_TEST_VENV_ROOT"]) / ".uv-cache"),
    )
    actor = tmp_path / "actor.py"
    actor.write_text(fixture.ACTOR, encoding="utf-8")
    upstream = tmp_path / "upstream.py"
    upstream.write_text(fixture.UPSTREAM, encoding="utf-8")
    launcher = Path(__file__).with_name("custody_lifetime_fixture.py")
    pins, jobs, finite, backends, backend_handles = [], [], [], [], []
    retained_stdin = None
    with contextlib.ExitStack() as retained:
        root, slots = fixture.build_runtimes(tmp_path, retained)
        environment = dict(os.environ, AGENT_MCP_INSTALL_DIR=str(root))
        for key in ("PYTHONPATH", "AGENT_MCP_CONTROL_TOKEN", "VIRTUAL_ENV"):
            environment.pop(key, None)

        async def venue(name):
            home = tmp_path / name
            home.mkdir()
            fixed = home / "current" / "serve.sock"
            config = home / "bridge.json"
            config.write_text(json.dumps({
                "server": {"type": "stdio", "command": [sys.executable, str(upstream)]},
                "auth": {"kind": "none"},
            }), encoding="utf-8")
            env = dict(environment, HOME=str(home), USERPROFILE=str(home),
                       AGENT_MCP_HOME=str(home), AGENT_MCP_SERVE_SOCKET=str(fixed))
            return home, fixed, config, env

        async def backend(label, home, fixed, config, env):
            (root / "current-version").write_text(slots[label][0], encoding="utf-8")
            handle = home / "current" / f"{label}.sock"
            process = await fixture.spawn(
                slots[label][1], actor, "backend", root, handle, fixed, config, env, home,
            )
            backends.append(process)
            backend_handles.append(handle)
            reply = await fixture.eventually(lambda: request_via_socket(handle, {"op": "ping"}))
            assert reply["version"] == slots[label][0]
            flip_data_handle(handle, legacy_path=fixed)
            return reply["pid"]

        def caller(mode, home, fixed, config, env, *, breakaway):
            python = slots["old"][1] / "Scripts" / "python.exe"
            log = retained.enter_context((home / f"{mode}.log").open("wb"))
            process, job = pu.spawn_sync_in_kill_on_close_job(
                [str(python), "-I", str(launcher), mode, str(actor), str(root),
                 str(fixed), str(config), str(home)],
                cwd=home, env=env, require_job=True, allow_breakaway=breakaway,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, **pu.no_window_kwargs(),
            )
            finite.append(process)
            jobs.append(job)
            return process, job

        owner = None
        try:
            # An inner breakaway Job cannot hide the retained restrictive outer.
            home, fixed, config, env = await venue("refusal")
            await backend("old", home, fixed, config, env)
            rejected, rejected_job = caller("nested", home, fixed, config, env, breakaway=False)
            assert await asyncio.to_thread(rejected.wait, 40) == 0
            refusal = json.loads((home / "refused.json").read_text())
            assert refusal == {
                "cleanup_confirmed": True, "admission_sent": False,
                "stdio": {"cleanup_confirmed": True, "admission_sent": False},
            }
            close_job(pu, rejected_job)
            assert not any(
                row["service"] == "agent-mcp-custody" and row["state"] == "running"
                for row in inspect_instances(registry)
            )

            home, fixed, config, env = await venue("survival")
            old_pid = await backend("old", home, fixed, config, env)
            await fixture.check_owner_capabilities(slots["old"], root, env, home, selected=True)
            await fixture.check_owner_capabilities(slots["new"], root, env, home, selected=False)
            setup, caller_job = caller("setup", home, fixed, config, env, breakaway=True)
            setup_record = await record(home / "setup.json")
            control = pin(setup_record["control"], setup_record["control_birth"])
            pins.append(control)
            keepers = [
                row for row in inspect_instances(registry)
                if row["service"] == "agent-mcp-custody" and row["state"] == "running"
            ]
            assert len(keepers) == 1
            keeper_record = keepers[0]
            keeper = pin(keeper_record["pid"], keeper_record["birth"])
            pins.append(keeper)
            import ctypes
            from ctypes import wintypes

            kernel = pu._kernel32()
            kernel.IsProcessInJob.argtypes = [
                wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
            ]
            for process, expected in ((keeper, False), (control, True)):
                member = wintypes.BOOL()
                assert kernel.IsProcessInJob(process._handle, caller_job.handle, ctypes.byref(member))
                assert bool(member.value) is expected
                assert process.poll() is None

            python = slots["old"][1] / "Scripts" / "python.exe"
            owner = await asyncio.create_subprocess_exec(
                str(python), "-I", str(launcher), "owner", str(actor), str(root),
                str(fixed), str(config), str(home),
                cwd=home, env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                **pu.no_window_kwargs(),
            )
            await fixture.send(owner, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                       "params": {}})
            initial = (await fixture.receive(owner))["result"]
            upstream_pid, upstream_birth = initial["pid"], birth_identity(initial["pid"])
            conduit_record = await record(home / "conduit.json")
            conduit = pin(conduit_record["pid"], conduit_record["birth"])
            pins.append(conduit)
            owner_record = await record(home / "owner.json")
            original_owner = pin(owner_record["pid"], owner_record["birth"])
            pins.append(original_owner)
            shim = pin(owner_record["shim"], owner_record["shim_birth"])
            shim_control = pin(conduit_record["control"], conduit_record["control_birth"])
            pins.extend((shim, shim_control))
            kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
            kernel.OpenJobObjectW.restype = wintypes.HANDLE
            shim_job = kernel.OpenJobObjectW(0x0004, False, owner_record["caller_job"])
            assert shim_job
            try:
                for process, expected in (
                    (shim, True), (shim_control, True), (conduit, False),
                    (original_owner, False),
                ):
                    member = wintypes.BOOL()
                    assert kernel.IsProcessInJob(process._handle, shim_job, ctypes.byref(member))
                    assert bool(member.value) is expected
                    assert process.poll() is None
            finally:
                assert kernel.CloseHandle(shim_job)
            assert initial["backend_pid"] == old_pid and initial["initialized"] == 1
            (home / "setup-release").write_text("complete", encoding="ascii")
            assert await asyncio.to_thread(setup.wait, 10) == 0
            close_job(pu, caller_job)
            await asyncio.to_thread(control.wait, 5)
            assert keeper.poll() is None
            (home / "shim-release").write_text("complete", encoding="ascii")
            await observed(lambda: (home / "shim-exited").exists())
            assert await asyncio.to_thread(shim.wait, 5) == 0
            (home / "caller-job-release").write_text("complete", encoding="ascii")
            await observed(lambda: (home / "caller-job-closed").exists())
            await asyncio.to_thread(shim_control.wait, 5)
            assert conduit.poll() is None and original_owner.poll() is None
            new_pid = await backend("new", home, fixed, config, env)
            await fixture.check_owner_capabilities(slots["new"], root, env, home, selected=True)
            await fixture.check_owner_capabilities(slots["old"], root, env, home, selected=False)
            sequence = 10

            async def inspect():
                nonlocal sequence
                sequence += 1
                await fixture.send(owner, {
                    "jsonrpc": "2.0", "id": sequence, "method": "tools/call",
                    "params": {"name": "inspect", "arguments": {}},
                })
                return (await fixture.receive(owner))["result"]

            changed = await fixture.eventually(
                inspect, lambda answer: answer["backend_pid"] == new_pid,
            )
            assert changed["pid"] == upstream_pid and changed["initialized"] == 1
            assert changed["pipeline_code"] == "new"
            # Retain stdin and all original pipes: owner exit, not EOF, must settle.
            kernel.DuplicateHandle.argtypes = [
                wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
            ]
            kernel.DuplicateHandle.restype = wintypes.BOOL
            duplicate = wintypes.HANDLE()
            assert kernel.DuplicateHandle(
                -1, owner.stdin.get_extra_info("pipe").fileno(), -1,
                ctypes.byref(duplicate), 0, False, 2,
            )
            retained_stdin = duplicate.value
            (home / "owner-release").write_text("complete", encoding="ascii")
            assert await asyncio.wait_for(owner.wait(), 10) == 0
            assert await asyncio.to_thread(original_owner.wait, 5) == 0
            assert await asyncio.to_thread(conduit.wait, 10) == 0
            await observed(lambda: identity_state(upstream_pid, upstream_birth) == "dead")
            keeper_handle = fixed.parent / "custody-v1" / fixed.name
            reply = await fixture.eventually(lambda: request_via_socket(keeper_handle, {"op": "ping"}))
            assert reply["attached"] == 0 and keeper.poll() is None
            kernel.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.GetHandleInformation.restype = wintypes.BOOL
            flags = wintypes.DWORD()
            assert kernel.GetHandleInformation(retained_stdin, ctypes.byref(flags))
            record_testsuite_property("caller_job_closed", True)
            record_testsuite_property("conduit_caller_job_closed", True)
            record_testsuite_property("conduit_bounded_control_exited", True)
            record_testsuite_property("bounded_control_exited", True)
            record_testsuite_property("restrictive_outer_refused", True)
            record_testsuite_property("restrictive_outer_stdio_refused", True)
            record_testsuite_property("interposed_parent_exited", True)
            record_testsuite_property("original_owner_death_settled", True)
            record_testsuite_property("post_parent_runtime", slots["new"][0])
        finally:
            for name in (
                "setup-release", "shim-release", "caller-job-release", "owner-release",
            ):
                if "home" in locals():
                    (home / name).write_text("settle", encoding="ascii")
            if owner is not None:
                owner.stdin.close()
                if retained_stdin is not None:
                    assert kernel.CloseHandle(retained_stdin)
                    retained_stdin = None
                await asyncio.wait_for(owner.wait(), 10)
                await asyncio.wait_for(owner.stdout.read(), 10)
                await asyncio.wait_for(owner.stderr.read(), 10)
            for job in reversed(jobs):
                if job.handle:
                    close_job(pu, job)
            for process in finite:
                await asyncio.to_thread(process.wait, 10)
            for handle in backend_handles:
                await fixture.shutdown(handle)
            for process in backends:
                assert await asyncio.wait_for(process.wait(), 10) == 0
            for process in pins:
                process.close()
