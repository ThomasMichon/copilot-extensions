"""Historical command convergence without implicit installation transfers."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
from pathlib import Path

import pytest

from agent_worktrees import installer as inst


@pytest.fixture(params=["Windows", "Linux"])
def command_home(request, monkeypatch, tmp_path):
    monkeypatch.setattr(inst.platform, "system", lambda: request.param)
    runtime = tmp_path / "runtime"
    current = tmp_path / ".copilot" / "installed-plugins" / "example" / "agent-worktrees"
    old = runtime / "snapshots" / "1.0.0"
    for root, version in ((old, "1.0.0"), (current, "1.1.0")):
        root.mkdir(parents=True)
        (root / "plugin.json").write_text(json.dumps({
            "name": "agent-worktrees", "version": version,
            "repository": "https://example.invalid/suite.git",
        }), encoding="utf-8")
    (runtime / "deploy-manifest.json").write_text(json.dumps({
        "source": {"path": current.as_posix(), "plugin": "agent-worktrees"},
    }), encoding="utf-8")
    monkeypatch.setattr(inst, "install_dir", lambda: runtime)
    monkeypatch.setattr(inst, "local_bin", lambda: tmp_path / "bin")
    monkeypatch.setattr(inst, "_command_arbitration_dir", lambda: runtime / "receipts")
    monkeypatch.setattr(inst.registry_paths, "installation_context", lambda: None)
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(current))
    monkeypatch.setattr(inst, "read_projects_registry",
                        lambda: {"projects": {"demo": {"path": str(tmp_path / "repo")}}})
    monkeypatch.setattr(inst, "_project_is_knowledge_only", lambda _name: False)
    monkeypatch.setattr(inst, "_project_identity", lambda name, repo_dir=None: {
        "name": inst._stub_key(name), "path": str(tmp_path / "repo"),
        "remote": "https://example.invalid/project.git",
    })
    return current, old


def seed_old(monkeypatch, roots, *, receipted=True):
    current, old = roots
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(old))
    inst._deploy_project_binstub("demo")
    if not receipted:
        inst._receipt_path("demo").unlink()
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(current))
    return {p: p.read_bytes() for p, _ in inst._project_binstub_specs("demo")}


@pytest.mark.parametrize("receipted", [True, False])
def test_snapshot_command_migration(command_home, monkeypatch, receipted):
    before = seed_old(monkeypatch, command_home, receipted=receipted)
    result = inst.reconcile_binstubs()
    assert result["migrated"] == ["demo"]
    assert result["preserved"] == result["blocked"] == []
    assert all(p.read_bytes() != data for p, data in before.items())
    assert inst._read_receipt("demo")["owner"]["payload_root"] == command_home[0].as_posix()
    mtimes = {p: p.stat().st_mtime_ns for p in before}
    receipt_mtime = inst._receipt_path("demo").stat().st_mtime_ns
    second = inst.reconcile_binstubs()
    assert second["added"] == 0
    assert second["migrated"] == []
    assert mtimes == {p: p.stat().st_mtime_ns for p in before}
    assert inst._receipt_path("demo").stat().st_mtime_ns == receipt_mtime


@pytest.mark.parametrize("receipted", [True, False])
@pytest.mark.parametrize("registered", [False, True])
def test_snapshot_obsolete_retirement(command_home, monkeypatch, registered, receipted):
    before = seed_old(monkeypatch, command_home, receipted=receipted)
    if registered:
        monkeypatch.setattr(inst, "_project_is_knowledge_only", lambda _name: True)
    else:
        monkeypatch.setattr(inst, "read_projects_registry", lambda: {"projects": {}})
    result = inst.reconcile_binstubs()
    assert set(result["removed"]) == {str(p) for p in before}
    assert result["preserved"] == result["blocked"] == []
    assert not inst._receipt_path("demo").exists()


@pytest.mark.parametrize("damage", ["edited", "foreign", "cell", "corrupt", "project", "source"])
def test_migration_preserves_unproven_command(command_home, monkeypatch, damage):
    before = seed_old(monkeypatch, command_home)
    receipt = inst._read_receipt("demo")
    if damage == "edited":
        path = next(iter(before))
        path.write_bytes(b"operator-owned command\n")
        before[path] = path.read_bytes()
    elif damage == "corrupt":
        inst._receipt_path("demo").write_text("{", encoding="utf-8")
    elif damage == "source":
        (inst.install_dir() / "deploy-manifest.json").write_text(
            json.dumps({"source": {"path": str(command_home[0].parent / "other")}}),
            encoding="utf-8")
    else:
        section, key = {
            "foreign": ("owner", "marketplace"),
            "cell": ("owner", "install_receipt"),
            "project": ("project", "path"),
        }[damage]
        receipt[section][key] = "another-owner"
        inst._write_receipt("demo", receipt)
    receipt_before = inst._receipt_path("demo").read_bytes()
    result = inst.reconcile_binstubs()
    assert result["preserved"] == result["blocked"] == ["demo"]
    assert {p: p.read_bytes() for p in before} == before
    assert inst._receipt_path("demo").read_bytes() == receipt_before


def test_partial_pair_is_repaired(command_home, monkeypatch):
    before = seed_old(monkeypatch, command_home)
    if inst.platform.system() != "Windows":
        pytest.skip("Windows sibling repair")
    next(iter(before)).unlink()
    result = inst.reconcile_binstubs()
    assert result["blocked"] == []
    assert all(p.exists() for p in before)


def test_obsolete_edited_sibling_protects_entire_command(command_home, monkeypatch):
    before = seed_old(monkeypatch, command_home)
    target = next(iter(before))
    target.write_bytes(b"edited without generated signature\n")
    before[target] = target.read_bytes()
    monkeypatch.setattr(inst, "read_projects_registry", lambda: {"projects": {}})
    result = inst.reconcile_binstubs()
    assert result["removed"] == []
    assert result["preserved"] == ["demo"]
    assert result["blocked"] == []
    assert {p: p.read_bytes() for p in before} == before


def test_foreign_inventory_is_advisory(command_home, monkeypatch):
    seed_old(monkeypatch, command_home)
    receipt = inst._read_receipt("demo")
    receipt["owner"]["marketplace"] = "another-marketplace"
    inst._write_receipt("demo", receipt)
    monkeypatch.setattr(inst, "read_projects_registry", lambda: {"projects": {}})
    result = inst.reconcile_binstubs()
    assert result["preserved"] == ["demo"]
    assert result["blocked"] == []


@pytest.mark.parametrize("receipted", [True, False])
def test_admitted_unregistered_retirement_failure_is_required(
    command_home, monkeypatch, capsys, receipted
):
    from agent_worktrees import project_command_transaction as tx

    before = seed_old(monkeypatch, command_home, receipted=receipted)
    monkeypatch.setattr(inst, "read_projects_registry", lambda: {"projects": {}})

    def fail_publication(*_args, **_kwargs):
        raise PermissionError("retirement publication denied")

    monkeypatch.setattr(tx, "publish", fail_publication)
    result = inst.reconcile_binstubs()
    assert result["blocked"] == result["preserved"] == ["demo"]
    assert result["removed"] == []
    assert {p: p.read_bytes() for p in before} == before
    assert "in sync" not in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["permission", "edited", "foreign"])
def test_interrupted_unregistered_retirement_retains_admission(
    command_home, monkeypatch, capsys, failure
):
    from agent_worktrees import project_command_transaction as tx

    before = seed_old(monkeypatch, command_home)
    monkeypatch.setattr(inst, "read_projects_registry", lambda: {"projects": {}})
    original = tx._replace

    def interrupt(path, data, mode):
        original(path, data, mode)
        raise KeyboardInterrupt("interrupted retirement")

    monkeypatch.setattr(tx, "_replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        inst.reconcile_binstubs()
    monkeypatch.setattr(tx, "_replace", original)
    if failure == "permission":
        def denied(*_args, **_kwargs):
            raise PermissionError("retirement recovery denied")
        monkeypatch.setattr(tx, "_replace", denied)
    elif failure == "edited":
        next(iter(before)).write_bytes(b"edited during retirement\n")
    else:
        receipt = inst._read_receipt("demo")
        receipt["owner"]["marketplace"] = "other-marketplace"
        inst._write_receipt("demo", receipt)
    intermediate = {p: p.read_bytes() if p.exists() else None for p in before}
    capsys.readouterr()
    result = inst.reconcile_binstubs()
    assert result["preserved"] == ["demo"]
    assert result["blocked"] == ([] if failure == "foreign" else ["demo"])
    assert result["removed"] == []
    assert {p: p.read_bytes() if p.exists() else None for p in before} == intermediate
    if failure != "foreign":
        assert "in sync" not in capsys.readouterr().out


@pytest.mark.parametrize("publication", ["command", "receipt"])
def test_interrupted_publication_recovers(command_home, monkeypatch, publication):
    before = seed_old(monkeypatch, command_home)
    original = inst._write_receipt
    armed = True

    def interrupted(project, data):
        nonlocal armed
        if armed and (
            ("transaction" in data and publication == "command")
            or ("transaction" not in data and publication == "receipt")
        ):
            armed = False
            if publication == "command":
                original(project, data)
            raise KeyboardInterrupt("simulated process interruption")
        original(project, data)

    monkeypatch.setattr(inst, "_write_receipt", interrupted)
    with pytest.raises(KeyboardInterrupt):
        inst.reconcile_binstubs()
    result = inst.reconcile_binstubs()
    assert result["blocked"] == []
    assert all(p.read_bytes() != data for p, data in before.items())
    assert "transaction" not in inst._read_receipt("demo")


def test_concurrent_reconcile_is_idempotent(command_home, monkeypatch):
    seed_old(monkeypatch, command_home)
    results, errors = [], []

    def run():
        try:
            results.append(inst.reconcile_binstubs())
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()
    assert errors == []
    assert len(results) == 2
    assert all(result["blocked"] == [] for result in results)
    assert sum(bool(result["migrated"]) for result in results) == 1


def historical_fixture(old: Path, *, ambient: bool) -> dict[str, bytes]:
    """Literal #1901 predecessor / #1909 predecessor templates (not recognizer output)."""
    if inst.platform.system() != "Windows":
        header = "" if ambient else "# agent-worktrees project binstub\n"
        launch = (
            '  export WORKTREE_PROJECT=demo\n  exec "$HOME/.agent-worktrees/bin/launch-session.sh"'
            if ambient else '  exec "$HOME/.agent-worktrees/bin/launch-session.sh" --project demo'
        )
        return {"demo": (
            f"#!/usr/bin/env bash\n{header}"
            "export PYTHONUTF8=1\n"
            "export AGENT_WORKTREES_LAUNCH_ID=demo-$$-$RANDOM-$(date +%s)\n"
            'export AGENT_WORKTREES_BINSTUB_STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"\n'
            'export AGENT_WORKTREES_LAUNCH_TRACE="$HOME/.agent-worktrees/logs/picker-launches.jsonl"\n'
            'mkdir -p "$(dirname "$AGENT_WORKTREES_LAUNCH_TRACE")" 2>/dev/null || true\n'
            """printf '%s\\n' '{"event":"binstub_start","timestamp":"'"$AGENT_WORKTREES_BINSTUB_STARTED"'","launch_id":"'"$AGENT_WORKTREES_LAUNCH_ID"'","project":"demo"}' >>"$AGENT_WORKTREES_LAUNCH_TRACE" 2>/dev/null || true\n"""
            f"if [[ $# -eq 0 ]]; then\n{launch}\nfi\n"
            "# This attributable project entry point is pinned to its owning payload.\n"
            f'exec {shlex.quote(str(old / "bin" / "payload" / "agent-worktrees"))} --project demo "$@"\n'
        ).encode("utf-8")}
    cmd_header = "" if ambient else "rem agent-worktrees project binstub\n"
    cmd_launch = (
        '  set "WORKTREE_PROJECT=demo"\n  call "%USERPROFILE%\\.agent-worktrees\\bin\\launch-session.cmd"'
        if ambient else '  call "%USERPROFILE%\\.agent-worktrees\\bin\\launch-session.cmd" --project demo'
    )
    cmd = (
        f"@echo off\n{cmd_header}"
        'set "PYTHONUTF8=1"\nset "AGENT_WORKTREES_LAUNCH_ID=demo-%RANDOM%-%RANDOM%"\n'
        'set "AGENT_WORKTREES_BINSTUB_STARTED=%DATE% %TIME%"\n'
        'set "AGENT_WORKTREES_LAUNCH_TRACE=%USERPROFILE%\\.agent-worktrees\\logs\\picker-launches.jsonl"\n'
        'if not exist "%USERPROFILE%\\.agent-worktrees\\logs" mkdir "%USERPROFILE%\\.agent-worktrees\\logs" >nul 2>&1\n'
        '(>>"%AGENT_WORKTREES_LAUNCH_TRACE%" echo {"event":"binstub_start","timestamp":"%AGENT_WORKTREES_BINSTUB_STARTED%","launch_id":"%AGENT_WORKTREES_LAUNCH_ID%","project":"demo"}) 2>nul\n'
        f'if "%~1"=="" (\n{cmd_launch}\n  exit /b %ERRORLEVEL%\n)\n'
        "rem This attributable project entry point is pinned to its owning payload.\n"
        f'"{old / "bin" / "payload" / "agent-worktrees.cmd"}" --project demo %*\n'
        "exit /b %ERRORLEVEL%"
    )
    ps_header = "" if ambient else "# agent-worktrees project binstub\n"
    ps_launch = (
        "    $_savedProj = $env:WORKTREE_PROJECT\n"
        "    $env:WORKTREE_PROJECT = 'demo'\n"
        "    try {\n"
        '        & "$env:USERPROFILE\\.agent-worktrees\\bin\\launch-session.ps1"\n'
        "        $_rc = $LASTEXITCODE\n    } finally {\n"
        "        if ($null -eq $_savedProj) { Remove-Item Env:WORKTREE_PROJECT -ErrorAction SilentlyContinue } else { $env:WORKTREE_PROJECT = $_savedProj }\n"
        "    }\n    exit $_rc"
        if ambient else
        '    & "$env:USERPROFILE\\.agent-worktrees\\bin\\launch-session.ps1" --project \'demo\'\n'
        "    exit $LASTEXITCODE"
    )
    ps = (
        f"{ps_header}$env:PYTHONUTF8 = '1'\n"
        "$env:AGENT_WORKTREES_LAUNCH_ID = 'demo-' + [guid]::NewGuid().ToString('N')\n"
        "$env:AGENT_WORKTREES_BINSTUB_STARTED = [DateTime]::UtcNow.ToString('o')\n"
        "$_awTraceDir = Join-Path $env:USERPROFILE '.agent-worktrees\\logs'\n"
        "$env:AGENT_WORKTREES_LAUNCH_TRACE = Join-Path $_awTraceDir 'picker-launches.jsonl'\n"
        "try {\n    [IO.Directory]::CreateDirectory($_awTraceDir) | Out-Null\n"
        "    $_awEvent = [ordered]@{ event = 'binstub_start'; timestamp = $env:AGENT_WORKTREES_BINSTUB_STARTED; launch_id = $env:AGENT_WORKTREES_LAUNCH_ID; project = 'demo' }\n"
        "    [IO.File]::AppendAllText($env:AGENT_WORKTREES_LAUNCH_TRACE, ($_awEvent | ConvertTo-Json -Compress) + [Environment]::NewLine)\n"
        f"}} catch {{}}\nif ($args.Count -eq 0) {{\n{ps_launch}\n}}\n"
        "# This attributable project entry point is pinned to its owning payload.\n"
        f"& '{old / 'bin' / 'payload' / 'agent-worktrees.ps1'}' --project 'demo' @args\n"
        "exit $LASTEXITCODE"
    )
    return {name: data.replace("\n", "\r\n").encode("utf-8")
            for name, data in (("demo.cmd", cmd), ("demo.ps1", ps))}


