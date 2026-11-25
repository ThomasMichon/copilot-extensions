"""Fresh Windows daemon context, preserving runtime and containment boundaries."""

import os
import sys
import time
import json
import shutil
from pathlib import Path
from types import FunctionType, SimpleNamespace
from collections.abc import Mapping

import pytest

import agent_procutil
from plugin_resolve import environment
from plugin_resolve import git as git_discovery
from agent_worktrees import status_monitor_runtime as runtime, status_updater_cli


@pytest.mark.parametrize("name", ["psmux", "tmux"])
def test_fresh_daemon_preserves_launcher_only_mux(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str,
) -> None:
    from agent_worktrees.daemon_launch_context import monitor_mux

    binary = tmp_path / "portable" / (name + (".exe" if os.name == "nt" else ""))
    binary.parent.mkdir()
    binary.touch()
    binary.chmod(0o755)
    inherited = {"PATH": str(binary.parent)}
    monkeypatch.setattr(runtime, "_core_helper", lambda key, local: local)
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="nt", path=os.path))
    monkeypatch.setattr(agent_procutil, "contained_test_mode", lambda: False)
    monkeypatch.setattr(git_discovery, "resolve_git", lambda **kw: str(tmp_path / "git.exe"))
    monkeypatch.setattr(status_updater_cli, "_background_environment", lambda: inherited.copy())
    monkeypatch.setattr(environment, "fresh_user_environment",
                        lambda *, overrides: {"PATH": str(tmp_path / "fresh"), **overrides})
    result = runtime._daemon_environment()
    assert result["PATH"] != inherited["PATH"]
    monkeypatch.chdir(tmp_path.parent)
    selected, path = monitor_mux(result)
    assert selected == name
    assert path is not None and Path(path) == binary
    assert inherited == {"PATH": str(binary.parent)}


@pytest.mark.parametrize("error", [FileNotFoundError("explicit"), OSError("environment read")])
def test_environment_failure_refuses_monitor_spawn_without_child(
    monkeypatch: pytest.MonkeyPatch, error: Exception,
) -> None:
    from agent_worktrees import runtime_monitor_launch, status_monitor_spawn

    def unavailable() -> dict[str, str]:
        raise error

    monkeypatch.setattr(runtime, "_core_helper", lambda key, local: local)
    monkeypatch.setattr(runtime, "_daemon_environment", unavailable)
    monkeypatch.setattr(runtime_monitor_launch, "retained_monitor_argv",
                        lambda argv: pytest.fail("No child preparation on environment refusal"))
    assert status_monitor_spawn._spawn_detached(["python", "-m", "agent_worktrees"]) is False


def test_fresh_os_context_preserves_only_runtime_overrides_and_scrubs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(runtime, "_core_helper", lambda name, local: local)
    inherited = {
        "PATH": "stale", "AGENT_HOME": str(tmp_path), "COPILOT_EXTENSIONS_CONTEXT": "cell",
        "COPILOT_AGENT_SESSION_ID": "old-session", "GH_TOKEN": "old-secret",
        "AGENT_WORKTREES_AHP_AUTH_TOKEN": "old-secret", "UNDECLARED": "stale",
        "WORKTREE_MANAGER_ROOT": str(tmp_path / "selected-manager"),
        "GH_CONFIG_DIR": str(tmp_path / "selected-gh"), "GH_HOST": "github.example.invalid",
    }
    calls: list[dict[str, str]] = []
    monkeypatch.setattr(agent_procutil, "contained_test_mode", lambda: False)
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="nt", path=os.path))
    monkeypatch.setattr(git_discovery, "resolve_git", lambda **kw: str(tmp_path / "git.exe"))
    monkeypatch.setattr(status_updater_cli, "_background_environment", lambda: inherited.copy())

    def fresh(*, overrides: Mapping[str, str]) -> dict[str, str]:
        calls.append(dict(overrides))
        return {"PATH": "fresh", "USERPROFILE": "os-user", "GITHUB_TOKEN": "os-secret", **overrides}

    monkeypatch.setattr(environment, "fresh_user_environment", fresh)
    result = runtime._daemon_environment()
    assert result["PATH"] == "fresh"
    assert result["COPILOT_EXTENSIONS_CONTEXT"] == "cell"
    assert result["WORKTREE_MANAGER_ROOT"] == str(tmp_path / "selected-manager")
    assert result["GH_CONFIG_DIR"] == str(tmp_path / "selected-gh")
    assert result["GH_HOST"] == "github.example.invalid"
    assert "UNDECLARED" not in result
    assert not any(key in result for key in ("GH_TOKEN", "GITHUB_TOKEN", "COPILOT_AGENT_SESSION_ID",
                                             "AGENT_WORKTREES_AHP_AUTH_TOKEN"))
    assert "PATH" not in calls[0]
    assert inherited["PATH"] == "stale"


