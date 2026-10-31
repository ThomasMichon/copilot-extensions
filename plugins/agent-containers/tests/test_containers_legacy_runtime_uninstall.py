"""Containers exercise real native removal and live broker refusal."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import time
import venv
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
path = PLUGIN.parent / "agent-machines/tests/test_legacy_runtime_uninstall.py"
spec = importlib.util.spec_from_file_location("containers_legacy_native_cases", path)
cases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cases)
cases.PLUGIN = PLUGIN

test_native_absent_uninstall_creates_nothing = cases.test_native_absent_uninstall_creates_nothing
test_native_uninstall_rejects_unsupported_options_without_mutation = (
    cases.test_native_uninstall_rejects_unsupported_options_without_mutation
)
test_native_uninstall_helper_rejects_trailing_options = (
    cases.test_native_uninstall_helper_rejects_trailing_options
)
test_native_refusal_precedes_staging_and_lock = cases.test_native_refusal_precedes_staging_and_lock
test_native_idle_cleanup_preserves_durable_bytes_and_is_idempotent = (
    cases.test_native_idle_cleanup_preserves_durable_bytes_and_is_idempotent
)
test_native_finite_assets_never_fall_back_to_purge = (
    cases.test_native_finite_assets_never_fall_back_to_purge
)
test_real_held_custody_refuses_concurrent_uninstall = (
    cases.test_real_held_custody_refuses_concurrent_uninstall
)
test_native_linked_root_refuses_without_following_target = (
    cases.test_native_linked_root_refuses_without_following_target
)
test_native_unknown_runtime_bin_is_preserved = cases.test_native_unknown_runtime_bin_is_preserved
test_posix_publishers_refuse_real_held_transaction = (
    cases.test_posix_publishers_refuse_real_held_transaction
)
test_native_adapter_uses_one_canonical_finite_owner = (
    cases.test_native_adapter_uses_one_canonical_finite_owner
)


@pytest.mark.parametrize("host", cases.HOSTS)
@pytest.mark.parametrize("override", [False, True])
def test_native_reconnect_closure_refusal_retains_all_transport_bytes(
    tmp_path: Path, host: str, override: bool,
) -> None:
    env = cases._environment(tmp_path / "home")
    root = tmp_path / "runtime"
    cases._fixture(root)
    home = tmp_path / "override" if override else root
    (home / "ssh").mkdir(parents=True)
    if override:
        env["AGENT_CONTAINERS_HOME"] = str(home)
    env["AGENT_CONTAINERS_STATE_DIR"] = str(tmp_path / "shared-state")
    records = {
        "id_ed25519": b"fixture-private-key",
        "known_hosts": b"fixture host",
        "container.config": b"Host fixture\n    HostName 127.0.0.1\n    Port 43210\n",
        "container.proxy.json": json.dumps({"pid": os.getpid(), "port": 43210}).encode(),
        "container.prepare.lock": b"preparing",
    }
    for name, content in records.items():
        (home / "ssh" / name).write_bytes(content)
    result = cases._run(host, root, env)
    assert result.returncode != 0
    assert "SSH active/reconnect dependency closure" in result.stdout + result.stderr
    assert {name: (home / "ssh" / name).read_bytes() for name in records} == records
    assert (root / "versions/1.2.3/runtime.txt").read_bytes() == b"runtime"
    assert not (root / ".install.lock").exists()


@dataclass
class RootedBroker:
    env: dict[str, str]
    root: Path
    process: subprocess.Popen[str]
    record: dict[str, Any]
    closure: dict[Path, bytes]

    def responsive(self) -> None:
        address = ("127.0.0.1", self.record["control_port"])
        with socket.create_connection(address, timeout=2) as peer:
            peer.sendall(b"ping\n")
            assert peer.recv(16) == b"pong\n"

    def assert_retained(self) -> None:
        assert {path: path.read_bytes() for path in self.closure} == self.closure
        assert self.process.poll() is None
        self.responsive()


@pytest.fixture
def rooted_broker(tmp_path: Path) -> Iterator[RootedBroker]:
    env = cases._environment(tmp_path / "home")
    root = tmp_path / "runtime"
    cases._fixture(root)
    env["COPILOT_EXTENSIONS_DAEMON_REGISTRY"] = str(root / "daemon-registry")
    snapshot = cases._snapshot(root)
    source = snapshot / "src"
    shutil.copytree(
        PLUGIN / "src/agent_containers", source / "agent_containers",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    slot = root / "versions/1.2.3"
    # A real runtime-rooted interpreter without pip or package installation.
    venv.EnvBuilder(with_pip=False).create(slot)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    endpoint = root / "ssh/fixture.proxy.json"
    script = """
