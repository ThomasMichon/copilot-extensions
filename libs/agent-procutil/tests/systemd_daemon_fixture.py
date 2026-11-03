"""Finite native lifetime proof; never touches an existing service or daemon."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agent_procutil as pu
from agent_procutil._caller_owned import _pidfd_exited
from agent_procutil._linux_pidfd import open_pidfd
from agent_procutil.daemon_instances import (
    DaemonInstanceSpec, DaemonLeasePolicy, daemon_entrypoint, daemon_test_lifecycle,
)


@daemon_entrypoint("systemd-resident-fixture", source=__file__, purpose="unattended-test")
def resident(root: Path) -> None:
    root.joinpath("resident.json").write_text(json.dumps({
        "pid": os.getpid(), "cgroup": Path("/proc/self/cgroup").read_text(),
        "literal": os.environ.get("FIXTURE_LITERAL"),
    }))
    for tick in range(100):
        root.joinpath("tick").write_text(str(tick))
        time.sleep(.1)


def parent(root: Path) -> None:
    control = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(20)"])
    process = pu.spawn_daemon(
        [sys.executable, str(Path(__file__).resolve()), "resident", str(root)],
        cwd=root, log_path=root / "resident.log",
        env_overrides={"FIXTURE_LITERAL": "$DO_NOT_EXPAND"},
        instance=DaemonInstanceSpec(
            "systemd-resident-fixture", root, Path(__file__).resolve(),
            purpose="unattended-test", lease=DaemonLeasePolicy(3, 30),
        ),
    )
    root.joinpath("parent.json").write_text(json.dumps({
        "control": control.pid, "launched": process.pid,
        "cgroup": Path("/proc/self/cgroup").read_text(),
    }))
    time.sleep(20)


def _read_ready(root: Path) -> tuple[dict, dict]:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            return (
                json.loads(root.joinpath("parent.json").read_text()),
                json.loads(root.joinpath("resident.json").read_text()),
            )
        except (FileNotFoundError, ValueError):
            time.sleep(.05)
    raise RuntimeError("native scope fixture did not become ready")


def qualify(root: Path) -> dict:
    if pu.contained_test_mode():
        raise RuntimeError("native independent-lifetime proof cannot escape contained test mode")
    root.mkdir()
    unit = "agent-daemon-parent-fixture-" + uuid.uuid4().hex + ".service"
    descriptors = []
    cleanup = []
    stopped = False
    with daemon_test_lifecycle(
        owner="systemd-lifetime-fixture", registry_root=root / "instances",
        lease=DaemonLeasePolicy(3, 30), cleanup_results=cleanup,
    ) as environment:
        try:
            subprocess.run([
                "systemd-run", "--user", "--quiet", "--collect", "--unit=" + unit,
                "--property=Type=exec", "--property=RuntimeMaxSec=20",
                "--property=TimeoutStopSec=3", "--property=MemoryMax=128M",
                "--property=TasksMax=32", "--expand-environment=no",
                *["--setenv=" + key + "=" + value for key, value in environment.items()],
                "--", sys.executable, str(Path(__file__).resolve()), "parent", str(root),
            ], check=True, timeout=5)
            launched, actual = _read_ready(root)
            descriptors = [open_pidfd(launched["control"]), open_pidfd(actual["pid"])]
            assert unit in launched["cgroup"]
            assert unit not in actual["cgroup"]
            assert "agent-daemon-" in actual["cgroup"] and ".scope" in actual["cgroup"]
            assert actual["literal"] == "$DO_NOT_EXPAND"
            subprocess.run(["systemctl", "--user", "stop", unit], check=True, timeout=5)
            stopped = True
            assert _pidfd_exited(descriptors[0]), "bounded control survived caller retirement"
            assert not _pidfd_exited(descriptors[1]), "resident died with the caller cgroup"
            previous = root.joinpath("tick").read_text()
            deadline = time.monotonic() + 2
            while root.joinpath("tick").read_text() == previous and time.monotonic() < deadline:
                time.sleep(.05)
            assert root.joinpath("tick").read_text() != previous
        finally:
            if not stopped:
                subprocess.run(["systemctl", "--user", "stop", unit], timeout=5, check=False)
            for descriptor in descriptors:
                os.close(descriptor)
    assert cleanup and all(result.status == "settled" for result in cleanup), cleanup
    return {"caller_retired": True, "bounded_retired": True, "resident_survived": True,
            "literal_preserved": True, "cleanup_settled": True}


if __name__ == "__main__":
    mode, location = sys.argv[1:]
    if mode == "parent":
        parent(Path(location))
    elif mode == "resident":
        resident(Path(location))
    elif mode == "qualify":
        print(json.dumps(qualify(Path(location))))
    else:
        raise ValueError("unknown fixture mode")
