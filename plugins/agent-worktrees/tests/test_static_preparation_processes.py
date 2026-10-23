"""Native ownership across Bridge -> Worktrees -> captured Git descendants."""

import asyncio
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time

import pytest

pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
    pytest.mark.skipif(
        sys.platform != "linux" or not hasattr(os, "pidfd_open"),
        reason="native Linux process-group and identity-safe cleanup required",
    ),
]


@pytest.mark.parametrize("ending", ["cancel", "owner-failure"])
def test_bridge_owner_retires_nested_git_without_test_detachment_suppression(tmp_path, ending):
    repo = Path(__file__).resolve().parents[3]
    source = repo / "plugins/agent-bridge/src/agent_bridge/local_cache_refresh.py"
    spec = importlib.util.spec_from_file_location("native_bridge_preparation", source)
    bridge = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bridge
    spec.loader.exec_module(bridge)
    ready, fail = tmp_path / "ready.json", tmp_path / "fail"
    fixture = (
        "import json,os,pathlib,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
        "members=[(p,pathlib.Path(f'/proc/{p}/stat').read_text().rsplit(')',1)[1].split()[19]) "
        "for p in (os.getpid(),child.pid)]\n"
        "path=pathlib.Path(sys.argv[1]);stage=path.with_suffix('.new')\n"
        "stage.write_text(json.dumps({'group':os.getpgrp(),'owner':os.getppid(),'members':members}))\n"
        "os.replace(stage,path)\n"
        "time.sleep(30)\n"
    )
    driver = (
        "import os,pathlib,sys,threading,time\n"
        "from agent_worktrees import static_instructions as s,push_timeout as p\n"
        "assert not os.environ.get('COPILOT_EXTENSIONS_TEST_CONTAINED')\n"
        "original=p.run_bounded\n"
        "def run(cmd,**kw):\n"
        " assert kw['inherit_process_group'] is True\n"
        " return original([sys.executable,'-c',sys.argv[1],sys.argv[2]],**kw)\n"
        "p.run_bounded=run\n"
        "def failure():\n"
        " while not pathlib.Path(sys.argv[3]).exists(): time.sleep(.01)\n"
        " os._exit(23)\n"
        "threading.Thread(target=failure,daemon=True).start()\n"
        "s._git(pathlib.Path.cwd(),'status')\n"
    )
    env = dict(os.environ, WORKTREE_MANAGER_ENGINE_OWNS_PROCESS_GROUP="1")
    env.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
    env["PYTHONPATH"] = str(repo / "plugins/agent-worktrees/src")
    members = []

    def live(pid, started):
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        except FileNotFoundError:
            return False
        return fields[19] == started and fields[0] != "Z"

    async def exercise():
        task = asyncio.create_task(bridge._run_bounded_result(
            [sys.executable, "-c", driver, fixture, str(ready), str(fail)],
            timeout=10, env=env, retire_group=True,
        ))
        try:
            until = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < until:
                await asyncio.sleep(.02)
            assert ready.exists(), "nested Git fixture did not reach admission"
            data = json.loads(ready.read_text())
            members.extend(data["members"])
            assert data["group"] == data["owner"] != os.getpgrp()
            if ending == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                fail.touch()
                assert (await task).returncode == 23
            until = time.monotonic() + 2
            while any(live(pid, started) for pid, started in members) and time.monotonic() < until:
                await asyncio.sleep(.02)
            assert not any(live(pid, started) for pid, started in members)
        finally:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    try:
        asyncio.run(exercise())
    finally:
        if not members and ready.exists():
            members.extend(json.loads(ready.read_text())["members"])
        for pid, started in members:
            try:
                descriptor = os.pidfd_open(pid)
            except ProcessLookupError:
                continue
            try:
                if live(pid, started):
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            finally:
                os.close(descriptor)
