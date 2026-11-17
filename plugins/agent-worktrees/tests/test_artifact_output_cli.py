"""The actual CLI negotiates the new provider without bootstrap or fallback."""

import argparse
import io
import json
from pathlib import Path
import sys
from typing import Any

import pytest

from agent_worktrees import __main__ as main, artifact_output_cli as cli, claims_cli, config_sources, state_root
from agent_worktrees import config as cfg, tracking, worktree_ops_cli
from test_artifact_output import Destination, destination as destination
from test_resource_producer import owner as owner


def test_real_main_capabilities_is_inert_and_strict(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(main, "_ensure_cluster_loaded", lambda: pytest.fail("user output loaded the CLI cluster"))
    monkeypatch.setattr(cli, "_owner", lambda *a: pytest.fail("capability lookup resolved an owner"))
    assert main.main(["resource-producer", "output-capabilities", "--json"]) == 0
    result = json.loads(capfd.readouterr().out)
    assert result["verified_delivery"] == 1 and result["cloud_acknowledgment"] is False
    with pytest.raises(SystemExit) as error:
        main.main(["resource-producer", "output-capabilities", "--json", "--unknown-option"])
    assert error.value.code == 2


@pytest.mark.parametrize("implicit_owner", [False, True])
def test_real_main_resolves_produces_and_retries_delivery(
    destination: Destination, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
    implicit_owner: bool,
) -> None:
    path, selection, operation_id = destination
    base_root = path.parent.parent / "source"
    base_root.mkdir()
    if implicit_owner:
        checkout = Path(tracking.load_record(path).worktree_path)
        monkeypatch.chdir(checkout)
        monkeypatch.setattr(main, "_INVOCATION_CWD", checkout)
        monkeypatch.setattr(cfg, "project_name", lambda: "p")
        monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: path.parent)
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "fixture-session")
        resolved = tracking.parse_claim_ref(worktree_ops_cli._resolve_owner_ref() or "")
        assert resolved is not None and resolved.session == "fixture-session"
    context = {
        "repository_id": "repo-example", "execution_machine": "m", "installation_cell": "test-cell",
        "binding": "unbound", "base_root": str(base_root),
    }
    monkeypatch.setattr(main, "_ensure_cluster_loaded", lambda: pytest.fail("user output loaded the CLI cluster"))
    monkeypatch.setattr(config_sources, "configuration_report", lambda *a: {
        "schema": "agent-worktrees.configuration", "version": 1, "context": context,
    })
    monkeypatch.setattr(
        claims_cli, "_coordination_readiness_for_owner_ref",
        lambda *a: state_root.CoordinationReadiness(
            True, "ready", state_root.StateRoot(str(path.parent), "launch_repo", "p", False, False, True),
        ),
    )
    base = {"topic": "Report", "owner_roots": {"downloads": selection["root"]}}
    def invoke(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        monkeypatch.setattr(sys, "stdin",
                            io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode("utf-8"))))
        argv = ["resource-producer", "output-" + operation, "--json"]
        if not implicit_owner:
            argv += ["--owner-ref", "m/p/owner"]
        assert main.main(argv) == 0
        value: object = json.loads(capfd.readouterr().out)
        assert isinstance(value, dict)
        return value
    assert invoke("resolve", base)["destination"] == selection["destination"]
    request = {**base, "files": {"report.txt": "fixture"}, "operation_id": operation_id}
    result = invoke("produce", request)
    assert result["phase"] == "delivered"
    assert invoke("deliver", {"token": result["token"]}) == result
    monkeypatch.setattr(config_sources, "configuration_report",
                        lambda *a: pytest.fail("completed retry resolved changed configuration"))
    assert invoke("produce", request) == result
    assert len(tracking.load_record(path).resources) == 1


def test_explicit_session_qualified_owner_is_not_normalized(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cfg, "load_project_config", lambda *a: pytest.fail("invalid owner resolved configuration"))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"token":"fixture"}')))
    args = argparse.Namespace(
        operation="inspect", json=True,
        owner_ref=tracking.format_claim_ref("m", "p", "owner", "fixture-session"),
    )
    assert cli.cmd_user_output(args) == 1
    assert "exact qualified local owner" in json.loads(capfd.readouterr().out)["message"]


@pytest.mark.parametrize("raw", [
    b"[]", b"{} {}", b'{"token":"x","token":"y"}',
    b'{"token":"x","proof":true}', b'{"token":"x","bootstrap":true}',
])
def test_bad_delivery_input_cannot_resolve_or_bootstrap_an_owner(
    monkeypatch: pytest.MonkeyPatch, raw: bytes, capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_owner", lambda *a: pytest.fail("bad input reached owner resolution"))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))
    args = argparse.Namespace(operation="deliver", owner_ref=None, json=True)
    assert cli.cmd_user_output(args) == 1
    assert json.loads(capfd.readouterr().out)["error"] == "rejected"
