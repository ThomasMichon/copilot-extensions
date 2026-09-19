"""Real Linux PTY/identity proof, run by the existing Linux CI plugin lane."""

import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_bridge.native_runtime import NativeRuntime
from agent_bridge.native_store import NativeError
from agent_bridge.session_host import launcher
from agent_bridge.session_host.client import SessionHostClient
from agent_bridge.session_host.execution_guard import check_catalog

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux native execution-host identity")


@pytest.mark.asyncio
async def test_real_native_cli_survives_frontend_loss_and_retires_only_its_generation(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    catalog = tmp_path / "catalog"
    marker = tmp_path / "counter.json"
    handles = []

    def launch(*a, **k):
        handle = launcher.launch_session_host(*a, **k)
        handles.append(handle)
        return handle

    monkeypatch.setattr("agent_bridge.config.config_dir", lambda: tmp_path / "bridge")
    runtime = NativeRuntime(root, launch=launch, catalog=catalog)
    program = (
        "import json,os,pathlib,sys\n"
        "assert os.isatty(0)\n"
        "f=os.open('/dev/tty',os.O_RDWR);os.close(f)\n"
        f"p=pathlib.Path({str(marker)!r})\n"
        "count=0\n"
        "def publish():\n"
        " q=p.with_suffix('.new');q.write_text(json.dumps({'pid':os.getpid(),'count':count,"
        "'execution':os.environ.get('AGENT_BRIDGE_NATIVE_EXECUTION_ID'),"
        "'host_nonce':os.environ.get('AGENT_BRIDGE_SESSION_HOST_NONCE')}));os.replace(q,p)\n"
        "publish();print('native prompt> ',end='',flush=True)\n"
        "for line in sys.stdin:\n"
        " count+=1;publish();print('count='+str(count),flush=True)\n"
    )
    request = {
        "executionId": "native-test", "generation": "generation-test", "codespace": "example-space",
        "owner": "example-owner", "cwd": str(tmp_path),
        "command": "exec " + shlex.join([sys.executable, "-u", "-c", program]),
    }
    unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
    first = second = None
    original = None
    try:
        prepared = await asyncio.to_thread(runtime.start, request)
        host = prepared["host"]
        original = dict(host)
        assert not marker.exists(), "caller command must wait until forwarding admission completes"
        assert check_catalog(catalog, "acp")[0] == 75
        await runtime.activate("native-test", "generation-test")
        for _ in range(100):
            if marker.exists():
                break
            await asyncio.sleep(.02)
        data = json.loads(marker.read_text())
        pid = data["pid"]
        assert data["execution"] == "native-test" and data["host_nonce"] is None
        acp_marker = tmp_path / "acp-started"
        with pytest.raises(RuntimeError, match="exited early"):
            await asyncio.to_thread(
                launcher.launch_session_host,
                [sys.executable, "-c", f"from pathlib import Path;Path({str(acp_marker)!r}).touch()"],
                cwd=str(tmp_path), state_dir=catalog, state_file_name="host-acp-candidate.json",
                env={"AGENT_BRIDGE_HOST_VENUE": "codespace:example-space"},
                nonce="acp-candidate-nonce", session_id="acp-candidate", ready_timeout=5,
            )
        assert not acp_marker.exists(), "ACP fallback must not create its child"
        first = await SessionHostClient.connect(port=host["port"])
        assert (await first.attach(nonce=host["nonce"].encode())).child_pid == pid
        assert b"native prompt> " in (await asyncio.wait_for(anext(first.frames()), 2))[1]
        await first.write(b"first\n")
        for _ in range(100):
            if json.loads(marker.read_text())["count"] == 1:
                break
            await asyncio.sleep(.01)
        await first.close()
        await asyncio.sleep(.1)
        repeated = await asyncio.to_thread(runtime.start, request)
        assert repeated["host"]["child_pid"] == pid and len(handles) == 1
        second = await SessionHostClient.connect(port=host["port"])
        assert (await second.attach(nonce=host["nonce"].encode())).child_pid == pid
        await second.write(b"second\n")
        for _ in range(100):
            if json.loads(marker.read_text())["count"] == 2:
                break
            await asyncio.sleep(.01)
        assert json.loads(marker.read_text())["count"] == 2
        runtime.bind_registration("native-test", "generation-test", "real-session", pid)
        live = {"status": "live", "updated_at": time.time()}
        db = SimpleNamespace(get_live_session=lambda _: live)
        assert runtime.status("native-test", "generation-test", db=db)["represented"]
        live["status"] = "expired"
        assert not runtime.status("native-test", "generation-test", db=db)["represented"]
        assert check_catalog(catalog, "acp")[0] == 75
        live.update(status="live", updated_at=time.time())
        assert runtime.status("native-test", "generation-test", db=db)["sessionId"] == "real-session"
        with pytest.raises(NativeError):
            await runtime.stop("native-test", "wrong-generation")
        corrupted = {**original, "host_pid": unrelated.pid}
        launcher._write_host_state(runtime._state_path("native-test"), corrupted)
        with pytest.raises(NativeError):
            await runtime.stop("native-test", "generation-test")
        assert unrelated.poll() is None
        launcher._write_host_state(runtime._state_path("native-test"), original)
        stopped = await runtime.stop("native-test", "generation-test")
        assert stopped["retired"] and stopped["state"] == "stopped"
        assert unrelated.poll() is None and len(handles) == 1
        assert check_catalog(catalog, "acp")[0] == 0
    finally:
        if original is not None:
            current = json.loads(runtime._state_path("native-test").read_text())
            if current.get("host_pid") != original["host_pid"]:
                launcher._write_host_state(runtime._state_path("native-test"), original)
            try:
                await runtime.stop("native-test", "generation-test")
            except Exception:
                for handle in handles:
                    handle.proc.terminate()
        for client in (first, second):
            if client:
                await client.close()
        for handle in handles:
            handle.proc.wait(timeout=10)
        unrelated.terminate()
        unrelated.wait(timeout=5)


@pytest.mark.asyncio
async def test_native_cli_outlives_the_actual_launching_frontend_process(tmp_path):
    root, catalog = tmp_path / "runtime", tmp_path / "catalog"
    marker = tmp_path / "running"
    program = f"import pathlib,os,time;pathlib.Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(60)"
    request = {
        "executionId": "survivor", "generation": "survivor-generation", "codespace": "example-space",
        "owner": "example-owner", "cwd": str(tmp_path),
        "command": "exec " + shlex.join([sys.executable, "-c", program]),
    }
    frontend = (
        "import asyncio,json,sys\nfrom pathlib import Path\n"
        "from agent_bridge.native_runtime import NativeRuntime\n"
        "r=NativeRuntime(Path(sys.argv[1]),catalog=Path(sys.argv[2]))\n"
        "q=json.loads(sys.argv[3]);r.start(q)\n"
        "print(json.dumps(asyncio.run(r.activate(q['executionId'],q['generation']))))\n"
    )
    env = {**os.environ, "AGENT_BRIDGE_CONFIG_DIR": str(tmp_path / "bridge")}
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", frontend, str(root), str(catalog), json.dumps(request)],
        capture_output=True, text=True, env=env, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    initial = json.loads(result.stdout)
    runtime = NativeRuntime(root, catalog=catalog, launch=lambda *a, **k: pytest.fail("replacement launch"))
    try:
        for _ in range(100):
            if marker.exists():
                break
            await asyncio.sleep(.02)
        assert int(marker.read_text()) == initial["host"]["child_pid"]
        resumed = runtime.start(request)
        assert resumed["host"]["child_pid"] == initial["host"]["child_pid"]
        assert resumed["host"]["host_pid"] == initial["host"]["host_pid"]
        assert check_catalog(catalog, "acp")[0] == 75
        assert (await runtime.stop("survivor", "survivor-generation"))["retired"]
    finally:
        await runtime.stop("survivor", "survivor-generation")
