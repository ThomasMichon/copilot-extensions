"""Real lease-only owners are not fabricated into governed process custody."""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest
from agent_procutil import no_window_kwargs
from test_cell_payload_upgrade import _release, _run
from test_cell_payload_upgrade import native_profile as native_profile
from test_cell_upgrade import _pending
from test_watch_invocation import installed as installed
from work_coalescing_singleton.client import request
from zdd import diagnostics, process_exit

from agent_pull_requests import _installation_context as api
from agent_pull_requests.watch_daemon import endpoint_from_rendezvous
from agent_pull_requests.watch_installation import WatchInstallation
from agent_pull_requests.watch_journal import _terminal_custody
from agent_pull_requests.watch_runtime import WatchRuntime
from agent_pull_requests.watch_storage import read_subscriptions_state

_ORIGINAL = """
import os, sys
from pathlib import Path
from agent_pull_requests import __main__ as cli
from agent_pull_requests.watch_daemon import WatchDaemon
from agent_pull_requests.watch_storage import _atomic_write_json
from zdd.diagnostics import process_start_time
_atomic_write_json(Path(sys.argv[2]), {
    "pid": os.getpid(), "startTime": process_start_time(os.getpid()),
})
original = WatchDaemon.compute
def observed(self, kind, payload):
    if kind == "shutdown":
        Path(sys.argv[1]).write_text("shutdown requested", encoding="utf-8")
    return original(self, kind, payload)
WatchDaemon.compute = observed
raise SystemExit(cli.main(["serve", "--poll-interval", "30"]))
"""

_ACTIVATE = """
import sys
from pathlib import Path
from agent_pull_requests import watch_entry, watch_install
def forbidden_spawn(*args, **kwargs):
    Path(sys.argv[1]).write_text("spawn requested", encoding="utf-8")
    raise AssertionError("unprovable lease holder must not spawn a successor")
watch_entry.spawn_daemon = forbidden_spawn
raise SystemExit(watch_install.main(sys.argv[2:]))
"""


def _health(endpoint):
    return request(
        *endpoint, kind="health", key="lease-refusal-health", payload={},
        request_deadline_s=3, completion_timeout_s=3,
    )


@pytest.mark.timeout(180)
@pytest.mark.skipif(os.name != "nt", reason="Native POSIX profile requires an isolated OS account")
@pytest.mark.parametrize("rendezvous", ["reachable", "unreachable"])
def test_actual_lease_only_owner_blocks_installer_without_control_or_duplicate(
    installed, tmp_path, rendezvous,
):
    _refuse_lease_owner(installed, tmp_path, rendezvous, substitute_profile=True)


def _completed_normal_slot(root, profile, *, substitute_profile=False):
    payload, _ = _release(root, profile=profile)
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"COPILOT_EXTENSIONS_CONTEXT", "PYTHONPATH", "PYTHONHOME"}
    }
    if substitute_profile:
        environment.update(HOME=str(profile), USERPROFILE=str(profile))
    result = _run(payload / "scripts" / "init.ps1", [], environment, root, timeout=300)
    assert result.returncode == 0, result.stderr
    receipt, = (profile / ".copilot-extensions" / "marketplaces").glob(
        "*/plugins/agent-pull-requests/install.json",
    )
    installation = WatchInstallation.capture(
        receipt=receipt, plugin_root=receipt.parent, payload_root=payload,
        durable_home=profile / ".copilot-extensions", os_profile=profile, environment={},
    )
    version = (receipt.parent / api.CURRENT_VERSION_FILE).read_text("utf-8").strip()
    slot = receipt.parent / "versions" / version
    ownership = api.read_json(slot / api.RUNTIME_SLOT_OWNERSHIP_FILE)
    runtime = WatchRuntime.capture(
        installation=installation, snapshot_id=ownership["snapshot"]["id"],
        runtime_version=version, runtime_root=slot, payload_root=payload,
        payload_version=json.loads((payload / "plugin.json").read_text("utf-8"))["version"],
        environment={},
    )
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return installation, runtime, python, None


@pytest.mark.portfolio_tier("T3")
@pytest.mark.timeout(600)
@pytest.mark.parametrize("rendezvous", [
    pytest.param("reachable", id="endpoint_live"),
    pytest.param("unreachable", id="endpoint_missing"),
])
def test_native_account_lease_only_owner_refuses_without_control(
    tmp_path_factory, native_profile, rendezvous,
):
    if native_profile is None:
        pytest.skip("fresh disposable hosted OS account was not explicitly selected")
    root = tmp_path_factory.mktemp("l")
    slot = _completed_normal_slot(root, native_profile)
    _refuse_lease_owner(slot, root, rendezvous, substitute_profile=False)


