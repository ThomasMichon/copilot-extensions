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


@pytest.mark.parametrize("target_state", ["ready", "missing-launcher", "incompatible"])
def test_real_target_list_process_preserves_private_fixture_and_continuation(
    tmp_path, monkeypatch, capsys, target_state,
):
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
    # Only the transport executable is synthetic. Keep the real proxy/process
    # owner, configured target shell, absolute target launcher and Manager CLI.
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
