"""Real CLI dispatch and registered provider/process boundaries in private state."""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as main, claim_providers, output
from agent_worktrees import resource_producer as producer, resource_producer_cli as family
from agent_worktrees import resource_relationship as relationship, resource_relationship_cli as cli
from agent_worktrees import locks, status_monitor_runtime, tracking, tracking_write
from test_resource_producer import owner as owner
from test_resource_relationship import call, reserve


def test_actual_lazy_cli_capabilities_and_registered_reservation(owner, monkeypatch):
    path, _ = owner
    monkeypatch.setattr(main, "_ensure_cluster_loaded", lambda: pytest.fail("relationship loaded full CLI cluster"))
    with output.capture_json_output() as captured:
        assert main.main(["resource-relationship", "capabilities", "--json"]) == 0
    capabilities = json.loads(captured.getvalue())
    assert capabilities["schema"] == relationship.SCHEMA
    assert capabilities["metadata_version"] == 2 and capabilities["handoff"] is False
    assert capabilities["inspect_absence"] is True
    assert capabilities["expected_creation_nonce"] is True
    assert capabilities["resident_inspect"] == 1
    assert capabilities["writer_scope"] == "executing-process"
    assert capabilities["writer_contract"] == relationship.writer_contract()
    payload = {"kind": "bridge", "namespace": "bridge-remote", "request_id": "cli-one", "recovery": {}}
    monkeypatch.setattr(family.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode())))
    with output.capture_json_output() as captured:
        assert main.main(["resource-relationship", "reserve", "--owner-ref", "m/p/owner#caller", "--json"]) == 0
    result = json.loads(captured.getvalue())
    assert result["owner"] == "m/p/owner"
    assert tracking.load_record(path).resources[0].producer == result["producer"]


def test_capabilities_bind_executing_build_not_mutable_payload(owner, monkeypatch, tmp_path):
    from agent_worktrees import _build_info, runtime_identity

    root = tmp_path / "selected-installation"
    monkeypatch.setattr(cli.cfg, "install_dir", lambda: root)
    monkeypatch.setattr(runtime_identity, "PROCESS_GENERATION", "1" * 32)
    monkeypatch.setitem(_build_info.BUILD_INFO, "runtime_generation", "2" * 32)
    with output.capture_json_output() as captured:
        assert main.main(["resource-relationship", "capabilities", "--json"]) == 0
    result = json.loads(captured.getvalue())
    assert result["writer_identity"] == {
        "installation_root": str(root), "runtime_generation": "1" * 32,
    }
    monkeypatch.setattr(runtime_identity, "PROCESS_GENERATION", None)
    with output.capture_json_output() as captured:
        assert main.main(["resource-relationship", "capabilities", "--json"]) == 0
    assert json.loads(captured.getvalue())["writer_identity"]["runtime_generation"] is None


