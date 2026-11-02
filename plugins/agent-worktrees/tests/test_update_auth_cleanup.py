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


@pytest.mark.parametrize(("initial_status", "failed_status"), [
    ("ok", "auth_required"), ("anonymous", "auth_required"),
    ("fixable_by_fallback", "auth_required"), ("ok", "fixable_by_fallback"),
])
def test_apply_rechecks_auth_and_rolls_back_regression(
    tmp_path, monkeypatch, initial_status, failed_status,
):
    receipt = str(tmp_path / "receipt.json")
    calls = []

    def invoke(argv, cwd):
        calls.append(argv)
        if argv[:2] == ["git-config", "restore"]:
            return restore_packet(changed=True, dry_run=False, receipt=receipt)
        if "--apply" not in argv:
            return {"plan": {"plan_sha256": SHA}, "changed": False, "dry_run": True}
        return {"changed": True, "dry_run": False, "backup_path": str(tmp_path / "undo.json")}

    before = report(initial_status)
    reports = iter([report(failed_status), before])
    monkeypatch.setattr(cleanup, "_invoke", invoke)
    result = cleanup.apply(tmp_path, SHA, before, lambda: next(reports))
    assert result["status"] == "auth_cleanup_reverted"
    assert result["report"] == before.summary()
    assert calls == [
        ["git-config", "restore", "--json", "--apply", "--git-config-plan-sha", SHA],
        ["git-config", "rollback", "--receipt", receipt, "--json"],
        ["git-config", "rollback", "--receipt", receipt, "--json", "--apply", "--plan-sha", SHA],
    ]


@pytest.mark.parametrize(("initial_status", "after_status"), [
    ("auth_required", "auth_required"),
    ("fixable_by_fallback", "fixable_by_fallback"),
    ("fixable_by_fallback", "ok"),
])
def test_successful_apply_preserves_receipt_and_reports_real_auth_health(
    tmp_path, monkeypatch, initial_status, after_status,
):
    monkeypatch.setattr(
        cleanup, "_invoke",
        lambda *a: restore_packet(changed=True, dry_run=False, receipt=str(tmp_path / "receipt")),
    )
    after = report(after_status)
    result = cleanup.apply(tmp_path, SHA, report(initial_status), lambda: after)
    assert result["status"] == "applied"
    assert result["report"] == after.summary()
    assert result["backup_path"]


def test_apply_failure_never_claims_an_unchanged_file(tmp_path, monkeypatch):
    def fail(*a):
        raise cleanup.PolicyUnavailable("owner_unconfirmed")

    monkeypatch.setattr(cleanup, "_invoke", fail)
    result = cleanup.apply(tmp_path, SHA, report(), lambda: report())
    assert result["status"] == "unconfirmed"
    assert result["changed"] is None


@pytest.mark.parametrize("failure", ["unconfirmed", "failed", "invalid-preview"])
def test_automatic_rollback_failure_retains_the_original_recovery_receipt(
    tmp_path, monkeypatch, failure,
):
    receipt = str(tmp_path / "receipt")

    def invoke(argv, cwd):
        if argv[:2] == ["git-config", "restore"]:
            return restore_packet(changed=True, dry_run=False, receipt=receipt)
        if failure == "invalid-preview":
            return {"dry_run": True}
        if "--apply" not in argv:
            return {"dry_run": True, "plan": {"plan_sha256": SHA}}
        if failure == "unconfirmed":
            raise cleanup.PolicyUnavailable("policy_owner_unconfirmed")
        return {"_failed": True, "changed": False}

    monkeypatch.setattr(cleanup, "_invoke", invoke)
    result = cleanup.apply(tmp_path, SHA, report(), lambda: report("auth_required"))
    assert result["status"] == "rollback_failed"
    assert result["changed"] is True
    assert result["backup_path"] == receipt


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


@pytest.mark.real_installer_processes
@pytest.mark.parametrize("explicit", [False, True])
def test_owner_discovers_selected_repository_despite_inherited_git_routing(
    tmp_path, monkeypatch, explicit,
):
    import subprocess
    import sys

    from agent_procutil import no_window_kwargs

    from agent_worktrees import _peer_launch, claim_providers, peer_launch_adapter, registry_paths

    selected, other = tmp_path / "selected", tmp_path / "other"
    for path in (selected, other):
        subprocess.run(
            ["git", "init", "--quiet", str(path)], check=True,
            **no_window_kwargs(),
        )
    target = tmp_path / "global-config"
    target.write_bytes(b"# preserve explicit configuration target\n")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_COMMON_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(target))
    driver = (
        "import json,os,subprocess; "
        "root=subprocess.check_output(['git','rev-parse','--show-toplevel'], "
        "text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).strip(); "
        "print(json.dumps({'root':root,'target':os.environ.get('GIT_CONFIG_GLOBAL')}))"
    )
    command = [sys.executable, "-c", driver]
    root = tmp_path / "marketplace" / "agent-worktrees"
    peer = root.parent / "agent-machines"
    peer.mkdir(parents=True)
    (peer / "plugin.json").write_text(json.dumps({"name": "agent-machines"}))
    monkeypatch.setattr(registry_paths, "_payload_root", lambda: root)
    monkeypatch.setattr(claim_providers, "_resolve_command", lambda *a, **k: command)
    monkeypatch.setattr(claim_providers, "_windows_batch_argv", lambda argv: argv)
    if explicit:
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "fixture")
        monkeypatch.setattr(peer_launch_adapter, "validate_context", lambda: {
            "pluginRoot": str(root), "installReceipt": "fixture",
        })
        monkeypatch.setattr(_peer_launch, "launch_prefix", lambda *a: command)
    else:
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    result = cleanup._invoke([], selected)
    assert Path(result["root"]).resolve() == selected.resolve()
    assert result["target"] == str(target)


