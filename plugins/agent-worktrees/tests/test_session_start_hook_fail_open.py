"""Session-start wrappers tolerate partial deployment without masking producers."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]


def _bash() -> str:
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Git"
            / "bin"
            / "bash.exe",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Programs"
            / "Git"
            / "bin"
            / "bash.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        pytest.skip("Git Bash is not available for Windows-path fixtures")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash is not available")
    return bash


def _powershell() -> str:
    for name in ("pwsh", "powershell.exe", "powershell"):
        executable = shutil.which(name)
        if executable is not None:
            return executable
    pytest.skip("PowerShell is not available")


def _hooks(event: str) -> list[dict[str, object]]:
    hooks = json.loads((_PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    return hooks["hooks"][event]


def test_session_guidance_projection_points_to_hook_written_file():
    projections = json.loads(
        (_PLUGIN / "instruction-projections.json").read_text(encoding="utf-8")
    )
    assert projections == {
        "schema": "copilot-extensions.instruction-projections",
        "version": 1,
        "projections": [
            {
                "id": "session-guidance",
                "template": "instructions/session-guidance.instructions.md",
                "destination": (
                    ".github/instructions/agent-worktrees/"
                    "session-guidance.instructions.md"
                ),
                "customizationKind": "instructions",
                "applyTo": "**",
            }
        ],
    }
    template = (
        _PLUGIN / "instructions" / "session-guidance.instructions.md"
    ).read_text(encoding="utf-8")
    assert "applyTo: \"**\"" in template
    assert "COPILOT_AGENT_SESSION_ID" in template
    assert (
        "instructions/agent-worktrees/session-guidance.instructions.md"
        in template
    )
    assert "its absence is not an error" in template


def _run(command: str, shell: str, home: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in (
        "COPILOT_PLUGIN_ROOT",
        "PLUGIN_ROOT",
        "CLAUDE_PLUGIN_ROOT",
    ):
        env.pop(key, None)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    return subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-Command", command]
        if "powershell" in Path(shell).name.lower()
        or Path(shell).name.lower().startswith("pwsh")
        else [shell, "-c", command],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _stage_stub(command: str, home: Path, cwd: Path, body: str) -> None:
    match = re.search(r"([a-z-]+\.sh)", command)
    assert match is not None
    for directory in (home / ".agent-worktrees" / "bin", cwd / "scripts"):
        directory.mkdir(parents=True, exist_ok=True)
        script = directory / match.group(1)
        script.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")


def _powershell_wrapper(command: str, script: Path) -> str:
    wrapper_start = command.rfind("if (Test-Path $s)")
    assert wrapper_start >= 0
    escaped = str(script).replace("'", "''")
    return f"$s = '{escaped}'; {command[wrapper_start:]}"


def test_bash_hooks_emit_empty_context_when_runtime_scripts_are_absent(tmp_path: Path):
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()

    for hook in _hooks("sessionStart"):
        result = _run(str(hook["bash"]), _bash(), home, cwd)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "{}"


def test_bash_session_end_hook_succeeds_when_runtime_script_is_absent(tmp_path: Path):
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()

    result = _run(str(_hooks("sessionEnd")[0]["bash"]), _bash(), home, cwd)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_bash_hooks_do_not_mask_runtime_script_failures(tmp_path: Path):
    for index, hook in enumerate(_hooks("sessionStart") + _hooks("sessionEnd")):
        home = tmp_path / str(index) / "home"
        cwd = tmp_path / str(index) / "cwd"
        home.mkdir(parents=True)
        cwd.mkdir()
        command = str(hook["bash"])
        if (
            "invoke-context-contributor.sh" in command
            or "hook_client.py" in command
        ):
            continue
        _stage_stub(command, home, cwd, "exit 23")

        result = _run(command, _bash(), home, cwd)
        assert result.returncode == 23


def test_bash_lifecycle_hook_is_one_bounded_client():
    commands = [
        str(hook["bash"])
        for hook in _hooks("sessionStart")
        if "hook_client.py" in str(hook["bash"])
    ]
    assert len(commands) == 1
    assert "sessionStart" in commands[0]


def test_register_session_bash_coalesces_command_catalog(tmp_path: Path):
    home = tmp_path / "home"
    bin_dir = home / ".agent-worktrees" / "bin"
    plugin_root = tmp_path / "plugin"
    scripts_dir = plugin_root / "scripts"
    bin_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    (plugin_root / "plugin.json").write_text(
        json.dumps({"name": "agent-worktrees", "version": "1.2.3"}),
        encoding="utf-8",
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    payload = json.dumps(
        {
            "sessionId": "session-1",
            "cwd": str(worktree),
            "source": "new",
            "timestamp": 1_000,
        }
    )

    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        f"if [ \"$1\" = -c ]; then exec {shlex.quote(sys.executable)} \"$@\"; fi\n"
        "cat >/dev/null\n"
        "printf '%s' '{\"additionalContext\":\"worktree binding\"}'\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    (bin_dir / "resolve-runtime.sh").write_text(
        f'AW_PY="{fake_python}"\n',
        encoding="utf-8",
    )
    catalog = scripts_dir / "emit-command-catalog.sh"
    catalog.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s' '{\"additionalContext\":\"command catalog\"}'\n",
        encoding="utf-8",
    )
    catalog.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["COPILOT_PLUGIN_ROOT"] = str(plugin_root)
    result = subprocess.run(
        [_bash(), str(_PLUGIN / "scripts" / "register-session.sh")],
        input=payload,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "additionalContext": "command catalog\n\nworktree binding"
    }
    state_file = next(
        (home / ".agent-worktrees" / ".session-context").glob(
            "register-session-*"
        )
    )
    state_mtime = state_file.stat().st_mtime_ns

    replay = subprocess.run(
        [
            _bash(),
            str(_PLUGIN / "scripts" / "register-session.sh"),
            "--context-only",
        ],
        input=payload,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout) == {
        "additionalContext": "command catalog\n\nworktree binding"
    }
    assert state_file.stat().st_mtime_ns == state_mtime

    other_session = subprocess.run(
        [
            _bash(),
            str(_PLUGIN / "scripts" / "register-session.sh"),
            "--context-only",
        ],
        input=json.dumps(
            {
                "sessionId": "session-2",
                "cwd": str(worktree),
                "source": "new",
                "timestamp": 1_000,
            }
        ),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert json.loads(other_session.stdout) == {}

    for stale_payload in (
        json.dumps(
            {
                "sessionId": "session-1",
                "cwd": str(tmp_path),
                "source": "new",
                "timestamp": 1_000,
            }
        ),
        json.dumps(
            {
                "sessionId": "session-1",
                "cwd": str(worktree),
                "source": "resume",
                "timestamp": 1_000,
            }
        ),
        json.dumps(
            {
                "sessionId": "session-1",
                "cwd": str(worktree),
                "source": "new",
                "timestamp": 1_001,
            }
        ),
        json.dumps(
            {"sessionId": "session-1", "cwd": str(worktree), "source": "new"}
        ),
        json.dumps(
            {
                "sessionId": "session-1",
                "cwd": str(worktree),
                "source": "new",
                "timestamp": "later",
            }
        ),
    ):
        stale = subprocess.run(
            [
                _bash(),
                str(_PLUGIN / "scripts" / "register-session.sh"),
                "--context-only",
            ],
            input=stale_payload,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert json.loads(stale.stdout) == {}

    (plugin_root / "plugin.json").write_text(
        json.dumps({"name": "agent-worktrees", "version": "1.2.4"}),
        encoding="utf-8",
    )
    changed_version = subprocess.run(
        [
            _bash(),
            str(_PLUGIN / "scripts" / "register-session.sh"),
            "--context-only",
        ],
        input=payload,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert json.loads(changed_version.stdout) == {}


def test_register_session_bash_fails_open_when_merge_python_breaks(tmp_path: Path):
    home = tmp_path / "home"
    bin_dir = home / ".agent-worktrees" / "bin"
    bin_dir.mkdir(parents=True)

    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = -c ]; then exit 23; fi\n"
        "cat >/dev/null\n"
        "printf '%s' '{\"additionalContext\":\"worktree binding\"}'\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    (bin_dir / "resolve-runtime.sh").write_text(
        f'AW_PY="{fake_python}"\n',
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("COPILOT_PLUGIN_ROOT", None)
    result = subprocess.run(
        [_bash(), str(_PLUGIN / "scripts" / "register-session.sh")],
        input='{"sessionId":"session-1","cwd":"/tmp/worktree"}',
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "{}\n"


def test_register_session_bash_omits_catalog_without_binding(tmp_path: Path):
    home = tmp_path / "home"
    bin_dir = home / ".agent-worktrees" / "bin"
    plugin_root = tmp_path / "plugin"
    scripts_dir = plugin_root / "scripts"
    bin_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    (plugin_root / "plugin.json").write_text(
        json.dumps({"name": "agent-worktrees", "version": "1.2.3"}),
        encoding="utf-8",
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    payload = json.dumps(
        {
            "sessionId": "session-1",
            "cwd": str(worktree),
            "source": "new",
            "timestamp": 1_000,
        }
    )

    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        f"if [ \"$1\" = -c ]; then exec {shlex.quote(sys.executable)} \"$@\"; fi\n"
        "cat >/dev/null\n"
        "printf '%s' '{}'\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    (bin_dir / "resolve-runtime.sh").write_text(
        f'AW_PY="{fake_python}"\n',
        encoding="utf-8",
    )
    catalog = scripts_dir / "emit-command-catalog.sh"
    catalog.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s' '{\"additionalContext\":\"command catalog\"}'\n",
        encoding="utf-8",
    )
    catalog.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["COPILOT_PLUGIN_ROOT"] = str(plugin_root)
    result = subprocess.run(
        [_bash(), str(_PLUGIN / "scripts" / "register-session.sh")],
        input=payload,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "{}\n"


def test_powershell_hooks_fail_open_when_runtime_scripts_are_absent(tmp_path: Path):
    powershell = _powershell()
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()

    for hook in _hooks("sessionStart"):
        result = _run(str(hook["powershell"]), powershell, home, cwd)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "{}"

    result = _run(
        str(_hooks("sessionEnd")[0]["powershell"]),
        powershell,
        home,
        cwd,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_powershell_hooks_do_not_mask_runtime_script_failures(tmp_path: Path):
    powershell = _powershell()

    for index, hook in enumerate(_hooks("sessionStart") + _hooks("sessionEnd")):
        home = tmp_path / str(index) / "home"
        cwd = tmp_path / str(index) / "cwd"
        home.mkdir(parents=True)
        cwd.mkdir()
        script = tmp_path / str(index) / "stub.ps1"
        script.write_text("exit 23\n", encoding="utf-8")
        raw_command = str(hook["powershell"])
        if (
            "invoke-context-contributor.ps1" in raw_command
            or "hook_client.py" in raw_command
        ):
            continue
        command = _powershell_wrapper(raw_command, script)

        result = _run(command, powershell, home, cwd)
        assert result.returncode != 0


def test_powershell_lifecycle_hook_is_one_bounded_client():
    commands = [
        str(hook["powershell"])
        for hook in _hooks("sessionStart")
        if "hook_client.py" in str(hook["powershell"])
    ]
    assert len(commands) == 1
    assert "sessionStart" in commands[0]


def test_register_session_powershell_coalesces_context_and_fails_open(
    tmp_path: Path,
):
    powershell = _powershell()
    home = tmp_path / "home"
    bin_dir = home / ".agent-worktrees" / "bin"
    plugin_root = tmp_path / "plugin"
    scripts_dir = plugin_root / "scripts"
    bin_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    (plugin_root / "plugin.json").write_text(
        json.dumps({"name": "agent-worktrees", "version": "1.2.3"}),
        encoding="utf-8",
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    payload = json.dumps(
        {
            "sessionId": "session-1",
            "cwd": str(worktree),
            "source": "new",
            "timestamp": 1_000,
        }
    )

    fake_python = tmp_path / "fake-python.ps1"
    fake_python.write_text(
        "Write-Output '{\"additionalContext\":\"worktree binding\"}'\n",
        encoding="utf-8",
    )
    escaped_python = str(fake_python).replace("'", "''")
    (bin_dir / "resolve-runtime.ps1").write_text(
        f"$AwPy = '{escaped_python}'\n",
        encoding="utf-8",
    )
    catalog = scripts_dir / "emit-command-catalog.ps1"
    catalog.write_text(
        "Write-Output '{\"additionalContext\":\"command catalog\"}'\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["COPILOT_PLUGIN_ROOT"] = str(plugin_root)
    command = [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-File",
        str(_PLUGIN / "scripts" / "register-session.ps1"),
    ]
    result = subprocess.run(
        command,
        input=payload,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "additionalContext": "command catalog\n\nworktree binding"
    }
    state_file = next(
        (home / ".agent-worktrees" / ".session-context").glob(
            "register-session-*"
        )
    )
    state_mtime = state_file.stat().st_mtime_ns

    replay = subprocess.run(
        [*command, "--context-only"],
        input=payload,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout) == {
        "additionalContext": "command catalog\n\nworktree binding"
    }
    assert state_file.stat().st_mtime_ns == state_mtime

    for stale_payload in (
        json.dumps(
            {
                "sessionId": "session-1",
                "cwd": str(tmp_path),
                "source": "new",
                "timestamp": 1_000,
            }
        ),
        json.dumps(
            {
                "sessionId": "session-1",
                "cwd": str(worktree),
                "source": "resume",
                "timestamp": 1_000,
            }
        ),
        json.dumps(
            {
                "sessionId": "session-1",
                "cwd": str(worktree),
                "source": "new",
                "timestamp": 1_001,
            }
        ),
        json.dumps(
            {"sessionId": "session-1", "cwd": str(worktree), "source": "new"}
        ),
        json.dumps(
            {
                "sessionId": "session-1",
                "cwd": str(worktree),
                "source": "new",
                "timestamp": "later",
            }
        ),
    ):
        stale = subprocess.run(
            [*command, "--context-only"],
            input=stale_payload,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert json.loads(stale.stdout) == {}

    (plugin_root / "plugin.json").write_text(
        json.dumps({"name": "agent-worktrees", "version": "1.2.4"}),
        encoding="utf-8",
    )
    changed_version = subprocess.run(
        [*command, "--context-only"],
        input=payload,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert json.loads(changed_version.stdout) == {}
    (plugin_root / "plugin.json").write_text(
        json.dumps({"name": "agent-worktrees", "version": "1.2.3"}),
        encoding="utf-8",
    )

    catalog.write_text(
        "Write-Output 'invalid catalog JSON'\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        command,
        input=payload,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "additionalContext": "worktree binding"
    }

    catalog.write_text(
        "Write-Output '{\"additionalContext\":\"   \"}'\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        command,
        input=payload,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "additionalContext": "worktree binding"
    }

    catalog.write_text(
        "Write-Output '{\"additionalContext\":\"worktree binding\"}'\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        command,
        input=payload,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "additionalContext": "worktree binding"
    }

    fake_python.write_text(
        "Write-Output '{}'\n",
        encoding="utf-8",
    )
    catalog.write_text(
        "Write-Output '{\"additionalContext\":\"command catalog\"}'\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        command,
        input=payload,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "{}"


def test_register_session_scripts_keep_catalog_and_binding_together():
    for name in ("register-session.sh", "register-session.ps1"):
        text = (_PLUGIN / "scripts" / name).read_text(encoding="utf-8")
        assert "emit-command-catalog" in text
        assert "registrationJson" in text or "registration_json" in text
