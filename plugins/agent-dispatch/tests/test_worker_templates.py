"""Provider discovery is inert and cannot weaken existing worker authority."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

from dropin_registry import EntryDecision, ScanAuthority
from plugin_activation import ActivationReport, ActivePlugin, ActivePluginRoot
import pytest

from agent_dispatch import registrar_discovery as rd, worker_templates as wt
from agent_dispatch.worker_instances import digest, save_instance, templates
from agent_dispatch.workers_catalog import catalog


def empty_activation():
    return ActivationReport(authority=ScanAuthority.COMPLETE, decisions={})


def repository_map(*entries):
    return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
        "repository_map_version": 1, "inventory_authority": "complete",
        "projection": {"quality": "current"}, "repos": entries,
    }))


def registered_root(name, root, repo_class="reference"):
    return {"name": name, "class": repo_class, "registration": "ready", "path": str(root)}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DIR", str(tmp_path / "registrar"))
    monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DROPINS_DIR", str(tmp_path / "dropins"))
    monkeypatch.setenv("AGENT_DISPATCH_OVERRIDES", str(tmp_path / "overrides.json"))
    monkeypatch.setattr("plugin_activation.resolve_active_plugins", empty_activation)
    monkeypatch.setattr("agent_dispatch.workers_catalog._observation", lambda: {"known": False})
    monkeypatch.setattr(
        wt, "run_agent_worktrees_capture",
        lambda *args, **kwargs: repository_map(),
    )


def write_index(root, *, index=None, entries=None):
    index = index or root / "dispatch-templates.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    fragment = index.parent / "recipes" / "source.json"
    fragment.parent.mkdir(exist_ok=True)
    fragment.write_text(json.dumps({"kind": "emitter", "spec": {"command": ["never-execute"]}}), encoding="utf-8")
    entry = {
        "id": "record-source", "title": "Record source", "description": "Adapter-owned records.",
        "kind": "emitter", "declaration": "recipes/source.json",
        "requirements": ["Choose a stable record identity."],
    }
    index.write_text(json.dumps({
        "schema": "agent-dispatch.template-index", "version": 1,
        "templates": entries if entries is not None else [entry],
    }), encoding="utf-8")
    return index, entry


def plugin_report(*roots):
    source = "example-provider@example-market"
    plugin = ActivePlugin(
        source, "example-provider", "example-market", roots[0].root,
        tuple(dict.fromkeys(scope for root in roots for scope in root.scopes)), roots,
    )
    return ActivationReport(
        authority=ScanAuthority.COMPLETE, decisions={source: EntryDecision.active(plugin)},
    )


def bytes_at(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_multi_root_templates_keep_identity_scope_revision_and_no_executables(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    write_index(first)
    write_index(second)
    report = plugin_report(
        ActivePluginRoot(first, ("global",), "installed"),
        ActivePluginRoot(second, ("project:sample",), "local"),
    )
    before = bytes_at(tmp_path)
    rows, summary = wt.discover_templates(report)
    assert summary["state"] == "complete" and summary["provider_templates"] == 2
    assert len({row["id"] for row in rows}) == len({row["revision"] for row in rows}) == 2
    assert [row["repo_scopes"] for row in rows] == [["global"], ["project:sample"]]
    assert all(row["instantiable"] == "true" and row["editable"] == "false" for row in rows)
    assert all(row["dependency_status"] == "inputs-required" for row in rows)
    assert "never-execute" not in json.dumps(rows)
    assert rows == wt.discover_templates(report)[0]
    assert before == bytes_at(tmp_path)
    index, entry = write_index(first)
    changed = json.loads(index.read_text())
    changed["templates"][0]["description"] = "Changed metadata."
    index.write_text(json.dumps(changed), encoding="utf-8")
    updated = wt.discover_templates(report)[0]
    assert updated[0]["id"] == rows[0]["id"] and updated[0]["revision"] != rows[0]["revision"]
    assert updated[1] == rows[1]


@pytest.mark.parametrize("problem", [
    "version", "version-bool", "schema", "fields", "duplicate", "kind",
    "path-escape", "absolute", "missing", "not-object", "oversize", "requirements", "nul-text",
])
def test_invalid_contributions_keep_healthy_peers_and_source_bytes(tmp_path, problem):
    root = tmp_path / "provider"
    index, entry = write_index(root)
    data = json.loads(index.read_text())
    good = copy.deepcopy(entry)
    good["id"] = "healthy"
    if problem == "version":
        data["version"] = 2
    elif problem == "version-bool":
        data["version"] = True
    elif problem == "schema":
        data["schema"] = "unknown"
    elif problem == "fields":
        data["unknown"] = True
    elif problem == "duplicate":
        data["templates"].append(copy.deepcopy(entry))
    elif problem == "kind":
        data["templates"][0]["kind"] = "workflow-engine"
    elif problem == "path-escape":
        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        data["templates"][0]["declaration"] = "../outside.json"
    elif problem == "absolute":
        data["templates"][0]["declaration"] = str(index)
    elif problem == "missing":
        data["templates"][0]["declaration"] = "missing.json"
    elif problem == "requirements":
        data["templates"][0]["requirements"] = ["x" * 1001]
    elif problem == "nul-text":
        data["templates"][0]["description"] = "Invalid\0description"
    else:
        bad = root / "bad.json"
        bad.write_text("[]" if problem == "not-object" else " " * (wt._MAX_BYTES + 1), encoding="utf-8")
        data["templates"][0]["declaration"] = "bad.json"
    data["templates"].append(good)
    index.write_text(json.dumps(data), encoding="utf-8")
    before = bytes_at(tmp_path)
    rows, summary = wt.discover_templates(plugin_report(ActivePluginRoot(root, ("global",), "installed")))
    assert summary["state"] == "partial" and summary["findings"]
    assert [row["title"] for row in rows] == ([] if problem in {"version", "version-bool", "schema", "fields"} else ["Record source"])
    if rows:
        assert rows[0]["id"].endswith(":healthy")
    assert bytes_at(tmp_path) == before


def test_disabled_plugin_and_stale_root_do_not_create_templates(tmp_path):
    root = tmp_path / "disabled"
    write_index(root)
    assert wt.discover_templates(empty_activation())[0] == []
    rows, summary = wt.discover_templates(plugin_report(ActivePluginRoot(root / "missing", ("global",), "installed")))
    assert rows == [] and summary["state"] == "partial"
    assert summary["findings"][0]["reason"] == "invalid-index"


def test_repository_layers_and_user_local_reuse_existing_roots(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    canonical = root / ".copilot-extensions" / "agent-dispatch"
    legacy = root / ".agent-dispatch"
    write_index(root, index=legacy / "templates" / "index.json")
    canonical_index, _ = write_index(root, index=canonical / "templates" / "index.json")
    overlay = canonical / "marketplaces" / "example-cell" / "templates" / "index.json"
    write_index(root, index=overlay)
    local = rd.registrar_dir() / "templates" / "index.json"
    write_index(rd.registrar_dir(), index=local)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", json.dumps({"marketplaceId": "example-cell"}))
    calls = []
    def read_map(*args, **kwargs):
        calls.append((args, kwargs))
        return repository_map(registered_root("library", root))
    monkeypatch.setattr(wt, "run_agent_worktrees_capture", read_map)
    monkeypatch.setattr(
        "agent_dispatch.repository_resources._registered_repository",
        lambda *a, **kw: pytest.fail("map consumer performed a per-repository lookup"),
    )
    before = bytes_at(tmp_path)
    rows, summary = wt.discover_templates(empty_activation())
    assert summary["state"] == "complete" and len(rows) == 3
    assert calls == [(("repos", "map", "--json"), {"timeout": 5})]
    assert {row["source"]["index"] for row in rows} == {str(canonical_index), str(overlay), str(local)}
    assert len({row["id"] for row in rows}) == 3
    assert before == bytes_at(tmp_path) and not rd.pointers_file().exists()


@pytest.mark.parametrize("broken_layer,healthy_layer", [
    ("canonical", "legacy"), ("canonical-parent", "legacy"),
    ("canonical-grandparent", "legacy"), ("canonical-loop-parent", "legacy"),
    ("canonical-file-parent", "legacy"), ("legacy", "overlay"),
    ("legacy-parent", "overlay"), ("overlay", "canonical"),
    ("overlay-parent", "canonical"), ("overlay-grandparent", "canonical"),
    ("shadowed-legacy", "canonical"), (None, "overlay"),
])
def test_unavailable_repository_layers_are_not_absent_optional_layers(monkeypatch, tmp_path, broken_layer, healthy_layer):
    root = tmp_path / "repo"
    canonical = root / ".copilot-extensions" / "agent-dispatch" / "templates"
    legacy = root / ".agent-dispatch" / "templates"
    overlay = canonical.parent / "marketplaces" / "example-cell" / "templates"
    paths = {
        "canonical": canonical, "canonical-parent": canonical.parent,
        "canonical-grandparent": canonical.parent.parent,
        "canonical-loop-parent": canonical.parent, "canonical-file-parent": canonical.parent,
        "legacy": legacy, "legacy-parent": legacy.parent,
        "overlay": overlay, "overlay-parent": overlay.parent,
        "overlay-grandparent": overlay.parent.parent, "shadowed-legacy": legacy,
    }
    healthy = paths[healthy_layer]
    write_index(root, index=healthy / "index.json")
    if broken_layer:
        target = paths[broken_layer]
        target.parent.mkdir(parents=True, exist_ok=True)
        if broken_layer == "canonical-file-parent":
            target.write_text("not a directory", encoding="utf-8")
        else:
            target.symlink_to(
                target if broken_layer == "canonical-loop-parent" else root / "missing-layer",
                target_is_directory=True,
            )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", json.dumps({"marketplaceId": "example-cell"}))
    monkeypatch.setattr(wt, "run_agent_worktrees_capture", lambda *a, **kw: repository_map(
        registered_root("library", root),
    ))
    before = bytes_at(tmp_path)
    rows, summary = wt.discover_templates(empty_activation())
    assert len(rows) == 1
    unavailable = broken_layer not in {None, "shadowed-legacy"}
    assert summary["state"] == ("partial" if unavailable else "complete")
    expected_sources = [paths[broken_layer.split("-")[0]]] if unavailable else []
    if broken_layer in {"canonical-parent", "canonical-grandparent", "canonical-loop-parent", "canonical-file-parent"}:
        expected_sources.append(overlay)
    assert sorted(summary["findings"], key=lambda finding: finding["source"]) == sorted([{
        "source": str(path), "reason": "invalid-index",
        "detail": "Template layer must be a directory.",
    } for path in expected_sources], key=lambda finding: finding["source"])
    assert wt.layered_repo_surface_dirs(root, "templates") == [healthy]
    assert bytes_at(tmp_path) == before and not rd.pointers_file().exists()


@pytest.mark.parametrize("broken", ["ancestor", "root", "templates", "index.json", None])
def test_user_local_broken_entries_remain_visible_not_optional(monkeypatch, tmp_path, broken):
    if broken == "ancestor":
        monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DIR", str(tmp_path / "local-storage" / "nested" / "registrar"))
    base = rd.registrar_dir()
    target = {
        "root": base, "templates": base / "templates",
        "index.json": base / "templates" / "index.json",
        "ancestor": tmp_path / "local-storage",
    }.get(broken)
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(tmp_path / "missing-entry", target_is_directory=broken != "index.json")
    rows, summary = wt.discover_templates(empty_activation())
    assert rows == []
    assert summary["state"] == ("partial" if broken else "complete")
    assert len(summary["findings"]) == (1 if broken else 0)
    if broken:
        assert summary["findings"][0]["reason"] == "invalid-index"
        assert target.is_symlink() and not target.exists()


@pytest.mark.parametrize("denied,probe", [
    ("configuration", "all"), ("canonical", "stat"),
    ("legacy", "all"), ("overlay", "stat"),
])
def test_permission_denied_layers_keep_readable_peers(monkeypatch, tmp_path, denied, probe):
    root = tmp_path / "repo"
    canonical = root / ".copilot-extensions" / "agent-dispatch" / "templates"
    legacy = root / ".agent-dispatch" / "templates"
    overlay = canonical.parent / "marketplaces" / "example-cell" / "templates"
    healthy = legacy if denied in {"configuration", "canonical"} else overlay if denied == "legacy" else canonical
    write_index(root, index=healthy / "index.json")
    target = {"configuration": canonical.parent, "canonical": canonical, "legacy": legacy, "overlay": overlay}[denied]
    if probe == "stat":
        target.mkdir(parents=True, exist_ok=True)
    original_stat = os.stat

    def inaccessible(path, *args, **kwargs):
        candidate = Path(os.fsdecode(path)) if not isinstance(path, int) else None
        if candidate is not None and candidate.is_relative_to(target) and (
            probe == "all" or kwargs.get("follow_symlinks", True)
        ):
            raise PermissionError("fixture layer is unreadable")
        return original_stat(path, *args, **kwargs)

    def inaccessible_lstat(path, *args, **kwargs):
        return inaccessible(path, *args, follow_symlinks=False, **kwargs)

    def inaccessible_is_dir(path):
        try:
            return stat.S_ISDIR(inaccessible(path).st_mode)
        except OSError:
            return False

    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", json.dumps({"marketplaceId": "example-cell"}))
    monkeypatch.setattr(wt, "run_agent_worktrees_capture", lambda *a, **kw: repository_map(
        registered_root("library", root),
    ))
    before = bytes_at(tmp_path)
    with monkeypatch.context() as denied_probe:
        # Windows native directory probes can bypass both Path.stat and os.stat.
        denied_probe.setattr(os, "stat", inaccessible)
        denied_probe.setattr(os, "lstat", inaccessible_lstat)
        denied_probe.setattr(Path, "is_dir", inaccessible_is_dir)
        rows, summary = wt.discover_templates(empty_activation())
    assert len(rows) == 1 and rows[0]["source"]["index"] == str(healthy / "index.json")
    assert summary["state"] == "partial"
    expected = [canonical, overlay] if denied == "configuration" else [target]
    assert {finding["source"] for finding in summary["findings"]} == {str(path) for path in expected}
    assert all(finding["reason"] == "invalid-index" for finding in summary["findings"])
    assert bytes_at(tmp_path) == before and not rd.pointers_file().exists()


def test_inert_errors_do_not_disable_existing_controls_and_activation_is_lazy(monkeypatch, tmp_path):
    directory = tmp_path / "source"
    directory.mkdir()
    (directory / "pool.json").write_text('{"name":"existing-pool"}', encoding="utf-8")
    rd.add_pointer("source", directory)
    (rd.registrar_dir() / "templates").mkdir()
    (rd.registrar_dir() / "templates" / "index.json").write_text("invalid", encoding="utf-8")
    calls = []
    def resolve():
        calls.append("activation")
        return ActivationReport(authority=ScanAuthority.INDETERMINATE, decisions={})
    monkeypatch.setattr("plugin_activation.resolve_active_plugins", resolve)
    before = bytes_at(tmp_path)
    data = catalog()
    assert calls == ["activation"]
    assert data["summary"]["errors"] == []
    assert data["summary"]["template_discovery"]["state"] == "partial"
    template_errors = [row for row in data["entries"] if row.get("finding_scope") == "templates"]
    assert template_errors and all(row["group"] == "Discovery errors" for row in template_errors)
    assert next(row for row in data["entries"] if row["title"] == "existing-pool")["pool_toggleable"] == "true"
    assert next(row for row in data["entries"] if row["id"] == "template:worker-pool")["instantiable"] == "true"
    assert before == bytes_at(tmp_path)
    monkeypatch.setattr("agent_dispatch.registrar_registry.resolve_active_plugins", lambda: pytest.fail("unexpected activation read"))
    rd.RegistrarSources(activation_source=lambda: pytest.fail("no candidate needs activation")).refresh(emit_warnings=False)


def test_standalone_missing_repository_integration_is_partial_not_healthy_empty(monkeypatch):
    monkeypatch.setattr(wt, "run_agent_worktrees_capture", lambda *args, **kwargs: None)
    data = catalog()
    summary = data["summary"]["template_discovery"]
    assert summary["state"] == "partial" and summary["findings"][0]["source"] == "registered-repositories"
    assert any(row["id"] == "template:worker-pool" for row in data["entries"])
    assert data["summary"]["errors"] == []
    assert not rd.registrar_dir().exists()


def test_external_probe_error_text_is_bounded_and_nul_free(monkeypatch):
    monkeypatch.setattr(wt, "run_agent_worktrees_capture", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout="", stderr="Probe\0failed" + "x" * 5000,
    ))
    data = catalog()
    errors = [row for row in data["entries"] if row.get("finding_scope") == "templates"]
    assert errors and all("\0" not in row["description"] and len(row["description"]) < 9000 for row in errors)


def test_template_resolution_error_text_is_bounded_and_nul_free(monkeypatch, tmp_path):
    from agent_dispatch.registrar import RegistrarError

    root = tmp_path / "provider"
    write_index(root)
    def unavailable(*args):
        raise RegistrarError("Fixture\0unavailable" + "x" * 5000)
    monkeypatch.setattr("agent_dispatch.worker_template_materialization.inspect_template", unavailable)
    report = plugin_report(ActivePluginRoot(root, ("global",), "installed"))
    row = wt.discover_templates(report)[0][0]
    assert row["instantiable"] == "false" and "fields" not in row
    assert len(row["unsupported_reason"]) <= 4096 and "\0" not in row["unsupported_reason"]


def test_new_provider_id_cannot_be_saved_or_activated(tmp_path):
    root = tmp_path / "provider"
    write_index(root)
    row = wt.discover_templates(plugin_report(ActivePluginRoot(root, ("global",), "installed")))[0][0]
    from agent_dispatch.registrar import RegistrarError
    with pytest.raises(RegistrarError, match="absent or changed"):
        save_instance(row["id"], row["revision"], {
            "name": "never-saved", "declaration": {"kind": "emitter"}, "repo_root": "",
        })
    assert not rd.registrar_dir().exists()
    assert digest(templates()["worker-pool"]) != row["revision"]


def test_real_cli_first_touch_inert_catalog_is_read_only(tmp_path):
    local = rd.registrar_dir() / "templates" / "index.json"
    write_index(rd.registrar_dir(), index=local)
    before = bytes_at(tmp_path)
    code = """
