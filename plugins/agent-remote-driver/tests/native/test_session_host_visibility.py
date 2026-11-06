"""Read-only Windows desktop observation; no canary, focus or input effects."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from agent_procutil import no_window_kwargs, windowless_python, windowless_python_env
from agent_procutil._interpreter import physical_executable
import pytest

from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host.osutil import pid_alive


@pytest.mark.asyncio
async def test_two_console_children_from_windowless_core_do_not_surface(tmp_path, monkeypatch):
    if sys.platform != "win32" or os.environ.get("COPILOT_NATIVE_DESKTOP_OBSERVE") != "1":
        pytest.skip("Explicit native Windows read-only desktop observation required")
    root = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(str(root / "libs" / "agent-procutil" / "tests"))
    from sdk_desktop_observer import DesktopObserver
    from sdk_desktop_win32 import WinAPI

    gui = windowless_python(sys.executable)
    assert Path(gui).name.lower() == "pythonw.exe"
    child_python = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert child_python is not None
    child = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line)\n"
        " print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':{}}),flush=True)\n"
    )
    observer = DesktopObserver(WinAPI(), "standalone-driver-two-launches")
    with observer:
        for incarnation in range(2):
            state = tmp_path / f"host-{incarnation}.json"
            process = subprocess.Popen(
                [
                    gui, "-I", "-m", "agent_remote_driver.session_host",
                    "--cwd", str(tmp_path), "--state-file", str(state), "--",
                    child_python, "-I", "-S", "-u", "-c", child,
                ],
                env={**os.environ, **windowless_python_env(sys.executable),
                     "PYTHONDONTWRITEBYTECODE": "1"},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
            )
            client = None
            try:
                for _ in range(200):
                    if state.exists():
                        try:
                            facts = json.loads(state.read_text())
                        except (OSError, ValueError):
                            pass
                        else:
                            break
                    assert process.poll() is None
                    await asyncio.sleep(0.025)
                else:
                    pytest.fail("windowless core did not publish readiness")
                assert facts["pid"] == process.pid
                client = await SessionHostClient.connect(port=facts["port"])
                hello = await client.attach(nonce=facts["nonce"].encode())
                await client.write(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
                _, frame = await asyncio.wait_for(anext(client.frames()), 5)
                assert json.loads(frame)["id"] == 1
                await asyncio.sleep(0.15)
                await client.terminate()
                await asyncio.to_thread(process.wait, timeout=5)
                assert process.returncode == 0 and not pid_alive(hello.child_pid)
            finally:
                if client is not None:
                    await client.close()
                if process.poll() is None:
                    process.kill()
                    await asyncio.to_thread(process.wait, timeout=5)
                process.communicate(timeout=5)
    packet = observer.packet()
    assert packet["passed"], {
        "violations": sorted({event["kind"] for event in packet["violations"]}),
        "error": packet["error"], "callback_error": packet["callback_error"],
        "cleanup_error": packet["cleanup_error"],
    }
