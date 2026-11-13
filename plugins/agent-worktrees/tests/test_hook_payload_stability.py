"""Raw hook entrypoints must not modify a cold installed payload."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

import pytest
from agent_procutil.owned_process import run_caller_owned

_PLUGIN = Path(__file__).resolve().parents[1]


def _entries(root):
    return {
        str(path.relative_to(root)): (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "directory"
        )
        for path in root.rglob("*")
    }


@pytest.mark.parametrize("event", ["sessionStart", "preToolUse", "postToolUse"])
@pytest.mark.parametrize("host", ["pwsh", "powershell.exe", "bash"])
def test_cold_raw_hook_preserves_payload_without_a_managed_session(tmp_path, event, host):
    if host == "bash" and os.name == "nt":
        pytest.skip("Native POSIX hook entry required")
    shell = shutil.which(host)
    if not shell:
        pytest.skip("Native hook shell unavailable")
    root = tmp_path / "payload"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in ("scripts", "src"):
        shutil.copytree(_PLUGIN / name, root / name, ignore=ignore)
    for name in ("plugin.json", "hooks.json"):
        shutil.copy2(_PLUGIN / name, root / name)
    home = tmp_path / "home"
    home.mkdir()
    cwd = tmp_path / "ordinary-directory"
    cwd.mkdir()
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(root), "COPILOT_PROJECT_DIR": str(cwd),
        "PATH": str(Path(sys._base_executable).parent) + os.pathsep + os.environ["PATH"],
    }
    for key in (
        "COPILOT_EXTENSIONS_CONTEXT", "AGENT_RT_ROOT", "WORKTREE_ID", "WORKTREE_PROJECT",
        "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX",
        "AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION",
    ):
        env.pop(key, None)
    hook = json.loads((root / "hooks.json").read_text())["hooks"][event][0]
    command = hook["bash" if host == "bash" else "powershell"]
    argv = [shell, "-c", command] if host == "bash" else [
        shell, "-NoProfile", "-Command", command,
    ]
    before = _entries(root)
    for _ in range(2):
        result = run_caller_owned(
            argv, cwd=cwd, env=env, timeout=30,
            input_text=json.dumps({"sessionId": "cold-hook", "cwd": str(cwd), "timestamp": 1}),
            max_stdout_bytes=8192, max_stderr_bytes=8192,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        if result.stdout.strip():
            assert isinstance(json.loads(result.stdout), dict)
        assert not (home / ".agent-worktrees" / "versions").exists()
        assert _entries(root) == before


def test_imported_hook_client_preserves_the_runtime_cache_policy(tmp_path):
    for name in ("hook_client.py", "hook_runtime.py"):
        shutil.copy2(_PLUGIN / "scripts" / name, tmp_path / name)
    result = run_caller_owned(
        [
            sys.executable, "-I", "-c",
            "import runpy,sys; before=sys.dont_write_bytecode; "
            "runpy.run_path(sys.argv[1]); "
            "assert sys.dont_write_bytecode == before",
            str(tmp_path / "hook_client.py"),
        ],
        cwd=tmp_path, env=dict(os.environ), timeout=10,
        max_stdout_bytes=8192, max_stderr_bytes=8192,
    )
    assert result.returncode == 0, result.stdout + result.stderr
