"""Opt-in POSIX enclosing-owner delegation with pinned descendant exits."""

import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import uuid

import psutil
import pytest


class ExitPin:
    def __init__(self, pid, group):
        birth = psutil.Process(pid).create_time()
        assert os.getpgid(pid) == group
        self.pid, self.exited = pid, False
        self.fd = self.queue = None
        if sys.platform == "darwin":
            self.queue = select.kqueue()
            self.queue.control([select.kevent(
                pid, filter=select.KQ_FILTER_PROC, flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE,
                fflags=select.KQ_NOTE_EXIT,
            )], 0, 0)
        else:
            from agent_procutil._linux_pidfd import open_pidfd
            self.fd = open_pidfd(pid)
        assert psutil.Process(pid).create_time() == birth
        assert os.getpgid(pid) == group

    def gone(self):
        if self.queue is not None:
            for event in self.queue.control([], 1, 0):
                assert event.ident == self.pid and not event.flags & select.KQ_EV_ERROR
                self.exited |= bool(event.fflags & select.KQ_NOTE_EXIT)
            return self.exited
        poll = select.poll()
        poll.register(self.fd, select.POLLIN)
        return bool(poll.poll(0))

    def close(self):
        if self.queue is not None:
            self.queue.close()
        if self.fd is not None:
            os.close(self.fd)


@pytest.mark.integration
@pytest.mark.skipif(sys.platform not in ("linux", "darwin"), reason="native POSIX enclosing group")
@pytest.mark.parametrize("outcome", ["timeout", "caller-exit"])
def test_borrowed_observations_keep_one_deadline_and_enclosing_descendant_custody(tmp_path, outcome):
    source = Path(__file__).resolve().parents[1] / "src"
    lookup = tmp_path / "lookup.py"
    lookup.write_text("""
import json,os,subprocess,sys,time
from pathlib import Path
child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'])
marker=Path(sys.argv[1])
prepared=marker.with_suffix('.prepared')
prepared.write_text(json.dumps({'pid':os.getpid(),'child':child.pid,'group':os.getpgrp(),
                               'nonce':os.environ['BUDGET_NATIVE_NONCE']}))
prepared.replace(marker)
marker.with_suffix('.published').write_text('complete')
time.sleep(60)
""")
    worker = tmp_path / "worker.py"
    worker.write_text("""
import json,os,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from agent_procutil.owned_process import run_caller_owned
from agent_procutil.owned_budget import OwnedExecutionBudget
budget=OwnedExecutionBudget(float(os.environ['BUDGET_NATIVE_DEADLINE']),2)
root=Path(sys.argv[2])
for index in range(2):
    with (root/'calls.jsonl').open('a') as stream:
        stream.write(json.dumps({'deadline':budget.deadline,'time':time.monotonic()})+'\\n')
    try:
        run_caller_owned([sys.executable,'-I',sys.argv[3],str(root/f'lookup-{index}.json')],
                         timeout=1.5 if index==0 else 10, max_stdout_bytes=4096,
                         max_stderr_bytes=4096, budget=budget, borrow_parent_scope=True)
    except __import__('subprocess').TimeoutExpired:
        pass
""")
    caller = tmp_path / "caller.py"
    caller.write_text("""
import json,os,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from agent_procutil.owned_process import run_caller_owned
from agent_procutil.owned_budget import OwnedExecutionBudget
root=Path(sys.argv[2]);budget=OwnedExecutionBudget.from_timeout(6,cleanup_reserve=2)
env=dict(os.environ);env['BUDGET_NATIVE_DEADLINE']=str(budget.deadline)
settled=[]
try:
    run_caller_owned([sys.executable,'-I',sys.argv[3],sys.argv[1],str(root),sys.argv[4]],
                     timeout=10,max_stdout_bytes=4096,max_stderr_bytes=4096,
                     env=env,budget=budget,on_settled=lambda:settled.append(True))
except subprocess.TimeoutExpired:
    pass
(root/'result.json').write_text(json.dumps({'deadline':budget.deadline,
    'returned':time.monotonic(),'settled':settled}))
""")
    for cycle in range(2):
        root = tmp_path / str(cycle)
        root.mkdir()
        nonce = uuid.uuid4().hex
        env = {**os.environ, "BUDGET_NATIVE_NONCE": nonce}
        process = subprocess.Popen(
            [sys.executable, "-I", str(caller), str(source), str(root), str(worker), str(lookup)],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        pins = []
        try:
            deadline = time.monotonic() + 5
            groups = []
            for index in range(2):
                marker = root / f"lookup-{index}.json"
                while not marker.with_suffix(".published").is_file():
                    assert process.poll() is None, process.communicate(timeout=2)
                    assert time.monotonic() < deadline, "native delegated observation never published"
                    time.sleep(.01)
                record = json.loads(marker.read_text())
                assert record["nonce"] == nonce
                groups.append(record["group"])
                pins.extend(ExitPin(record[key], record["group"]) for key in ("pid", "child"))
            assert len(set(groups)) == 1
            calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
            assert len(calls) == 2 and calls[0]["deadline"] == calls[1]["deadline"]
            if outcome == "caller-exit":
                process.kill()
            stdout, stderr = process.communicate(timeout=10)
            if outcome == "timeout":
                assert process.returncode == 0, (stdout, stderr)
                result = json.loads((root / "result.json").read_text())
                assert result["settled"] == [True]
                assert result["returned"] < result["deadline"]
            exit_deadline = time.monotonic() + 3
            while not all(pin.gone() for pin in pins):
                assert time.monotonic() < exit_deadline, "enclosing owner left a delegated descendant"
                time.sleep(.01)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
            for pin in pins:
                pin.close()
