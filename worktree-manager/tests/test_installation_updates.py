"""Manager composes owner facts and durable requests without widening targets."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import installation_updates as ui
from worktree_manager import __main__ as manager
from worktree_manager import agent_plugin_runtime, catalog, discovery, self_install, update_driver_cli, update_orchestration

READ_OPERATIONS = ui._operations
RESOLVE_ENGINE_INVOCATION = agent_plugin_runtime.resolve_installed_plugin_invocation


def test_configuration_child_preserves_sealed_runtime():
    assert ui.configuration_pivot().list_cmd[:4] == (
        sys.executable, "-B", "-m", "worktree_manager.installation_updates",
    )


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
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
    monkeypatch.setattr(ui, "_operations", lambda **kwargs: {})
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
    monkeypatch.setattr(ui, "_operations", lambda **kwargs: (_ for _ in ()).throw(ValueError("unreadable journal")))
    rows = ui.inventory()["entries"]
    assert all(row["activity"] == "unknown" for row in rows)
    assert "unreadable journal" in rows[0]["card"]["body"]


@pytest.mark.parametrize("origin", ["packaged", "checkout"])
@pytest.mark.parametrize("error", [OSError("read denied"), ValueError("malformed catalog")])
def test_independent_catalog_read_failure_keeps_manager_and_owner_rows(
    inventory, monkeypatch, origin, error,
):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(catalog if origin == "packaged" else discovery,
                        "load_catalog" if origin == "packaged" else "discover", fail)
    result = ui.inventory()
    rows = {row["id"]: row for row in result["entries"]}
    assert rows["worktree-manager"]["updatable"] and rows["guide"]["payload"] == "2"
    assert "catalog unavailable" in result["summary"]["banner_text"].lower()


def test_submit_revalidates_and_forwards_only_selected_target(inventory, monkeypatch):
    calls = []
    monkeypatch.setattr(update_driver_cli, "cmd_update_request", lambda args: calls.append(args) or 0)
    assert ui.submit("guide") == 0
    assert calls == [["--target", "guide", "--detach", "--json"]]
    inventory["updatable"] = False
    monkeypatch.setattr(ui.engine_client, "run_json", lambda *a, **k: {"contract": 1, "entries": [inventory]})
    assert ui.submit("guide") == 1
    assert len(calls) == 1


@pytest.fixture
def engine_cells(inventory, monkeypatch, tmp_path):
    contexts = {
        name: {
            "COPILOT_EXTENSIONS_CONTEXT": str(tmp_path / name / "install.json"),
            "AGENT_WORKTREES_UPDATE_CONTEXT": str(tmp_path / name / "project"),
            "AGENT_WORKTREES_PAYLOAD_ROOT": str(tmp_path / name / "payload"),
        }
        for name in ("cell-a", "cell-b")
    }
    roots = {name: tmp_path / name / "runtime" for name in contexts}
    selected = []
    resolutions = []

    def resolve(plugin, *, environment):
        assert plugin == "agent-worktrees"
        resolutions.append(dict(environment))
        for name, context in contexts.items():
            if all(environment.get(key) == value for key, value in context.items()):
                return (["/fixture/engine"], roots[name])
        return None

    def select(name):
        selected[:] = [name]
        for key, value in contexts[name].items():
            monkeypatch.setenv(key, value)

    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_invocation", resolve)
    monkeypatch.setattr(ui.engine_client, "run_json", lambda *a, **k: {
        "contract": 1, "execution_source": str(roots[selected[0]]),
        "entries": [dict(inventory)],
    })
    select("cell-a")
    return SimpleNamespace(select=select, contexts=contexts, resolutions=resolutions)


@pytest.fixture
def manager_request(engine_cells, monkeypatch, tmp_path):
    import update_driver

    calls = []
    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    request = SimpleNamespace(
        id="targeted-request", state="pending", exit_code=None, error=None,
        log_path=tmp_path / "targeted.log",
    )

    def enqueue(spec, **kwargs):
        calls.append(spec)
        request.spec = spec
        return request

    monkeypatch.setattr(ui, "_operations", READ_OPERATIONS)
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "manager-source")
    monkeypatch.setattr(update_driver, "runtime_command", lambda module, args=(): (("/fixture/python", "-m", module, *args), {}))
    monkeypatch.setattr(update_driver, "enqueue", enqueue)
    monkeypatch.setattr(update_driver, "status", lambda: (request,) if calls else ())
    monkeypatch.setattr(update_driver, "ensure_worker", lambda *a, **k: None)
    monkeypatch.setattr(update_driver, "wait", lambda *a, **k: pytest.fail("detached submission waited"))
    monkeypatch.setattr(ui.engine_client, "run_engine_passthrough", lambda *a, **k:
        pytest.fail("plugin submission bypassed Manager admission"))
    return request, calls


def test_manager_plugin_submission_uses_the_manager_durable_admission_boundary(
    manager_request, engine_cells,
):
    request, calls = manager_request
    assert ui.submit("guide") == 0
    assert len(calls) == 1 and calls[0].backend == "manager"
    assert calls[0].argv == ("--target", "guide")
    assert calls[0].source == "manager-source"
    assert all(dict(calls[0].context)[key] == value for key, value in engine_cells.contexts["cell-a"].items())
    for state, exit_code, activity, error in (
        ("pending", None, "queued", None),
        ("running", None, "running", None),
        ("completed", 1, "failed", "selected payload build failed"),
        ("completed", 0, "succeeded", None),
    ):
        request.state, request.exit_code, request.error = state, exit_code, error
        progress = f"Selected guide update: {activity}"
        request.log_path.write_text(progress, encoding="utf-8")
        row = next(row for row in ui.inventory()["entries"] if row["id"] == "guide")
        assert row["activity"] == activity
        assert row["card"]["status"] == activity
        body = row["card"]["body"]
        assert progress in body and request.id in body
        operation = json.loads(body.split("Durable operation: ", 1)[1].split("\n\n", 1)[0])
        assert operation["exit_code"] == exit_code and operation["error"] == error
        assert operation["log"] == str(request.log_path)
        engine_cells.select("cell-b")
        foreign = next(row for row in ui.inventory()["entries"] if row["id"] == "guide")
        assert foreign["activity"] == "idle"
        assert "Durable operation:" not in foreign["card"]["body"]
        assert progress not in foreign["card"]["body"]
        assert engine_cells.resolutions[-1] == dict(calls[0].context)
        engine_cells.select("cell-a")

    for spec in (
        replace(calls[0], source="foreign-manager-source"),
        replace(calls[0], backend="foreign-backend"),
        replace(calls[0], backend="engine"),
    ):
        request.spec = spec
        row = next(row for row in ui.inventory()["entries"] if row["id"] == "guide")
        assert row["activity"] == "idle"
        assert "Durable operation:" not in row["card"]["body"]


def test_same_manager_plugin_submissions_refresh_only_their_addressed_engine_cell(
    manager_request, engine_cells, monkeypatch,
):
    import update_driver

    request, calls = manager_request
    request.id = "cell-a-request"
    assert ui.submit("guide") == 0
    first = SimpleNamespace(**vars(request))
    first.state, first.exit_code, first.error = "completed", 1, "cell-a build failed"
    engine_cells.select("cell-b")
    request.id = "cell-b-request"
    assert ui.submit("guide") == 0
    request.state = "running"
    assert calls[0].source == calls[1].source == "manager-source"
    assert dict(calls[0].context) != dict(calls[1].context)
    monkeypatch.setattr(update_driver, "status", lambda: (first, request))
    for cell, activity, expected, excluded in (
        ("cell-b", "running", request.id, first.id),
        ("cell-a", "failed", first.id, request.id),
    ):
        engine_cells.select(cell)
        row = next(row for row in ui.inventory()["entries"] if row["id"] == "guide")
        assert row["activity"] == activity
        assert expected in row["card"]["body"]
        assert excluded not in row["card"]["body"]
    request.state, request.exit_code = "completed", 0
    engine_cells.select("cell-b")
    assert next(row for row in ui.inventory()["entries"] if row["id"] == "guide")["activity"] == "succeeded"


@pytest.mark.parametrize("context", [
    (), (("COPILOT_EXTENSIONS_CONTEXT", "malformed-context"),),
    (("COPILOT_EXTENSIONS_CONTEXT", "/foreign/install.json"),),
])
def test_unattributable_plugin_context_is_unknown_without_progress_or_ambient_fallback(
    manager_request, engine_cells, context,
):
    request, calls = manager_request
    assert ui.submit("guide") == 0
    request.spec = replace(calls[0], context=context)
    request.state, request.exit_code = "completed", 0
    request.log_path.write_text("unattributed completion details", encoding="utf-8")
    engine_cells.select("cell-b")
    result = ui.inventory()
    rows = {row["id"]: row for row in result["entries"]}
    assert rows["guide"]["activity"] == "unknown"
    body = rows["guide"]["card"]["body"]
    assert "Durable update attribution unavailable" in body
    assert "unattributed completion details" not in body
    assert str(request.log_path) not in body
    assert rows["worktree-manager"]["activity"] == "idle"
    assert engine_cells.resolutions == [dict(context)]


def test_manager_only_history_does_not_depend_on_engine_cell_or_saved_context(
    manager_request, engine_cells, monkeypatch,
):
    import update_driver

    request, calls = manager_request
    assert ui.submit("worktree-manager") == 0
    request.state, request.exit_code = "completed", 0
    request.spec = replace(calls[0], context=())
    plugin = SimpleNamespace(**vars(request))
    plugin.spec = replace(request.spec, argv=("--target", "guide"))
    plugin.id = "unattributable-plugin-request"
    monkeypatch.setattr(update_driver, "status", lambda: (request, plugin))
    engine_cells.select("cell-b")
    rows = {row["id"]: row for row in ui.inventory()["entries"]}
    row = rows["worktree-manager"]
    assert row["activity"] == "succeeded"
    assert request.id in row["card"]["body"]
    assert rows["guide"]["activity"] == "unknown"
    assert engine_cells.resolutions == [{}]


def test_default_root_submission_history_uses_real_resolver_without_inheriting_cell_selection(
    manager_request, monkeypatch, tmp_path,
):
    from _installation_context_fixtures import namespaced_fixture, patch_profile, write_json, write_slot

    request, calls = manager_request
    home = tmp_path / "account"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for key in update_driver_cli._CONTEXT_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(ui.engine_client, "inherited_engine_command", lambda: None)
    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_invocation", RESOLVE_ENGINE_INVOCATION)
    patch_profile(monkeypatch, agent_plugin_runtime, home)
    root = home / ".agent-worktrees"
    write_slot(root / "versions" / "1.0.0", windows=os.name == "nt")
    (root / "current-version").write_text("1.0.0", encoding="utf-8")
    write_json(root / "deploy-manifest.json", {
        "service": "agent-worktrees", "source": {"plugin": "agent-worktrees"},
    })
    invocation = RESOLVE_ENGINE_INVOCATION("agent-worktrees", environment={})
    assert invocation is not None and invocation[1] == root
    selected = [str(invocation[1].resolve())]
    monkeypatch.setattr(ui.engine_client, "run_json", lambda *a, **k: {
        "contract": 1, "execution_source": selected[0], "entries": [{
            "id": "guide", "title": "Guide", "kind": "none", "enabled": "active",
            "installed": True, "payload": "2", "selected": "not applicable",
            "running": "not applicable", "source": "fixture", "updatable": True, "limitation": "",
        }],
    })
    assert ui.submit("guide") == 0
    assert calls[0].context == ()
    for state, exit_code, activity in (
        ("pending", None, "queued"), ("running", None, "running"),
        ("completed", 1, "failed"), ("completed", 0, "succeeded"),
    ):
        request.state, request.exit_code = state, exit_code
        assert ui.inventory()["entries"][1]["activity"] == activity

    install, _python = namespaced_fixture(home, windows=os.name == "nt")
    write_json(home / ".copilot-extensions" / "installation-mode.json", {
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True},
    })
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(install))
    monkeypatch.setenv("AGENT_HOME", str(home / "foreign-override"))
    monkeypatch.setenv("AGENT_WORKTREES_UPDATE_CONTEXT", str(home / "cell-project"))
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(home / "payload"))
    cell = RESOLVE_ENGINE_INVOCATION("agent-worktrees")
    assert cell is not None and cell[1] == install.parent
    assert RESOLVE_ENGINE_INVOCATION("agent-worktrees", environment={}) == invocation
    selected[0] = str(cell[1].resolve())
    row = ui.inventory()["entries"][1]
    assert row["activity"] == "idle" and "Durable operation:" not in row["card"]["body"]
    selected[0] = str(root.resolve())
    assert ui.inventory()["entries"][1]["activity"] == "succeeded"
    request.spec = replace(calls[0], context=(("COPILOT_EXTENSIONS_CONTEXT", str(home / "missing.json")),))
    row = ui.inventory()["entries"][1]
    assert row["activity"] == "unknown"
    assert "Durable update attribution unavailable" in row["card"]["body"]


def test_missing_engine_identity_reports_plugin_unknown_without_affecting_manager(
    manager_request, monkeypatch,
):
    assert ui.submit("guide") == 0
    monkeypatch.setattr(ui.engine_client, "run_json", lambda *a, **k: {"contract": 1, "entries": []})
    rows = {row["id"]: row for row in ui.inventory()["entries"]}
    assert rows["guide"]["activity"] == "unknown"
    assert "did not report its installation identity" in rows["guide"]["card"]["body"]
    assert rows["worktree-manager"]["activity"] == "idle"


def test_public_manager_plugin_target_refuses_unsupported_source_before_submission(
    inventory, tmp_path, monkeypatch, capsys,
):
    import update_driver
    from worktree_manager.runtime_source_config import RuntimeSource, configured_source, set_source

    root = self_install.default_root()
    root.mkdir()
    set_source(RuntimeSource(channel="dev", allow_dev=True), root=root)
    assert configured_source().channel == "dev"
    baseline = (root / "config.toml").read_bytes()
    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(update_driver, "enqueue", lambda *a, **k: pytest.fail("unsupported source queued"))
    monkeypatch.setattr(ui.engine_client, "run_engine_passthrough", lambda *a, **k: pytest.fail("source policy bypassed"))
    assert manager.main(["update", "--target", "guide", "--detach", "--json"]) == 1
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["state"] == "unavailable"
    assert "source-backed execution is unavailable through this targeted/bootstrap/repair entry point" in receipt["error"]
    assert (root / "config.toml").read_bytes() == baseline


def test_explicit_headless_engine_submission_uses_canonical_no_window_flags(monkeypatch):
    import agent_procutil

    seen = {}
    monkeypatch.setattr(ui.engine_client, "engine_base_command", lambda: ["synthetic-engine"])
    monkeypatch.setattr(agent_procutil, "no_window_kwargs", lambda: {"creationflags": 0x08000000})
    monkeypatch.setattr(ui.engine_client.subprocess, "run", lambda argv, **kwargs:
        seen.update(argv=argv, **kwargs) or SimpleNamespace(returncode=0))
    assert ui.engine_client.run_engine_passthrough(
        None, ["update", "--target", "guide", "--detach"], headless=True,
    ) == 0
    assert seen["creationflags"] == 0x08000000
    if os.name == "nt":
        assert seen["stdout"] is sys.stdout and seen["stderr"] is sys.stderr


@pytest.mark.parametrize("target", ["guide", "worktree-manager"])
@pytest.mark.parametrize("compatibility_flags", [
    [], ["--no-anchor-sync", "--no-prune-pivots"],
])
def test_target_bypasses_bootstrap_and_suite_orchestration(
    inventory, monkeypatch, target, compatibility_flags,
):
    from worktree_manager import update_bootstrap

    monkeypatch.setattr(update_bootstrap, "prepare_update", lambda *a: pytest.fail("target entered suite bootstrap"))
    monkeypatch.setattr(update_orchestration, "preflight_runtime_source", lambda *a: True)
    monkeypatch.setattr(self_install, "self_update", lambda **k: SimpleNamespace(
        action="already-current", cutover=None, reason="", version="1",
    ) if target == "worktree-manager" else pytest.fail("plugin updated Manager"))
    calls = []
    monkeypatch.setattr(ui.engine_client, "run_engine_passthrough", lambda project, args:
        calls.append((project, args)) or 0)
    assert manager._cmd_update(["--target=" + target, *compatibility_flags]) == 0
    assert calls == ([] if target == "worktree-manager" else [
        (None, ["update", "--no-manager", "--target", "guide", *compatibility_flags]),
    ])


@pytest.mark.parametrize("cutover", [
    {"action": "error", "reason": "synthetic cutover lease busy"},
    {"action": "cutover", "result": {"ok": False, "reason": "synthetic cutover lease busy"}},
])
def test_manager_cutover_failure_keeps_actionable_owner_detail(inventory, monkeypatch, capsys, cutover):
    monkeypatch.setattr(self_install, "self_update", lambda **kwargs: SimpleNamespace(
        action="updated", cutover=cutover, reason="", version="2",
    ))
    monkeypatch.setattr(ui.engine_client, "run_engine_passthrough", lambda *a, **k: pytest.fail("Manager-only failure updated plugins"))
    assert manager._cmd_update(["--target", "worktree-manager"]) != 0
    output = capsys.readouterr()
    assert "synthetic cutover lease busy" in output.out + output.err


@pytest.mark.parametrize("flags", [
    ["--force"], ["--force-runtime", "guide"], ["--skip-modules"], ["--repair-engine", "source"],
    ["--engine-acquisition-timeout", "120"],
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
    spec = SimpleNamespace(backend="engine", source="selected-source", argv=("--target", "guide"))
    log = tmp_path / "operation.log"
    log.write_text("Building selected guide payload")
    request = SimpleNamespace(spec=spec, id="fixture-operation", state="pending", exit_code=None, error=None, log_path=log)
    monkeypatch.setattr(update_driver, "status", lambda: (request,))
    def read():
        return ui._operations(engine_source="selected-source")

    assert read()["guide"]["state"] == "queued"
    request.state = "running"
    assert "Building selected" in read()["guide"]["progress"]
    spec.argv = ("--target=other", "--target=guide")
    assert read()["guide"]["state"] == "running"
    spec.argv = ("--target=other", "--target", "guide")
    assert read()["guide"]["state"] == "running"
    request.state, request.exit_code = "completed", 1
    assert read()["guide"]["state"] == "failed"
    request.exit_code = 0
    assert read()["guide"]["state"] == "succeeded"
    spec.source = "other-installation"
    assert read() == {}


@pytest.mark.parametrize("shape", ["reference", "inventory"])
def test_known_roster_uses_both_canonical_marketplace_result_shapes(inventory, monkeypatch, shape):
    import plugin_resolve
    from plugin_resolve import inventory as plugin_inventory

    result = {shape: {"marketplace_root" if shape == "reference" else "catalog_root": "/synthetic/catalog"}}
    monkeypatch.setattr(plugin_inventory, "installed_item", lambda *a: result)
    monkeypatch.setattr(plugin_resolve, "load_marketplace", lambda path: SimpleNamespace(plugins={"catalog-only": object()}))
    rows = {row["id"]: row for row in ui.inventory()["entries"]}
    assert "catalog-only" in rows and not rows["catalog-only"]["updatable"]


def test_engine_history_is_attributed_only_to_selected_execution_source(inventory, tmp_path, monkeypatch):
    import update_driver

    monkeypatch.setattr(ui, "_operations", READ_OPERATIONS)
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "manager-source")
    request = SimpleNamespace(
        spec=SimpleNamespace(backend="engine", source="/selected/engine", argv=("--target=guide",)),
        id="engine-operation", state="running", exit_code=None, error=None, log_path=tmp_path / "log",
    )
    monkeypatch.setattr(update_driver, "status", lambda: (request,))
    monkeypatch.setattr(ui.engine_client, "run_json", lambda *a, **k: {
        "contract": 1, "execution_source": "/selected/engine", "entries": [dict(inventory)],
    })
    assert ui.inventory()["entries"][1]["activity"] == "running"
    request.state, request.exit_code = "completed", 1
    assert ui.inventory()["entries"][1]["activity"] == "failed"
    request.spec.source = "/other/engine"
    assert ui.inventory()["entries"][1]["activity"] == "idle"


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
    assert ui.submit("worktree-manager") == 0
    assert seen["spec"].argv == ("--target", "worktree-manager")
    assert seen["worker"]
    assert json.loads(capsys.readouterr().out)["state"] == "pending"


def test_refreshed_inventory_uses_owner_version_not_receipt_success(inventory):
    assert ui.inventory()["entries"][1]["payload"] == "2"
    inventory["payload"] = "3"
    assert ui.inventory()["entries"][1]["payload"] == "3"


@pytest.mark.parametrize("suite_budget", [None, "120"])
def test_real_detached_target_request_survives_submitter_exit_and_retains_failure(tmp_path, monkeypatch, suite_budget):
    import update_driver
    from agent_procutil import no_window_kwargs

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    events = tmp_path / "events.jsonl"
    monkeypatch.setenv("UPDATE_DRIVER_TEST_EVENTS", str(events))
    if suite_budget is not None:
        monkeypatch.setenv(update_driver_cli.TIMEOUT_ENV, suite_budget)
    fixture = Path(__file__).parent / "fixtures" / "update_driver_cli.py"
    result = subprocess.run(
        [sys.executable, str(fixture), "submit", "--detach", "--json", "--target", "worktree-manager"],
        timeout=20, capture_output=True, text=True, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    request = update_driver.wait(receipt["request_id"], timeout=20)
    assert request.state == "completed" and request.exit_code == 6
    assert request.spec.argv == ("--target", "worktree-manager")
    records = [json.loads(line) for line in events.read_text().splitlines()]
    assert records[0]["arguments"] == ["--target", "worktree-manager"]
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
