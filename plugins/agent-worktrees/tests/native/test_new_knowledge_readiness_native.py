"""Local New is unbound-safe without granting personal storage or coordination."""

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
from types import SimpleNamespace
from typing import Any

from agent_procutil import no_window_kwargs
import pytest

pytestmark = [pytest.mark.real_installer_processes, pytest.mark.timeout(60)]


@pytest.fixture
def native_launch(tmp_path: Path) -> SimpleNamespace:
    home, anchor = tmp_path / "home", tmp_path / "repository"
    worktrees = tmp_path / "worktrees"

    def write(path: Path, value: object) -> None:
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
    write(home / ".sample" / "config.yaml", config)
    environment = {
        **os.environ, "AGENT_HOME": str(home), "HOME": str(home), "USERPROFILE": str(home),
    }
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    entry = Path(sysconfig.get_path("scripts")) / (
        "agent-worktrees.exe" if os.name == "nt" else "agent-worktrees"
    )
    assert entry.is_file()

    def invoke(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(entry), "--project", "sample", *arguments],
            cwd=anchor, env=environment, capture_output=True, text=True, timeout=20,
            **no_window_kwargs(),
        )

    return SimpleNamespace(
        home=home, anchor=anchor, worktrees=worktrees, config=config,
        write=write, invoke=invoke, environment=environment,
    )


def test_native_new_refuses_unresolved_binding_without_allocating(
    native_launch: SimpleNamespace,
) -> None:
    fixture = native_launch
    home, anchor, worktrees = fixture.home, fixture.anchor, fixture.worktrees
    invoke = fixture.invoke
    fixture.write(home / ".sample" / "config.yaml", {
        **fixture.config, "knowledge_repo": "missing",
    })
    original = {
        str(path.relative_to(anchor)): path.read_bytes()
        for path in anchor.rglob("*") if path.is_file()
    }
    readiness = invoke("coordination-readiness")
    if readiness.returncode != 3:
        pytest.fail(readiness.stdout + readiness.stderr)
    observation = json.loads(readiness.stdout)
    assert observation["error"] == "explicit knowledge binding is unresolved"
    assert observation.get("ready") is not True
    result = invoke("resolve", "--json", "--new")
    assert result.returncode != 0, (result.stdout, result.stderr)
    expected = "knowledge binding is unresolved"
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


@pytest.mark.parametrize("protocol", ["ordinary", "manager", "request-v1", "request-v2"])
def test_native_unbound_new_tracks_one_workspace_and_preserves_dependent_gates(
    native_launch: SimpleNamespace, protocol: str,
) -> None:
    fixture = native_launch
    for arguments in (
        ["init", "--template=", "--initial-branch=main"],
        ["add", "."],
        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
         "-c", "commit.gpgsign=false", "-c", "core.hooksPath=",
         "commit", "--quiet", "-m", "Fixture"],
    ):
        result = subprocess.run(
            ["git", *arguments], cwd=fixture.anchor, env=fixture.environment,
            capture_output=True, text=True, timeout=20, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def plan(*arguments: str) -> dict[str, Any]:
        result = fixture.invoke("resolve", "--json", *arguments)
        assert result.returncode == 0, result.stdout + result.stderr
        payload: dict[str, Any] = json.loads(result.stdout)
        return payload

    if protocol in {"ordinary", "manager"}:
        arguments = ["--new", "--no-mux"]
        if protocol == "manager":
            arguments.extend([
                "--require-launch-attempt", "--launch-attempt-id", "a" * 32,
            ])
    else:
        version = int(protocol[-1])
        request_id = str(version) * 32
        request = {
            "version": version, "request_id": request_id, "kind": "new",
            "worktree_id": None, "text": "First task", "no_mux": True,
        }
        if version == 2:
            request.update(attempt_id=None, native_seed_required=True)
        encoded = base64.b64encode(json.dumps(request).encode()).decode()
        arguments = ["--launch-request-b64", encoded]
        if version == 2:
            arguments.append("--require-native-seed")
    created = plan(*arguments)
    if protocol in {"ordinary", "manager"}:
        assert created["launch"]["cmd"]
        worktree_id = created["worktree"]["id"]
        if protocol == "manager":
            receipt = created["launch"]["launch_attempt_receipt"]
            assert receipt["phase"] == "plan-ready"
            assert receipt["worktree_id"] == worktree_id
    else:
        worktree_id = created["worktree_id"]
        assert created["seed_id"] == request["request_id"]
    checkout = fixture.worktrees / worktree_id
    assert (checkout / ".git").is_file()
    tracking = fixture.home / ".sample" / "worktrees"
    records = list(tracking.glob("*.yaml"))
    assert len(records) == 1
    assert worktree_id in records[0].name
    if protocol.startswith("request-"):
        assert plan(*arguments)["worktree_id"] == worktree_id
        assert len(list(fixture.worktrees.iterdir())) == 1
        # Settle the synthetic consumer before testing completed-session Resume.
        delivered = subprocess.run(
            [sys.executable, "-c", """
import json
import sys
from pathlib import Path
from agent_worktrees import config, launch_seed_state
config.set_active_project("sample")
path = Path(sys.argv[1])
receipt = launch_seed_state.take(path, seed_id=sys.argv[2], kind="new")
assert receipt["seed"]["text"] == "First task"
assert launch_seed_state.finish(path, receipt) == {"finished": True}
assert launch_seed_state.peek(path) is None
print(json.dumps({"seed_id": receipt["seed"]["seed_id"], "finished": True}))
""", str(records[0]), request_id],
            cwd=fixture.anchor, env=fixture.environment, capture_output=True,
            text=True, timeout=20, **no_window_kwargs(),
        )
        assert delivered.returncode == 0, delivered.stdout + delivered.stderr
        assert json.loads(delivered.stdout) == {
            "seed_id": request["request_id"], "finished": True,
        }
        replay = fixture.invoke("resolve", "--json", *arguments)
        assert replay.returncode != 0, replay.stdout + replay.stderr
        assert "completed or superseded" in json.loads(replay.stdout)["error"]
    resumed = plan("--worktree-id", worktree_id, "--no-mux")
    assert resumed["launch"]["cmd"] and resumed["worktree"]["id"] == worktree_id
    assert resumed["launch"]["seed_pending"] is False

    for command in ("state-root", "coordination-readiness"):
        result = fixture.invoke(command, "--json") if command == "state-root" else (
            fixture.invoke(command)
        )
        assert result.returncode == 3, result.stdout + result.stderr
        observed = json.loads(result.stdout)
        if command == "state-root":
            assert observed["state_root"] is None and observed["bound"] is False
        else:
            assert observed["ready"] is False
            assert observed["code"] == "knowledge_binding_required"
    before_claim = records[0].read_bytes()
    claim = fixture.invoke(
        "claims", "add", "workdir", str(fixture.anchor), "--json",
        "--owner-ref", f"fixture/sample/{worktree_id}",
    )
    assert claim.returncode == 3, claim.stdout + claim.stderr
    assert json.loads(claim.stdout)["code"] == "knowledge_binding_required"
    assert records[0].read_bytes() == before_claim
    assert not list(fixture.home.rglob("*.paired-allocation.json"))
    assert "knowledge_repo" not in json.loads(
        (fixture.home / ".sample" / "config.yaml").read_text(encoding="utf-8"),
    )