@pytest.mark.parametrize("ambient", [True, False])
@pytest.mark.parametrize("receipted", [True, False])
def test_historical_template_families(command_home, monkeypatch, ambient, receipted):
    before = seed_old(monkeypatch, command_home, receipted=receipted)
    golden = historical_fixture(command_home[1], ambient=ambient)
    for p in before:
        p.write_bytes(golden[p.name])
    if receipted:
        receipt = inst._read_receipt("demo")
        receipt["stubs"] = {p.name: inst._file_sha256(p) for p in before}
        inst._write_receipt("demo", receipt)
    result = inst.reconcile_binstubs()
    assert result["migrated"] == ["demo"]
    assert result["blocked"] == []
    assert all(b"WORKTREE_PROJECT" not in p.read_bytes() for p in before)


def test_old_caller_cannot_downgrade_command(command_home, monkeypatch):
    inst._deploy_project_binstub("demo")
    before = {p: p.read_bytes() for p, _ in inst._project_binstub_specs("demo")}
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(command_home[1]))
    result = inst.reconcile_binstubs()
    assert result["blocked"] == ["demo"]
    assert {p: p.read_bytes() for p in before} == before


def test_receipt_source_can_differ_from_exact_snapshot_route(command_home, monkeypatch):
    """The receipt can name the installed source while bytes pin its snapshot."""
    seed_old(monkeypatch, command_home)
    receipt = inst._read_receipt("demo")
    receipt["owner"] = inst._binstub_owner()
    inst._write_receipt("demo", receipt)
    result = inst.reconcile_binstubs()
    assert result["migrated"] == ["demo"]
    assert result["blocked"] == []


