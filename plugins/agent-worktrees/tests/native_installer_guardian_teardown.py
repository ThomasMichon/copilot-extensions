"""Inject a native teardown fault inside the process that owns the Job."""

from __future__ import annotations

from pathlib import Path
import textwrap


def write_procutil(
    destination: Path, *, procutil: Path, runtime: Path, root: Path,
    frozen: Path, marker: Path, observations: Path, writer: str,
) -> None:
    configuration = {
        "procutil": str(procutil), "runtime": str(runtime), "root": str(root),
        "frozen": str(frozen), "marker": str(marker), "observations": str(observations),
        "writer": writer,
    }
    program = r'''
import importlib.util,json,sys,time
from pathlib import Path

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module

procutil=load("fault_procutil",CONFIG["procutil"])
runtime=load("fault_runtime",CONFIG["runtime"])
root=Path(CONFIG["root"])
report=Path(CONFIG["observations"])
marker=Path(CONFIG["marker"])
SyncProcessLaunchError=procutil.SyncProcessLaunchError
no_window_kwargs=procutil.no_window_kwargs
original=procutil.JobHandle.terminate_and_wait

def terminate_and_wait(job,timeout):
    entries=json.loads(report.read_text()) if report.exists() else []
    assert (Path(CONFIG["frozen"])/"pyproject.toml").is_file()
    try:
        with runtime.construction_lease(root,"1.0.0",timeout=0):
            raise AssertionError("unsettled child lost construction custody")
    except runtime.ConstructionTimeout:
        pass
    try:
        with runtime._kernel_lease(root,"1.0.0",kind="retention",shared=False,timeout=0):
            raise AssertionError("unsettled child lost its retention lease")
    except runtime.RetentionTimeout:
        pass
    with runtime._cleanup_lease(root,"1.0.0",allow_construction_owner=True) as admitted:
        assert not admitted,"unsettled child lost cleanup-blocking retention"
    assert not runtime.marker_path(root,"1.0.0").exists()
    deadline=time.monotonic()+10
    while not marker.is_file() or not marker.read_text():
        assert time.monotonic()<deadline,"writer tree did not start"
        time.sleep(0.02)
    pids=[int(value) for value in marker.read_text().split()]
    entry={"frozen":True,"construction_blocked":True,"retention_blocked":True,"cleanup_blocked":True,
           "complete":False,"pids":pids}
    if not entries:
        assert all(runtime._pid_alive(pid) for pid in pids)
        entry["writers_live"]=True
        entries.append(entry)
        report.write_text(json.dumps(entries))
        raise OSError("Job accounting temporarily unavailable")
    original(job,timeout)
    assert job.handle is None
    assert all(not runtime._pid_alive(pid) for pid in pids)
    entry["writers_settled"]=True
    entries.append(entry)
    report.write_text(json.dumps(entries))

procutil.JobHandle.terminate_and_wait=terminate_and_wait

def spawn_sync_in_kill_on_close_job(argv,**kwargs):
    return procutil.spawn_sync_in_kill_on_close_job(
        [sys.executable,"-I","-c",CONFIG["writer"]],**kwargs,
    )
'''
    destination.write_text(
        "CONFIG = " + repr(configuration) + "\n" + textwrap.dedent(program),
        encoding="utf-8",
    )
