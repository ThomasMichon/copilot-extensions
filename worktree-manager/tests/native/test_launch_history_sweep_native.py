"""Actual transport subprocess and target Manager reader, not live SSH admission."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import textwrap

import pytest
from machine_transport import MachineEntry, SSHEnvironment
from ssh_manager.config_sources import SSHConfig
from ssh_manager.proxy import create_ssh_subprocess

from worktree_manager import launch_history_cli, launch_history_sweep as sweep, machine_roster
from worktree_manager.launch_attempts import LaunchAttempts


def _target(tmp_path, monkeypatch, target_state="ready", *, engine_ready=False):
    windows = os.name == "nt"
    shell = "pwsh" if windows else "bash"
    shell_executable = shutil.which(shell)
    if not shell_executable:
        pytest.skip("Explicit native lane requires the target shell")
    target_home = tmp_path / "target"
    target_home.mkdir()
    manager = target_home / ".worktree-manager"
    store = LaunchAttempts(manager)
    for identity in ("a" * 32, "b" * 32):
        store.record_intent(
            identity, action="new", project="example", surface="native-sweep",
            is_local=True, prompt=" PRIVATE SYNTHETIC INPUT\n ", options={"no_mux": True},
        )
    bin_dir = target_home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    if windows:
        launcher = bin_dir / "worktree-manager.ps1"
        executable = "'" + sys.executable.replace("'", "''") + "'"
        launcher.write_text(
            f"& {executable} -B -m worktree_manager @args\nexit $LASTEXITCODE\n", encoding="utf-8",
        )
    else:
        launcher = bin_dir / "worktree-manager"
        launcher.write_text(
            "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' -B -m worktree_manager "$@"\n',
            encoding="utf-8",
        )
        launcher.chmod(0o700)
    if target_state == "missing-launcher":
        launcher.unlink()
    elif target_state == "incompatible":
        launcher.write_text(
            "'unsupported old reader'\nexit 0\n" if windows
            else "#!/bin/sh\nprintf 'unsupported old reader\\n'\nexit 0\n",
            encoding="utf-8",
        )
    transport = tmp_path / "transport.py"
    transport.write_text(textwrap.dedent("""
        import os, subprocess, sys
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        command = sys.argv[-1]
        args = command.split() if os.name == "nt" else ["/bin/sh", "-c", command]
        result = subprocess.run(args, capture_output=True, timeout=15, **options)
        sys.stdout.buffer.write(result.stdout)
        sys.stdout.buffer.flush()
        sys.stderr.buffer.write(result.stderr)
        sys.stderr.buffer.flush()
        raise SystemExit(result.returncode)
    """), encoding="utf-8")
    environment = dict(os.environ)
    environment.update({
        "HOME": str(target_home), "USERPROFILE": str(target_home),
        "WORKTREE_MANAGER_ROOT": str(manager), "PYTHONDONTWRITEBYTECODE": "1",
        "WORKTREE_MANAGER_ENGINE_CMD": "this-must-never-be-run",
    })
    for name in ("COPILOT_EXTENSIONS_CONTEXT", "WORKTREE_MANAGER_ENGINE_ARGV"):
        environment.pop(name, None)
    if engine_ready:
        engine = manager / "synthetic_engine.py"
        engine.write_text(textwrap.dedent("""
            import json, sys
            attempt = sys.argv[sys.argv.index("launch-attempts") + 1]
            operation = "d" * 32
            print(json.dumps({
                "schema": "worktree-launch-attempt-inspection", "version": 1,
                "attempt_id": attempt, "scope": "local-project", "admissions": [], "errors": [],
                "has_more": False, "next_after": None, "prompt_acceptance": "unknown",
                "receipts": [{
                    "source": "resolution", "operation_id": operation, "evidence": {
                        "schema": "worktree-launch-resolution", "version": 1,
                        "attempt_id": attempt, "operation_id": operation,
                        "input": "UNEXPECTED PRIVATE ENGINE FIELD",
                        "events": [
                            {"at": "2026-10-25T00:00:00+00:00",
                             "phase": "resolution-requested", "details": {}},
                            {"at": "2026-10-25T00:00:01+00:00",
                             "phase": "plan-ready", "details": {"exit_code": 0}},
                        ],
                    },
                }],
            }))
        """), encoding="utf-8")
        environment.pop("WORKTREE_MANAGER_ENGINE_CMD")
        environment["WORKTREE_MANAGER_ENGINE_ARGV"] = json.dumps([sys.executable, "-B", str(engine)])
    platform = "windows" if windows else "linux"
    entry = MachineEntry(
        key="fixture-" + platform, display_name="Synthetic target", environment=platform,
        execution_platform=platform, physical_host="fixture", ssh_ready=True,
        ssh_environments=[SSHEnvironment(platform, "fixture-alias", shell)],
    )
    monkeypatch.setattr(machine_roster, "selected_entries", lambda _: {entry.key: entry})
    config = SSHConfig(host_alias="fixture-alias", proxy_command="none")
    monkeypatch.setattr(sweep, "SSHProfileSource", lambda *a, **kw: type(
        "Profile", (), {"get_ssh_config": lambda self: config},
    )())
    # The transport and optional engine fixture are synthetic. Keep the real
    # process owner, target shell, absolute target launcher and Manager CLI.
    async def spawn(*args, **kwargs):
        kwargs["env"] = environment
        return await create_ssh_subprocess(
            sys.executable, "-B", str(transport), *args[1:], **kwargs,
        )
    monkeypatch.setattr(sweep, "create_ssh_subprocess", spawn)
    def snapshot():
        # Native shells may maintain their own OS caches outside Manager state.
        return {
            path.relative_to(target_home): path.read_bytes()
            for root in (manager, bin_dir) for path in root.rglob("*") if path.is_file()
        }
    return store, entry, snapshot


@pytest.mark.parametrize("target_state", ["ready", "missing-launcher", "incompatible"])
def test_real_target_list_process_preserves_private_fixture_and_continuation(
    tmp_path, monkeypatch, capsys, target_state,
):
    _, entry, snapshot = _target(tmp_path, monkeypatch, target_state)
    before = snapshot()
    query = ["--sweep-project", "example", "--source-machine", entry.key, "--limit", "1", "--json"]
    status = launch_history_cli.main(query)
    output = capsys.readouterr().out
    if target_state != "ready":
        source = json.loads(output)["sources"][0]
        assert status == 1 and source["status"] == "unavailable"
        assert source["code"] == (
            "target-reader-unavailable" if target_state == "missing-launcher" else "incompatible-response"
        )
        assert "unsupported old reader" not in output
        assert snapshot() == before
        return
    assert status == 0
    first = json.loads(output)["sources"][0]["page"]
    assert first["has_more"] is True and len(first["attempts"]) == 1
    assert "PRIVATE SYNTHETIC INPUT" not in output and '"input"' not in output
    assert launch_history_cli.main([
        *query, "--before", str(first["next_before"]), "--before-id", first["next_before_id"],
    ]) == 0
    second = json.loads(capsys.readouterr().out)["sources"][0]["page"]
    assert second["has_more"] is False and len(second["attempts"]) == 1
    assert first["attempts"][0]["attempt_id"] != second["attempts"][0]["attempt_id"]
    assert snapshot() == before


def test_real_target_exact_private_detail_and_partial_engine_are_read_only(tmp_path, monkeypatch, capsys):
    store, entry, snapshot = _target(tmp_path, monkeypatch)
    store.append("a" * 32, owner="manager", phase="unknown", code="interrupted")
    with monkeypatch.context() as expired:
        expired.setattr("worktree_manager.launch_attempts._timestamp", lambda: 1)
        store.record_intent(
            "c" * 32, action="resume", project="example", surface="expired-native",
            is_local=True, prompt="EXPIRED PRIVATE INPUT",
        )
    before = snapshot()
    query = ["a" * 32, "--sweep-project", "example", "--source-machine", entry.key, "--json"]
    assert launch_history_cli.main([*query, "--event-limit", "1"]) == 1
    raw = capsys.readouterr().out
    source = json.loads(raw)["source"]
    assert source["status"] == "partial" and source["target_exit_code"] == 1
    assert source["detail"]["engine"]["status"] == "unavailable"
    first = source["detail"]["attempt"]
    assert first["events_has_more"] and first["manager_outcome"] == "interrupted"
    assert "PRIVATE SYNTHETIC INPUT" not in raw and '"input"' not in raw
    assert "this-must-never-be-run" not in raw
    assert launch_history_cli.main([
        *query, "--after-sequence", str(first["next_event_sequence"]),
        "--event-limit", "1", "--include-input",
    ]) == 1
    detail = json.loads(capsys.readouterr().out)["source"]["detail"]["attempt"]
    assert [event["phase"] for event in detail["events"]] == ["unknown"]
    assert not detail["events_has_more"]
    assert detail["input"] == {"prompt": " PRIVATE SYNTHETIC INPUT\n ", "options": {"no_mux": True}}
    assert launch_history_cli.main([
        "c" * 32, "--sweep-project", "example", "--source-machine", entry.key,
        "--include-input", "--json",
    ]) == 1
    raw = capsys.readouterr().out
    assert json.loads(raw)["source"]["code"] == "unreadable-or-expired"
    assert "EXPIRED PRIVATE INPUT" not in raw and '"detail"' not in raw
    assert snapshot() == before


def test_real_target_exact_detail_projects_engine_without_echoing_raw_fields(tmp_path, monkeypatch, capsys):
    _, entry, snapshot = _target(tmp_path, monkeypatch, engine_ready=True)
    before = snapshot()
    assert launch_history_cli.main([
        "a" * 32, "--sweep-project", "example", "--source-machine", entry.key, "--json",
    ]) == 0
    raw = capsys.readouterr().out
    source = json.loads(raw)["source"]
    assert source["status"] == "read" and source["target_exit_code"] == 0
    engine = source["detail"]["engine"]
    assert engine["status"] == "read" and engine["absence"] == "unknown"
    assert engine["owner_observations"]["resolution"] == [{
        "operation_id": "d" * 32, "last_phase": "plan-ready",
        "at": "2026-10-25T00:00:01+00:00", "terminal_phase": "plan-ready",
        "recorded_exit_code": 0, "wrapper_exit_code": None,
    }]
    assert "UNEXPECTED PRIVATE ENGINE FIELD" not in raw
    assert "PRIVATE SYNTHETIC INPUT" not in raw and '"input"' not in raw
    assert snapshot() == before
