"""Native hook consumers must not mutate their installed source payload."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("native_adapter", [False, True])
def test_native_post_tool_hook_keeps_payload_content_only(tmp_path: Path, native_adapter: bool) -> None:
    shell = shutil.which("pwsh" if os.name == "nt" else "bash")
    if not shell:
        pytest.skip("Native hook shell unavailable")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for source in (PLUGIN / "scripts").iterdir():
        if source.is_file() and source.suffix == ".py":
            shutil.copyfile(source, scripts / source.name)
    if native_adapter:
        shutil.copyfile(PLUGIN / "scripts" / "post-tool-hook.ps1", scripts / "post-tool-hook.ps1")
        # Admission is a fixture seam; the shipped adapter and Python client run unchanged.
        (scripts / "managed-session.ps1").write_text(
            "function Test-ManagedWorktreesSession { param([string]$Raw) return $true }\n",
            encoding="utf-8",
        )
    witness = tmp_path / "import-observed"
    with (scripts / "hook_runtime.py").open("a", encoding="utf-8") as stream:
        stream.write(
            "\nfrom pathlib import Path as _WitnessPath\n"
            "import os as _witness_os\nimport sys as _witness_sys\n"
            "_WitnessPath(_witness_os.environ['HOOK_HYGIENE_WITNESS']).write_text("
            "str(_witness_sys.dont_write_bytecode), encoding='utf-8')\n"
        )
    home = tmp_path / "home"
    home.mkdir()
    before = {path.relative_to(payload): path.read_bytes()
              for path in payload.rglob("*") if path.is_file()}
    hook = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))["hooks"]["postToolUse"][0]
    command = (
        [shell, "-NoLogo", "-NoProfile", "-Command", hook["powershell"]]
        if os.name == "nt" else [shell, "-c", hook["bash"]]
    )
    environment = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload),
        "HOOK_HYGIENE_WITNESS": str(witness),
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
    }
    for name in ("COPILOT_EXTENSIONS_CONTEXT", "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"):
        environment.pop(name, None)
    for _ in range(2):
        witness.unlink(missing_ok=True)
        result = subprocess.run(
            command, input=json.dumps({"cwd": str(home), "toolName": "view"}),
            env=environment, cwd=home, capture_output=True, text=True, encoding="utf-8",
            timeout=25, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        expected_diagnostic = (
            "agent-worktrees postToolUse: resident request unavailable; using local advisory fallback\n"
            if native_adapter and os.name == "nt" else ""
        )
        assert result.stderr == expected_diagnostic
        assert witness.read_text(encoding="utf-8") == "True"
        assert {path.relative_to(payload): path.read_bytes()
                for path in payload.rglob("*") if path.is_file()} == before


def test_native_guard_keeps_payload_content_only(tmp_path: Path) -> None:
    shell = shutil.which("pwsh" if os.name == "nt" else "bash")
    if not shell:
        pytest.skip("Native hook shell unavailable")
    guard = PLUGIN.parent / "copilot-extensions-maintainer"
    payload = tmp_path / "payload space's [literal]"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(guard / "plugin.json", payload / "plugin.json")
    for source in (guard / "scripts").glob("*.py"):
        shutil.copyfile(source, scripts / source.name)
    witness = tmp_path / "import-observed"
    with (scripts / "pretool_killswitch.py").open("a", encoding="utf-8") as stream:
        stream.write(
            "\nfrom pathlib import Path as _WitnessPath\n"
            "import os as _witness_os\nimport sys as _witness_sys\n"
            "_WitnessPath(_witness_os.environ['HOOK_HYGIENE_WITNESS']).write_text("
            "str(_witness_sys.dont_write_bytecode), encoding='utf-8')\n"
        )
    before = {path.relative_to(payload): path.read_bytes()
              for path in payload.rglob("*") if path.is_file()}
    state = tmp_path / "pretool.json"
    state.write_text('{"active": false}', encoding="utf-8")
    hook = json.loads((guard / "hooks.json").read_text(encoding="utf-8"))["hooks"]["preToolUse"][0]
    command = (
        [shell, "-NoLogo", "-NoProfile", "-Command", hook["powershell"]]
        if os.name == "nt" else [shell, "-c", hook["bash"]]
    )
    environment = {
        **os.environ, "COPILOT_PLUGIN_ROOT": str(payload),
        "PRETOOL_KILLSWITCH_STATE_FILE": str(state),
        "HOOK_HYGIENE_WITNESS": str(witness),
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
    }
    for name in ("COPILOT_EXTENSIONS_CONTEXT", "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"):
        environment.pop(name, None)
    for _ in range(2):
        witness.unlink(missing_ok=True)
        result = subprocess.run(
            command, input=json.dumps({"toolName": "view", "cwd": str(tmp_path)}),
            env=environment, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
            timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == "" and result.stderr == ""
        assert witness.read_text(encoding="utf-8") == "True"
        assert {path.relative_to(payload): path.read_bytes()
                for path in payload.rglob("*") if path.is_file()} == before