def test_same_owner_edited_pair_is_preserved(command_home, monkeypatch):
    inst._deploy_project_binstub("demo")
    before = {p: p.read_bytes() for p, _ in inst._project_binstub_specs("demo")}
    p = next(iter(before))
    p.write_bytes(b"edited\n")
    before[p] = p.read_bytes()
    monkeypatch.setattr(inst, "_project_is_knowledge_only", lambda _name: True)
    result = inst.reconcile_binstubs()
    assert result["removed"] == []
    assert result["blocked"] == ["demo"]
    assert {p: p.read_bytes() for p in before} == before


def test_failed_publication_restores_usable_command(command_home, monkeypatch):
    before = seed_old(monkeypatch, command_home)
    previous = inst._receipt_path("demo").read_bytes()
    original = inst._write_receipt

    def failed(project, data):
        if "transaction" not in data:
            raise OSError("publication failed")
        original(project, data)

    monkeypatch.setattr(inst, "_write_receipt", failed)
    result = inst.reconcile_binstubs()
    assert result["blocked"] == ["demo"]
    assert {p: p.read_bytes() for p in before} == before
    assert inst._receipt_path("demo").read_bytes() == previous


@pytest.mark.parametrize("retire", [False, True])
@pytest.mark.parametrize("edited", [False, True])
def test_interrupted_sibling_transition(command_home, monkeypatch, retire, edited):
    from agent_worktrees import project_command_transaction as tx

    before = seed_old(monkeypatch, command_home)
    if retire:
        monkeypatch.setattr(inst, "_project_is_knowledge_only", lambda _name: True)
    original = tx._replace
    interrupted_path = None

    def interrupted(path, data, mode):
        nonlocal interrupted_path
        original(path, data, mode)
        if interrupted_path is None:
            interrupted_path = path
            raise KeyboardInterrupt("interrupted after first sibling")

    monkeypatch.setattr(tx, "_replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        inst.reconcile_binstubs()
    monkeypatch.setattr(tx, "_replace", original)
    if edited:
        interrupted_path.write_bytes(b"edited intermediate command\n")
        intermediate = {p: p.read_bytes() if p.exists() else None for p in before}
    result = inst.reconcile_binstubs()
    if edited:
        assert result["blocked"] == ["demo"]
        assert {p: p.read_bytes() if p.exists() else None for p in before} == intermediate
    else:
        assert result["blocked"] == []
        assert all(p.exists() != retire for p in before)
        if retire:
            assert not inst._receipt_path("demo").exists()
        else:
            assert "transaction" not in inst._read_receipt("demo")


def test_corrupt_transaction_is_preserved(command_home, monkeypatch):
    seed_old(monkeypatch, command_home)
    receipt = inst._read_receipt("demo")
    receipt["transaction"] = {"version": 1}
    inst._write_receipt("demo", receipt)
    before = {p: p.read_bytes() for p, _ in inst._project_binstub_specs("demo")}
    result = inst.reconcile_binstubs()
    assert result["blocked"] == ["demo"]
    assert {p: p.read_bytes() for p in before} == before
    assert inst._read_receipt("demo") == receipt


def test_unknown_unreceipted_bytes_are_preserved(command_home, monkeypatch, capsys):
    before = seed_old(monkeypatch, command_home, receipted=False)
    p = next(iter(before))
    p.write_bytes(before[p] + b"\necho extra\n")
    before[p] = p.read_bytes()
    result = inst.reconcile_binstubs()
    assert result["blocked"] == ["demo"]
    assert {p: p.read_bytes() for p in before} == before
    assert "in sync" not in capsys.readouterr().out


def test_native_relocated_command_routes_to_selected_payload(
    command_home, monkeypatch, request
):
    windows = inst.platform.system() == "Windows"
    if windows != (os.name == "nt"):
        pytest.skip("native platform command boundary")
    seed_old(monkeypatch, command_home)
    payload = command_home[0] / "bin" / "payload"
    payload.mkdir(parents=True)
    if windows:
        native_powershell = request.getfixturevalue("native_powershell")
        (payload / "agent-worktrees.ps1").write_text(
            'Write-Output ("selected:" + ($args -join ","))\nexit 0\n',
            encoding="utf-8")
        argv = [str(native_powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(inst.local_bin() / "demo.ps1"), "status"]
    else:
        native_bash = request.getfixturevalue("native_bash")
        target = payload / "agent-worktrees"
        target.write_text('#!/usr/bin/env bash\nprintf "selected:%s,%s,%s\\n" "$1" "$2" "$3"\n',
                          encoding="utf-8")
        target.chmod(0o755)
        argv = [str(native_bash), str(inst.local_bin() / "demo"), "status"]
    assert inst.reconcile_binstubs()["blocked"] == []
    result = subprocess.run(argv, capture_output=True, text=True, timeout=15, **inst.no_window_kwargs())
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "selected:--project,demo,status"
