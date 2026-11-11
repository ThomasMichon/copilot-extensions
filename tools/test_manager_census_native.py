"""Short opt-in real CIM profiles; not whole-suite lifecycle acceptance."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
    pytest.mark.skipif(os.name != "nt", reason="real Windows CIM provider"),
]


_LOAD = r"""
import subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agent_procutil import no_window_kwargs
home, ready, stop = map(Path, sys.argv[2:5])
mode = sys.argv[5]
children = []
deadline = time.monotonic() + 15
try:
    while not ready.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('census did not become ready')
        time.sleep(.02)
    children.append(subprocess.Popen(
        [sys.executable, '-I', '-S', '-B', '-c', 'import time;time.sleep(12)',
         str(home / '.agent-census' / 'persistent')], **no_window_kwargs(),
    ))
    end = time.monotonic() + 6
    count = 0
    while time.monotonic() < end:
        if mode != 'quiet':
            program = (
                'import time;end=time.monotonic()+.15\n'
                'while time.monotonic()<end: sum(range(10000))'
            ) if mode == 'burst' else 'import time;time.sleep(.15)'
            for _ in range(8 if mode == 'burst' else 1):
                children.append(subprocess.Popen(
                    [sys.executable, '-I', '-S', '-B', '-c', program,
                     str(home / '.agent-census' / 'ephemeral')], **no_window_kwargs(),
                ))
                count += 1
            for child in children[1:]:
                child.wait(timeout=2)
            del children[1:]
        else:
            time.sleep(.05)
    print(count)
finally:
    stop.write_text('stop')
    for child in children:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=2)
"""


@pytest.mark.parametrize("mode", ("quiet", "churn", "burst"))
def test_real_manager_census_profile(tmp_path, record_property, monkeypatch, mode):
    monkeypatch.syspath_prepend(str(ROOT / "libs" / "agent-procutil" / "src"))
    from agent_procutil.owned_process import run_caller_owned

    script = runpy.run_path(str(
        ROOT / "worktree-manager" / "tests" / "native" / "manager_suite_artifacts.py"
    ))["_runtime_process_trace_script"]
    home = tmp_path / "h"
    home.mkdir()
    ready, stop, report = [tmp_path / name for name in ("ready", "stop", "report.json")]
    (tmp_path / "process-trace-window").write_text("profile")
    deadline = time.monotonic() + 23

    def load():
        return run_caller_owned(
            [sys.executable, "-I", "-B", "-c", _LOAD,
             str(ROOT / "libs" / "agent-procutil" / "src"),
             str(home), str(ready), str(stop), mode],
            timeout=max(.1, min(20, deadline - time.monotonic())),
            max_stdout_bytes=4096, max_stderr_bytes=4096,
            cwd=tmp_path, env=os.environ.copy(),
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        loading = pool.submit(load)
        try:
            result = run_caller_owned(
                [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command",
                 script(home, ready, stop, report)],
                timeout=max(.1, min(20, deadline - time.monotonic())),
                max_stdout_bytes=4096, max_stderr_bytes=4096,
                cwd=tmp_path, env=os.environ.copy(),
            )
        finally:
            stop.write_text("stop")
        load_result = loading.result()

    trace = json.loads(report.read_text("utf-8"))
    summary = {key: value for key, value in trace.items() if key != "records"}
    summary.update(
        mode=mode, observed_records=len(trace["records"]),
        load_children=int(load_result.stdout.strip()) if load_result.returncode == 0 else None,
    )
    record_property("census_profile", json.dumps(summary, sort_keys=True))
    assert load_result.returncode == 0, "caller-owned load failed"
    assert result.returncode == 0 and trace["scan_errors"] == 0, summary
    assert trace["stop_observed"] and trace["window_armed"]
    assert trace["cim_session_created"] and trace["cim_session_closed"]
    assert trace["timing"]["scan_successes"] >= 2, summary
    assert not trace["truncated"] and trace["records"], summary
    assert {row["component"] for row in trace["records"]} == {"agent-census"}
    if mode != "quiet":
        assert int(load_result.stdout.strip()) >= 5
        assert len(trace["records"]) >= 3, summary