import json, os, pathlib, socket, sys, threading
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from agent_containers import docker_proxy as proxy
stopping = threading.Event()
original_accept = socket.socket.accept
def accept(listener, *args, **kwargs):
    if stopping.is_set():
        raise KeyboardInterrupt("owned fixture shutdown")
    return original_accept(listener, *args, **kwargs)
socket.socket.accept = accept
def stop_on_input():
    os.read(0, 1)
    stopping.set()
threading.Thread(target=stop_on_input, daemon=True).start()
print(json.dumps({"pid": os.getpid(), "executable": sys.executable}), flush=True)
try:
    proxy.serve("synthetic", "fixture-id", pathlib.Path(sys.argv[3]))
except KeyboardInterrupt:
    pass
"""
    process = subprocess.Popen(
        [
            str(python), "-I", "-B", "-c", script, str(source),
            str(cases.ROOT / "libs/agent-procutil/src"), str(endpoint),
        ],
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + 12
        while not endpoint.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert endpoint.exists(), "Runtime-rooted broker did not publish its endpoint"
        record = json.loads(endpoint.read_text(encoding="utf-8"))
        # Windows venv redirectors launch the actual broker in a base child.
        # Attribute its PID through this owned script's pipe, not the launcher.
        handshake = json.loads(process.stdout.readline())
        assert record["pid"] == handshake["pid"]
        assert Path(handshake["executable"]) == python
        assert Path(record["runtime"]).is_relative_to(root)
        config = endpoint.with_name("fixture.config")
        config.write_text(
            f"Host fixture\n    HostName 127.0.0.1\n    Port {record['port']}\n",
            encoding="utf-8",
        )
        closure = {
            path: path.read_bytes() for path in (
                python, Path(record["runtime"]), endpoint, config,
            )
        }

        fixture = RootedBroker(env, root, process, record, closure)
        fixture.responsive()
        yield fixture
    finally:
        _stdout, stderr = process.communicate("\n", timeout=15)
        assert process.returncode == 0, stderr


def test_native_uninstall_refuses_actual_live_loopback_broker(rooted_broker: RootedBroker) -> None:
    for host in cases.HOSTS:
        rooted_broker.responsive()
        result = cases._run(host, rooted_broker.root, rooted_broker.env)
        assert result.returncode != 0
        assert "SSH active/reconnect dependency closure" in result.stdout + result.stderr
        assert "responsive-broker-endpoint" in result.stdout + result.stderr
        rooted_broker.assert_retained()


def test_transport_guard_reports_actual_runtime_rooted_broker_without_stopping_it(
    rooted_broker: RootedBroker, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_CONTAINERS_HOME", str(rooted_broker.root))
    guard = cases._component("legacy_runtime_uninstall")
    with pytest.raises(RuntimeError, match="responsive-broker-endpoint"):
        guard.check_under_custody(rooted_broker.root, PLUGIN, "agent-containers")
    rooted_broker.assert_retained()


def test_transport_guard_distinguishes_stale_artifacts_from_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    cases._fixture(root)
    ssh = root / "ssh"
    ssh.mkdir()
    record = ssh / "fixture.proxy.json"
    record.write_text(json.dumps({
        "schema_version": 1, "pid": 2147483647, "control_port": 1,
        "runtime": str(PLUGIN / "src/agent_containers/docker_proxy.py"),
    }), encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINERS_HOME", str(root))
    guard = cases._component("legacy_runtime_uninstall")
    before = record.read_bytes()
    with pytest.raises(RuntimeError, match="stale-proxy-artifacts"):
        guard.check_under_custody(root, PLUGIN, "agent-containers")
    assert record.read_bytes() == before
    assert not (root / ".install.lock").exists()
    assert not (root / ".construction-leases").exists()
