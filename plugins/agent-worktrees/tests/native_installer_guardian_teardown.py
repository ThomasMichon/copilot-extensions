"""Inject a native teardown fault inside the process that owns the Job."""

from __future__ import annotations

from pathlib import Path
import textwrap


def fault_source(
    *, root: Path,
    frozen: Path, marker: Path, observations: Path, writer: str,
) -> bytes:
    configuration = {
        "root": str(root),
        "frozen": str(frozen), "marker": str(marker), "observations": str(observations),
        "writer": writer,
    }
    program = r'''
import json,sys,time
from pathlib import Path

runtime=sys.modules[__name__.removesuffix("_procutil")+"_runtime"]
root=Path(CONFIG["root"])
report=Path(CONFIG["observations"])
marker=Path(CONFIG["marker"])
original=JobHandle.terminate_and_wait
original_spawn=spawn_sync_in_kill_on_close_job

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

def spawn_sync_in_kill_on_close_job(argv,**kwargs):
    if list(argv[1:4]) != ["-I","-m","venv"]:
        return original_spawn(argv,**kwargs)
    process,job=original_spawn(
        [sys.executable,"-I","-c",CONFIG["writer"]],**kwargs)
    job.terminate_and_wait=lambda timeout: terminate_and_wait(job,timeout)
    return process,job
'''
    return ("\nCONFIG = " + repr(configuration) + "\n" + textwrap.dedent(program)).encode("utf-8")
