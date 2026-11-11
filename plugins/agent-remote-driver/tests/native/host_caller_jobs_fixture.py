"""Explicit Windows production-lifetime lane; never disables test containment.

Run with an installed core interpreter and a fresh private output directory.
The child is a synthetic ACP peer, not native Copilot acceptance.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

import agent_procutil as pu
from agent_procutil._interpreter import physical_executable
from agent_procutil.daemon_instances import DaemonLeasePolicy, daemon_test_lifecycle
from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host.launcher import launch_session_host
from agent_remote_driver.session_host.osutil import pid_alive, process_start_time


def _publish(path, value):
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def _alive(identity):
    return pid_alive(identity["pid"]) and process_start_time(identity["pid"]) == identity["birth"]


def _until_exited(identities):
    deadline = time.monotonic() + 10
    while any(_alive(identity) for identity in identities) and time.monotonic() < deadline:
        time.sleep(.025)
    assert not any(_alive(identity) for identity in identities), identities


def _leaf(directory):
    python = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert python is not None
    control = subprocess.Popen(
        [python, "-I", "-S", "-c", "import time;time.sleep(100)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **pu.no_window_kwargs(),
    )
    result = {"control": {"pid": control.pid, "birth": process_start_time(control.pid)}}
    assert result["control"]["birth"]
    try:
        program = (
            "import json,sys\nfrom pathlib import Path\n"
            f"Path({str(directory / 'child-started')!r}).touch()\n"
            "for line in sys.stdin:\n"
            " r=json.loads(line)\n"
            " if 'id' in r:\n"
            "  print(json.dumps({'jsonrpc':'2.0','id':r['id'],"
            "'result':{'method':r['method'],'literal':r.get('params')}}),flush=True)\n"
        )
        handle = launch_session_host(
            [python, "-I", "-S", "-u", "-c", program],
            cwd=str(directory), state_dir=directory / "host-state",
            ready_timeout=20, session_id="owned-job-fixture",
            unexpected_reap_seconds=0, active_reap_seconds=0,
        )
        result.update(
            port=handle.port, nonce=handle.nonce,
            host={"pid": handle.host_pid, "birth": process_start_time(handle.host_pid)},
            child={"pid": handle.child_pid, "birth": process_start_time(handle.child_pid)},
        )
    except Exception as error:
        result.update(
            errorType=type(error).__name__, error=str(error), traceback=traceback.format_exc(),
            cleanupConfirmed=getattr(error, "cleanup_confirmed", None),
            admissionSent=getattr(error, "admission_sent", None),
        )
    _publish(directory / "leaf.json", result)


def _middle(directory):
    with (directory / "leaf.log").open("wb") as log:
        process, inner = pu.spawn_sync_in_kill_on_close_job(
            [sys.executable, __file__, "leaf", str(directory)],
            cwd=directory, env=os.environ.copy(), require_job=True, allow_breakaway=True,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            **pu.no_window_kwargs(),
        )
    try:
        assert inner is not None
        assert process.wait(40) == 0, (directory / "leaf.log").read_text(errors="replace")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(5)
        if inner is not None:
            inner.close()
    _publish(directory / "middle.json", {"innerJobClosed": True})


async def _probe(result):
    client, attached = None, False

    async def exchange():
        nonlocal client, attached
        client = await SessionHostClient.connect(port=result["port"])
        hello = await client.attach(nonce=result["nonce"].encode())
        attached = True
        assert hello.child_pid == result["child"]["pid"]
        payload = {"literal": "after all caller Jobs\n'quoted' & $value"}
        await client.write((json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": payload,
        }) + "\n").encode())
        while True:
            cursor, frame = await anext(client.frames())
            reply = json.loads(frame)
            await client.ack(cursor)
            if reply.get("id") == 1:
                assert reply["result"] == {"method": "initialize", "literal": payload}
                return

    try:
        await asyncio.wait_for(exchange(), 10)
    finally:
        try:
            if attached:
                await asyncio.wait_for(client.terminate(), 3)
        finally:
            if client is not None:
                await asyncio.wait_for(client.close(), 3)


def qualify(directory):
    """Prove all-caller-Job survival and pre-execution strict-outer refusal."""
    assert sys.platform == "win32", "This native lane requires Windows"
    assert not pu.contained_test_mode(), "Use a production-lifetime lane, not a contained runner"
    directory = directory.resolve()
    directory.mkdir()
    cleanup, summary = [], []
    try:
        with daemon_test_lifecycle(
            owner="core-host-caller-job-fixture",
            registry_root=directory / "instances", cleanup_results=cleanup,
            lease=DaemonLeasePolicy(ttl_seconds=15, max_lifetime_seconds=150),
            deadline=time.monotonic() + 180,
        ) as lifecycle:
            for case, permissive in (("permissive", True), ("strict-outer", False)):
                selected = directory / case
                selected.mkdir()
                with (selected / "middle.log").open("wb") as log:
                    process, outer = pu.spawn_sync_in_kill_on_close_job(
                        [sys.executable, __file__, "middle", str(selected)],
                        cwd=selected, env={**os.environ, **lifecycle},
                        require_job=True, allow_breakaway=permissive,
                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        **pu.no_window_kwargs(),
                    )
                try:
                    assert outer is not None
                    assert process.wait(50) == 0, (selected / "middle.log").read_text(errors="replace")
                    result = json.loads((selected / "leaf.json").read_text(encoding="utf-8"))
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(5)
                    if outer is not None:
                        outer.close()
                assert json.loads((selected / "middle.json").read_text())["innerJobClosed"]
                _until_exited([result["control"]])
                if permissive:
                    assert "errorType" not in result, result
                    assert result["host"]["birth"] and result["child"]["birth"]
                    assert _alive(result["host"]) and _alive(result["child"])
                    asyncio.run(_probe(result))
                    _until_exited([result["host"], result["child"]])
                else:
                    assert result.get("errorType") == "DaemonSpawnError", result
                    assert result["cleanupConfirmed"] is True and result["admissionSent"] is False
                    assert not (selected / "child-started").exists()
                summary.append({
                    "case": case, "callerJobsClosed": True, "boundedControlExited": True,
                    "brokerAfterCallerJobs": permissive, "refusedBeforeHostEntry": not permissive,
                })
                _publish(directory / "summary.json", summary)
    finally:
        _publish(directory / "cleanup.json", [
            {"status": result.status, "instanceId": result.instance_id} for result in cleanup
        ])
    assert cleanup and all(result.status == "settled" for result in cleanup), cleanup
    print(json.dumps(summary))


if __name__ == "__main__":
    operation, selected = sys.argv[1:]
    {"qualify": qualify, "middle": _middle, "leaf": _leaf}[operation](Path(selected))