@pytest.mark.parametrize("explicit", [False, True])
def test_fresh_daemon_carries_process_path_only_portable_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, explicit: bool,
) -> None:
    portable = tmp_path / "portable" / ("git.exe" if os.name == "nt" else "git")
    portable.parent.mkdir()
    portable.touch()
    portable.chmod(0o755)
    inherited = {"PATH": str(portable.parent)}
    if explicit:
        inherited["AGENT_GIT_EXECUTABLE"] = portable.name
    monkeypatch.setattr(runtime, "_core_helper", lambda name, local: local)
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="nt", path=os.path))
    monkeypatch.setattr(agent_procutil, "contained_test_mode", lambda: False)
    monkeypatch.setattr(status_updater_cli, "_background_environment", lambda: inherited.copy())
    monkeypatch.setattr(environment, "fresh_user_environment",
                        lambda *, overrides: {"PATH": "fresh-system-only", **overrides})
    result = runtime._daemon_environment()
    assert result["PATH"] == "fresh-system-only"
    assert result[
        "AGENT_GIT_EXECUTABLE" if explicit else "AGENT_GIT_DISCOVERED_EXECUTABLE"
    ] == str(portable)


def test_fresh_daemon_does_not_replace_an_invalid_explicit_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "_core_helper", lambda name, local: local)
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="nt", path=os.path))
    monkeypatch.setattr(agent_procutil, "contained_test_mode", lambda: False)
    monkeypatch.setattr(status_updater_cli, "_background_environment",
                        lambda: {"AGENT_GIT_EXECUTABLE": "missing-explicit", "PATH": "old"})
    monkeypatch.setattr(git_discovery, "resolve_git",
                        lambda **kw: (_ for _ in ()).throw(FileNotFoundError("explicit")))
    monkeypatch.setattr(environment, "fresh_user_environment",
                        lambda **kw: pytest.fail("invalid explicit selection must not fall back"))
    with pytest.raises(FileNotFoundError, match="explicit"):
        runtime._daemon_environment()


def test_fresh_daemon_retains_only_selected_gh_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from agent_worktrees.daemon_launch_context import build

    portable = tmp_path / "portable" / ("gh.exe" if os.name == "nt" else "gh")
    portable.parent.mkdir()
    portable.touch()
    portable.chmod(0o755)
    inherited = {"PATH": os.pathsep.join(("portable", "unrelated-stale-bin"))}
    monkeypatch.chdir(tmp_path)
    result = build(
        inherited, refresh=True,
        fresh=lambda **kw: {"Path": "fresh-system-only", **kw["overrides"]},
        git=lambda **kw: str(tmp_path / "git"),
        auth_excluded=(), scoped=(), paths=(),
    )
    monkeypatch.chdir(tmp_path.parent)
    assert result["Path"].split(os.pathsep) == [str(portable.parent), "fresh-system-only"]
    selected = shutil.which("gh", path=result["Path"])
    assert selected is not None and Path(selected) == portable
    assert inherited["PATH"] == os.pathsep.join(("portable", "unrelated-stale-bin"))


