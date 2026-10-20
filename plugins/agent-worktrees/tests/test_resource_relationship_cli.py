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
from agent_worktrees import tracking
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
    payload = {"kind": "bridge", "namespace": "bridge-remote", "request_id": "cli-one", "recovery": {}}
    monkeypatch.setattr(family.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode())))
    with output.capture_json_output() as captured:
        assert main.main(["resource-relationship", "reserve", "--owner-ref", "m/p/owner#caller", "--json"]) == 0
    result = json.loads(captured.getvalue())
    assert result["owner"] == "m/p/owner"
    assert tracking.load_record(path).resources[0].producer == result["producer"]


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