@pytest.mark.portfolio_tier("T3")
@pytest.mark.timeout(600)
@pytest.mark.skipif(
    os.name != "nt", reason="private profile uses the native Windows profile contract",
)
@pytest.mark.parametrize("rendezvous", ["reachable", "unreachable"])
def test_normal_payload_lease_only_owner_preserves_selected_metadata(tmp_path_factory, rendezvous):
    root = tmp_path_factory.mktemp("l")
    slot = _completed_normal_slot(root, root / "p", substitute_profile=True)
    _refuse_lease_owner(slot, root, rendezvous, substitute_profile=True)


def _refuse_lease_owner(installed, tmp_path, rendezvous, *, substitute_profile):
    installation, runtime, python, _ = installed
    protected_paths = [
        *(installation.plugin_root / name for name in (
            api.CURRENT_VERSION_FILE, api.LAST_KNOWN_GOOD_FILE, "deploy-manifest.json",
        )),
        *(installation.run_root / name for name in (
            "active.json",
        )),
    ]
    protected = {path: path.read_bytes() if path.exists() else None for path in protected_paths}
    journal_path = installation.run_root / "watch-owner-transaction.json"
    journal = json.loads(journal_path.read_text("utf-8")) if journal_path.exists() else None
    pending = _pending(installation.state_root, python)
    shutdown = tmp_path / "shutdown-requested"
    identity = tmp_path / "original-native-process.json"
    spawned = tmp_path / "spawn-requested"
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"COPILOT_EXTENSIONS_CONTEXT", "PYTHONPATH", "PYTHONHOME"}
    }
    environment["AGENT_PULL_REQUESTS_HOME"] = str(installation.state_root)
    if substitute_profile:
        environment["USERPROFILE"] = str(installation.os_profile)
    owner = subprocess.Popen(  # noqa: S603 -- test-owned interpreter and legacy entry
        [str(python), "-I", "-c", _ORIGINAL, str(shutdown), str(identity)], env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **no_window_kwargs(),
    )
    endpoint = None
    reference = None
    try:
        record = installation.state_root / "watch-daemon.lock"
        deadline = time.monotonic() + 30
        while endpoint is None:
            if owner.poll() is not None:
                pytest.fail(f"Original lease owner exited: {owner.communicate(timeout=5)}")
            if record.is_file():
                endpoint = endpoint_from_rendezvous(json.loads(record.read_text("utf-8")))
            if time.monotonic() >= deadline:
                raise TimeoutError("Original lease owner did not publish its legacy endpoint")
            if endpoint is None:
                time.sleep(0.05)
        # A Windows venv Popen PID may name its redirector, not the original
        # daemon. Capture the actual test-owned process before installer control.
        original = json.loads(identity.read_text("utf-8"))
        assert diagnostics.process_start_time(original["pid"]) == original["startTime"]
        reference = process_exit.open_process(original["pid"], original["startTime"])
        assert _health(endpoint)["pid"] == original["pid"]
        assert read_subscriptions_state(installation.state_root) == [pending]
        if rendezvous == "unreachable":
            document = json.loads(record.read_text("utf-8"))
            document["watch_endpoint"] = "127.0.0.1:1"
            record.write_text(json.dumps(document), encoding="utf-8")
        recorded = record.read_bytes()
        state = installation.state_root / "watch-subscriptions.json"
        baseline = state.read_bytes()
        # Execute the real installer activation in its actual completed target.
        # Observation forbids spawning; it does not substitute any owner evidence.
        result = subprocess.run(  # noqa: S603 -- test-owned completed activation target
            [
                str(python), "-I", "-c", _ACTIVATE, str(spawned),
                "--context", str(installation.receipt),
                "--payload-root", str(installation.payload_root),
                "--snapshot-id", runtime.snapshot_id,
                "--runtime-version", runtime.runtime_version,
            ],
            env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=60, check=False, **no_window_kwargs(),
        )
        assert result.returncode != 0
        assert "already holds" in result.stderr
        assert not shutdown.exists()
        assert not spawned.exists()
        assert not reference.wait(0)
        assert _health(endpoint)["pid"] == original["pid"]
        assert record.read_bytes() == recorded
        assert state.read_bytes() == baseline
        assert {
            path: path.read_bytes() if path.exists() else None for path in protected_paths
        } == protected
        if journal is None:
            assert not journal_path.exists()
        else:
            after = json.loads(journal_path.read_text("utf-8"))
            assert _terminal_custody(after) == _terminal_custody(journal)
    finally:
        try:
            if reference is not None:
                if not reference.wait(0):
                    if endpoint is None:
                        raise RuntimeError("Test-owned original lacks a graceful cleanup endpoint")
                    stopped = request(
                        *endpoint, kind="shutdown", key="test-owned-original-cleanup", payload={},
                        request_deadline_s=5, completion_timeout_s=5,
                    )
                    assert stopped["shutting_down"]
                assert reference.wait(30), "Test-owned original did not exit gracefully"
                owner.communicate(timeout=5)
        finally:
            if reference is not None:
                reference.close()
            for stream in (owner.stdout, owner.stderr):
                if stream is not None:
                    stream.close()
    assert shutdown.read_text("utf-8") == "shutdown requested"
