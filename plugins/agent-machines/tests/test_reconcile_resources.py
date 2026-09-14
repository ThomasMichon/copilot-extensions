"""Reconcile-level tests: resources flow through plan / restore / JSON.

These verify the engine wiring (not the handler internals, which
``test_resources.py`` covers): resolved resources appear in the plan, a restore
applies them between surfaces and modules, ``--only`` selects a resource, and
the JSON payload carries a ``resources`` list.
"""

from __future__ import annotations

from pathlib import Path

from agent_machines import reconcile as reconcile_module
from agent_machines import resources as R
from agent_machines.manifest import load_package
from agent_machines.reconcile import plan, restore, restore_result_to_dict
from agent_machines.surfaces import SurfaceResult

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


def test_maintenance_safe_restore_still_applies_manage_entries(tmp_path, monkeypatch):
    called: dict[str, object] = {}

    def fake_apply_surfaces(resolved, *, home=None, dry_run=True, only=None):
        called["packages"] = [pkg.name for pkg in resolved]
        return [
            SurfaceResult(
                surface="copilot.settings",
                file="settings.json",
                changed=True,
                dry_run=dry_run,
                changes=[{"key": "model", "before": "old", "after": "new"}],
            )
        ]

    monkeypatch.setattr("agent_machines.reconcile.apply_surfaces", fake_apply_surfaces)
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "file", "id": "conf", "path": "$HOME/.psmux.conf",
          "strategy": "enforce", "content": "x\n"}],
    )
    result = restore(
        [pkg],
        "box-1",
        dry_run=False,
        plat="windows",
        home=tmp_path,
        maintenance_safe=True,
    )
    assert called["packages"] == ["acme/a"]
    assert result.surface_results[0].surface == "copilot.settings"
    assert result.resource_results[0].status == "skipped"


def test_runtime_spot_check_repairs_only_after_failed_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(reconcile_module.shutil, "which", lambda binary: "pwsh" if binary == "pwsh" else None)
    payload = tmp_path / ".copilot" / "installed-plugins" / "copilot-extensions" / "agent-machines"
    (payload / "scripts").mkdir(parents=True)
    (payload / "plugin.json").write_text(
        '{"name":"agent-machines","runtimeScope":"machine-gated","installerReadiness":"installer-readiness.json"}',
        encoding="utf-8",
    )
    (payload / "scripts" / "init.ps1").write_text("# noop\n", encoding="utf-8")
    state = {"healthy": False}
    calls: list[list[str]] = []

    def runner(argv, *, timeout):
        calls.append(list(argv))
        if argv == ["agent-machines", "installer-readiness"]:
            return R.RunOutcome(0, "", "") if state["healthy"] else R.RunOutcome(1, "", "broken")
        if any(str(token).endswith("init.ps1") for token in argv):
            state["healthy"] = True
            return R.RunOutcome(0, "", "")
        raise AssertionError(argv)

    repaired = reconcile_module.runtime_spot_check_results(
        home=tmp_path, plat="windows", dry_run=False, runner=runner
    )
    assert repaired[0].status == "changed"
    assert repaired[0].action == "repair"
    assert calls == [
        ["agent-machines", "installer-readiness"],
        ["pwsh", "-File", str(payload / "scripts" / "init.ps1")],
        ["agent-machines", "installer-readiness"],
    ]

    calls.clear()
    already_healthy = reconcile_module.runtime_spot_check_results(
        home=tmp_path, plat="windows", dry_run=False, runner=runner
    )
    assert already_healthy[0].status == "ok"
    assert already_healthy[0].action == "none"
    assert calls == [["agent-machines", "installer-readiness"]]


def _stub_result() -> R.ResourceResult:
    return R.ResourceResult("file", "conf", changed=False, dry_run=True, action="none")


def test_runtime_command_runner_resolves_pathext_shim(monkeypatch):
    """Regression: `_runtime_command_runner`'s default (non-test-injected)
    subprocess call must resolve a bare plugin binstub name (a `.cmd` shim on
    Windows) the same way self_update.default_command_runner does, or the
    runtime spot-check silently fails with WinError 2 on every real Windows
    machine despite the binstub genuinely being on PATH."""
    captured: dict[str, list[str]] = {}

    def fake_which(name):
        return f"C:\\Users\\tmichon\\.local\\bin\\{name}.cmd" if name == "agent-machines" else None

    def fake_run(argv, **kwargs):
        captured["argv"] = argv

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Proc()

    monkeypatch.setattr(reconcile_module.shutil, "which", fake_which)
    monkeypatch.setattr(reconcile_module.subprocess, "run", fake_run)
    reconcile_module._runtime_command_runner(["agent-machines", "installer-readiness"], timeout=60)
    assert captured["argv"][0] == "C:\\Users\\tmichon\\.local\\bin\\agent-machines.cmd"

