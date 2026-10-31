"""Actual Manager roster consumer under disposable caller cleanup Jobs."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

from windows_broker_fixture import admit


READER = """
import json,sys,time
from pathlib import Path
slot=Path(sys.argv[1]).resolve()
import worktree_manager
if not Path(worktree_manager.__file__).resolve().is_relative_to(slot):
    raise RuntimeError('Picker reader is not using the constructed Manager runtime')
from worktree_manager.production_picker import context
from worktree_manager.production_picker.picker_tui.data_ssh import LiveLoader,Source
context.set_project(sys.argv[3])
source=Source('consumer-fixture','Win',None,local=True)
loader=LiveLoader([source])
loader.start()
deadline=time.monotonic()+30
try:
    while loader.state(source.machine,source.env)!='ready':
        if time.monotonic()>=deadline:
            raise RuntimeError('Manager loader failed to publish a current roster: '+
                str(loader.error(source.machine,source.env)))
        time.sleep(0.05)
    rows=loader.records()
except Exception:
    for frame in list(sys._current_frames().values())[:8]:
        locations=[]
        while frame is not None:
            path=Path(frame.f_code.co_filename)
            locations.append(path.name+':'+str(frame.f_lineno)+':'+frame.f_code.co_name)
            frame=frame.f_back
        if locations:
            print('Owned reader thread locations: '+' <- '.join(locations[:16]),file=sys.stderr)
    from agent_procutil.daemon_instances import inspect_instances
    from agent_procutil._daemon_instance_identity import identity_state
    for record in inspect_instances():
        if record.get('service')=='agent-worktrees-status-monitor':
            identity=(identity_state(int(record['pid']),record['birth'])
                      if 'pid' in record and 'birth' in record else 'unbound')
            print('Owned resident instance state='+record['state']+'; identity='+identity,
                  file=sys.stderr)
    for path in (Path(sys.argv[2])/'logs/status-monitor.log',
                 Path(sys.argv[2])/'picker-status-monitor.log',
                 Path(sys.argv[2]).parent/'.agent-worktrees-status-monitor.log'):
        if path.is_file():
            with path.open('rb') as stream:
                stream.seek(max(0,path.stat().st_size-8192))
                print('Owned resident diagnostic '+path.name+':\\n'+
                    stream.read().decode('utf-8',errors='replace'),file=sys.stderr)
    raise
finally:
    loader.cancel()
from zdd import routing
endpoint=routing.read_active_endpoint(Path(sys.argv[2])/'status-monitor-routing')
if endpoint is None:
    raise RuntimeError('Manager current-roster read has no resident route')
print(json.dumps({'rows':len(rows),'pid':endpoint.pid,'generation':endpoint.generation}))
"""

CONTROL = """
import json,sys
from pathlib import Path
from zdd import routing
from agent_worktrees.status_monitor_cutover import ControlClient
root=Path(sys.argv[1])
endpoint=routing.read_active_endpoint(root/'status-monitor-routing')
if endpoint is None:
    raise RuntimeError('Expected owned resident route is absent')
client=ControlClient('http://'+endpoint.client_host+':'+str(endpoint.port),
    token=(root/'status-monitor-control.token').read_text().strip(),config_dir=root)
print(json.dumps(client.shutdown() if sys.argv[2]=='shutdown' else client.health()))
"""


def qualify(root: Path, runtime: Path, command: list[str], projects: list[Path], *,
            manager_slot: Path,
            environment: dict[str, str], run, procutil) -> dict:
    admit(root, True)
    from agent_procutil.daemon_broker import WindowsDaemonProcess
    from zdd import routing

    python = manager_slot / "Scripts" / "python.exe"
    env = {**environment, "WORKTREE_MANAGER_ENGINE_ARGV": json.dumps(command),
           "WORKTREE_MANAGER_ROOT": str(manager_slot.parent.parent)}
    cycles = []
    for cycle in range(2):
        endpoint = routing.read_active_endpoint(runtime / "status-monitor-routing")
        if endpoint is not None:
            raise RuntimeError("Cold Manager acceptance requires a stopped resident")
        callers = []
        resident = None
        try:
            for index in range(2):
                process, job = procutil.spawn_sync_in_kill_on_close_job(
                    [str(python), "-I", "-B", "-c", READER, str(manager_slot), str(runtime),
                     f"broker-fixture-{index}"],
                    cwd=projects[index], env=env, require_job=True, allow_breakaway=True,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", **procutil.no_window_kwargs(),
                )
                callers.append((process, job))
                if job is None:
                    raise RuntimeError("Manager reader caller Job is unavailable")
            replies = []
            for process, _job in callers:
                stdout, stderr = process.communicate(timeout=45)
                if process.returncode:
                    raise RuntimeError("Actual Manager current-roster read failed: " + stdout + stderr)
                replies.append(json.loads(stdout))
            endpoint = routing.read_active_endpoint(runtime / "status-monitor-routing")
            if endpoint is None or any(
                (reply["pid"], reply["generation"]) != (endpoint.pid, endpoint.generation)
                for reply in replies
            ):
                raise RuntimeError("Concurrent Manager reads did not retain one serving owner")
            resident = WindowsDaemonProcess(endpoint.pid, ["owned-fixture-status-monitor"])
        finally:
            for process, job in callers:
                if job is not None:
                    job.terminate_and_wait(5)
                    job.close()
                process.wait(timeout=5)
            endpoint = routing.read_active_endpoint(runtime / "status-monitor-routing")
            if endpoint is None and sys.exc_info()[0] is not None:
                deadline = time.monotonic() + 30
                while endpoint is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                    endpoint = routing.read_active_endpoint(runtime / "status-monitor-routing")
            if resident is None and endpoint is not None:
                resident = WindowsDaemonProcess(endpoint.pid, ["owned-fixture-status-monitor"])
            if resident is not None:
                try:
                    if sys.exc_info()[0] is None:
                        for _sample in range(2):
                            health = json.loads(run([command[0], "-I", "-B", "-c", CONTROL,
                                                     str(runtime), "health"], timeout=15))
                            if health.get("status") != "ready" or resident.poll() is not None:
                                raise RuntimeError("Resident did not survive every Manager caller Job")
                            time.sleep(1)
                        cycles.append({"cycle": cycle + 1, "concurrent_current_reads": True,
                                       "all_caller_jobs_closed": True, "resident_survived": True})
                finally:
                    try:
                        result = json.loads(run([command[0], "-I", "-B", "-c", CONTROL,
                                                 str(runtime), "shutdown"], timeout=15))
                        if result != {"shutdown": True}:
                            raise RuntimeError("Owned resident did not accept canonical shutdown")
                        resident.wait(timeout=30)
                    finally:
                        resident.close()
    return {"actual_installed_manager_reader": True, "cycles": cycles,
            "resident_cleanup_confirmed": True, "full_interactive_picker": False}
