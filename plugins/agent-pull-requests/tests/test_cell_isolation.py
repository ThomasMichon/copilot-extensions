"""Two actual independently installed payloads cannot cross owner custody."""

from __future__ import annotations

from contextlib import ExitStack
import json
import os
from time import monotonic
from uuid import uuid4

import pytest
from work_coalescing_singleton.client import request
from zdd import process_exit, routing

from agent_pull_requests.watch_storage import read_subscriptions_state
from test_cell_payload_upgrade import _release, _run, _scenario_remaining
from test_cell_payload_upgrade import native_profile as native_profile  # noqa: F401 -- pytest fixture
from test_cell_upgrade import _pending


def _json(command, arguments, environment, caller):
    result = _run(command, arguments, environment, caller, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _stop(command, environment, caller):
    result = _run(command, ["stop", "--json"], environment, caller, timeout=150)
    assert result.returncode == 0, result.stderr


def _cleanup(cell, environment, caller):
    try:
        if not cell["stopped"]:
            _stop(cell["command"], environment, caller)
        if cell["reference"] is not None:
            assert cell["reference"].wait(0)
    finally:
        if cell["reference"] is not None:
            cell["reference"].close()


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
# The measured full workflow is 681s; every native command/RPC is also bounded.
@pytest.mark.timeout(1140)
@pytest.mark.skipif(
    os.name != "nt" and os.environ.get("APR_NATIVE_OS_ACCOUNT_ACCEPTANCE") != "1",
    reason="Native POSIX cells need an isolated OS account",
)
def test_two_normal_shipped_cells_isolate_state_endpoints_and_lifecycle(tmp_path_factory, native_profile):
    scenario_deadline = monotonic() + 1140
    root = tmp_path_factory.mktemp("i")
    releases = []
    for name in ("a", "b"):
        place = root / name
        place.mkdir()
        payload, profile = _release(place, marketplace_name=name, profile=native_profile)
        releases.append((name, payload, profile))
    profile = releases[0][2]
    (profile / ".copilot" / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            name: {"source": {"source": "directory", "path": str(payload.parent.parent)}}
            for name, payload, _ in releases
        },
        "enabledPlugins": {
            f"agent-pull-requests@{name}": True for name, _, _ in releases
        },
    }), encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"COPILOT_EXTENSIONS_CONTEXT", "PYTHONPATH", "PYTHONHOME"}
    }
    if native_profile is None:
        environment.update(HOME=str(profile), USERPROFILE=str(profile))
    caller = root / "caller"
    caller.mkdir()
    cells = []
    with ExitStack() as cleanup:
        for _, payload, _ in releases:
            init = payload / "scripts" / "init.ps1"
            command = payload / "bin" / "agent-pull-requests.ps1"
            result = _run(init, [], environment, caller, timeout=300)
            assert result.returncode == 0, result.stderr
            matches = [
                path for path in (profile / ".copilot-extensions" / "marketplaces").glob(
                    "*/plugins/agent-pull-requests/install.json",
                )
                if json.loads(path.read_text("utf-8"))["payload"]["root"] == str(payload)
            ]
            receipt, = matches
            plugin = receipt.parent
            version = json.loads((payload / "plugin.json").read_text("utf-8"))["version"]
            python = plugin / "versions" / version / (
                "Scripts/python.exe" if os.name == "nt" else "bin/python"
            )
            saved = _pending(plugin / "state", python)
            cell = {"command": command, "reference": None, "stopped": False}
            # Register cleanup before attempting startup, including lost replies.
            cleanup.callback(_cleanup, cell, environment, caller)
            result = _run(
                command, ["serve"], environment, caller,
                timeout=_scenario_remaining(scenario_deadline),
            )
            assert result.returncode == 0, result.stderr
            health = _json(command, ["watch", "health", "--json"], environment, caller)
            owner = health["instance"]
            reference = process_exit.open_process(owner["pid"], owner["startTime"])
            cell["reference"] = reference
            journal = plugin / "run" / "watch-owner-transaction.json"
            document = json.loads(journal.read_text("utf-8"))
            assert document["successor"] == owner and document["phase"] == "complete"
            cell.update({
                "payload": payload, "init": init, "command": command, "receipt": receipt,
                "plugin": plugin, "saved": saved, "owner": owner, "reference": reference,
                "document": document, "journal": journal.read_bytes(),
                "route": routing.read_table(plugin / "run", strict=True),
            })
            cells.append(cell)
        first, second = cells
        assert first["plugin"] != second["plugin"]
        assert first["owner"]["marketplaceId"] != second["owner"]["marketplaceId"]
        assert first["owner"]["pid"] != second["owner"]["pid"]
        assert tuple(first["document"]["endpoints"]["successor"][key] for key in ("host", "port")) != tuple(
            second["document"]["endpoints"]["successor"][key] for key in ("host", "port")
        )
        assert first["saved"]["registration_id"] != second["saved"]["registration_id"]
        for own, foreign in ((first, second), (second, first)):
            status = _json(own["command"], ["watch", "status", "--json"], environment, caller)
            pending = status["pending_deliveries"]
            assert [entry["registration_id"] for entry in pending] == [own["saved"]["registration_id"]]
            assert foreign["saved"]["registration_id"] not in {
                entry["registration_id"] for entry in pending
            }
            result = _run(own["command"], [
                "watch", "unsubscribe", "--repo", "example/project", "--number", "1",
                "--subscriber-id", "upgrade-subscriber",
                "--registration-id", foreign["saved"]["registration_id"], "--json",
            ], environment, caller, timeout=60)
            assert result.returncode != 0
            assert json.loads(result.stdout).get("unregistered") is False
            crossed = {**environment, "COPILOT_EXTENSIONS_CONTEXT": str(foreign["receipt"])}
            for arguments in (["watch", "health", "--json"], ["stop", "--json"], ["restart", "--json"]):
                result = _run(own["command"], arguments, crossed, caller, timeout=60)
                assert result.returncode != 0
            result = _run(own["init"], [], crossed, caller, timeout=60)
            assert result.returncode != 0
            endpoint = foreign["document"]["endpoints"]["successor"]
            for operation in ("adopt", "activate", "retire", "resume"):
                response = request(
                    endpoint["host"], endpoint["port"], endpoint["token"],
                    kind="owner-control", key=uuid4().hex,
                    payload={
                        "operation": operation, "instance": foreign["owner"],
                        "transactionId": own["document"]["transactionId"],
                        "authorization": own["document"]["authorization"],
                        "nonce": str(uuid4()), "timeout": 5,
                    },
                    request_deadline_s=5, completion_timeout_s=5,
                )
                assert response == {"error": "watch-owner-control-refused", "retryable": False}
        for cell in cells:
            health = _json(cell["command"], ["watch", "health", "--json"], environment, caller)
            assert health["instance"] == cell["owner"]
            assert all(health[field] for field in ("writerHeld", "readReady", "effectsEnabled"))
            assert not cell["reference"].wait(0)
            assert read_subscriptions_state(cell["plugin"] / "state") == [cell["saved"]]
            run = cell["plugin"] / "run"
            assert (run / "watch-owner-transaction.json").read_bytes() == cell["journal"]
            assert routing.read_table(run, strict=True) == cell["route"]
        _stop(first["command"], environment, caller)
        first["stopped"] = True
        assert first["reference"].wait(0)
        health = _json(second["command"], ["watch", "health", "--json"], environment, caller)
        assert health["instance"] == second["owner"] and health["effectsEnabled"]
        assert not second["reference"].wait(0)
        assert read_subscriptions_state(second["plugin"] / "state") == [second["saved"]]
        assert not (profile / ".agent-pull-requests").exists()
        assert not (profile / ".local" / "bin").exists()
        assert not list(caller.iterdir())
