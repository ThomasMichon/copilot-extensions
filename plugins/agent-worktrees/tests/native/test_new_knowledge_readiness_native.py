"""New authoring consumes actual native readiness before repository allocation."""

import base64
import json
import os
from pathlib import Path
import subprocess
import sysconfig

from agent_procutil import no_window_kwargs
import pytest

pytestmark = [pytest.mark.real_installer_processes, pytest.mark.timeout(60)]


@pytest.mark.parametrize("configured", [False, True], ids=["unbound", "unresolved"])
def test_native_new_refuses_required_knowledge_without_allocating(tmp_path, configured):
    home, anchor = tmp_path / "home", tmp_path / "repository"
    worktrees = tmp_path / "worktrees"

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    write(anchor / ".agent-worktrees" / "config.yaml", {
        "repo_name": "sample", "stateless": True,
    })
    platform = "windows" if os.name == "nt" else "linux"
    write(home / ".agent-worktrees" / "projects.yaml", {"projects": ["sample"]})
    write(home / ".agent-worktrees" / "repos.yaml", {"repos": {
        "sample": {"class": "worktree", platform: str(anchor)},
    }})
    config = {
        "repo_name": "sample", "machine": "fixture",
        "repos": {"sample": {
            "anchor": str(anchor), "worktree_root": str(worktrees),
        }},
    }
    if configured:
        config["knowledge_repo"] = "missing"
    write(home / ".sample" / "config.yaml", config)
    original = {
        str(path.relative_to(anchor)): path.read_bytes()
        for path in anchor.rglob("*") if path.is_file()
    }
    environment = {
        **os.environ, "AGENT_HOME": str(home), "HOME": str(home), "USERPROFILE": str(home),
    }
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    entry = Path(sysconfig.get_path("scripts")) / (
        "agent-worktrees.exe" if os.name == "nt" else "agent-worktrees"
    )
    assert entry.is_file()

    def invoke(*arguments):
        return subprocess.run(
            [str(entry), "--project", "sample", *arguments],
            cwd=anchor, env=environment, capture_output=True, text=True, timeout=20,
            **no_window_kwargs(),
        )

    readiness = invoke("coordination-readiness")
    if readiness.returncode != 3:
        pytest.fail(readiness.stdout + readiness.stderr)
    observation = json.loads(readiness.stdout)
    if configured:
        assert observation["error"] == "explicit knowledge binding is unresolved"
        assert observation.get("ready") is not True
    else:
        assert observation["version"] == 1 and observation["ready"] is False
    result = invoke("resolve", "--json", "--new")
    assert result.returncode != 0, (result.stdout, result.stderr)
    expected = "knowledge binding is unresolved" if configured else (
        "New authoring requires knowledge readiness before allocation"
    )
    assert expected in json.loads(result.stdout)["error"]
    assert "Traceback" not in readiness.stderr + result.stderr
    for version in (1, 2):
        request = {
            "version": version, "request_id": str(version) * 32, "kind": "new",
            "worktree_id": None, "text": "First task", "no_mux": True,
        }
        if version == 2:
            request.update(attempt_id=None, native_seed_required=True)
        encoded = base64.b64encode(json.dumps(request).encode()).decode()
        structured = invoke(
            "resolve", "--json", "--launch-request-b64", encoded,
            *(["--require-native-seed"] if version == 2 else []),
        )
        assert structured.returncode != 0, (structured.stdout, structured.stderr)
        assert expected in json.loads(structured.stdout)["error"]
        assert "Traceback" not in structured.stderr
    assert not list(home.rglob("launch-requests"))
    assert not worktrees.exists()
    assert {
        str(path.relative_to(anchor)): path.read_bytes()
        for path in anchor.rglob("*") if path.is_file()
    } == original