@pytest.mark.parametrize("digest", ["", "a" * 63, "--apply", "A" * 64])
def test_invalid_consent_never_reaches_owner(tmp_path, monkeypatch, digest):
    monkeypatch.setattr(cleanup, "_invoke", lambda *a: pytest.fail("owner invoked"))
    assert cleanup.apply(tmp_path, digest, report(), lambda: report())["status"] == "blocked"


def test_cleanup_offer_is_retained_in_status():
    import update_telemetry

    offer = {"status": "offered", "plan": {"plan_sha256": SHA}}
    payload = update_telemetry.fold([{
        "phase": "auth_cleanup", "action": "offer", "event": "info",
        "details": {"cleanup_offer": offer},
    }], run={})
    assert payload["auth"]["cleanup_offer"] == offer


@pytest.mark.real_installer_processes
@pytest.mark.parametrize("padding", [1, 1000])
def test_automatic_offer_never_persists_inline_helper_credentials(
    tmp_path, monkeypatch, capsys, padding,
):
    import importlib.util
    import sys

    import update_telemetry as ut

    from agent_worktrees import update_auth_context as context
    from agent_worktrees import update_events

    owner_source = (
        Path(__file__).resolve().parents[3] / "plugins" / "agent-machines" / "src"
        / "agent_machines" / "git_config_transaction.py"
    )
    spec = importlib.util.spec_from_file_location("cleanup_git_config_fixture", owner_source)
    transaction = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, transaction)
    spec.loader.exec_module(transaction)
    secret = "inline-helper-private-fixture-value"
    helper = "!f() { printf '%s' '" + secret + "'; }; f # " + "padding " * padding
    plan = transaction.preview(
        tmp_path / "global-config", {"credential.helper": [helper]},
    ).summary()
    packet = restore_packet(changed=True)
    packet["resources"][0]["change_plan"] = plan
    monkeypatch.setattr(cleanup, "_invoke", lambda *a: packet)
    events_path = tmp_path / "run" / "events.jsonl"
    monkeypatch.setattr(update_events, "_run", ut.Run(
        run_id="fixture", events_path=events_path, proc="engine",
        owner="engine", is_owner=True,
    ))
    offered = context._cleanup_offer(tmp_path)
    events, errors = ut.read_events(events_path)
    assert errors == 0
    status = ut.fold(events, run={"events_path": str(events_path)})
    assert offered["plan"]["plan_sha256"] == plan["plan_sha256"]
    assert status["auth"]["cleanup_offer"]["plan"]["plan_sha256"] == plan["plan_sha256"]
    assert secret not in json.dumps(offered)
    assert secret not in json.dumps(status)
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    assert all(secret not in path.read_text() for path in events_path.parent.iterdir())
    assert cleanup.offer(tmp_path, include_values=True)["plan"] == plan


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
    root = Path(__file__).resolve().parents[3]
    paths = [root / "plugins" / "agent-machines" / "src", *root.glob("libs/*/src")]
    driver = (
        f"import os,sys; sys.path[:0]={list(map(str, paths))!r}; "
        "from pathlib import Path; from types import SimpleNamespace; "
        "from agent_machines import __main__ as cli; "
        "from agent_machines.manifest import load_package; "
        "cli._collect_reconcile_packages=lambda *a: "
        "([load_package(Path(os.environ['POLICY_FIXTURE']))], 'fixture'); "
        "cli._resolve_machine_identity=lambda *a: "
        "SimpleNamespace(canonical='fixture',raw='fixture',"
        "accepted=('fixture',),warnings=()); sys.exit(cli.main(sys.argv[1:]))"
    )
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "GIT_CONFIG_GLOBAL": str(target), "POLICY_FIXTURE": str(fixture),
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

    from agent_worktrees import update_auth_context as context
    from agent_worktrees import update_auth_sources as sources

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

    from agent_worktrees import update_auth_context as context
    from agent_worktrees import update_auth_sources as sources

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

    from agent_worktrees import update_auth_context as context
    from agent_worktrees import update_auth_sources as sources

    selected, override = tmp_path / "selected", tmp_path / "override"
    monkeypatch.setattr(context, "_marketplace_context", lambda: selected)
    monkeypatch.setattr(sources, "marketplaces", lambda *a: [])
    monkeypatch.setattr(sources, "anchors", lambda: [])
    monkeypatch.setattr(auth, "report", lambda *a, **k: report())
    monkeypatch.setattr(auth, "fallback", lambda *a, **k: None)
    calls = []
    def offer(cwd, *, include_values=False):
        assert include_values is True
        calls.append(cwd)
        return {"status": "current"}

    monkeypatch.setattr(cleanup, "offer", offer)
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


@pytest.mark.parametrize("lines", [1, 1000])
def test_large_preview_survives_the_real_emitter_and_status_fold(tmp_path, monkeypatch, lines):
    import update_telemetry as ut
    from test_update_telemetry_lib import _schema, _validate

    from agent_worktrees import update_events

    events_path = tmp_path / "events.jsonl"
    run = ut.Run(
        run_id="fixture", events_path=events_path, proc="engine",
        owner="engine", is_owner=True,
    )
    monkeypatch.setattr(update_events, "_run", run)
    offer = {
        "status": "offered", "plan": {
            "plan_sha256": SHA, "diff": "declared helper policy\n" * lines,
        },
    }
    update_events.auth_cleanup_offer(offer)
    events, errors = ut.read_events(events_path)
    assert errors == 0
    assert all(_validate(event, _schema("update-event")) == [] for event in events)
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