def test_contained_daemon_never_replaces_sandbox_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(runtime, "_core_helper", lambda name, local: local)
    inherited = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "PATH": "fixture"}
    monkeypatch.setattr(agent_procutil, "contained_test_mode", lambda: True)
    monkeypatch.setattr(status_updater_cli, "_background_environment", lambda: inherited.copy())
    monkeypatch.setattr(environment, "fresh_user_environment",
                        lambda **kw: pytest.fail("must not escape the isolated test environment"))
    assert runtime._daemon_environment() == inherited


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("report_key", ["AGENT_CONFIGURATION_CONTEXT", "Agent_Configuration_Context"])
def test_monitor_drops_operation_report_but_keeps_installation_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, refresh: bool, report_key: str,
) -> None:
    inherited = {
        "HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "PATH": "fixture",
        report_key: '{"version":1,"state_root":{"status":"unbound"}}',
        "COPILOT_EXTENSIONS_CONTEXT": "selected-cell",
    }
    monkeypatch.setattr(runtime, "_core_helper", lambda name, local: local)
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="nt", path=os.path))
    monkeypatch.setattr(agent_procutil, "contained_test_mode", lambda: not refresh)
    monkeypatch.setattr(status_updater_cli, "_background_environment", lambda: inherited.copy())
    monkeypatch.setattr(git_discovery, "resolve_git", lambda **kw: str(tmp_path / "git.exe"))
    calls = []

    def fresh(*, overrides: Mapping[str, str]) -> dict[str, str]:
        calls.append(dict(overrides))
        return {**inherited, **overrides, "AGENT_CONFIGURATION_CONTEXT": "os-report"}

    monkeypatch.setattr(environment, "fresh_user_environment", fresh)
    result = runtime._daemon_environment()
    assert not any(key.upper() == "AGENT_CONFIGURATION_CONTEXT" for key in result)
    assert result["COPILOT_EXTENSIONS_CONTEXT"] == "selected-cell"
    assert report_key in inherited
    assert len(calls) == int(refresh)


