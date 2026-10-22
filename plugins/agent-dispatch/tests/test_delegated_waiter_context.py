from __future__ import annotations

from contextlib import nullcontext
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from agent_dispatch import delegated_waiter_cli, delegated_waiter_context, peer_launch, procutil
from agent_dispatch.delegated_waiter_contract import NOTIFICATIONS, PROTOCOL, validate_delegation
from tests.test_delegated_waiter_cli import args, clean_context as clean_context
from tests.test_procutil import FIRST_MARKETPLACE_ID, _make_namespaced_context

pytestmark = pytest.mark.contract("agent_dispatch.delegated_waiter.context")


@pytest.fixture
def cell(tmp_path, monkeypatch, clean_context):
    receipt = _make_namespaced_context(tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID)
    entries = receipt.parent / "bin"
    entries.mkdir()
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    for suffix in ("ps1", "sh"):
        for entry in ("resolve-runtime", "delegated-waiter-callback"):
            shutil.copyfile(scripts / f"{entry}.{suffix}", entries / f"{entry}.{suffix}")
    context = peer_launch.validate_owner("agent-dispatch", receipt.parent, str(receipt))
    for key, value in peer_launch.peer_environment(context, {}).items():
        monkeypatch.setenv(key, value)
    return receipt


def test_canonical_cell_routing_remains_eligible(cell, monkeypatch):
    client = SimpleNamespace(health=lambda: {"delegated_run_waiters": {
        "protocol": PROTOCOL, "coordinator_id": "queue",
    }})
    monkeypatch.setattr(procutil, "agent_pull_requests_launch_prefix", lambda: ["attributable"])
    monkeypatch.setattr(delegated_waiter_cli, "owner_request", lambda *_: {
        "pid": 3, "capabilities": [NOTIFICATIONS],
    })
    identity, prefix, reason = delegated_waiter_cli.negotiate(
        args(), ("real-wait",), lambda _: nullcontext(client),
    )
    assert reason == "negotiated" and prefix == ["attributable"]
    assert identity["installation"] == {"root": str(cell.parent), "receipt": str(cell)}
    assert validate_delegation(identity) == identity


@pytest.mark.parametrize("variable", [
    "AGENT_DISPATCH_RUN_DIR", "AGENT_DISPATCH_ROUTING_DIR", "AGENT_DISPATCH_INSTALL_DIR",
    "AGENT_DISPATCH_TOKEN", "AGENT_DISPATCH_ENDPOINT", "AGENT_DISPATCH_PORT",
])
def test_cell_overrides_cannot_masquerade_as_intrinsic_routing(cell, monkeypatch, variable):
    if variable == "AGENT_DISPATCH_INSTALL_DIR":
        # A mismatched root is invalid installation ownership, not an optional miss.
        monkeypatch.setenv(variable, str(cell.parent / "foreign"))
        with pytest.raises(ValueError):
            delegated_waiter_context.capture()
        return
    monkeypatch.setenv(variable, "caller-override")
    monkeypatch.setattr(procutil, "agent_pull_requests_launch_prefix", lambda: pytest.fail("no probe"))
    assert delegated_waiter_cli.negotiate(args(), ("real-wait",), None)[2] == (
        "caller_context_requires_process"
    )


def test_deactivated_cell_is_a_visible_refusal(cell):
    value = json.loads(cell.read_text(encoding="utf-8"))
    value["state"] = "inactive"
    cell.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        delegated_waiter_context.capture()


@pytest.mark.parametrize("installation", [
    None, {}, {"root": "path", "receipt": "path", "token": "not-permitted"},
    {"root": "", "receipt": "path"}, {"root": "path", "receipt": "\0"},
])
def test_installation_identity_has_no_extra_or_invalid_fields(installation):
    with pytest.raises(ValueError):
        delegated_waiter_context.validate_installation(installation)
