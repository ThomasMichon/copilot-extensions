"""Opt-in POSIX enclosing-owner delegation with pinned descendant exits."""

import json
import os
from pathlib import Path
import select
import shutil
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
def test_normal_legacy_completion_stops_its_native_caller_watch(monkeypatch, capsys):
    import importlib.util
    from types import SimpleNamespace
    from agent_procutil import owned_process

    scripts = Path(__file__).resolve().parents[3] / "plugins" / "agent-worktrees" / "scripts"
    loaded = {}
    for name in ("hook_policy_process", "hook_process_identity"):
        spec = importlib.util.spec_from_file_location(name, scripts / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded[name + ".py"] = module
    monkeypatch.setitem(sys.modules, "hook_client", SimpleNamespace(
        _load_sibling=lambda name: loaded[name],
    ))
    policy = loaded["hook_policy_process.py"]
    watchers = []
    original_watch = policy._watch_legacy_parent
    def watch(stop):
        thread = original_watch(stop)
        watchers.append(thread)
        return thread
    def settled(argv, **kwargs):
        kwargs["on_settled"]()
        return subprocess.CompletedProcess(argv, 0, "{}", "")
    monkeypatch.setattr(policy, "_watch_legacy_parent", watch)
    monkeypatch.setattr(owned_process, "require_caller_owned_support", lambda: None)
    monkeypatch.setattr(owned_process, "run_caller_owned", settled)
    assert policy.execute(b"{}", legacy=True) == 0
    assert len(watchers) == 1 and not watchers[0].is_alive()
    assert capsys.readouterr().out == ""


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
root=Path(sys.argv[2]);budget=OwnedExecutionBudget.from_timeout(8,cleanup_reserve=2)
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


@pytest.mark.integration
@pytest.mark.skipif(sys.platform not in ("linux", "darwin"), reason="native POSIX guardian")
def test_slow_guardian_settlement_publishes_pending_output_before_parent_cutoff(monkeypatch):
    from agent_procutil import _caller_owned as protocol
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from agent_procutil.owned_process import run_caller_owned

    monkeypatch.setattr(protocol, "_BOOTSTRAP",
        "import sys,time;sys.path.insert(0,sys.argv.pop(1));"
        "from agent_procutil import _caller_owned as p;"
        "exec('original=p._run_owned\\ndef slow(*a,**k):\\n"
        " try:return original(*a,**k)\\n finally:time.sleep(.75)\\np._run_owned=slow');"
        "p._main()")
    budget = OwnedExecutionBudget.from_timeout(4, cleanup_reserve=2)
    settled = []
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run_caller_owned(
            [sys.executable, "-I", "-c", "import time;print('pending',flush=True);time.sleep(60)"],
            timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
            budget=budget, on_settled=lambda: settled.append(True),
        )
    assert settled == [True]
    assert caught.value.output == b"pending\n"
    assert time.monotonic() < budget.deadline


@pytest.mark.integration
@pytest.mark.skipif(sys.platform not in ("linux", "darwin"), reason="native POSIX guardian")
def test_short_individual_timeout_does_not_subtract_aggregate_cleanup_allowance():
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from agent_procutil.owned_process import run_caller_owned

    budget = OwnedExecutionBudget.from_timeout(10, cleanup_reserve=2)
    result = run_caller_owned(
        [sys.executable, "-I", "-c", "print('executed')"],
        timeout=1, max_stdout_bytes=4096, max_stderr_bytes=4096, budget=budget,
    )
    assert result.stdout.strip() == "executed"
    assert time.monotonic() < budget.deadline


@pytest.mark.integration
@pytest.mark.skipif(sys.platform not in ("linux", "darwin"), reason="native POSIX guardian")
def test_short_aggregate_budget_does_not_charge_the_cleanup_reserve_twice():
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from agent_procutil.owned_process import run_caller_owned

    budget = OwnedExecutionBudget.from_timeout(3, cleanup_reserve=2)
    result = run_caller_owned(
        [sys.executable, "-I", "-c", "print('executed')"],
        timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096, budget=budget,
    )
    assert result.stdout.strip() == "executed"
    assert time.monotonic() < budget.deadline


@pytest.mark.integration
@pytest.mark.skipif(sys.platform not in ("linux", "darwin"), reason="native POSIX legacy caller")
@pytest.mark.parametrize("parent_watch", ["absent", "enabled"])
def test_legacy_wrapper_exit_retires_unconfirmed_supervisor_without_settlement(tmp_path, parent_watch):
    source = Path(__file__).resolve().parents[1] / "src"
    policy_source = Path(__file__).resolve().parents[3] / "plugins/agent-worktrees/scripts"
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("hook_policy_process.py", "hook_process_identity.py"):
        shutil.copyfile(policy_source / name, scripts / name)
    (scripts / "hook_client.py").write_text("""
import importlib.util,json,os,subprocess,sys,time
from pathlib import Path
def _load_sibling(name):
    spec=importlib.util.spec_from_file_location('fixture_identity',Path(__file__).with_name(name))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
if __name__=='__main__':
    child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'],
                           stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    marker=Path(os.environ['LEGACY_NATIVE_ROOT'])/'lookup.json'
    marker.with_suffix('.prepared').write_text(json.dumps({
        'pid':os.getpid(),'child':child.pid,'group':os.getpgrp(),
        'nonce':os.environ['LEGACY_NATIVE_NONCE']}))
    marker.with_suffix('.prepared').replace(marker)
    marker.with_suffix('.published').touch()
    time.sleep(60)
""")
    supervisor = tmp_path / "supervisor.py"
    supervisor.write_text("""
import json,os,sys,time
from pathlib import Path
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from agent_procutil import _caller_owned,owned_process
import hook_policy_process as policy
root=Path(os.environ['LEGACY_NATIVE_ROOT'])
if os.environ['LEGACY_NATIVE_WATCH']=='absent':
    from types import SimpleNamespace
    policy._watch_legacy_parent=lambda stop:SimpleNamespace(join=lambda **kwargs:None)
real_run=owned_process.run_caller_owned
def refuse_confirmation(process,**kwargs):
    marker=root/'pending.json'
    marker.with_suffix('.prepared').write_text(json.dumps({
        'pid':os.getpid(),'group':os.getpgrp(),'caller':os.getppid(),
        'nonce':os.environ['LEGACY_NATIVE_NONCE']}))
    marker.with_suffix('.prepared').replace(marker)
    marker.with_suffix('.published').touch()
    raise owned_process.OwnedCleanupError(process)
_caller_owned._confirm_group=refuse_confirmation
def record_settlement(*args,**kwargs):
    original=kwargs['on_settled']
    def settled():
        (root/'settled').touch();original()
    kwargs['on_settled']=settled
    return real_run(*args,**kwargs)
owned_process.run_caller_owned=record_settlement
policy.execute(b'{}',legacy=True,settlement_deadline=time.monotonic()+2,
               initial_decision={'permissionDecision':'ask','permissionDecisionReason':'confirmed fixture'})
raise AssertionError('unconfirmed cleanup must not return a decision')
""")
    nonce = uuid.uuid4().hex
    environment = {
        **os.environ, "LEGACY_NATIVE_ROOT": str(tmp_path), "LEGACY_NATIVE_NONCE": nonce,
        "LEGACY_NATIVE_WATCH": parent_watch,
    }
    process = subprocess.Popen(
        ["/bin/bash", "-c", '"$1" -I "$2" "$3" "$4"; status=$?; exit "$status"',
         "fixture-wrapper", sys.executable, str(supervisor), str(source), str(scripts)],
        env=environment, start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    wrapper_pin = ExitPin(process.pid, process.pid)
    pins = [wrapper_pin]
    try:
        deadline = time.monotonic() + 8
        records = {}
        for name in ("lookup", "pending"):
            marker = tmp_path / (name + ".json")
            while not marker.with_suffix(".published").is_file():
                assert process.poll() is None, process.communicate(timeout=2)
                assert time.monotonic() < deadline, "legacy custody witness never published"
                time.sleep(.01)
            record = records[name] = json.loads(marker.read_text())
            assert record["nonce"] == nonce
            pins.append(ExitPin(record["pid"], record["group"]))
            if name == "lookup":
                pins.append(ExitPin(record["child"], record["group"]))
        assert records["pending"]["caller"] == process.pid
        assert not pins[-1].gone(), "unconfirmed supervisor already exited"
        assert not (tmp_path / "settled").exists()
        # Keep the exact wrapper unreaped until its owned group cleanup is complete.
        process.kill()
        exit_deadline = time.monotonic() + 3
        while not wrapper_pin.gone() or (parent_watch == "enabled" and not all(pin.gone() for pin in pins)):
            assert time.monotonic() < exit_deadline, "legacy supervisor survived its Bash caller"
            time.sleep(.01)
        if parent_watch == "absent":
            time.sleep(.1)
            assert not pins[-1].gone(), "unwatched control did not retain the supervisor"
        assert not (tmp_path / "settled").exists()
    finally:
        try:
            from agent_procutil.owned_process import _retire_posix_group

            _retire_posix_group(process)
        finally:
            try:
                process.communicate(timeout=5)
            finally:
                for pin in pins:
                    pin.close()