@pytest.mark.parametrize("selection", ["portable/git.exe", "git.exe"])
def test_relative_git_selection_is_anchored_before_child_cwd_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, selection: str,
) -> None:
    monkeypatch.setattr(runtime, "_core_helper", lambda name, local: local)
    monkeypatch.setattr(agent_procutil, "contained_test_mode", lambda: True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(status_updater_cli, "_background_environment",
                        lambda: {"AGENT_GIT_EXECUTABLE": selection})
    result = runtime._daemon_environment()["AGENT_GIT_EXECUTABLE"]
    monkeypatch.chdir(tmp_path.parent)
    assert result == (str(tmp_path / selection) if "/" in selection else selection)


@pytest.mark.skipif(os.name != "nt", reason="native Windows OS user environment")
def test_native_fresh_environment_real_leased_daemon_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil.daemon_instances import DaemonInstanceSpec
    source = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(source))
    from tools.plugin_test_environment import isolated_environment

    original = dict(os.environ)
    base = environment.fresh_user_environment()
    base.update({key: value for key, value in original.items()
                 if key.startswith("COPILOT_EXTENSIONS_")})
    fresh = isolated_environment(base, tmp_path / "state", allow_explicit_tiers=True)
    for key in tuple(fresh):
        if key.upper() in {"AGENT_GIT_EXECUTABLE", "AGENT_GIT_DISCOVERED_EXECUTABLE"}:
            del fresh[key]
    fresh.update({
        "GH_TOKEN": "fixture-os-secret",
        "GITHUB_TOKEN": "fixture-os-secret",
        "COPILOT_EXTENSIONS_DAEMON_REGISTRY": str(tmp_path / "instances"),
    })
    gh = shutil.which("gh")
    assert gh is not None, "Native account-selector qualification requires the GitHub CLI"
    portable = tmp_path / "portable-gh"
    portable.mkdir()
    shutil.copy2(gh, portable / Path(gh).name)
    inherited = {key: value for key, value in fresh.items() if key.upper() != "PATH"}
    inherited.update({
        "PATH": os.pathsep.join((str(portable), str(tmp_path / "stale-bin"))),
        "WORKTREE_MANAGER_ROOT": str(tmp_path / "selected-manager"),
        "COPILOT_AGENT_SESSION_ID": "fixture-session",
        "AGENT_CONFIGURATION_CONTEXT": '{"version":1,"state_root":{"status":"unbound"}}',
        "AGENT_WORKTREES_AHP_AUTH_TOKEN": "fixture-secret",
        "GH_CONFIG_DIR": str(tmp_path / "selected-gh"),
        "GH_HOST": "github.example.invalid",
    })
    selected_gh = Path(inherited["GH_CONFIG_DIR"])
    selected_gh.mkdir()
    (selected_gh / "hosts.yml").write_text(
        "github.example.invalid:\n"
        "    user: fixture\n"
        "    users:\n"
        "        fixture:\n"
        "            oauth_token: synthetic-fixture-token\n",
        encoding="utf-8",
    )
    provider = runtime._core_helper(
        "_background_environment", status_updater_cli._background_environment,
    )
    assert isinstance(provider, FunctionType)
    with monkeypatch.context() as patch:
        patch.setitem(provider.__globals__, "os", SimpleNamespace(environ=inherited, path=os.path))
        patch.setattr(environment, "_windows_environment", lambda: dict(fresh))
        patch.setattr(agent_procutil, "contained_test_mode", lambda: False)
        env = runtime._daemon_environment()
        assert dict(os.environ) == original
    # Native containment is restored before the real leased child is launched.
    assert agent_procutil.contained_test_mode()
    result = tmp_path / "result.json"
    code = (
        "import json,os,subprocess,sys;"
        "from plugin_resolve.git import resolve_git;"
        "selected=resolve_git();"
        "r=subprocess.run([selected,'--version'],capture_output=True,text=True);"
        "excluded=not any(k in os.environ for k in "
        "['GH_TOKEN','GITHUB_TOKEN','COPILOT_AGENT_SESSION_ID','AGENT_WORKTREES_AHP_AUTH_TOKEN',"
        "'AGENT_CONFIGURATION_CONTEXT']);"
        "open(sys.argv[1],'w').write(json.dumps({'exit':r.returncode,'version':r.stdout,"
        "'git':selected,'home':os.environ.get('AGENT_HOME'),"
        "'manager':os.environ.get('WORKTREE_MANAGER_ROOT'),"
        "'path':os.environ.get('PATH'),'excluded':excluded,"
        "'gh_config':os.environ.get('GH_CONFIG_DIR'),'gh_host':os.environ.get('GH_HOST')}));"
        "from agent_worktrees.git_ops import gh_token_for_account;"
        "assert gh_token_for_account('fixture')=='synthetic-fixture-token';"
        "a=subprocess.run([selected,'-c','credential.helper=',"
        "'-c','credential.helper=!gh auth git-credential','credential','fill'],"
        "input='protocol=https\\nhost=github.example.invalid\\nusername=fixture\\n\\n',"
        "capture_output=True,text=True);"
        "assert a.returncode==0 and 'password=synthetic-fixture-token' in a.stdout;"
    )
    child = agent_procutil.spawn_daemon(
        [sys.executable, "-c", code, str(result)], env=env,
        cwd=str(tmp_path), log_path=tmp_path / "fixture.log",
        instance=DaemonInstanceSpec("git-environment-fixture", tmp_path, Path(__file__).resolve()),
    )
    try:
        deadline = time.monotonic() + 15
        while not result.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert result.exists()
        child.wait(timeout=10)
        assert child.returncode == 0
        observed = json.loads(result.read_text())
        assert observed["exit"] == 0
        assert observed["version"].startswith("git version ")
        assert Path(observed["git"]).is_absolute()
        assert Path(observed["home"]).resolve().is_relative_to(tmp_path.resolve())
        assert observed["manager"] == str(tmp_path / "selected-manager")
        assert observed["excluded"] is True
        assert observed["gh_config"] == str(selected_gh)
        assert observed["gh_host"] == "github.example.invalid"
        assert str(tmp_path / "stale-bin") not in observed["path"].split(os.pathsep)
        assert dict(os.environ) == original
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


@pytest.mark.parametrize("value", [[], "invalid", 1, None])
def test_non_mapping_handoff_state_is_unavailable(tmp_path: Path, value: object) -> None:
    path = tmp_path / "handoff.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert runtime._monitor_read_session_state_handoff(path) is None
