"""Opt-in real Docker proof of delayed-launch and helper fencing."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_flags

from agent_containers import dispatch_projection as projection
from agent_containers.config import FleetConfig, SECURITY_HOME_LABEL


pytestmark = pytest.mark.skipif(
    os.environ.get("CE_TEST_DISPATCH_DOCKER") != "1",
    reason="requires explicit isolated Docker acceptance invocation and cached Node image",
)


def docker(*arguments, check=True):
    return subprocess.run(
        ["docker", *arguments], check=check, capture_output=True, text=True,
        timeout=30, creationflags=no_window_flags(),
    )


@pytest.fixture
def guest():
    name = "dispatch-gate-proof-" + uuid.uuid4().hex[:12]
    physical = None
    try:
        physical = docker(
            "run", "--rm", "-d", "--name", name, "--pull", "never", "--network", "none",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "1000:1000", "--tmpfs", "/run:rw,mode=0755",
            "--tmpfs", "/tmp:rw,mode=1777", "--label", f"{SECURITY_HOME_LABEL}=/home/node",
            "node:22-bookworm-slim", "sleep", "180",
        ).stdout.strip()
        assert re.fullmatch("[0-9a-f]{64}", physical)
        inspected = json.loads(docker("inspect", physical).stdout)[0]
        assert inspected["HostConfig"]["ReadonlyRootfs"] is True
        assert inspected["HostConfig"]["NetworkMode"] == "none"
        yield physical, inspected
    finally:
        if physical is not None and re.fullmatch("[0-9a-f]{64}", physical):
            docker("rm", "--force", physical)
        else:
            found = docker("inspect", "--format", "{{.Id}}", name, check=False)
            candidate = found.stdout.strip()
            if found.returncode == 0 and re.fullmatch("[0-9a-f]{64}", candidate):
                docker("rm", "--force", candidate)


def read(physical, name):
    return docker(
        "exec", "-u", "0", physical, "/usr/local/bin/node", "-e",
        "const fs=require('fs');try{process.stdout.write(fs.readFileSync(process.argv[1],'utf8'))}"
        "catch(e){if(e.code!=='ENOENT')throw e}",
        name,
    ).stdout


def wait_file(physical, path):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = read(physical, path)
        if result:
            return result
        time.sleep(0.01)
    pytest.fail("guest did not reach its bounded gate proof boundary")


def test_delayed_guest_launch_and_old_helpers_cannot_cross_lifecycle_closure(guest, monkeypatch):
    physical, inspected = guest
    resolve = projection.resolve_executable
    executables = {}
    def immutable_executable(container, user, snapshot, *, kind, deadline):
        assert container == snapshot["Id"] == physical
        assert snapshot["State"]["StartedAt"] == inspected["State"]["StartedAt"]
        assert all(snapshot[key] == inspected[key] for key in ("Config", "HostConfig", "Mounts"))
        key = (user, kind)
        if key not in executables:
            executables[key] = resolve(
                container, user, snapshot, kind=kind, deadline=deadline,
            )
        return executables[key]
    # Discovery is unchanged and exercised once for each user/kind; the image
    # and physical instance stay immutable throughout this guest-gate proof.
    monkeypatch.setattr(projection, "resolve_executable", immutable_executable)
    first, reopened = (1, "a" * 32), (3, "b" * 32)
    assert projection._publish_worker_admission(physical, inspected, first) == 0
    target = SimpleNamespace(container_id=physical, user="1000", fleet=FleetConfig())
    binding = {
        "container_id": physical, "task_id": "task-1", "worker_id": "worker-1",
        "coordinator_url": "http://coordinator:8080",
    }
    argv = projection.build_spawn_command(
        target, binding, "printf executed > /tmp/executed", worker_admission=first,
    )
    delayed = list(argv)
    delayed[-1] = (
        "printf '%s' \"$$\" > /tmp/ready; "
        "for i in {1..3000}; do [[ -e /tmp/go ]] && break; sleep .01; done; "
        "( " + argv[-1] + " ); code=$?; printf '%s' \"$code\" > /tmp/result"
    )
    wrapper = subprocess.Popen(
        delayed, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=no_window_flags(),
    )
    try:
        pid = wait_file(physical, "/tmp/ready")
        wrapper.kill()
        wrapper.communicate(timeout=5)
        alive = docker(
            "exec", "-u", "0", physical, "/usr/local/bin/node", "-e",
            "const fs=require('fs');process.exit(fs.existsSync('/proc/'+process.argv[1])?0:1)",
            pid,
        )
        assert alive.returncode == 0
        assert projection.probe_worker_liveness(physical, inspected).state == "idle"
        assert projection._publish_worker_admission(physical, inspected, (2, "")) == 0
        assert json.loads(read(physical, projection.WORKER_ADMISSION)) == {
            "generation": 2, "nonce": "",
        }
        docker("exec", "-u", "0", physical, "/usr/local/bin/node", "-e",
               "require('fs').writeFileSync('/tmp/go','go')")
        assert wait_file(physical, "/tmp/result") == "75"
        assert read(physical, "/tmp/executed") == ""
        assert projection._publish_worker_admission(physical, inspected, reopened) == 0
        assert projection._publish_worker_admission(physical, inspected, first) != 0
        assert projection._publish_worker_admission(physical, inspected, (2, "")) != 0
        assert subprocess.run(
            argv, capture_output=True, text=True, timeout=20,
            creationflags=no_window_flags(),
        ).returncode == 75
        fresh = projection.build_spawn_command(
            target, binding, "printf fresh", worker_admission=reopened,
        )
        result = subprocess.run(
            fresh, capture_output=True, text=True, timeout=20,
            creationflags=no_window_flags(),
        )
        assert (result.returncode, result.stdout) == (0, "fresh"), result.stderr
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.communicate(timeout=5)
