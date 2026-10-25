"""Manager composes owner facts and durable requests without widening targets."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import installation_updates as ui
from worktree_manager import __main__ as manager
from worktree_manager import catalog, discovery, self_install, update_driver_cli, update_orchestration

READ_OPERATIONS = ui._operations


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path / "manager")
    monkeypatch.setattr(self_install, "manager_repo", lambda root: "https://example.invalid/suite.git")
    monkeypatch.setattr(self_install, "manager_ref", lambda root: "selected")
    monkeypatch.setattr(self_install, "current_version", lambda root: "1")
    monkeypatch.setattr(catalog, "load_catalog", lambda: SimpleNamespace(plugins=[
        SimpleNamespace(name="absent", summary="not installed"),
    ]))
    monkeypatch.setattr(discovery, "discover", lambda **k: SimpleNamespace(
        plugins=[SimpleNamespace(name="guide", description="Payload only")], detail="fixture catalog", kind="checkout",
    ))
    from plugin_resolve import inventory as plugin_inventory
    monkeypatch.setattr(plugin_inventory, "installed_item", lambda *a: {"reference": {}})
    monkeypatch.setattr(ui, "_operations", lambda: {})
    owner_row = {
        "id": "guide", "title": "Guide", "kind": "none", "enabled": "active", "installed": True,
        "payload": "2", "selected": "not applicable", "running": "not applicable",
        "source": "guide@example", "updatable": True, "limitation": "",
    }
    monkeypatch.setattr(ui.engine_client, "run_json", lambda *a, **k: {"contract": 1, "entries": [dict(owner_row)]})
    return owner_row


def test_complete_inventory_separates_known_payload_runtime_and_running(inventory):
    rows = {row["id"]: row for row in ui.inventory()["entries"]}
    assert set(rows) == {"worktree-manager", "guide", "absent"}
    assert rows["worktree-manager"]["selected"] == "1"
    assert "daemon unknown" in rows["worktree-manager"]["running"]
    assert rows["guide"]["payload"] == "2"
    assert not rows["absent"]["updatable"]
    assert "setup/enablement" in rows["absent"]["card"]["body"]


def test_old_owner_is_actionable_not_a_suite_fallback(inventory, monkeypatch):
    monkeypatch.setattr(ui.engine_client, "run_json", lambda *a, **k: {})
    rows = {row["id"]: row for row in ui.inventory()["entries"]}
    assert not rows["guide"]["updatable"]
    assert "targeted-update" in rows["guide"]["limitation"]
    monkeypatch.setattr(update_driver_cli, "cmd_update_request", lambda *a: pytest.fail("old owner submitted"))
    assert ui.submit("guide") == 1


@pytest.mark.parametrize("payload", [
    {"contract": True, "entries": []}, {"contract": 1, "entries": ["invalid"]},
    {"contract": 1, "entries": [{"id": "worktree-manager"}]},
])
def test_malformed_owner_keeps_manager_and_unknown_components_inspectable(inventory, monkeypatch, payload):
    monkeypatch.setattr(ui.engine_client, "run_json", lambda *a, **k: payload)
    rows = {row["id"]: row for row in ui.inventory()["entries"]}
    assert rows["worktree-manager"]["updatable"]
    assert rows["guide"]["installed"] == "unknown" and not rows["guide"]["updatable"]


def test_history_failure_is_unknown_not_idle_or_success(inventory, monkeypatch):
    monkeypatch.setattr(ui, "_operations", lambda: (_ for _ in ()).throw(ValueError("unreadable journal")))
    rows = ui.inventory()["entries"]
    assert all(row["activity"] == "unknown" for row in rows)
    assert "unreadable journal" in rows[0]["card"]["body"]


def test_submit_revalidates_and_forwards_only_selected_target(inventory, monkeypatch):
    calls = []
    monkeypatch.setattr(update_driver_cli, "cmd_update_request", lambda args: calls.append(args) or 0)
    assert ui.submit("guide") == 0
    assert calls == [["--target", "guide", "--detach", "--json"]]
    inventory["updatable"] = False
    monkeypatch.setattr(ui.engine_client, "run_json", lambda *a, **k: {"contract": 1, "entries": [inventory]})
    assert ui.submit("guide") == 1
    assert len(calls) == 1


@pytest.mark.parametrize("target", ["guide", "worktree-manager"])
def test_target_bypasses_bootstrap_and_suite_orchestration(inventory, monkeypatch, target):
    from worktree_manager import update_bootstrap

    monkeypatch.setattr(update_bootstrap, "prepare_update", lambda *a: pytest.fail("target entered suite bootstrap"))
    monkeypatch.setattr(update_orchestration, "preflight_runtime_source", lambda *a: True)
    monkeypatch.setattr(self_install, "self_update", lambda **k: SimpleNamespace(
        action="already-current", cutover=None, reason="", version="1",
    ) if target == "worktree-manager" else pytest.fail("plugin updated Manager"))
    calls = []
    monkeypatch.setattr(ui.engine_client, "run_engine_passthrough", lambda project, args:
        calls.append((project, args)) or 0)
    assert manager._cmd_update(["--target=" + target]) == 0
    assert calls == ([] if target == "worktree-manager" else [(None, ["update", "--no-manager", "--target", "guide"])])


@pytest.mark.parametrize("flags", [
    ["--force"], ["--force-runtime", "guide"], ["--skip-modules"], ["--repair-engine", "source"],
])
def test_target_rejects_widening_or_repair_flags(flags):
    with pytest.raises(SystemExit):
        update_driver_cli.parse_options(["--target", "guide", *flags])


@pytest.mark.parametrize("arguments", [
    ["--target", ""], ["--target="], ["--target", " "],
])
def test_manager_rejects_empty_target_before_submission(arguments):
    with pytest.raises(SystemExit):
        update_driver_cli.parse_options(arguments)


def test_durable_history_distinguishes_acceptance_running_failure_and_completion(inventory, tmp_path, monkeypatch):
    import update_driver

    monkeypatch.setattr(ui, "_operations", READ_OPERATIONS)
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "selected-source")
    spec = SimpleNamespace(backend="manager", source="selected-source", argv=("--target", "guide"))
    log = tmp_path / "operation.log"
    log.write_text("Building selected guide payload")
    request = SimpleNamespace(spec=spec, id="fixture-operation", state="pending", exit_code=None, error=None, log_path=log)
    monkeypatch.setattr(update_driver, "status", lambda: (request,))
    assert ui._operations()["guide"]["state"] == "queued"
    request.state = "running"
    assert "Building selected" in ui._operations()["guide"]["progress"]
    spec.argv = ("--target=other", "--target=guide")
    assert ui._operations()["guide"]["state"] == "running"
    spec.argv = ("--target=other", "--target", "guide")
    assert ui._operations()["guide"]["state"] == "running"
    request.state, request.exit_code = "completed", 1
    assert ui._operations()["guide"]["state"] == "failed"
    request.exit_code = 0
    assert ui._operations()["guide"]["state"] == "succeeded"
    spec.source = "other-installation"
    assert ui._operations() == {}


@pytest.mark.parametrize("shape", ["reference", "inventory"])
def test_known_roster_uses_both_canonical_marketplace_result_shapes(inventory, monkeypatch, shape):
    import plugin_resolve
    from plugin_resolve import inventory as plugin_inventory

    result = {shape: {"marketplace_root" if shape == "reference" else "catalog_root": "/synthetic/catalog"}}
    monkeypatch.setattr(plugin_inventory, "installed_item", lambda *a: result)
    monkeypatch.setattr(plugin_resolve, "load_marketplace", lambda path: SimpleNamespace(plugins={"catalog-only": object()}))
    rows = {row["id"]: row for row in ui.inventory()["entries"]}
    assert "catalog-only" in rows and not rows["catalog-only"]["updatable"]


def test_durable_submission_uses_existing_owner_not_an_observer_queue(inventory, monkeypatch, tmp_path, capsys):
    import update_driver

    request = SimpleNamespace(id="fixture-request", state="pending", exit_code=None, error=None, log_path=tmp_path / "log")
    seen = {}
    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "fixture-source")
    monkeypatch.setattr(update_driver, "runtime_command", lambda module, args=(): (("/fixture/python", "-m", module, *args), {}))
    monkeypatch.setattr(update_driver, "enqueue", lambda spec, **k: seen.update(spec=spec) or request)
    monkeypatch.setattr(update_driver, "ensure_worker", lambda *a, **k: seen.update(worker=True))
    monkeypatch.setattr(update_driver, "wait", lambda *a, **k: pytest.fail("detached UI waited or cancelled owner"))
    assert ui.submit("guide") == 0
    assert seen["spec"].argv == ("--target", "guide")
    assert seen["worker"]
    assert json.loads(capsys.readouterr().out)["state"] == "pending"


def test_refreshed_inventory_uses_owner_version_not_receipt_success(inventory):
    assert ui.inventory()["entries"][1]["payload"] == "2"
    inventory["payload"] = "3"
    assert ui.inventory()["entries"][1]["payload"] == "3"


def test_real_detached_target_request_survives_submitter_exit_and_retains_failure(tmp_path, monkeypatch):
    import update_driver
    from agent_procutil import no_window_kwargs

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    events = tmp_path / "events.jsonl"
    monkeypatch.setenv("UPDATE_DRIVER_TEST_EVENTS", str(events))
    fixture = Path(__file__).parent / "fixtures" / "update_driver_cli.py"
    result = subprocess.run(
        [sys.executable, str(fixture), "submit", "--detach", "--json", "--target", "guide"],
        timeout=20, capture_output=True, text=True, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    request = update_driver.wait(receipt["request_id"], timeout=20)
    assert request.state == "completed" and request.exit_code == 6
    assert request.spec.argv == ("--target", "guide")
    records = [json.loads(line) for line in events.read_text().splitlines()]
    assert records[0]["arguments"] == ["--target", "guide"]
    assert records[-1]["phase"] == "end"
    assert records[0]["pid"] != os.getpid()


def test_real_inventory_cli_uses_selected_provider_and_no_installation(tmp_path, monkeypatch):
    from agent_procutil.owned_process import run_owned

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    engine = tmp_path / "engine.py"
    engine.write_text(
        "import json,sys\n"
        "assert sys.argv[1:]==['update','--inventory','--json'], sys.argv\n"
        "print(json.dumps({'contract':1,'entries':[{'id':'fixture-guide','title':'Fixture guide',"
        "'kind':'none','installed':True,'enabled':'active','payload':'3','selected':'not applicable',"
        "'running':'not applicable','source':'synthetic owner','updatable':True,'limitation':''}]}))\n"
    )
    before = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    result = run_owned([
        sys.executable, "-m", "worktree_manager.installation_updates",
        "--engine-argv", json.dumps([sys.executable, str(engine)]), "list",
    ], timeout=20)
    assert result.returncode == 0, result.stderr
    row = next(row for row in json.loads(result.stdout)["entries"] if row["id"] == "fixture-guide")
    assert row["payload"] == "3" and row["running"] == "not applicable"
    assert before == {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
