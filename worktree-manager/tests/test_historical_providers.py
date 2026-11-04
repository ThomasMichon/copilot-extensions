from __future__ import annotations

import json
import sys

import pytest

from worktree_manager import engine_client
from worktree_manager.historical_providers import IndexRegistrations, WorktreesDaemons


def test_selected_native_provider_uses_one_process_for_a_family_batch(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    script = tmp_path / "provider.py"
    script.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "operation=sys.argv[2]\n"
        f"with Path({str(log)!r}).open('a') as log: log.write(operation+'\\n')\n"
        "if operation=='capabilities': print(json.dumps({'detector':'worktrees-monitor-control-v1','revision':1,'inspect':True,'apply':True}))\n"
        "elif operation=='inspect': print(json.dumps({'findings':[]}))\n"
        "else:\n selected=json.load(sys.stdin)\n print(json.dumps({'actions':[{'resource':v['resource'],'status':'applied','reason':'native-confirmed'} for v in selected]}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(engine_client, "installed_engine_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: pytest.fail("looked up a provisioning command"))
    adapter = WorktreesDaemons()
    assert adapter.inspect(tmp_path, tmp_path) == []
    findings = [{"resource": "first"}, {"resource": "second"}]
    result = adapter.apply_many(tmp_path, tmp_path, findings)
    assert [item["resource"] for item in result] == ["first", "second"]
    assert log.read_text().splitlines() == ["capabilities", "inspect", "apply"]


def test_missing_native_provider_does_not_fall_back_to_path_or_provision(monkeypatch):
    monkeypatch.setattr(engine_client, "installed_engine_command", lambda: None)
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: pytest.fail("provisioned"))
    with pytest.raises(ValueError, match="never provisions"):
        WorktreesDaemons()


def test_old_provider_is_explicitly_unsupported_without_inventory_or_effect(monkeypatch):
    calls = []
    monkeypatch.setattr(engine_client, "installed_engine_command", lambda: ["selected"])
    monkeypatch.setattr(engine_client, "run_json", lambda _project, args, **kwargs:
                        calls.append(args) or {"status": "unavailable"})
    with pytest.raises(ValueError, match="does not support"):
        WorktreesDaemons()
    assert len(calls) == 1 and calls[0][1] == "capabilities"


@pytest.mark.parametrize("field,value", [
    ("payload_root", None), ("payload_root", []), ("payload_root", "relative"),
    ("context", ""), ("context", {}), ("context", "relative"),
    ("durable_home", None), ("durable_home", "relative"),
    ("marketplace_id", ""), ("marketplace_id", 42),
    ("namespace_generation", True), ("namespace_generation", "1"),
    ("install_generation", None), ("install_generation", -1),
    ("activation_generation", False), ("activation_generation", 1.5),
    ("maintenance_token", []), ("maintenance_token", ""),
])
def test_index_selection_rejects_malformed_fields_before_loading_owner(tmp_path, monkeypatch, field, value):
    from worktree_manager import cleanup_cli, historical_registrations

    monkeypatch.setattr(historical_registrations, "_owner", lambda *_: pytest.fail("loaded native owner"))
    selected = {"payload_root": str(tmp_path), "context": str(tmp_path / "context.json"),
                "durable_home": str(tmp_path), "marketplace_id": "example-cell",
                "namespace_generation": 1, "install_generation": 1, "activation_generation": 1}
    selected[field] = value
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(selected), encoding="utf-8")
    with pytest.raises(ValueError, match="Index selection"):
        IndexRegistrations(path)
    report = cleanup_cli.inspect({"component": "agent-index", "selection": str(path), "root": str(tmp_path)})
    assert report["findings"] == []
    assert len(report["errors"]) == 1 and report["errors"][0]["family"] == "provider"


def test_index_selection_requires_complete_object_and_preserves_valid_selection(tmp_path):
    path = tmp_path / "selection.json"
    for value in ({"payload_root": None}, {}, [], None):
        path.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(ValueError, match="Index selection"):
            IndexRegistrations(path)
    selected = {"payload_root": str(tmp_path), "context": str(tmp_path / "context.json"),
                "durable_home": str(tmp_path), "marketplace_id": "example-cell",
                "namespace_generation": 1, "install_generation": 1, "activation_generation": 1}
    path.write_text(json.dumps(selected), encoding="utf-8")
    assert IndexRegistrations(path).selection == selected
