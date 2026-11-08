"""Canonical completion and native process evidence in disposable cells."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from agent_procutil import no_window_kwargs
from zdd.diagnostics import process_start_time

from agent_pull_requests import _installation_context as api
from agent_pull_requests.watch_installation import PLUGIN_ID, WatchInstallation
from agent_pull_requests.watch_runtime import WatchInstance, WatchRuntime
from test_watch_installation import _installation, _mutate, _policy


def _complete(installation, *, version="1.0.0", snapshot_files=None) -> WatchRuntime:
    common = {
        "context": installation.receipt,
        "expected_marketplace_id": installation.marketplace_id,
        "expected_plugin_id": PLUGIN_ID, "snapshot_id": version,
        "durable_home": installation.durable_home, "environment": {},
    }
    snapshot = installation.plugin_root / "snapshots" / version
    snapshot.mkdir(parents=True)
    content = b"test-owned immutable payload"
    (snapshot / "entry.py").write_bytes(content)
    for relative, data in (snapshot_files or {}).items():
        path = snapshot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    api.stamp_snapshot_provenance(
        **common, expected_namespace_generation=installation.namespace_generation,
        expected_install_generation=installation.install_generation,
    )
    slot = api.provision_runtime_slot(**common, runtime_version=version)
    runtime_root = Path(slot["slotRoot"])
    digest = hashlib.sha256(
        b"F\0entry.py\0" + hashlib.sha256(content).hexdigest().encode("ascii") + b"\n",
    ).hexdigest()
    if snapshot_files:
        digest = api._snapshot_content_sha256(snapshot)
    (runtime_root / ".install-complete.json").write_text(json.dumps({
        "version": version, "completed_at": "2026-01-01T00:00:00Z",
        "pid": os.getpid(), "payload_hash": digest,
    }), encoding="utf-8")
    payload = json.loads(installation.receipt.read_text(encoding="utf-8"))["payload"]
    api.complete_runtime_slot(
        **common, runtime_version=version,
        expected_payload_root=payload["root"], expected_payload_version=payload["version"],
    )
    return WatchRuntime.capture(
        installation=installation, snapshot_id=version, runtime_version=version,
        runtime_root=runtime_root, payload_root=Path(payload["root"]),
        payload_version=payload["version"], environment={},
    )


@pytest.fixture
def completed(tmp_path):
    installation = _installation(tmp_path)
    return installation, _complete(installation)


def _instance(runtime, *, pid=None):
    pid = os.getpid() if pid is None else pid
    token = process_start_time(pid)
    if token is None:
        pytest.skip("native process birth evidence requires Windows or Linux")
    return WatchInstance(runtime, pid, token, str(uuid4()))


def test_completed_runtime_is_read_only_and_not_current_selection(completed):
    installation, runtime = completed
    runtime.require_current(installation, environment={})
    assert not installation.state_root.exists()
    assert not installation.run_root.exists()
    assert not (installation.plugin_root / "current.version").exists()


def test_uncompleted_slot_refuses(completed):
    installation, runtime = completed
    completion = api.validate_runtime_slot_completion(
        context=installation.receipt,
        expected_marketplace_id=installation.marketplace_id,
        expected_plugin_id=PLUGIN_ID, snapshot_id=runtime.snapshot_id,
        runtime_version=runtime.runtime_version, expected_payload_root=runtime.payload_root,
        expected_payload_version=runtime.payload_version,
        durable_home=installation.durable_home, environment={},
    )
    Path(completion["completion"]).unlink()
    with pytest.raises(api.InstallationContextError):
        runtime.require_current(installation, environment={})


@pytest.mark.parametrize("field", ["runtime_root", "payload_root"])
def test_runtime_relative_roots_refuse(completed, field):
    installation, runtime = completed
    args = {
        key: getattr(runtime, key) for key in (
            "snapshot_id", "runtime_version", "runtime_root", "payload_root", "payload_version",
        )
    }
    args[field] = Path("relative")
    with pytest.raises(ValueError, match="absolute"):
        WatchRuntime.capture(installation=installation, **args, environment={})


def test_wrong_completed_slot_root_refuses(completed):
    installation, runtime = completed
    with pytest.raises(PermissionError, match="completed slot"):
        WatchRuntime.capture(
            installation=installation, snapshot_id=runtime.snapshot_id,
            runtime_version=runtime.runtime_version,
            runtime_root=runtime.runtime_root.parent / "foreign",
            payload_root=runtime.payload_root, payload_version=runtime.payload_version,
            environment={},
        )


def test_same_plugin_foreign_cell_refuses(completed, tmp_path):
    installation, runtime = completed
    other = _installation(tmp_path, source="two")
    with pytest.raises(PermissionError, match="another installation"):
        runtime.require_current(other, environment={})
    assert not installation.state_root.exists() and not other.state_root.exists()


def test_valid_but_rewritten_completion_refuses_pinned_runtime(completed):
    installation, runtime = completed
    build = runtime.runtime_root / ".install-complete.json"
    completion = runtime.runtime_root / api.RUNTIME_SLOT_COMPLETION_FILE
    assert completion.exists()
    _mutate(build, lambda value: value.update(completed_at="2026-02-01T00:00:00Z"))
    completion.unlink()
    api.complete_runtime_slot(
        context=installation.receipt,
        expected_marketplace_id=installation.marketplace_id,
        expected_plugin_id=PLUGIN_ID, snapshot_id=runtime.snapshot_id,
        runtime_version=runtime.runtime_version, expected_payload_root=runtime.payload_root,
        expected_payload_version=runtime.payload_version,
        durable_home=installation.durable_home, environment={},
    )
    with pytest.raises(PermissionError, match="completion evidence changed"):
        runtime.require_current(installation, environment={})


def _advance_installation(old_installation, tmp_path):
    new_payload = tmp_path / "payload-new"
    new_payload.mkdir()
    advanced = api.stamp_context(
        payload_root=new_payload, plugin_id=PLUGIN_ID,
        durable_home=old_installation.durable_home,
        payload_version="2.0.0", payload_origin="explicit",
        source_descriptor={"source": "github", "repo": "example/one"},
        marketplace_key="one", expected_namespace_generation=1,
        expected_install_generation=1, environment={},
    )
    api.compare_and_swap_activation(
        context=old_installation.receipt, durable_home=old_installation.durable_home,
        expected_marketplace_id=old_installation.marketplace_id,
        expected_plugin_id=PLUGIN_ID,
        expected_namespace_generation=advanced["namespaceGeneration"],
        expected_install_generation=advanced["generation"],
        expected_activation_generation=1, activation_mode="namespaced",
        activation_state="active", legacy_disposition="absent",
        legacy_probe={
            "declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z",
        },
        environment={}, os_profile=old_installation.os_profile,
    )
    fresh = WatchInstallation.capture(
        receipt=old_installation.receipt, plugin_root=old_installation.plugin_root,
        payload_root=new_payload, durable_home=old_installation.durable_home,
        os_profile=old_installation.os_profile, environment={},
    )
    assert advanced["generation"] > old_installation.install_generation
    return fresh


def test_fresh_binding_accepts_historical_owned_incumbent(completed, tmp_path):
    old_installation, runtime = completed
    fresh = _advance_installation(old_installation, tmp_path)
    with pytest.raises((PermissionError, api.InstallationContextError)):
        runtime.require_current(old_installation, environment={})
    runtime.require_current(fresh, environment={})
    assert runtime.payload_root != fresh.payload_root


@pytest.mark.parametrize("field", [
    "marketplaceId", "pluginId", "pluginRoot", "installReceipt", "snapshotId",
    "runtimeVersion", "runtimeRoot", "payloadRoot", "payloadVersion",
    "completionFingerprint", "pid", "startTime", "instanceId",
])
def test_matching_native_birth_is_not_foreign_owner_authority(completed, field):
    installation, runtime = completed
    instance = _instance(runtime)
    evidence = instance.evidence()
    evidence[field] = "foreign"
    with pytest.raises(PermissionError, match="expected ownership"):
        instance.capture_exit(
            installation, read_health=lambda: {"instance": evidence}, environment={},
        )


def test_stale_process_token_refuses_before_control_health(completed):
    installation, runtime = completed
    instance = replace(_instance(runtime), start_time="0")
    with pytest.raises(ProcessLookupError, match="identity"):
        instance.capture_exit(
            installation, read_health=lambda: pytest.fail("must not contact stale target"),
            environment={},
        )

@pytest.mark.parametrize("overrides", [
    {"pid": True}, {"pid": 0}, {"start_time": ""}, {"start_time": "not-native"},
    {"instance_id": ""}, {"instance_id": "00000000-0000-0000-0000-000000000000"},
])
def test_instance_identity_rejects_invalid_or_nonrandom_values(completed, overrides):
    _, runtime = completed
    with pytest.raises(ValueError):
        replace(_instance(runtime), **overrides)


@pytest.mark.parametrize("change", ["owner", "runtime"])
def test_changed_ownership_after_reference_capture_closes_reference(completed, monkeypatch, change):
    from agent_pull_requests import watch_runtime

    installation, runtime = completed
    instance = _instance(runtime)
    opened = []
    native_open = watch_runtime.process_exit.open_process

    def capture(*args):
        reference = native_open(*args)
        opened.append(reference)
        if change == "runtime":
            (runtime.runtime_root / api.RUNTIME_SLOT_COMPLETION_FILE).unlink()
        return reference

    monkeypatch.setattr(watch_runtime.process_exit, "open_process", capture)
    reads = []

    def health():
        reads.append(True)
        evidence = instance.evidence()
        if change == "owner":
            evidence["instanceId"] = str(uuid4())
        return {"instance": evidence}

    error = PermissionError if change == "owner" else api.InstallationContextError
    with pytest.raises(error):
        instance.capture_exit(installation, read_health=health, environment={})
    assert len(opened) == 1
    assert len(reads) == (1 if change == "owner" else 0)
    with pytest.raises(RuntimeError, match="closed"):
        opened[0].wait(0)


def test_reference_capture_brackets_one_actual_health_exchange(completed, monkeypatch):
    from agent_pull_requests import watch_runtime

    installation, runtime = completed
    instance = _instance(runtime)
    seen = []
    native_open = watch_runtime.process_exit.open_process
    validate = WatchRuntime.require_identity

    def capture(*args):
        seen.append("open")
        return native_open(*args)

    def identity(self, *args, **kwargs):
        seen.append("runtime")
        return validate(self, *args, **kwargs)

    def health():
        seen.append("health")
        return {"instance": instance.evidence()}

    monkeypatch.setattr(watch_runtime.process_exit, "open_process", capture)
    monkeypatch.setattr(WatchRuntime, "require_identity", identity)
    with instance.capture_exit(installation, read_health=health, environment={}) as reference:
        assert not reference.wait(0)
    assert seen == ["open", "runtime", "health", "runtime"]


def test_governance_change_during_health_refuses(completed):
    installation, runtime = completed
    instance = _instance(runtime)

    def health():
        _policy(installation.os_profile, False)
        return {"instance": instance.evidence()}

    with pytest.raises(PermissionError, match="governance"):
        instance.capture_exit(installation, read_health=health, environment={})


def test_real_owned_graceful_exit_captured_before_control(completed):
    installation, runtime = completed
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; print('ready', flush=True); sys.stdin.readline()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **no_window_kwargs(),
    )
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "ready"
        instance = _instance(runtime, pid=child.pid)
        with instance.capture_exit(
            installation, read_health=lambda: {"instance": instance.evidence()}, environment={},
        ) as reference:
            assert not reference.wait(0)
            assert child.stdin is not None
            child.stdin.write("graceful\n")
            child.stdin.flush()
            assert reference.wait(5)
            assert child.wait(timeout=5) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()
