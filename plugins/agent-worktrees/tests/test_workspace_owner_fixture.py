"""Cross-process contracts for the synthetic owner's real command surface."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable

import pytest
from agent_procutil import no_window_kwargs
import yaml
from agent_worktrees import config as cfg


OWNER = Path(__file__).with_name("workspace_owner_fixture.py")
OwnerCommand = Callable[..., subprocess.CompletedProcess[str]]
Owner = tuple[Path, OwnerCommand]


@pytest.fixture
def owner(tmp_path: Path) -> Owner:
    root = tmp_path / "owner"
    anchor = root / "anchor"
    anchor.mkdir(parents=True)
    authority = cfg.inrepo_config_path(anchor)
    authority.parent.mkdir(parents=True)
    authority.write_text("default_branch: main\n", encoding="utf-8")
    git_environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    git_environment.update({
        "HOME": str(root / "home"), "USERPROFILE": str(root / "home"),
        "GIT_CONFIG_GLOBAL": str(root / "home" / ".gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
    })
    for args in (
        ["init", "-q", "--initial-branch=main"],
        ["add", "--", str(authority)],
        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "fixture"],
        ["remote", "add", "origin", str(anchor)],
    ):
        subprocess.run(
            ["git", "-C", str(anchor), *args],
            check=True, capture_output=True, text=True, timeout=15, env=git_environment,
            **no_window_kwargs(),
        )

    def command(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        for key in ("FIXTURE_COPILOT_PATH", "FIXTURE_COPILOT_ARGS"):
            environment.pop(key, None)
        environment.update(env or {})
        return subprocess.run(
            [sys.executable, str(OWNER), str(root), *args],
            cwd=root, env=environment, capture_output=True, text=True,
            encoding="utf-8", timeout=30, **no_window_kwargs(),
        )

    return root, command


def _json(result: subprocess.CompletedProcess[str]) -> dict:
    if result.returncode:
        pytest.fail(f"owner exit {result.returncode}\n{result.stdout}\n{result.stderr}")
    payload = json.loads(result.stdout)
    assert payload["version"] == 1
    return payload


def _create(command: OwnerCommand, *, env: dict[str, str] | None = None) -> dict:
    return _json(command(
        "--project", "fixture", "create", "--creation-key", "fixture-test",
        "--no-owner", "--json", env=env,
    ))


def test_workspace_owner_fixture_metadata_roundtrip(owner: Owner) -> None:
    root, command = owner
    created = _create(command)
    worktree = created["worktree"]
    wid = worktree["id"]
    assert Path(worktree["path"]).joinpath(".git").is_file()
    assert (root / "state" / "worktrees" / f"{wid}.yaml").is_file()
    assert _create(command)["worktree"]["id"] == wid
    assert _json(command("list", "--json"))["worktrees"][0]["id"] == wid
    for name in ("pane-wrapper.ps1", "pane-launch.ps1", "pane-wrapper.sh"):
        assert (root / "home" / ".agent-worktrees" / "bin" / name).is_file()

    exact = _json(command("record", "--worktree-id", wid, "--json"))
    assert exact["ok"] is True
    assert exact["worktree"]["id"] == wid
    assert exact["worktree"]["repo"] == "fixture"
    assert exact["worktree"]["path"] == worktree["path"]
    assert exact["worktree"]["session_count"] == 0
    assert exact["worktree"]["sessions_indexed"] is True
    assert _json(command("head-session", "--worktree-id", wid, "--json"))["head_session"] is None

    native_id = "fixture-native-conversation"
    bound = _json(command(
        "bind-session", "--worktree-id", wid, "--session-id", native_id,
    ))
    assert bound["bound"] is True
    assert bound["session"] == native_id
    assert bound["is_head"] is True
    head = _json(command("head-session", "--worktree-id", wid, "--json"))
    assert head["head_session"] == native_id
    assert head["tracked"] is True
    indexed = _json(command("list-sessions", "--worktree", wid, "--json"))
    assert indexed["head_session"] == native_id
    assert indexed["head_revision"] > 0
    # Binding alone does not invent a native transcript for the session reader.
    assert indexed["sessions"] == []
    exact = _json(command("record", "--worktree-id", wid, "--json"))
    assert exact["worktree"]["last_session_id"] == native_id
    assert exact["worktree"]["session_count"] == 1
    identity = command("get", "worktree-dir", "--session-id", native_id)
    assert identity.returncode == 0, identity.stderr
    assert Path(identity.stdout.strip()) == Path(worktree["path"])
    assert command("get", "project").stdout.strip() == "fixture"
    assert not (root / "home" / ".copilot" / "session-state").exists()

    membership = root / "state" / "config.yaml"
    assert yaml.safe_load(membership.read_text())["repos"]["fixture"]["anchor"] == str(root / "anchor")
    membership.write_text("repos: {}\n", encoding="utf-8")
    refused = command("record", "--worktree-id", wid, "--json")
    assert refused.returncode == 1
    assert json.loads(refused.stdout)["error"]["code"] == "project-unavailable"


def test_workspace_owner_fixture_isolates_home_and_uses_creation_trust(owner: Owner, tmp_path: Path) -> None:
    root, command = owner
    caller = tmp_path / "caller-home"
    for relative in (".copilot/config.json", ".agent-worktrees/config.yaml", ".agent-worktrees/repos.yaml"):
        path = caller / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("caller state must not be read or changed\n", encoding="utf-8")
    before = {path.relative_to(caller): path.read_bytes() for path in caller.rglob("*") if path.is_file()}
    copilot = root / "home" / ".copilot"
    copilot.mkdir(parents=True)
    config_path = copilot / "config.json"
    config_path.write_text('// fixture settings\n{"theme": "fixture-theme"}\n', encoding="utf-8")
    permission = {"allowedTools": ["fixture-tool"]}
    (copilot / "permissions-config.json").write_text(json.dumps({
        "locations": {str(root / "anchor"): permission},
    }), encoding="utf-8")
    poisoned = {key: str(caller) for key in (
        "HOME", "USERPROFILE", "AGENT_HOME", "COPILOT_HOME", "XDG_CONFIG_HOME",
        "APPDATA", "LOCALAPPDATA",
    )}
    poisoned.update({
        "COPILOT_EXTENSIONS_CONTEXT": "not-a-fixture-cell",
        "COPILOT_PLUGIN_ROOT": str(caller),
        "AGENT_WORKTREES_PAYLOAD_ROOT": str(caller),
        "GIT_CONFIG_GLOBAL": str(caller / ".gitconfig"),
    })
    worktree = _create(command, env=poisoned)["worktree"]
    assert config_path.read_text(encoding="utf-8").startswith("// fixture settings\n")
    config = json.loads(config_path.read_text(encoding="utf-8").split("\n", 1)[1])
    assert config["theme"] == "fixture-theme"
    assert worktree["path"] in config["trustedFolders"]
    locations = json.loads((copilot / "permissions-config.json").read_text())["locations"]
    assert locations[worktree["path"]]["allowedTools"] == ["fixture-tool"]
    assert _json(command("record", "--worktree-id", worktree["id"], env=poisoned))["ok"] is True
    after = {path.relative_to(caller): path.read_bytes() for path in caller.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.parametrize("custom_launch", [False, True])
def test_workspace_owner_fixture_native_resolve_preview(owner: Owner, custom_launch: bool) -> None:
    root, command = owner
    wid = _create(command)["worktree"]["id"]
    native_id = "fixture-native-conversation"
    _json(command("bind-session", "--worktree-id", wid, "--session-id", native_id))
    env = {}
    if custom_launch:
        env = {
            "FIXTURE_COPILOT_PATH": str(root / "not-executable-copilot"),
            "FIXTURE_COPILOT_ARGS": json.dumps(["--fixture-option", "value with spaces"]),
        }
    result = command(
        "resolve", "--worktree-id", wid, "--native-resume-session", native_id,
        "--no-resume", "--no-mux", "--dry-run", "--json", env=env,
    )
    payload = _json(result)
    assert payload["worktree"]["id"] == wid
    plan = payload["launch"]
    assert plan["action"] == "exec"
    assert plan["preview"] is True
    assert plan["no_mux"] is True
    argv = plan["cmd"]
    assert "agent_worktrees.launch_seed_exec" in argv
    assert argv[argv.index("--native-resume-session") + 1] == native_id
    assert "--admission-only" in argv
    assert "--resume=" + native_id not in argv
    for key in ("HOME", "USERPROFILE", "AGENT_HOME"):
        assert plan["env"][key] == str(root / "home")
    assert plan["env"]["COPILOT_HOME"] == str(root / "home" / ".copilot")
    if custom_launch:
        assert env["FIXTURE_COPILOT_PATH"] in argv
        wrapper = next(
            Path(part) for part in argv
            if Path(part).name in {"launch-command.ps1", "launch-command.sh"}
        )
        assert wrapper.is_file()
        source = OWNER.parents[1] / "scripts" / wrapper.name
        assert wrapper.read_bytes() == source.read_bytes()
        option = argv.index("--fixture-option")
        assert argv[option + 1] == "value with spaces"
    else:
        assert "--fixture-option" not in argv
        assert "--allow-all" in argv
    assert not (root / "home" / ".copilot" / "session-state").exists()
    refusal = command(
        "resolve", "--worktree-id", wid, "--native-resume-session", native_id,
        "--dry-run", "--json", env=env,
    )
    assert refusal.returncode == 3
    assert json.loads(refusal.stdout)["error"] == (
        "Native resume requires an existing unseeded --worktree-id and --no-resume"
    )


def test_workspace_owner_fixture_embody_preview_and_foreign_project_refusal(owner: Owner) -> None:
    root, command = owner
    wid = _create(command)["worktree"]["id"]
    preview = _json(command("embody", "--worktree-id", wid, "--dry-run", "--json"))
    assert preview["worktree_id"] == wid
    assert preview["dry_run"] is True
    assert not (root / "home" / ".copilot" / "session-state").exists()
    refused = command("--project", "foreign", "get", "project")
    assert refused.returncode != 0
    assert "refuses a foreign project" in refused.stderr


def test_workspace_owner_fixture_launch_adapter_preserves_backend_stdio(owner: Owner) -> None:
    root, command = owner
    worktree = _create(command)["worktree"]
    native_id = "fixture-native-conversation"
    _json(command("bind-session", "--worktree-id", worktree["id"], "--session-id", native_id))
    backend = root / "backend.py"
    backend.write_text(
        "import sys\n"
        "assert '--acp' in sys.argv and '--stdio' in sys.argv\n"
        "print(sys.stdin.readline().strip(), flush=True)\n",
        encoding="utf-8",
    )
    payload = _json(command(
        "resolve", "--worktree-id", worktree["id"], "--native-resume-session", native_id,
        "--no-resume", "--no-mux", "--json",
        env={
            "FIXTURE_COPILOT_PATH": sys.executable,
            "FIXTURE_COPILOT_ARGS": json.dumps([str(backend)]),
        },
    ))
    plan = payload["launch"]
    environment = {**os.environ, **plan["env"]}
    adapter = plan["cmd"][plan["cmd"].index("--") + 1:]
    result = subprocess.run(
        [*adapter, "--acp", "--stdio"], cwd=plan["work_dir"], env=environment,
        input='{"jsonrpc":"2.0","id":1}\n', capture_output=True, text=True,
        encoding="utf-8", timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '{"jsonrpc":"2.0","id":1}'
    assert not (root / "home" / ".copilot" / "session-state").exists()


@pytest.mark.parametrize("arguments", ['{"not": "an array"}', '["ok", 7]', "not-json"])
def test_workspace_owner_fixture_rejects_invalid_launch_arguments(owner: Owner, arguments: str) -> None:
    _, command = owner
    result = command("get", "project", env={"FIXTURE_COPILOT_ARGS": arguments})
    assert result.returncode != 0
    assert "must be a JSON string array" in result.stderr
