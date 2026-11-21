"""Private loopback trial; Dispatch and Bridge run in separate installed environments."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch


def dispatch(root: Path, phase: str, lost: bool, peer: Path) -> None:
    from agent_dispatch import bridge
    from agent_dispatch import coordinator_execution_runtime as runtime
    from agent_dispatch import coordinator_execution_sender as sender
    from agent_dispatch.coordinator_execution_wakes import deliver_recorded_owner_wake
    from agent_dispatch.execution_scope_binding import resolve_execution_installation
    from agent_dispatch.loop_governance import LoopGovernance
    from agent_dispatch.ordinary_agent_storage import record_created_agent
    from agent_dispatch.queue import TaskQueue
    from agent_dispatch.queue_common import Task
    from agent_dispatch.queue_execution_runtimes import CoordinatorExecutionRuntime
    from agent_dispatch.queue_records import TaskError
    from agent_dispatch.wake_recovery_records import WakeRecoveryRecords

    assert Path(str(bridge.__file__)).resolve().is_relative_to(Path(sys.prefix).resolve())
    assert not any(n.startswith("agent_bridge") for n in sys.modules)
    owner, sid, native = "host-a/worktree-a", "bridge-a", "native-a"
    with ExitStack() as stack:
        stack.enter_context(patch.object(bridge.remote_dispatch, "local_machine", return_value="host-a"))
        stack.enter_context(patch.object(bridge, "_agent_bridge_launch_prefix", return_value=[str(peer), "-I", "-B", "-m", "agent_bridge"]))
        if phase == "send":
            queue = TaskQueue(root / "outbox" / "tasks.db")
            task = queue.create("fixture", repo="queue-project", source="wake-test")
            assert isinstance(task, Task) and task.repo is not None and task.source is not None
            res, _ = queue.reserve_spawn(task.id, reserved_by="supervisor", driver="ordinary-bridge")
            queue.record_spawn_allocation(res.key, "execution-project")
            record_created_agent(queue, res.key, task_generation=task.generation, agent_info={
                "session_id":sid, "ready":True, "status":"awaiting_first_prompt",
                "connection":{"native_session_id":native,"worktree_id":"worktree-a","repo":"execution-project",
                              "target_dir":str(root/"worktree-a")}})
            queue.record_spawn(res.key, session_handle=f"local-body:{sid}", startup_complete=True,
                               startup_revision=res.startup_revision)
            queue.claim_one(owner, task_id=task.id)
            queue.start(task.id, owner, owner_session_id=native)
            queue.submit_steer(task.id, fields={"decision":"accept"}, idempotency_key="reply",
                               wake_requested=True, wake_message="original")
            queue.handoff_producer_scope(task.repo, task.source, producer_id="fixture", expected_generation=0)
            baseline = dict(schema="copilot-extensions.installation-loop-baseline", version=1,
                            context=str(root/"fixture.json"), marketplaceId="example",pluginId="agent-dispatch",
                            actualMode="namespaced", namespaceGeneration=1, installGeneration=1, activationGeneration=1, legacy={})
            gov = LoopGovernance(helper=lambda _: {"status":"ready","baseline":baseline})
            queue.bind_execution_scope(resolve_execution_installation(gov),repo=task.repo,source=task.source,
                                       expected_producer_generation=1)
            stack.enter_context(patch.object(sender, "LoopGovernance", return_value=gov))
            stack.enter_context(patch.object(runtime, "_own_process", return_value=CoordinatorExecutionRuntime("app", 1234, "fixture")))
            wake = queue.claim_due_wake()
            assert wake is not None
            store = WakeRecoveryRecords.create(root/"dispatch-records")
            ack = deliver_recorded_owner_wake(queue,wake,"app",store)
            assert ack == (not lost)
            receipt = queue.steer_receipt(task.id, "reply")
            assert receipt is not None
            (root/"expected.json").write_text(json.dumps([task.id,wake.id,receipt["acceptance"]]))
        else:
            assert not (root/"outbox").exists()
            tid, wid, accepted = json.loads((root/"expected.json").read_bytes())
            store = WakeRecoveryRecords(root/"dispatch-records"/"wake.records")
            try:
                store.observe_original(tid,wid,expected_target=(sid,native,"host-a"))
            except TaskError:
                if phase != "absent" or not lost:
                    raise
            state = store.inspect(tid,wid)
            assert (state["task_id"],state["owner"],state["owner_session_id"],state["wake_id"]) == (tid,owner,native,wid)
            assert state["steer_acceptances"] == [accepted]
            if phase == "absent" and lost:
                assert state["outcome"] == "unknown" and state["receipt"] is None
            else:
                assert state["outcome"] == "sent" and state["receipt"]["invocation_key"] == wid


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--scratch-parent", required=True, type=Path)
    cli.add_argument("interpreters", nargs=4, type=Path, help="Dispatch A, Bridge A, Dispatch B, Bridge B")
    opts = cli.parse_args()
    root = Path(tempfile.mkdtemp(dir=opts.scratch_parent, prefix="ipc-drill-")).absolute()
    da, ba, db, bb = [p.absolute() for p in opts.interpreters]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    def prefix(python: Path) -> Path:
        value = subprocess.check_output([str(python), "-I", "-B", "-c", "import sys; print(sys.prefix)"],
                                        timeout=15, text=True, creationflags=flags)
        return Path(value.strip()).resolve(strict=True)
    if prefix(da) == prefix(db) or prefix(ba) == prefix(bb):
        raise ValueError("distinct generations required")
    server_script = Path(__file__).resolve().parents[1] / "plugins" / "agent-bridge" / "tests" / "ipc_trial_server.py"
    for lost in (False, True):
        lane = root / ("ambiguous" if lost else "confirmed")
        lane.mkdir()
        for folder in ("origin","outbox",".agent-bridge"):
            (lane/folder).mkdir()
        token = secrets.token_hex(32)
        (lane/"auth-key").write_text(token)
        (lane/".agent-bridge"/"auth.yaml").write_text("token: "+token)
        env = {k:os.environ[k] for k in ("SystemRoot","WINDIR","LANG") if k in os.environ}
        env.update(HOME=str(lane),USERPROFILE=str(lane),APPDATA=str(lane),LOCALAPPDATA=str(lane),
                   TEMP=str(lane),TMP=str(lane),PATH="",AGENT_BRIDGE_NO_ENSURE="1",NO_PROXY="*")
        for phase, d, b in (("send",da,ba),("absent",db,bb),("original",da,ba)):
            if phase == "absent":
                (lane/"origin").rename(lane/"withheld")
                (lane/"outbox").rename(lane/"withheld-outbox")
            if phase == "original":
                (lane/"withheld").rename(lane/"origin")
            args = [str(lane),phase,str(int(lost))]
            with (lane/(phase+".log")).open("wb") as stream:
                proc = subprocess.Popen([str(b),"-I","-B",str(server_script),*args],executable=str(b),
                                        cwd=lane,env=env,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,creationflags=flags)
                try:
                    deadline = time.monotonic()+45
                    while not (lane/("endpoint-"+phase)).is_file():
                        if proc.poll() is not None or time.monotonic()>deadline:
                            raise RuntimeError(f"fixture startup failed; see {lane}")
                        time.sleep(.05)
                    port = int((lane/("endpoint-"+phase)).read_text())
                    (lane/".agent-bridge"/"config.yaml").write_text(f"bind: 127.0.0.1\nport: {port}\n")
                    while True:
                        try:
                            with socket.create_connection(("127.0.0.1",port),timeout=.5):
                                break
                        except OSError:
                            if time.monotonic()>deadline:
                                raise
                            time.sleep(.05)
                    result = subprocess.run([str(d),"-I","-B",__file__,"dispatch",*args,str(b)],executable=str(d),
                                   cwd=lane,env={**env,"AGENT_BRIDGE_BASE_URL":f"http://127.0.0.1:{port}"},
                                   capture_output=True,text=True,timeout=90,creationflags=flags)
                    if result.returncode:
                        raise RuntimeError(result.stderr.replace(token, "[redacted]")[-3000:])
                finally:
                    if proc.poll() is None:
                        (lane / ("stop-" + phase)).touch()
                    try:
                        proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=15)
                        raise
                assert (lane / ("closed-" + phase)).is_file()
        assert (lane/"prompt-count").read_text().splitlines() == ["1"] and not (lane/"forbidden-send").exists()
        print(f"{lane.name}: passed", flush=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["dispatch"]:
        dispatch(Path(sys.argv[2]),sys.argv[3],bool(int(sys.argv[4])),Path(sys.argv[5]))
    else:
        main()
