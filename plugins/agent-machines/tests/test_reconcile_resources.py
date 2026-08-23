"""Reconcile-level tests: resources flow through plan / restore / JSON.

These verify the engine wiring (not the handler internals, which
``test_resources.py`` covers): resolved resources appear in the plan, a restore
applies them between surfaces and modules, ``--only`` selects a resource, and
the JSON payload carries a ``resources`` list.
"""

from __future__ import annotations

from pathlib import Path

from agent_machines import resources as R
from agent_machines.manifest import load_package
from agent_machines.reconcile import plan, restore, restore_result_to_dict

from ._helpers import base_package, write_package


def _pkg(tmp_path: Path, name: str, resources: list[dict]):
    data = base_package(name=name, gate=["box-1"], resources=resources)
    path = write_package(tmp_path / name.replace("/", "_"), "pkg.yaml", data)
    return load_package(path, source_repo=name.split("/")[0])


def test_plan_lists_resolved_resources(tmp_path):
    pkg = _pkg(tmp_path, "acme/a", [
        {"type": "file", "id": "conf", "path": "$HOME/.psmux.conf",
         "strategy": "ensure-present", "content": "x\n"},
    ])
    p = plan([pkg], "box-1", "windows")
    assert any(r["type"] == "file" and r["id"] == "conf" for r in p.resources)


def test_restore_applies_file_resource(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    pkg = _pkg(tmp_path, "acme/a", [
        {"type": "file", "id": "conf", "path": "$HOME/.psmux.conf",
         "strategy": "ensure-present", "content": "created\n"},
    ])
    result = restore([pkg], "box-1", dry_run=False, plat="windows", home=tmp_path)
    assert any(r.type == "file" and r.changed for r in result.resource_results)
    assert (tmp_path / ".psmux.conf").read_text(encoding="utf-8") == "created\n"


def test_restore_dry_run_does_not_apply(tmp_path):
    pkg = _pkg(tmp_path, "acme/a", [
        {"type": "file", "id": "conf", "path": "$HOME/.psmux.conf",
         "strategy": "enforce", "content": "y\n"},
    ])
    result = restore([pkg], "box-1", dry_run=True, plat="windows", home=tmp_path)
    fr = next(r for r in result.resource_results if r.type == "file")
    assert fr.dry_run and not (tmp_path / ".psmux.conf").exists()


def test_only_selects_resource_and_skips_modules(tmp_path, monkeypatch):
    # A module that would run on box-1; --only a resource id must skip it.
    called = {"module": False}
    monkeypatch.setattr(R, "apply_resources",
                        lambda *a, **k: [_stub_result()])

    def _fake_run_modules(*a, **k):
        called["module"] = True
        return []

    from agent_machines import modules as _modules
    monkeypatch.setattr(_modules, "run_modules", _fake_run_modules)

    pkg = _pkg(tmp_path, "acme/a", [
        {"type": "file", "id": "conf", "path": "$HOME/.psmux.conf",
         "strategy": "enforce", "content": "y\n"},
    ])
    restore([pkg], "box-1", dry_run=True, plat="windows", home=tmp_path,
            only=["file:conf"])
    assert called["module"] is False  # module runner skipped for a resource-only --only


def test_restore_json_includes_resources(tmp_path):
    pkg = _pkg(tmp_path, "acme/a", [
        {"type": "file", "id": "conf", "path": "$HOME/.psmux.conf",
         "strategy": "ensure-present", "content": "z\n"},
    ])
    result = restore([pkg], "box-1", dry_run=True, plat="windows", home=tmp_path)
    payload = restore_result_to_dict(result)
    assert "resources" in payload
    assert payload["plan"]["resources"][0]["id"] == "conf"


def _stub_result() -> R.ResourceResult:
    return R.ResourceResult("file", "conf", changed=False, dry_run=True, action="none")
