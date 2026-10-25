"""Native-process proof: repeated main-thread interrupts cannot retire live work."""

import json
import os
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs


_NATIVE = r'''
import _thread,json,sys,threading,time
from pathlib import Path
from types import SimpleNamespace
from agent_procutil import _daemon_instance_store as store
from single_instance_lease import SingleInstance,AlreadyRunningError
from agent_pull_requests import __main__ as cli,watch_daemon,watch_entry
from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_writer import WatchWriter
root=Path(sys.argv[1])
passive=sys.argv[2]=="passive"
delivery=sys.argv[3]=="delivery"
entered,release,timedout=threading.Event(),threading.Event(),threading.Event()
receipts,owners,calls=[],[],[]
def fetch(*args):
    if not delivery:
        entered.set()
        assert release.wait(20)
    return PRSnapshot(pr_state="closed",merged=True) if delivery else PRSnapshot(pr_state="open")
def notify(event):
    calls.append(event.subscriber.subscriber_id)
    entered.set()
    assert release.wait(20)
def activate(daemon):
    daemon.compute("register",dict(repo="sample/repo",number=1,subscriber_id="original",
                                  until=["merged"],notify={}))
    assert entered.wait(5)
def wait():
    assert entered.wait(5)
    return
def bound_close(daemon):
    original=daemon.close
    def close(*args,**kwargs):
        try:
            return original(timeout=0.03)
        except TimeoutError:
            timedout.set()
            raise
    daemon.close=close
if passive:
    writer=WatchWriter(root=root,authorize=lambda _:None,fetch=fetch,notify=notify)
    writer._lease.acquire()
    daemon=writer.daemon
    daemon.reload_state(timeout=1)
    daemon.resume_effects(timeout=1)
    activate(daemon)
    bound_close(daemon)
    owners.append(writer)
    owner=SimpleNamespace(writer=writer,compute=writer.compute,publish_launch=lambda _:None,
                          wait_for_shutdown=wait)
    watch_entry.WatchControlOwner=lambda *a,**kw:owner
else:
    original=watch_daemon.WatchDaemon
    def factory(**kwargs):
        daemon=original(fetch=fetch,notify=notify,poll_interval=0.01)
        owners.append(daemon)
        activate(daemon)
        bound_close(daemon)
        daemon.wait_for_shutdown=wait
        return daemon
    watch_daemon.WatchDaemon=factory
def controller():
    assert timedout.wait(10)
    receipt=store._admitted[1]
    receipts.append(receipt)
    contender=SingleInstance(root,service="agent-pull-requests-watch")
    try:
        contender.acquire()
    except AlreadyRunningError:
        held=True
    else:
        held=False
        contender.release()
    native=[t.name for t in threading.enumerate() if t.name.startswith(("pr-watch:","pr-notify:"))]
    print(json.dumps(dict(stage="held",native=native,lease=held,
                          state=store._read(receipt.record_path)["state"])),flush=True)
    for _ in range(3):
        _thread.interrupt_main()
        time.sleep(0.05)
    assert store._read(receipt.record_path)["state"]=="running"
    release.set()
controller_thread=threading.Thread(target=controller)
controller_thread.start()
error=None
try:
    args=SimpleNamespace(passive_instance="reserved" if passive else None,port=1 if passive else 0,
                         poll_interval=0.01)
    if passive:
        original_server=watch_entry.WatchControlServer
        def server(compute,**kwargs):
            kwargs["bind_port"]=0
            return original_server(compute,**kwargs)
        watch_entry.WatchControlServer=server
        watch_entry.serve_passive(SimpleNamespace(runtime=SimpleNamespace(runtime_root=root)),
                                 args,fetch=fetch)
    else:
        cli._cmd_serve(args)
except BaseException as exc:
    error=type(exc).__name__
controller_thread.join(5)
assert not controller_thread.is_alive()
assert receipts
contender=SingleInstance(root,service="agent-pull-requests-watch")
contender.acquire()
contender.release()
native=[t.name for t in threading.enumerate() if t.name.startswith(
    ("pr-watch:","pr-notify:","pr-watch-retirement","work-coalescing-singleton"))]
print(json.dumps(dict(stage="settled",state=store._read(receipts[0].record_path)["state"],
                      native=native,error=error,calls=calls,lease_released=True)),flush=True)
'''


@pytest.mark.parametrize("path", ["legacy", "passive"])
@pytest.mark.parametrize("worker", ["poller", "delivery"])
def test_native_workers_keep_admission_and_lease_through_repeated_interrupts(
    tmp_path, path, worker,
):
    env = dict(os.environ)
    env["AGENT_PULL_REQUESTS_HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-B", "-c", _NATIVE, str(tmp_path), path, worker],
        env=env, capture_output=True, text=True, timeout=35, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    held, settled = rows
    print(json.dumps({"path": path, "worker": worker, "held": held, "settled": settled}))
    assert held["native"] and held["lease"] and held["state"] == "running"
    assert settled["state"] == "finished" and settled["native"] == []
    assert settled["error"] == "TimeoutError"
    assert settled["lease_released"]
    assert settled["calls"] == (["original"] if worker == "delivery" else [])
