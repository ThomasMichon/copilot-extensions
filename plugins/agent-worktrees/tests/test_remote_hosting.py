"""Remote launch admission is automatic; ordinary inspection stays ungated."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_worktrees import remote_hosting


@pytest.mark.parametrize("args", [
    ["list", "--json"], ["status"], ["create", "--json"],
    ["resolve", "--json", "--launch-request-status", "receipt"],
    ["handoff-cutover", "--retire-pane", "pane"], ["--help"],
])
def test_inspection_does_not_receive_a_launch_admission_gate(args: list[str]) -> None:
    from agent_worktrees.remote_seed_launch import shell_command

    assert remote_hosting.shell_command("bash", "source", args) == shell_command(
        "bash", ["source", *args],
    )


@pytest.mark.parametrize("args", [
    [], ["--new"], ["resolve", "--new"], ["embody", "--anchor"],
    ["--worktree-id", "retained"], ["create", "--seed", "prompt"],
])
def test_normal_launch_automatically_checks_its_receiving_owner(args: list[str]) -> None:
    command = remote_hosting.shell_command("pwsh", "target", args)
    script = base64.b64decode(command.split("-EncodedCommand ", 1)[1]).decode("utf-16le")
    assert script.index("'agent-policy' 'check'") < script.rindex("& 'target'")
    assert "--require-agent-hosting" not in script
    if "--worktree-id" in args:
        assert "'check' '--worktree-id' 'retained'" in script
    else:
        assert "'check' '--project-root'" in script


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="native Bash")
@pytest.mark.parametrize("reply", ["allowed", "legacy", "malformed", "denied"])
def test_real_remote_launch_refuses_before_any_effect(
    tmp_path: Path, reply: str,
) -> None:
    target = tmp_path / "target"
    effects = tmp_path / "effects"
    payload = {
        "version": 1, "repository": str(tmp_path), "expose_agent": None,
        "sources": [], "denied_by": [], "allowed": True,
    }
    target.write_text(
        "#!/usr/bin/env python3\n"
        "import json,sys\nfrom pathlib import Path\n"
        "if sys.argv[1:3]==['agent-policy','check']:\n"
        f" reply={reply!r}\n"
        " if reply=='legacy': raise SystemExit(2)\n"
        " if reply=='denied': print('expose_agent: false'); raise SystemExit(3)\n"
        f" print(json.dumps({payload!r}) if reply=='allowed' else '{{}}')\n"
        "else:\n"
        f" Path({str(effects)!r}).write_text('launch or allocation')\n",
        encoding="utf-8",
    )
    target.chmod(0o700)
    command = remote_hosting.shell_command("bash", str(target), ["resolve", "--new"])
    process = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True, check=False, timeout=15,
    )
    assert (process.returncode == 0) is (reply == "allowed")
    assert effects.exists() is (reply == "allowed")


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="native Bash")
@pytest.mark.parametrize("interpreter", ["python3", "python", None])
def test_remote_launch_respects_available_python_names(
    tmp_path: Path, interpreter: str | None,
) -> None:
    binary = tmp_path / "bin"
    binary.mkdir()
    if interpreter is not None:
        (binary / interpreter).symlink_to(sys.executable)
    if interpreter == "python":
        assert shutil.which("python3", path=str(binary)) is None
    target = tmp_path / "target"
    effects = tmp_path / "effects"
    payload = json.dumps({
        "version": 1, "repository": str(tmp_path), "expose_agent": None,
        "sources": [], "denied_by": [], "allowed": True,
    })
    target.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = agent-policy ]; then\n"
        f" printf '%s\\n' {shlex.quote(payload)}\n"
        f"else printf launch > {shlex.quote(str(effects))}; fi\n",
        encoding="utf-8",
    )
    target.chmod(0o700)
    command = remote_hosting.shell_command("bash", str(target), ["--new"])
    shell = shutil.which("bash")
    assert shell is not None
    process = subprocess.run(
        [shell, "-c", shlex.split(command)[-1]],
        env={**os.environ, "PATH": str(binary)}, capture_output=True,
        text=True, check=False, timeout=15,
    )
    assert (process.returncode == 0) is (interpreter is not None), process.stderr
    assert effects.exists() is (interpreter is not None)
    if interpreter is None:
        assert "requires Python on PATH" in process.stderr


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="native Bash")
@pytest.mark.parametrize("selector", [["--new"], ["--worktree-id", "retained"]])
def test_real_seed_preparation_refuses_legacy_owner_before_allocation_or_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selector: list[str],
) -> None:
    from agent_worktrees import remote_seed_launch

    target = tmp_path / "target"
    effects = tmp_path / "effects"
    probe = tmp_path / "policy-probe"
    target.write_text(
        "#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\n"
        "if sys.argv[1:3]==['agent-policy','check']:\n"
        f" Path({str(probe)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        " raise SystemExit(2)\n"
        f"Path({str(effects)!r}).write_text('allocated or staged')\nprint('{{}}')\n",
        encoding="utf-8",
    )
    target.chmod(0o700)
    monkeypatch.setattr(remote_seed_launch.cfg, "project_name", lambda: str(target))

    def ssh(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", argv[-1]], capture_output=True, text=True,
            check=False, timeout=15,
        )

    monkeypatch.setattr(remote_seed_launch, "subprocess", SimpleNamespace(
        run=ssh, TimeoutExpired=subprocess.TimeoutExpired,
    ))
    with pytest.raises(RuntimeError, match="admission .* failed"):
        remote_seed_launch.prepare(None, "example-host", "bash", selector, "prompt")
    assert probe.is_file()
    query = json.loads(probe.read_text(encoding="utf-8"))
    assert ("--worktree-id" in query) is ("--worktree-id" in selector)
    assert not effects.exists()


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell")
@pytest.mark.parametrize("reply", ["allowed", "legacy", "malformed", "denied"])
def test_real_native_remote_launch_checks_before_any_effect(
    tmp_path: Path, reply: str,
) -> None:
    target = tmp_path / "target.ps1"
    effects = tmp_path / "effects"
    policy = json.dumps({
        "version": 1, "repository": str(tmp_path), "expose_agent": None,
        "sources": [], "denied_by": [], "allowed": True,
    }).replace("'", "''")
    if reply == "allowed":
        output = policy
    elif reply == "denied":
        output = ('{"version":1,"code":"repository-agent-exposure-denied",'
                  '"error":"expose_agent: false"}')
    else:
        output = "{}"
    code = 2 if reply == "legacy" else 3 if reply == "denied" else 0
    target.write_text(
        "if ($args[0] -eq 'agent-policy') {\n"
        f" Write-Output '{output}'; $global:LASTEXITCODE={code}; return\n"
        "}\n"
        f"[IO.File]::WriteAllText('{str(effects).replace(chr(39), chr(39)*2)}','launch')\n"
        "$global:LASTEXITCODE=0\n",
        encoding="utf-8",
    )
    command = remote_hosting.shell_command("pwsh", str(target), ["resolve", "--new"])
    process = subprocess.run(
        shlex.split(command), capture_output=True, text=True, check=False, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert (process.returncode == 0) is (reply == "allowed")
    assert effects.exists() is (reply == "allowed")
    if reply == "denied":
        assert process.returncode == 3
        assert "expose_agent: false" in process.stderr
