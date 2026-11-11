"""Cold session snapshot callers must preserve every admitted payload entry."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

import pytest
from agent_procutil.owned_process import run_caller_owned

from test_snapshot_runtime import _cell, _configured_caller, _snapshot


def _entries(root):
    return {
        str(path.relative_to(root)): (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "directory"
        )
        for path in root.rglob("*")
    }


@pytest.mark.parametrize("kind", ["writer", "mesh"])
@pytest.mark.parametrize("host", ["pwsh", "powershell.exe", "bash"])
def test_cold_snapshot_preserves_complete_payload(tmp_path, monkeypatch, kind, host):
    if host == "bash" and os.name == "nt":
        pytest.skip("Native POSIX snapshot wrapper required")
    shell = shutil.which(host)
    if not shell:
        pytest.skip("Native snapshot shell unavailable")
    _, payloads = _cell(tmp_path / "cell", monkeypatch, legacy=True, cold_payload=True)
    repo, _ = _configured_caller(tmp_path)
    monkeypatch.setenv("PATH", str(Path(sys._base_executable).parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "owned-cache"))
    suffix = "sh" if host == "bash" else "ps1"
    stem = "write-session-guidance" if kind == "writer" else "emit-mesh-pointer"
    script = payloads["agent-ssh"] / "scripts" / f"{stem}.{suffix}"
    argv = [shell, str(script)] if host == "bash" else [
        shell, "-NoProfile", "-File", str(script),
    ]
    before = {name: _entries(root) for name, root in payloads.items()}
    for _ in range(2):
        result = run_caller_owned(
            argv, cwd=repo, env=dict(os.environ),
            input_text=json.dumps({"sessionId": "session-1", "cwd": str(repo)}),
            timeout=40, max_stdout_bytes=8192, max_stderr_bytes=8192,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        if kind == "writer":
            assert json.loads(result.stdout) == {}
            value = _snapshot(Path.home())
        else:
            context = json.loads(result.stdout)["additionalContext"]
            value = json.loads(context.split("```json\n", 1)[1].split("\n```", 1)[0])
        assert value["state_root"] == "bound"
        assert value["topology"]["status"] == "available"
        assert {name: _entries(root) for name, root in payloads.items()} == before