@pytest.mark.parametrize("raw", [
    b"[]", b"{} {}", b'{"request_id":"one","request_id":"two"}',
    b'{"value":"\xff"}', b"x" * (producer.MAX_INPUT_BYTES + 1),
])
def test_invalid_ingress_never_resolves_authority(monkeypatch, raw):
    monkeypatch.setattr(family.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
    monkeypatch.setattr(cli.cfg, "load_project_config", lambda *a, **k: pytest.fail("invalid input reached config"))
    with output.capture_json_output() as captured:
        assert cli.cmd_resource_relationship(argparse.Namespace(operation="reserve", owner_ref="m/p/owner")) == 1
    assert json.loads(captured.getvalue())["error"] == "rejected"


def test_actual_provider_callback_proves_local_retirement_without_peer_absence(owner, monkeypatch, tmp_path):
    path, _ = owner
    pending = reserve(path)
    callback = tmp_path / "relationship_provider.py"
    callback.write_text(
        "import json,sys\n"
        "assert sys.argv[1] == 'claim-status'\n"
        "print(json.dumps({'exists': True, 'relationship_verification': {\n"
        " 'version': 1, 'subject': 'bridge-remote:' + sys.argv[2],\n"
        " 'retired': True, 'quiescent': True}}))\n",
        encoding="utf-8",
    )
    provider = claim_providers.ClaimProviderManifest(
        namespace="bridge-remote", plugin="fixture-provider@test", plugin_root=str(tmp_path),
        status_command=(sys.executable, str(callback)),
    )
    monkeypatch.setattr(claim_providers.peer_launch_adapter, "explicit_context", lambda: False)
    monkeypatch.setattr(claim_providers, "discover_claim_providers", lambda *a: ({"bridge-remote": provider}, ()))
    result = call(path, "close", {"token": pending["token"]})
    assert result.get("ok"), result
    assert result["phase"] == "closed"
    assert result["producer"]["closeout"]["exists"] is True
    assert not tracking.load_record(path).resources[0].is_unsettled


def test_actual_interpreter_cli_reserve_replay_inspect_and_bind(owner):
    path, config = owner
    code = """
import sys
from pathlib import Path
from agent_worktrees import __main__ as main, config as cfg
path, root, *argv = sys.argv[1:]
config = cfg.Config(srcroot=root, machine="m", platform="linux", repo_name="p",
 repos={"p": cfg.RepoConfig(anchor=root, worktree_root=str(Path(root) / "trees"))})
cfg.detect_platform = lambda: "linux"
cfg.load_project_config = lambda *a, **k: config
cfg.load_config = lambda *a, **k: config
cfg.project_dir = lambda *a, **k: Path(path).parent.parent
sys.exit(main.main(argv))
"""

    def execute(operation, payload):
        result = subprocess.run(
            [sys.executable, "-c", code, str(path), config.srcroot,
             "resource-relationship", operation, "--owner-ref", "m/p/owner", "--json"],
            input=json.dumps(payload), capture_output=True, text=True,
            encoding="utf-8", timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        return json.loads(result.stdout)

    payload = {"kind": "bridge", "namespace": "bridge-remote", "request_id": "child-one", "recovery": {}}
    pending = execute("reserve", payload)
    before = path.read_bytes()
    assert execute("reserve", payload) == pending and path.read_bytes() == before
    assert execute("inspect", {"request_id": "child-one"}) == pending
    assert execute("inspect", {"request_id": "never-reserved", "allow_missing": True}) == {
        "schema": relationship.SCHEMA, "version": 1, "ok": True,
        "owner": "m/p/owner", "request_id": "never-reserved", "absent": True,
    }
    bound = execute("bind", {"token": pending["token"], "identity": {"native_id": "conversation"}})
    assert bound["phase"] == "bound"
    assert tracking.load_record(path).resources[0].producer == bound["producer"]
    nonce = tracking.load_record(path).creation_nonce
    inspected = execute("inspect", {"token": pending["token"], "expected_creation_nonce": nonce})
    assert inspected["creation_nonce"] == nonce
    assert inspected["writer_contract"] == relationship.writer_contract()


def resident_inspect(monkeypatch, payload, *extra):
    monkeypatch.setattr(family.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode())))
    with output.capture_json_output() as captured:
        code = main.main([
            "resource-relationship", "inspect", "--owner-ref", "m/p/owner",
            "--resident-only", "--json", *extra,
        ])
    return code, json.loads(captured.getvalue())


@pytest.fixture
def resident(owner, monkeypatch, tmp_path):
    lock = tmp_path / "resident.lock"
    monkeypatch.setattr(status_monitor_runtime, "_monitor_lock_path", lambda: lock)
    monkeypatch.setattr(status_monitor_runtime, "_ensure_status_monitor", lambda: pytest.fail("inspection bootstrapped"))
    monkeypatch.setattr(tracking_write, "run_direct", lambda *a, **k: pytest.fail("inspection substituted direct writer"))
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    data = {**tracking_write.rendezvous_fields(server), "runtime_generation": "fixture-runtime"}
    lock.write_text(json.dumps(data), encoding="utf-8")
    try:
        yield lock, data, server
    finally:
        server.close()


@pytest.mark.parametrize("existing", [False, True])
def test_resident_only_actual_transport_checks_nonce_without_record_changes(owner, resident, monkeypatch, existing):
    path, _ = owner
    pending = reserve(path) if existing else None
    payload = {"token": pending["token"]} if pending else {"request_id": "missing", "allow_missing": True}
    payload["expected_creation_nonce"] = tracking.load_record(path).creation_nonce
    before = path.read_bytes()
    code, result = resident_inspect(monkeypatch, payload)
    assert code == 0, result
    assert result["writer_contract"] == relationship.writer_contract()
    assert result["creation_nonce"] == payload["expected_creation_nonce"]
    assert result["resident"] == {
        "generation": resident[1]["tracking_write_generation"], "runtime_generation": "fixture-runtime",
    }
    assert path.read_bytes() == before
    code, result = resident_inspect(monkeypatch, {**payload, "expected_creation_nonce": "replaced-workspace"})
    assert code == 1 and "incarnation" in result["message"] and "resident" not in result
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["missing", "prior", "missing-generation", "missing-runtime"])
def test_resident_only_unknown_selection_refuses_before_transport(owner, resident, monkeypatch, change):
    path, _ = owner
    lock, data, _ = resident
    if change == "missing":
        data = {}
    elif change == "prior":
        data["tracking_write_verb_versions"] = {}
    else:
        data.pop("tracking_write_generation" if change == "missing-generation" else "runtime_generation")
    lock.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(tracking_write.wcs_client, "request", lambda *a, **k: pytest.fail("unknown resident dialed"))
    before = path.read_bytes()
    code, result = resident_inspect(monkeypatch, {
        "request_id": "missing", "allow_missing": True,
        "expected_creation_nonce": tracking.load_record(path).creation_nonce,
    })
    assert code == 1 and "resident" in result["message"]
    assert path.read_bytes() == before


