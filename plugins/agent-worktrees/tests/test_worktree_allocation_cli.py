"""Actual lazy CLI and finite synthetic SSH -> receiving CLI process boundaries."""

from __future__ import annotations

import argparse
import io
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil.owned_process import run_caller_owned

from agent_worktrees import __main__ as main, output, resource_producer_cli as family
from agent_worktrees import owned_worktree as allocation, owned_worktree_remote as remote
from agent_worktrees import worktree_allocation_cli as cli
from agent_worktrees import allocation_creation_result
from test_receiving_allocation import create, receiver as receiver
from test_resource_producer import owner as owner
from test_owned_worktree_transactions import TARGET


@pytest.mark.parametrize("command,role", [
    ("worktree-allocation", "origin"), ("worktree-allocation-receive", "receiver"),
])
def test_actual_lazy_capability_dispatch_needs_no_full_cluster(monkeypatch, command, role):
    monkeypatch.setattr(main, "_ensure_cluster_loaded", lambda: pytest.fail("full CLI cluster loaded"))
    with output.capture_json_output() as captured:
        assert main.main([command, "capabilities", "--json"]) == 0
    result = json.loads(captured.getvalue())
    assert result["schema"] == allocation.SCHEMA and result["role"] == role
    assert result["metadata_version"] == 3 and result["claim_linkage"] == "non-blocking"
    assert result["agent_launch"] is False
    assert result["max_identity_bytes"] == 4096 and result["lifecycle_capacity_reserved"] is True
    if role == "receiver":
        assert "result" in result["operations"]
        assert result["creation_result"] == {
            "schema": allocation_creation_result.SCHEMA, "version": 1,
            "max_response_bytes": allocation_creation_result.MAX_RESPONSE_BYTES,
        }


def test_actual_wire_encoding_counts_newline_and_does_not_expand_pretty_printed_input():
    value = {"nested": [[True] * 100] * 25}
    assert len(json.dumps(value, indent=2)) > allocation.producer.MAX_INPUT_BYTES
    with output.capture_json_output() as captured:
        cli._emit(value)
    assert json.loads(captured.getvalue()) == value
    assert len(captured.getvalue()) <= allocation.producer.MAX_INPUT_BYTES
    at_limit = {"value": "x" * (allocation.producer.MAX_INPUT_BYTES - len(allocation.wire_json({"value": ""})))}
    assert len(allocation.wire_json(at_limit)) == allocation.producer.MAX_INPUT_BYTES
    at_limit["value"] += "x"
    with pytest.raises(ValueError, match="wire response"):
        cli._emit(at_limit)


def test_origin_cli_uses_exact_qualified_local_creator_and_registered_ledger(owner, monkeypatch):
    path, _ = owner
    payload = {"request_id": "cli", "target": TARGET}
    monkeypatch.setattr(family.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode())))
    with output.capture_json_output() as captured:
        assert main.main(["worktree-allocation", "reserve", "--owner-ref", "m/p/owner", "--json"]) == 0
    result = json.loads(captured.getvalue())
    assert result["owner"] == "m/p/owner" and result["phase"] == "pending"
    before = path.read_bytes()
    monkeypatch.setattr(family.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(
        json.dumps({"token": result["token"]}).encode(),
    )))
    with output.capture_json_output() as captured:
        assert main.main(["worktree-allocation", "inspect", "--owner-ref", "m/p/owner", "--json"]) == 0
    assert json.loads(captured.getvalue()) == result and path.read_bytes() == before


