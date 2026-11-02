"""Real Linux runner-loss and private FIFO custody; no Docker socket or backend."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROOF = ROOT / "tools" / "clean-room" / "gateway-role-tunnels"
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux FIFO/process custody")


@pytest.fixture
def fixture_owner(monkeypatch):
    monkeypatch.syspath_prepend(str(PROOF))
    return importlib.import_module("fixture_service")


@pytest.mark.parametrize("kind", ["missing", "regular", "link", "orphan", "public"])
def test_missing_or_unsafe_runner_refuses_before_service_work(fixture_owner, tmp_path, kind):
    control = tmp_path / "control"
    if kind == "regular":
        control.write_text("not a lifetime channel")
    elif kind == "link":
        control.symlink_to(tmp_path / "absent")
    elif kind in ("orphan", "public"):
        os.mkfifo(control, mode=0o600)
        if kind == "public":
            control.chmod(0o666)
    state = tmp_path / "must-not-exist"
    with pytest.raises((ValueError, OSError)):
        fixture_owner._supervise(
            [sys.executable, "not-launched"], Path(__file__), root=state, control=control,
        )
    assert not state.exists()


def test_engine_closes_exact_lifetime_before_removing_channel(fixture_owner, tmp_path):
    engine_module = importlib.import_module("engine")
    (tmp_path / "proof").mkdir()
    engine = engine_module.Engine(tmp_path, {})
    engine.open_runner_lifetime()
    path, writer, _, _ = engine.runner_lifetime
    assert not os.get_inheritable(writer)
    reader = fixture_owner._open_runner(path)
    try:
        assert fixture_owner._runner_alive(reader)
        assert engine.cleanup() == []
        assert not fixture_owner._runner_alive(reader)
        assert not path.exists()
        assert engine.cleanup() == []
    finally:
        os.close(reader)


def _wait_json(path: Path, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            time.sleep(0.05)
    raise AssertionError("bounded fixture readiness was not observed")


def test_runner_loss_stops_owner_renewal_and_the_actual_service(tmp_path):
    from agent_procutil import no_window_kwargs
    from agent_procutil._daemon_instance_identity import identity_state
    from agent_procutil.daemon_instances import inspect_instances

    root = tmp_path / "run"
    root.mkdir()
    child = tmp_path / "service.py"
    child.write_text(
        "import json,os,sys,time\n"
        "from pathlib import Path\n"
        "from agent_procutil.daemon_instances import admit_daemon_instance,inspect_instances\n"
        "root=Path(sys.argv[1])\n"
        "with admit_daemon_instance('gateway-role-fixture',"
        "runtime_root=root/'gateway-role-runtime',source=Path(__file__)) as guard:\n"
        " row=next(r for r in inspect_instances(guard.receipt.registry_root)"
        " if r['instance_id']==guard.receipt.instance_id)\n"
        " (root/'started.json').write_text(json.dumps(row))\n"
        " while True: time.sleep(.1)\n"
    )
    writer_code = (
        "import json,os,sys,time\nfrom pathlib import Path\n"
        "sys.path.insert(0,sys.argv[1])\nfrom engine import Engine\n"
        "root=Path(sys.argv[2]);(root/'proof').mkdir()\n"
        "owner=Engine(root,{});owner.open_runner_lifetime()\n"
        "(root/'writer.json').write_text(json.dumps({'pid':os.getpid(),"
        "'inheritable':os.get_inheritable(owner.runner_lifetime[1])}))\n"
        "time.sleep(60)\n"
    )
    supervisor_code = (
        "import sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\n"
        "from fixture_service import _supervise\n"
        "from agent_procutil.daemon_instances import DaemonLeasePolicy\n"
        "root=Path(sys.argv[2]);child=Path(sys.argv[3])\n"
        "raise SystemExit(_supervise([sys.executable,str(child),str(root/'state')],child,"
        "root=root/'state',control=root/'proof/runner.pipe',"
        "lease=DaemonLeasePolicy(2,20)))\n"
    )
    writer = subprocess.Popen(
        [sys.executable, "-c", writer_code, str(PROOF), str(root)], **no_window_kwargs(),
    )
    supervisor = None
    try:
        owner = _wait_json(root / "writer.json")
        assert not owner["inheritable"]
        supervisor = subprocess.Popen(
            [sys.executable, "-c", supervisor_code, str(PROOF), str(root), str(child)],
            **no_window_kwargs(),
        )
        first = _wait_json(root / "state" / "started.json")
        assert first["purpose"] == "unattended-test"
        assert int(first["owner_pid"]) == supervisor.pid
        assert int(first["pid"]) not in (supervisor.pid, writer.pid)
        time.sleep(4.2)
        current = next(
            row for row in inspect_instances(root / "state" / ".gateway-role-instance-registry")
            if row["instance_id"] == first["instance_id"]
        )
        assert float(current["deadline"]) > float(first["deadline"]) + 2
        writer.kill()
        writer.wait(timeout=5)
        assert supervisor.wait(timeout=8) == 0
        assert identity_state(int(first["pid"]), first["birth"]) == "dead"
    finally:
        for process in (writer, supervisor):
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