def test_resident_only_unavailable_does_not_substitute_direct_writer(owner, resident, monkeypatch):
    path, _ = owner
    resident[2].close()
    before = path.read_bytes()
    code, result = resident_inspect(monkeypatch, {
        "request_id": "missing", "allow_missing": True,
        "expected_creation_nonce": tracking.load_record(path).creation_nonce,
    })
    assert code == 1 and "unavailable" in result["message"]
    assert path.read_bytes() == before


def test_resident_only_copied_discovery_requires_live_authentication(owner, resident, monkeypatch):
    path, _ = owner
    lock, data, _ = resident
    data["tracking_write_token"] = "stale-discovery-token"
    lock.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    code, result = resident_inspect(monkeypatch, {
        "request_id": "missing", "allow_missing": True,
        "expected_creation_nonce": tracking.load_record(path).creation_nonce,
    })
    assert code == 1 and "unavailable" in result["message"] and "resident" not in result
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["generation", "runtime", "endpoint", "token", "installation"])
def test_resident_only_changed_selection_invalidates_observation(owner, resident, monkeypatch, change):
    path, _ = owner
    lock, data, _ = resident
    original = locks.read_lock
    reads = 0

    def read_lock(selected):
        nonlocal reads
        reads += 1
        if change == "installation":
            other = lock.parent / "other.lock"
            other.write_text(json.dumps(data), encoding="utf-8")
            monkeypatch.setattr(status_monitor_runtime, "_monitor_lock_path", lambda: other)
        elif reads > 1:
            data[{"generation": "tracking_write_generation", "runtime": "runtime_generation",
                  "endpoint": "tracking_write_endpoint", "token": "tracking_write_token"}[change]] = (
                "127.0.0.1:1" if change == "endpoint" else "replacement"
            )
            lock.write_text(json.dumps(data), encoding="utf-8")
        return original(selected)

    monkeypatch.setattr(locks, "read_lock", read_lock)
    before = path.read_bytes()
    code, result = resident_inspect(monkeypatch, {
        "request_id": "missing", "allow_missing": True,
        "expected_creation_nonce": tracking.load_record(path).creation_nonce,
    })
    assert code == 1 and "changed" in result["message"] and "resident" not in result
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("writer_contract", None), ("writer_contract", {**relationship.writer_contract(), "creation_nonce": True}),
    ("creation_nonce", "other"), ("owner", "m/p/other"), ("request_id", "other"), ("ok", 1),
])
def test_resident_only_rejects_live_incompatible_response(owner, resident, monkeypatch, field, value):
    path, _ = owner
    request = tracking_write.wcs_client.request

    def incompatible(*args, **kwargs):
        return {**request(*args, **kwargs), field: value}

    monkeypatch.setattr(tracking_write.wcs_client, "request", incompatible)
    before = path.read_bytes()
    code, result = resident_inspect(monkeypatch, {
        "request_id": "missing", "allow_missing": True,
        "expected_creation_nonce": tracking.load_record(path).creation_nonce,
    })
    assert code == 1 and "executing writer" in result["message"]
    assert path.read_bytes() == before


def test_resident_selector_requires_inspect_and_explicit_nonce(owner, resident, monkeypatch):
    code, result = resident_inspect(monkeypatch, {"request_id": "missing", "allow_missing": True})
    assert code == 1 and "requires expected_creation_nonce" in result["message"]
    with output.capture_json_output() as captured:
        assert main.main(["resource-relationship", "reserve", "--resident-only", "--json"]) == 1
    assert "only for inspect" in json.loads(captured.getvalue())["message"]
    with pytest.raises(SystemExit):
        main.main(["resource-relationship", "inspect", "--resident-onyl", "--json"])