import runpy, sys
from types import SimpleNamespace
from agent_dispatch import worker_templates
worker_templates.run_agent_worktrees_capture = lambda *a, **k: SimpleNamespace(returncode=0, stdout='{"repository_map_version":1,"projection":{"quality":"current"},"inventory_authority":"complete","repos":[]}', stderr='')
sys.argv = ['agent-dispatch', 'workers', 'catalog']
runpy.run_module('agent_dispatch', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=20,
        env={**os.environ, "AGENT_DISPATCH_NO_AUTOSTART": "1"},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    row = next(row for row in data["entries"] if row["id"].startswith("template:provider:"))
    assert row["instantiable"] == "true" and row["editable"] == "false"
    assert row["resolution"] == "recipe-resolved" and row["fields"]
    assert row["fields"][1]["default"] == "{}"
    assert "Save alone never publishes" in row["card"]["body"]
    assert bytes_at(tmp_path) == before


def test_duplicate_json_fields_are_explicit_errors(tmp_path):
    root = tmp_path / "provider"
    index, _ = write_index(root)
    index.write_text('{"schema":"unknown","schema":"agent-dispatch.template-index","version":1,"templates":[]}', encoding="utf-8")
    rows, summary = wt.discover_templates(plugin_report(ActivePluginRoot(root, ("global",), "installed")))
    assert rows == [] and "duplicate JSON field" in summary["findings"][0]["detail"]


def test_bad_repository_inventory_row_and_duplicate_names_keep_healthy_sources(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    write_index(root, index=root / ".copilot-extensions" / "agent-dispatch" / "templates" / "index.json")
    monkeypatch.setattr(wt, "run_agent_worktrees_capture", lambda *a, **kw: repository_map(
        False, registered_root("healthy", root),
        registered_root("duplicate", root), registered_root("duplicate", root),
    ))
    rows, summary = wt.discover_templates(empty_activation())
    assert len(rows) == 1 and rows[0]["source"]["provider"] == "repo:healthy"
    assert summary["state"] == "partial"
    assert {finding["reason"] for finding in summary["findings"]} == {"invalid", "ambiguous"}


def test_plugin_source_and_templates_share_one_lazy_activation_report(monkeypatch, tmp_path):
    root = tmp_path / "plugin"
    write_index(root)
    (root / "plugin.json").write_text('{"name":"example-provider","version":"1.0.0"}', encoding="utf-8")
    registrar = root / "registrar"
    registrar.mkdir()
    (registrar / "pool.json").write_text('{"name":"provider-pool"}', encoding="utf-8")
    dropins = Path(os.environ["AGENT_DISPATCH_REGISTRAR_DROPINS_DIR"])
    dropins.mkdir()
    (dropins / "provider.json").write_text(json.dumps({
        "schema_version": 1, "plugin": "example-provider@example-market",
        "plugin_root": str(root), "registrar": "registrar",
    }), encoding="utf-8")
    report = plugin_report(ActivePluginRoot(root, ("global",), "installed"))
    calls = []
    def resolve():
        calls.append("activation")
        return report
    monkeypatch.setattr("plugin_activation.resolve_active_plugins", resolve)
    before = bytes_at(tmp_path)
    data = catalog()
    assert calls == ["activation"] and data["summary"]["errors"] == []
    assert data["summary"]["template_discovery"]["state"] == "complete"
    assert any(row["title"] == "provider-pool" for row in data["entries"])
    assert any(row["id"].startswith("template:provider:") for row in data["entries"])
    assert before == bytes_at(tmp_path)


def test_older_inventory_contract_never_claims_complete_discovery(monkeypatch):
    calls = []
    def inventory(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"repos":[]}', stderr="")
    monkeypatch.setattr(wt, "run_agent_worktrees_capture", inventory)
    rows, summary = wt.discover_templates(empty_activation())
    assert not rows and summary["state"] == "partial"
    assert summary["findings"][0]["reason"] == "inventory-authority-unavailable"
    assert calls == [(("repos", "map", "--json"), {"timeout": 5})]


@pytest.mark.parametrize("change", [
    {"repository_map_version": True},
    {"repository_map_version": 2},
    {"inventory_authority": "advisory"},
    {"projection": {"quality": "stale"}},
    {"availability": {"status": "stale", "reason": "refresh failed"}},
])
def test_stale_or_incompatible_map_never_falls_back_to_point_resolution(monkeypatch, tmp_path, change):
    root = tmp_path / "library"
    write_index(root, index=root / ".copilot-extensions" / "agent-dispatch" / "templates" / "index.json")
    data = json.loads(repository_map(registered_root("library", root)).stdout)
    data.update(change)
    calls = []

    def read_map(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")

    monkeypatch.setattr(wt, "run_agent_worktrees_capture", read_map)
    monkeypatch.setattr(
        "agent_dispatch.repository_resources._registered_repository",
        lambda *a, **kw: pytest.fail("stale map triggered point resolution"),
    )
    rows, summary = wt.discover_templates(empty_activation())
    assert rows == [] and summary["state"] == "partial"
    assert summary["findings"][0]["reason"] == "inventory-authority-unavailable"
    assert calls == [("repos", "map", "--json")]


def test_unavailable_map_entry_keeps_healthy_registered_peer(monkeypatch, tmp_path):
    root = tmp_path / "library"
    write_index(root, index=root / ".copilot-extensions" / "agent-dispatch" / "templates" / "index.json")
    monkeypatch.setattr(wt, "run_agent_worktrees_capture", lambda *a, **kw: repository_map(
        registered_root("library", root),
        {"name": "missing", "class": "worktree", "registration": "unavailable",
         "path": None, "reason": "registered root is absent"},
    ))
    rows, summary = wt.discover_templates(empty_activation())
    assert len(rows) == 1 and rows[0]["source"]["provider"] == "repo:library"
    assert summary["state"] == "partial"
    assert summary["findings"] == [{
        "source": "repo:missing", "reason": "unavailable", "detail": "registered root is absent",
    }]


def test_consumer_name_restrictions_do_not_poison_healthy_map_peers(monkeypatch, tmp_path):
    root = tmp_path / "library"
    write_index(root, index=root / ".copilot-extensions" / "agent-dispatch" / "templates" / "index.json")
    monkeypatch.setattr(wt, "run_agent_worktrees_capture", lambda *a, **kw: repository_map(
        registered_root(".github", root), registered_root("_tools", root),
        registered_root("library", root),
    ))
    rows, summary = wt.discover_templates(empty_activation())
    assert len(rows) == 1 and rows[0]["source"]["provider"] == "repo:library"
    assert summary["state"] == "partial"
    assert len(summary["findings"]) == 2
    assert {finding["reason"] for finding in summary["findings"]} == {"invalid"}


def test_repository_names_do_not_inherit_template_id_length_limit(monkeypatch, tmp_path):
    name = "a" * 81
    root = tmp_path / "library"
    write_index(root, index=root / ".copilot-extensions" / "agent-dispatch" / "templates" / "index.json")
    monkeypatch.setattr(wt, "run_agent_worktrees_capture", lambda *a, **kw: repository_map(
        registered_root(name, root),
    ))
    rows, summary = wt.discover_templates(empty_activation())
    assert summary["state"] == "complete" and len(rows) == 1
    assert rows[0]["source"]["provider"] == f"repo:{name}"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO contract; directory refusal covers nonregular files on Windows")
@pytest.mark.parametrize("target_kind", ["index", "declaration"])
def test_real_cli_refuses_fifo_without_blocking_healthy_templates(tmp_path, target_kind):
    local = rd.registrar_dir() / "templates" / "index.json"
    write_index(rd.registrar_dir(), index=local)
    target = local if target_kind == "index" else local.parent / "recipes" / "source.json"
    target.unlink()
    os.mkfifo(target)
    code = """
import runpy, sys
from types import SimpleNamespace
from agent_dispatch import worker_templates
worker_templates.run_agent_worktrees_capture = lambda *a, **k: SimpleNamespace(returncode=0, stdout='{"repository_map_version":1,"projection":{"quality":"current"},"inventory_authority":"complete","repos":[]}', stderr='')
sys.argv = ['agent-dispatch', 'workers', 'catalog']
runpy.run_module('agent_dispatch', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=10,
        env={**os.environ, "AGENT_DISPATCH_NO_AUTOSTART": "1"},
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["summary"]["template_discovery"]["state"] == "partial"
    assert any("regular file" in finding["detail"] for finding in data["summary"]["template_discovery"]["findings"])
    assert any(row["id"] == "template:worker-pool" for row in data["entries"])


def test_nonregular_json_is_refused_before_open(tmp_path):
    directory = tmp_path / "not-file.json"
    directory.mkdir()
    with pytest.raises(ValueError, match="regular file"):
        wt._json_object(directory)
