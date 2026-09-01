"""CLI tests for ``agent-machines restore`` output surfacing.

Covers issue: restore must (a) surface a module's captured stdout in human mode
(by default in a dry-run) and (b) actually emit JSON under ``--json`` -- both were
previously dropped, leaving ``restore --only <module>`` dry-runs unreviewable.
"""

from __future__ import annotations

import json

from agent_machines import __main__ as cli
from agent_machines import modules, reconcile, resources
from agent_machines.surfaces import SurfaceResult
from agent_machines.surfaces._common import SurfaceStateError


def _fake_result(stdout: str) -> reconcile.RestoreResult:
    mr = modules.ModuleResult(
        name="probe", source_repo="acme", ran=True, dry_run=True,
        returncode=0, command=["x"], stdout_tail=stdout,
    )
    plan = reconcile.Plan(
        machine="box", surfaces=[], drift_key="k", package_names=[],
        modules=[{"name": "probe", "source_repo": "acme"}],
    )
    return reconcile.RestoreResult(plan=plan, surface_results=[], module_results=[mr])


def _patch(monkeypatch, result: reconcile.RestoreResult) -> None:
    monkeypatch.setattr(
        cli,
        "_collect_reconcile_packages",
        lambda args, machine: ([], "repo:acme"),
    )
    monkeypatch.setattr(cli._reconcile, "resolve_union", lambda packages, machine: [])
    monkeypatch.setattr(cli._validator, "validate", lambda resolved, *a, **k: [])
    monkeypatch.setattr(cli._reconcile, "restore", lambda *a, **k: result)


def test_dryrun_surfaces_module_stdout(monkeypatch, capsys):
    _patch(monkeypatch, _fake_result("[OK] did the thing\n[PLAN] would do more"))
    rc = cli.main(["restore", "--machine", "box", "--only", "probe"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "module probe <- acme: ok" in out
    assert "[OK] did the thing" in out
    assert "[PLAN] would do more" in out


def test_apply_is_terse_without_verbose(monkeypatch, capsys):
    result = _fake_result("[CHANGE] mutated")
    for m in result.module_results:
        m.dry_run = False
    _patch(monkeypatch, result)
    rc = cli.main(["restore", "--machine", "box", "--only", "probe", "--apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "module probe <- acme: ok" in out
    assert "[CHANGE] mutated" not in out  # hidden without --verbose in apply


def test_apply_verbose_surfaces_module_stdout(monkeypatch, capsys):
    result = _fake_result("[CHANGE] mutated")
    for m in result.module_results:
        m.dry_run = False
    _patch(monkeypatch, result)
    rc = cli.main(["restore", "--machine", "box", "--only", "probe", "--apply", "--verbose"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[CHANGE] mutated" in out


def test_json_emits_structured_result_with_stdout(monkeypatch, capsys):
    _patch(monkeypatch, _fake_result("[OK] did the thing"))
    rc = cli.main(["restore", "--machine", "box", "--only", "probe", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)  # must be valid JSON, not the human summary
    assert data["ok"] is True
    assert data["modules"][0]["name"] == "probe"
    assert data["modules"][0]["stdout_tail"] == "[OK] did the thing"
    assert "plan" in data
    assert data["scope"] == "repo:acme"


def test_restore_prints_exact_plugin_removal(monkeypatch, capsys):
    result = _fake_result("")
    result.module_results = []
    result.surface_results = [
        SurfaceResult(
            surface="copilot.settings",
            file="settings.json",
            changed=True,
            dry_run=True,
            changes=[
                {
                    "op": "remove",
                    "key": "enabledPlugins",
                    "items": ["optional@example-marketplace"],
                    "contributors": ["example/activation"],
                }
            ],
        )
    ]
    _patch(monkeypatch, result)
    rc = cli.main(["restore", "--machine", "box"])
    assert rc == 0
    assert (
        "- enabledPlugins.optional@example-marketplace"
        in capsys.readouterr().out
    )


def test_restore_reports_malformed_surface_without_traceback(monkeypatch, capsys):
    _patch(monkeypatch, _fake_result(""))

    def fail(*args, **kwargs):
        raise SurfaceStateError("settings.json: enabledPlugins must be an object")

    monkeypatch.setattr(cli._reconcile, "restore", fail)
    rc = cli.main(["restore", "--machine", "box"])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err == (
        "restore refused: settings.json: enabledPlugins must be an object\n"
    )


def _resource_error_result() -> reconcile.RestoreResult:
    result = _fake_result("")
    result.module_results = []
    result.resource_results = [
        resources.ResourceResult(
            type="package",
            id="example.package",
            changed=True,
            dry_run=False,
            action="error",
            detail="install failed",
        )
    ]
    return result


def test_resource_error_makes_json_not_ok_and_exit_nonzero(monkeypatch, capsys):
    _patch(monkeypatch, _resource_error_result())
    rc = cli.main(["restore", "--machine", "box", "--apply", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc != 0
    assert data["ok"] is False
    assert data["resources"][0]["status"] == "error"


def test_resource_error_makes_human_restore_exit_nonzero(monkeypatch, capsys):
    _patch(monkeypatch, _resource_error_result())
    rc = cli.main(["restore", "--machine", "box", "--apply"])
    captured = capsys.readouterr()
    assert rc != 0
    assert "resource package:example.package: ERROR install failed" in captured.err
    assert "did error" not in captured.out


def _resource_deferred_result() -> reconcile.RestoreResult:
    result = _fake_result("")
    result.module_results = []
    result.resource_results = [
        resources.ResourceResult(
            type="package",
            id="example.package",
            changed=False,
            dry_run=True,
            action="defer",
            detail="deferred update from 1.0.0 to 2.0.0",
            deferred_reason="process guard matched running: example.exe",
            commands=[["winget", "upgrade", "--id", "example.package"]],
        )
    ]
    return result


def test_resource_deferral_is_explicit_in_json_and_successful(monkeypatch, capsys):
    _patch(monkeypatch, _resource_deferred_result())
    rc = cli.main(["restore", "--machine", "box", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["ok"] is True
    assert data["resources"][0]["status"] == "deferred"
    assert data["resources"][0]["deferred_reason"] == (
        "process guard matched running: example.exe"
    )


def test_resource_deferral_is_explicit_in_human_dry_run(monkeypatch, capsys):
    _patch(monkeypatch, _resource_deferred_result())
    rc = cli.main(["restore", "--machine", "box"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "resource package:example.package: deferred" in captured.out
    assert "process guard matched running: example.exe" in captured.out
    assert "$ winget upgrade --id example.package" in captured.out