@pytest.mark.parametrize("raw", [
    b"[]", b"{} {}", b'{"a":1,"a":2}', b'{"bad":"\xff"}',
    b'{"bad":NaN}', b"x" * (allocation.producer.MAX_INPUT_BYTES + 1),
])
@pytest.mark.parametrize("receiver", [False, True])
def test_invalid_json_never_resolves_local_or_receiving_authority(monkeypatch, raw, receiver):
    monkeypatch.setattr(family.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
    monkeypatch.setattr(cli.cfg, "load_config", lambda *a, **k: pytest.fail("invalid ingress resolved config"))
    monkeypatch.setattr(cli.cfg, "load_project_config", lambda *a, **k: pytest.fail("invalid ingress resolved owner"))
    command = cli.cmd_worktree_allocation_receive if receiver else cli.cmd_worktree_allocation
    with output.capture_json_output() as captured:
        assert command(argparse.Namespace(operation="create" if receiver else "reserve", owner_ref="m/p/owner")) == 1
    assert json.loads(captured.getvalue())["error"] == "rejected"


def test_remote_named_inspect_and_ack_cross_real_finite_interpreter_boundary(receiver, monkeypatch):
    config, admission, records = receiver
    _, record = create(receiver)
    identity = record.receiving_allocation["identity"]
    metadata = {
        "version": allocation.METADATA_VERSION, "lifetime": allocation.LIFETIME,
        "namespace": allocation.NAMESPACE, "creator": admission.origin,
        "request_id": admission.request_id, "token": admission.token,
        "recovery": admission.target, "phase": "bound", "identity": identity,
    }
    origin_config = replace(
        config, repo_name="parent", repos={"parent": replace(
            config.default_repo, anchor=str(Path(config.srcroot) / "origin-anchor"),
        )},
    )

    def creator_project(project):
        assert project == "parent"
        return origin_config

    def creator_registry(anchor, *, config):
        assert config is origin_config and anchor == origin_config.default_repo.anchor
        return {}

    monkeypatch.setattr(remote.cfg, "load_config", lambda *a, **k: pytest.fail("ambient project read"))
    monkeypatch.setattr(remote.cfg, "load_project_config", creator_project)
    monkeypatch.setattr(remote.cfg, "load_machines_yaml", creator_registry)
    monkeypatch.setattr(remote, "resolve_machine_selection", lambda *a: SimpleNamespace(
        entry=SimpleNamespace(ssh_ready=True, key=admission.target["machine"]),
        target=SimpleNamespace(logical_machine=admission.target["machine"]),
    ))
    monkeypatch.setattr(remote, "resolve_ssh_target", lambda *a: ("receiving-alias", "bash"))
    script = """
import sys
from pathlib import Path
from agent_worktrees import __main__ as main, config as cfg
project, records, root, anchor, trees, operation = sys.argv[1:]
config = cfg.Config(srcroot=root, machine="test", platform=cfg.detect_platform(), repo_name=project,
 repos={project:cfg.RepoConfig(anchor=anchor, worktree_root=trees)})
cfg.load_config = lambda *a, **k:config
cfg.load_project_config = lambda *a, **k:config
cfg.project_dir = lambda *a, **k:Path(records).parent
cfg.tracking_dir = lambda *a, **k:Path(records)
sys.exit(main.main(["--project", project, "worktree-allocation-receive", operation, "--json"]))
"""
    seen = []

    def transport(argv: list[str], **kwargs):
        assert argv[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        assert argv[-2] == "receiving-alias"
        assert "worktree-allocation-receive" in argv[-1]
        assert "claimant-liveness" not in argv[-1]
        operation = next(
            name for name in ("capabilities", "result", "confirm", "inspect")
            if f" {name} " in argv[-1]
        )
        seen.append((operation, json.loads(kwargs["input_text"]) if kwargs["input_text"] else None))
        return run_caller_owned(
            [
                sys.executable, "-c", script, config.repo_name, str(records), config.srcroot,
                config.default_repo.anchor, config.default_repo.worktree_root, operation,
            ],
            **kwargs,
        )

    monkeypatch.setattr(remote, "run_caller_owned", transport)
    declared = remote.capabilities(admission.origin, admission.target)
    assert declared["creation_result"]["schema"] == allocation_creation_result.SCHEMA
    assert "result" in declared["operations"]
    observed = remote.request("inspect", metadata)
    assert observed["status"] == "allocated"
    assert allocation.verified_observation(observed, metadata)
    ordinary = remote.request("result", metadata)
    assert ordinary["result"]["worktree"]["creation_nonce"] == record.creation_nonce
    assert ordinary["allocation"] == observed and "launch" not in ordinary["result"]
    unknown = remote.request("result", {**metadata, "request_id": "unseen-original"})
    assert unknown["result"] is None and unknown["allocation"]["status"] == "unknown"
    acknowledged = remote.request("confirm", metadata)
    assert acknowledged["status"] == "confirmed"
    assert acknowledged["identity"] == identity
    assert [operation for operation, _ in seen] == ["capabilities", "inspect", "result", "result", "confirm"]
    assert all(payload["origin"] == admission.origin for _, payload in seen if payload is not None)
    assert len(list(Path(records).glob("*.yaml"))) == 1
