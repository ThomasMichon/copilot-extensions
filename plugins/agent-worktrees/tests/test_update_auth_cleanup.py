"""The updater consumes the policy owner's transactions, never edits Git itself."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import update_auth as auth
from agent_worktrees import update_auth_cleanup as cleanup

pytestmark = pytest.mark.guard
SHA = "a" * 64


def report(status="ok"):
    src = auth.source("fixture", "https://example.test/team/repo")
    return auth.AuthReport([auth.SourceAuth(src, [], status=status)])


def restore_packet(*, changed=False, dry_run=True, receipt=None):
    return {
        "ok": True, "surfaces": [], "modules": [],
        "resources": [{
            "type": "git-config", "id": "user-global", "dry_run": dry_run,
            "changed": changed, "status": "changed" if changed else "ok",
            "backup_path": receipt,
            "change_plan": {
                "schema": "agent-machines.git-config-plan/v1", "plan_sha256": SHA,
                "diff": "- old\n+ declared\n",
            },
        }],
    }


def test_offer_uses_the_canonical_strict_restore_and_never_applies(tmp_path, monkeypatch):
    calls = []

    def invoke(argv, cwd):
        calls.append((argv, cwd))
        return restore_packet(changed=True)

    monkeypatch.setattr(cleanup, "_invoke", invoke)
    offer = cleanup.offer(tmp_path)
    assert offer["status"] == "offered"
    assert offer["plan"]["plan_sha256"] == SHA
    assert calls == [(["git-config", "restore", "--json"], tmp_path)]
    assert "agent_machines" not in cleanup.__dict__


@pytest.mark.parametrize("kind", ["modules", "surfaces", "foreign_resource"])
def test_offer_refuses_broader_or_malformed_owner_responses(tmp_path, monkeypatch, kind):
    payload = restore_packet(changed=True)
    if kind == "foreign_resource":
        payload["resources"].append({"type": "file"})
    else:
        payload[kind] = [{"changed": True}]
    monkeypatch.setattr(cleanup, "_invoke", lambda *a: payload)
    assert cleanup.offer(tmp_path)["status"] == "unavailable"


def test_apply_rechecks_auth_and_rolls_back_regression(tmp_path, monkeypatch):
    receipt = str(tmp_path / "receipt.json")
    calls = []

    def invoke(argv, cwd):
        calls.append(argv)
        if argv[:2] == ["git-config", "restore"]:
            return restore_packet(changed=True, dry_run=False, receipt=receipt)
        if "--apply" not in argv:
            return {"plan": {"plan_sha256": SHA}, "changed": False, "dry_run": True}
        return {"changed": True, "dry_run": False, "backup_path": str(tmp_path / "undo.json")}

    reports = iter([report("auth_required"), report()])
    monkeypatch.setattr(cleanup, "_invoke", invoke)
    result = cleanup.apply(tmp_path, SHA, report(), lambda: next(reports))
    assert result["status"] == "auth_cleanup_reverted"
    assert result["report"]["all_ok"]
    assert calls == [
        ["git-config", "restore", "--json", "--apply", "--git-config-plan-sha", SHA],
        ["git-config", "rollback", "--receipt", receipt, "--json"],
        ["git-config", "rollback", "--receipt", receipt, "--json", "--apply", "--plan-sha", SHA],
    ]


def test_successful_apply_preserves_receipt_and_reports_real_auth_health(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cleanup, "_invoke",
        lambda *a: restore_packet(changed=True, dry_run=False, receipt=str(tmp_path / "receipt")),
    )
    result = cleanup.apply(tmp_path, SHA, report("auth_required"), lambda: report("auth_required"))
    assert result["status"] == "applied"
    assert result["report"]["all_ok"] is False
    assert result["backup_path"]


def test_apply_failure_never_claims_an_unchanged_file(tmp_path, monkeypatch):
    def fail(*a):
        raise cleanup.PolicyUnavailable("owner_unconfirmed")

    monkeypatch.setattr(cleanup, "_invoke", fail)
    result = cleanup.apply(tmp_path, SHA, report(), lambda: report())
    assert result["status"] == "unconfirmed"
    assert result["changed"] is None


def test_legacy_owner_is_from_selected_payload_not_path(tmp_path, monkeypatch):
    from agent_worktrees import registry_paths

    worktrees = tmp_path / "marketplace" / "agent-worktrees"
    peer = worktrees.parent / "agent-machines"
    (peer / "bin").mkdir(parents=True)
    (peer / "plugin.json").write_text(json.dumps({"name": "agent-machines"}))
    import os
    command = peer / "bin" / ("agent-machines.cmd" if os.name == "nt" else "agent-machines")
    command.touch()
    if os.name != "nt":
        command.chmod(0o700)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(registry_paths, "_payload_root", lambda: worktrees)
    argv, env = cleanup._command(["git-config", "restore", "--json"])
    assert str(command.resolve()) in argv
    assert env["AGENT_MACHINES_NO_SELFPROVISION"] == "1"
    assert not any(key.startswith("AGENT_WORKTREES_") for key in env)


def test_explicit_context_refusal_never_falls_back_to_legacy(tmp_path, monkeypatch):
    from agent_worktrees import peer_launch_adapter

    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "invalid-receipt")
    monkeypatch.setattr(
        peer_launch_adapter, "validate_context",
        lambda: (_ for _ in ()).throw(peer_launch_adapter.ContextRefused("refused")),
    )
    with pytest.raises(cleanup.PolicyUnavailable):
        cleanup._command(["git-config", "restore", "--json"])


@pytest.mark.parametrize("digest", ["", "a" * 63, "--apply", "A" * 64])
def test_invalid_consent_never_reaches_owner(tmp_path, monkeypatch, digest):
    monkeypatch.setattr(cleanup, "_invoke", lambda *a: pytest.fail("owner invoked"))
    assert cleanup.apply(tmp_path, digest, report(), lambda: report())["status"] == "blocked"


def test_cleanup_offer_is_retained_in_status():
    import update_telemetry

    offer = {"status": "offered", "plan": {"plan_sha256": SHA}}
    payload = update_telemetry.fold([{
        "phase": "auth_cleanup_offer", "event": "info",
        "details": {"cleanup_offer": offer},
    }], run={})
    assert payload["auth"]["cleanup_offer"] == offer


@pytest.mark.parametrize("names", [
    {"auth_cleanup": True},
    {"apply_auth_cleanup": SHA},
    {"revert_auth_cleanup": "receipt"},
    {"auth_report": True, "auth_environment": True, "auth_cleanup": True},
    {"apply_auth_cleanup": ""},
    {"auth_report": True, "apply_auth_cleanup": ""},
    {"revert_auth_cleanup": ""},
])
def test_cleanup_cli_cannot_hide_mutation_in_an_update_or_manager_export(names):
    import argparse
    from agent_worktrees import update_options

    with pytest.raises(SystemExit):
        update_options.validate_auth_flags(argparse.Namespace(**names))


@pytest.mark.real_installer_processes
def test_native_owner_subprocess_preview_apply_and_regression_rollback(tmp_path, monkeypatch):
    import os
    import sys

    home = tmp_path / "home"
    home.mkdir()
    target = home / ".gitconfig"
    original = b"# keep exact bytes\r\n[user]\r\n\tname = Example\r\n"
    target.write_bytes(original)
    fixture = tmp_path / "policy.yaml"
    fixture.write_text(
        "schema_version: 4\npackage: example/auth\n"
        "resources:\n  - type: git-config\n    entries:\n"
        "      - key: credential.helper\n        values: [fixture-helper]\n",
        encoding="utf-8",
    )
    driver = (
        "import os,sys; from pathlib import Path; from types import SimpleNamespace; "
        "from agent_machines import __main__ as cli; "
        "from agent_machines.manifest import load_package; "
        "cli._collect_reconcile_packages=lambda *a: ([load_package(Path(os.environ['POLICY_FIXTURE']))], 'fixture'); "
        "cli._resolve_machine_identity=lambda *a: SimpleNamespace(canonical='fixture',raw='fixture',"
        "accepted=('fixture',),warnings=()); sys.exit(cli.main(sys.argv[1:]))"
    )
    root = Path(__file__).resolve().parents[3]
    paths = [root / "plugins" / "agent-machines" / "src", *root.glob("libs/*/src")]
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "GIT_CONFIG_GLOBAL": str(target), "POLICY_FIXTURE": str(fixture),
        "PYTHONPATH": os.pathsep.join(map(str, paths)),
    }
    monkeypatch.setattr(
        cleanup, "_command", lambda args: ([sys.executable, "-c", driver, *args], env),
    )
    offer = cleanup.offer(tmp_path)
    assert offer["status"] == "offered", offer
    assert target.read_bytes() == original
    responses = iter([report("auth_required"), report()])
    result = cleanup.apply(
        tmp_path, offer["plan"]["plan_sha256"], report(), lambda: next(responses),
    )
    assert result["status"] == "auth_cleanup_reverted", result
    assert result["report"]["all_ok"]
    assert target.read_bytes() == original
    assert Path(result["backup_path"]).is_file()


@pytest.mark.parametrize("operation", ["apply", "rollback"])
def test_report_cli_displays_the_post_mutation_health_not_the_old_report(
    tmp_path, monkeypatch, capsys, operation,
):
    import argparse
    from agent_worktrees import update_auth_context as context, update_auth_sources as sources

    before, after = report("auth_required"), report()
    monkeypatch.setenv("CE_UPDATE_AUTH", "on")
    monkeypatch.setattr(sources, "anchors", lambda: [])
    responses = iter([before, after])
    monkeypatch.setattr(auth, "report", lambda *a, **k: next(responses))
    monkeypatch.setattr(auth, "fallback", lambda *a, **k: None)
    if operation == "apply":
        def apply(cwd, digest, initial, recheck):
            assert initial is before
            return {"status": "applied", "report": recheck().summary()}
        monkeypatch.setattr(cleanup, "apply", apply)
    else:
        monkeypatch.setattr(cleanup, "rollback", lambda *a: {"status": "reverted"})
    args = argparse.Namespace(
        auth_source=["https://example.test/team/repo"], auth_cwd=str(tmp_path),
        auth_report=True, apply_auth_cleanup=SHA, json=True,
        revert_auth_cleanup="receipt" if operation == "rollback" else None,
    )
    assert context.cmd_report(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["all_ok"]
    assert result["results"][0]["status"] == "ok"


@pytest.mark.parametrize("mode", ["off", "report-only"])
def test_read_only_modes_block_apply_before_the_owner_is_called(
    tmp_path, monkeypatch, capsys, mode,
):
    import argparse
    from agent_worktrees import update_auth_context as context, update_auth_sources as sources

    monkeypatch.setenv("CE_UPDATE_AUTH", mode)
    monkeypatch.setattr(sources, "anchors", lambda: [])
    monkeypatch.setattr(auth, "report", lambda *a, **k: report())
    monkeypatch.setattr(auth, "fallback", lambda *a, **k: None)
    monkeypatch.setattr(cleanup, "_invoke", lambda *a: pytest.fail("owner invoked"))
    args = argparse.Namespace(
        auth_source=["https://example.test/team/repo"], auth_cwd=str(tmp_path),
        apply_auth_cleanup=SHA, json=True,
    )
    assert context.cmd_report(args) == 1
    assert json.loads(capsys.readouterr().out)["cleanup"]["changed"] is False


@pytest.mark.parametrize("explicit", [False, True])
def test_report_cleanup_uses_selected_repository_unless_explicitly_overridden(
    tmp_path, monkeypatch, capsys, explicit,
):
    import argparse
    from agent_worktrees import update_auth_context as context, update_auth_sources as sources

    selected, override = tmp_path / "selected", tmp_path / "override"
    monkeypatch.setattr(context, "_marketplace_context", lambda: selected)
    monkeypatch.setattr(sources, "marketplaces", lambda *a: [])
    monkeypatch.setattr(sources, "anchors", lambda: [])
    monkeypatch.setattr(auth, "report", lambda *a, **k: report())
    monkeypatch.setattr(auth, "fallback", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(cleanup, "offer", lambda cwd: calls.append(cwd) or {"status": "current"})
    assert context.cmd_report(argparse.Namespace(
        auth_cleanup=True, auth_cwd=str(override) if explicit else None, json=True,
    )) == 0
    capsys.readouterr()
    assert calls == [override if explicit else selected]


def test_update_offer_uses_selected_repository_not_process_cwd(tmp_path, monkeypatch):
    import argparse
    from contextlib import nullcontext
    from types import SimpleNamespace
    from agent_worktrees import update_auth_context as context

    selected = tmp_path / "selected"
    session = SimpleNamespace(
        reports={selected: report("auth_required")}, anchor_reports={},
        unresolved=False, anchor_failed=False,
    )
    monkeypatch.setattr(context, "session", lambda: nullcontext(session))
    monkeypatch.setattr(context, "_marketplace_context", lambda: selected)
    calls = []
    monkeypatch.setattr(context, "_cleanup_offer", lambda cwd: calls.append(cwd))
    assert context.run_update(argparse.Namespace(phase="runtimes"), lambda *a: 0) == 0
    assert calls == [selected]


def test_large_preview_survives_the_real_emitter_and_status_fold(tmp_path, monkeypatch):
    import update_telemetry as ut
    from agent_worktrees import update_events

    events_path = tmp_path / "events.jsonl"
    run = ut.Run(
        run_id="fixture", events_path=events_path, proc="engine",
        owner="engine", is_owner=True,
    )
    monkeypatch.setattr(update_events, "_run", run)
    offer = {
        "status": "offered", "plan": {
            "plan_sha256": SHA, "diff": "declared helper policy\n" * 1000,
        },
    }
    update_events.auth_cleanup_offer(offer)
    events, errors = ut.read_events(events_path)
    assert errors == 0
    status = ut.fold(events, run={"events_path": str(events_path)})
    assert status["auth"]["cleanup_offer"] == offer


@pytest.mark.parametrize("damage", ["missing", "changed", "escape"])
def test_invalid_retained_preview_is_unavailable_without_losing_its_digest(
    tmp_path, monkeypatch, damage,
):
    import update_telemetry as ut
    from agent_worktrees import update_events

    events_path = tmp_path / "events.jsonl"
    monkeypatch.setattr(update_events, "_run", ut.Run(
        run_id="fixture", events_path=events_path, proc="engine",
        owner="engine", is_owner=True,
    ))
    update_events.auth_cleanup_offer({
        "status": "offered", "plan": {"plan_sha256": SHA, "diff": "declared\n" * 1000},
    })
    events, errors = ut.read_events(events_path)
    assert errors == 0
    reference = events[0]["details"]["cleanup_offer"]
    path = tmp_path / reference["preview_file"]
    if damage == "missing":
        path.unlink()
    elif damage == "changed":
        path.write_text("{}")
    else:
        reference["preview_file"] = "../outside.json"
    result = ut.fold(events, run={"events_path": str(events_path)})["auth"]["cleanup_offer"]
    assert result["status"] == "unavailable"
    assert result["plan_sha256"] == SHA
