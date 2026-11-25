"""Actor-free controls for the extracted process and launch-context owners."""

from __future__ import annotations

from collections.abc import Mapping
import importlib.util
import importlib
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from typing import NoReturn, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins/agent-worktrees/src/agent_worktrees"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location("unit_" + name, SOURCE / (name + ".py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PROCESS = load("git_process")
CONTEXT = load("daemon_launch_context")
PACKAGE = ModuleType("unit_git_lookup")
PACKAGE.__path__ = [str(ROOT / "libs/plugin-resolve/src/plugin_resolve")]
sys.modules[PACKAGE.__name__] = PACKAGE
LOOKUP = importlib.import_module(PACKAGE.__name__ + ".git")


@pytest.mark.parametrize("kind", ["text", "binary", "bounded-text", "bounded-binary", "observation"])
def test_process_composition_preserves_owner_and_output(kind: str) -> None:
    binary = "binary" in kind
    bounded = kind.startswith("bounded") or kind == "observation"
    calls: list[tuple[str, dict[str, object]]] = []

    def run(label: str, values: dict[str, object]) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
        calls.append((label, values))
        if binary:
            return subprocess.CompletedProcess(["git"], 0, b"output", b"")
        return subprocess.CompletedProcess(["git"], 0, "output", "")

    def plain(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
        return run("plain", kwargs)

    def limited(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
        return run("bounded", kwargs)

    def windows() -> Mapping[str, object]:
        assert not bounded
        return {}

    result = PROCESS.execute(
        ["git", "status"], cwd=None, env={}, capture=True, timeout=5 if bounded else None,
        kill_tree=bounded, input_data=b"input" if binary else None,
        observation_deadline=20 if kind == "observation" else None,
        run=plain, bounded_run=limited, window_options=windows,
    )
    assert result.stdout == (b"output" if binary else "output")
    assert len(calls) == 1
    label, values = calls[0]
    assert label == ("bounded" if bounded else "plain")
    assert values.get("input") == (b"input" if binary else None)
    if kind == "observation":
        assert values["observation_deadline"] == 20
    if not binary and not bounded:
        assert values["text"] is True
        assert values["encoding"] == "utf-8"


@pytest.mark.parametrize("branch", ["topic", "HEAD", "failed"])
def test_branch_probe_retains_text_and_failure(branch: str) -> None:
    def run(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args == ("rev-parse", "--abbrev-ref", "HEAD")
        assert kwargs["timeout"] == 3
        return subprocess.CompletedProcess(list(args), 1 if branch == "failed" else 0, branch, "")

    assert PROCESS.current_branch(
        ".", timeout=3, kill_tree=True, observation_deadline=10, run=run,
    ) == ("topic" if branch == "topic" else None)


@pytest.mark.parametrize("kind", ["automatic", "explicit", "invalid", "contained"])
def test_environment_composition_preserves_selection_and_exclusions(tmp_path: Path, kind: str) -> None:
    inherited = {
        "PATH": "stale", "AGENT_HOME": "relative-home",
        "Agent_Configuration_Context": "caller-operation-report",
        "COPILOT_EXTENSIONS_CONTEXT": "selected-cell",
        "WORKTREE_MANAGER_ROOT": str(tmp_path / "manager"),
        "GH_TOKEN": "input-secret", "COPILOT_AGENT_SESSION_ID": "session",
        "GH_CONFIG_DIR": "selected-gh", "GH_HOST": "github.example.invalid",
    }
    if kind != "automatic":
        inherited["AGENT_GIT_EXECUTABLE"] = "portable/git.exe"
    original = dict(inherited)
    events: list[str] = []

    def git(*, env: Mapping[str, str]) -> str:
        events.append("git")
        assert env["PATH"] == "stale"
        if kind == "invalid":
            raise FileNotFoundError("explicit")
        return str(tmp_path / "git.exe")

    def fresh(*, overrides: Mapping[str, str]) -> dict[str, str]:
        events.append("fresh")
        assert "PATH" not in overrides
        return {
            "PATH": "fresh", "GITHUB_TOKEN": "os-secret", **overrides,
            "AGENT_CONFIGURATION_CONTEXT": "os-operation-report",
        }

    def compose() -> dict[str, str]:
        return cast(dict[str, str], CONTEXT.build(
            inherited, refresh=kind != "contained", fresh=fresh, git=git,
            auth_excluded={"GH_TOKEN", "GITHUB_TOKEN"},
            scoped=("COPILOT_AGENT_SESSION_ID",),
            paths=("AGENT_HOME", "WORKTREE_MANAGER_ROOT", "GH_CONFIG_DIR"),
        ))

    if kind == "invalid":
        with pytest.raises(FileNotFoundError):
            compose()
        assert events == ["git"]
    else:
        result = compose()
        assert result["PATH"] == ("stale" if kind == "contained" else "fresh")
        assert not {"GH_TOKEN", "GITHUB_TOKEN", "COPILOT_AGENT_SESSION_ID"} & result.keys()
        assert not any(key.upper() == "AGENT_CONFIGURATION_CONTEXT" for key in result)
        assert result["COPILOT_EXTENSIONS_CONTEXT"] == "selected-cell"
        assert result["AGENT_HOME"] == os.path.abspath("relative-home")
        assert result["GH_CONFIG_DIR"] == os.path.abspath("selected-gh")
        assert result["GH_HOST"] == "github.example.invalid"
        if kind == "contained":
            assert events == []
            assert result["AGENT_GIT_EXECUTABLE"] == os.path.abspath("portable/git.exe")
        else:
            assert events == ["git", "fresh"]
            key = "AGENT_GIT_DISCOVERED_EXECUTABLE" if kind == "automatic" else "AGENT_GIT_EXECUTABLE"
            assert result[key] == str(tmp_path / "git.exe")
    assert inherited == original


def test_launch_directory_and_interpreter_keep_selected_owners(tmp_path: Path) -> None:
    assert CONTEXT.cwd(lambda: tmp_path) == str(tmp_path)

    def missing() -> Path:
        raise ValueError("unavailable")

    assert CONTEXT.cwd(missing) == os.path.expanduser("~")
    assert CONTEXT.windowless("python", lambda selected: selected + "w") == "pythonw"


def test_fresh_path_retains_portable_gh_without_unrelated_launcher_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "portable" / ("gh.exe" if os.name == "nt" else "gh")
    binary.parent.mkdir()
    binary.touch()
    binary.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    inherited = {"Path": os.pathsep.join(("portable", "stale-other"))}

    def fresh(*, overrides: Mapping[str, str]) -> dict[str, str]:
        return {"PATH": "fresh", **overrides}

    result = CONTEXT.build(
        inherited, refresh=True, fresh=fresh, git=lambda **kw: str(tmp_path / "git"),
        auth_excluded=(), scoped=(), paths=(),
    )
    assert result["PATH"].split(os.pathsep) == [str(binary.parent), "fresh"]
    assert inherited["Path"] == os.pathsep.join(("portable", "stale-other"))


@pytest.mark.parametrize("name", ["psmux", "tmux"])
def test_mux_hint_survives_fresh_path(tmp_path: Path, name: str) -> None:
    binary = tmp_path / (name + (".exe" if os.name == "nt" else ""))
    binary.touch()
    assert CONTEXT.monitor_mux({
        "PATH": str(tmp_path / "fresh"),
        "AGENT_WORKTREES_MONITOR_MUX": str(binary),
    }) == (name, str(binary))


@pytest.mark.parametrize("entry", ["bin", ""])
def test_automatic_git_path_uses_posix_child_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str,
) -> None:
    caller, child = tmp_path / "caller", tmp_path / "child"
    caller.mkdir()
    child.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setattr(LOOKUP, "_windows", lambda: False)
    observed: list[str] = []

    def which(name: str, path: str) -> str:
        assert name == "git"
        observed.append(path)
        return str(child / entry / "git")

    monkeypatch.setattr(LOOKUP, "_which", which)
    assert LOOKUP.resolve_git(env={"PATH": entry}, cwd=child) == str(child / entry / "git")
    assert observed == [str(child / entry)]
    assert Path.cwd() == caller


def test_automatic_empty_path_uses_launcher_when_child_cwd_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(LOOKUP, "_windows", lambda: False)
    observed: list[str] = []

    def which(name: str, path: str) -> str:
        assert name == "git"
        observed.append(path)
        return str(tmp_path / "git")

    monkeypatch.setattr(LOOKUP, "_which", which)
    assert LOOKUP.resolve_git(env={"PATH": ""}) == str(tmp_path / "git")
    assert observed == [str(tmp_path)]


@pytest.mark.parametrize("kind", [
    "path", "explicit", "daemon-relative", "daemon-absolute", "daemon-home", "daemon-cwd", "mux",
])
def test_posix_symlink_parent_segments_reach_the_physical_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    caller, nested = tmp_path / "caller", tmp_path / "physical" / "nested"
    caller.mkdir()
    nested.mkdir(parents=True)
    name = "psmux" if kind == "mux" else "git"
    desired, wrong = nested.parent / "tools" / name, caller / "tools" / name
    for binary in (desired, wrong):
        binary.parent.mkdir()
        binary.touch()
        binary.chmod(0o755)
    monkeypatch.chdir(caller)
    monkeypatch.setattr(LOOKUP, "_windows", lambda: False)
    if os.name == "nt":
        def which(name: str, path: str) -> str:
            return name if os.path.dirname(name) else os.path.join(path, name)

        monkeypatch.setattr(LOOKUP.shutil, "which", which)
        monkeypatch.setattr(LOOKUP, "_usable", lambda path: True)
        monkeypatch.setattr(CONTEXT, "os", SimpleNamespace(
            name="posix", path=os.path, getcwd=os.getcwd, fspath=os.fspath,
            defpath=os.defpath, environ=os.environ,
        ))
    else:
        (caller / "link").symlink_to(nested, target_is_directory=True)
    entry = str(Path("link") / ".." / "tools")
    expected = caller / entry / name
    target = desired
    if kind.startswith("daemon-") and kind != "daemon-cwd":
        home = kind == "daemon-home"
        key = "AGENT_HOME" if home else "AGENT_GIT_EXECUTABLE"
        value = entry if home else str(Path(entry) / name)
        if kind == "daemon-absolute":
            value = str(caller / value)
        result = CONTEXT.build(
            {key: value}, refresh=False, fresh=lambda **kw: pytest.fail("No refresh"),
            git=lambda **kw: pytest.fail("No discovery"),
            auth_excluded=(), scoped=(), paths=("AGENT_HOME",),
        )[key]
        if home:
            expected, target = expected.parent, desired.parent
    elif kind == "daemon-cwd":
        root = caller / entry
        result = CONTEXT._anchor(root) if os.name == "nt" else CONTEXT.cwd(lambda: root)
        expected, target = root, desired.parent
    elif kind == "mux":
        mux, result = CONTEXT.monitor_mux({"PATH": entry})
        assert mux == "psmux"
    else:
        selected = str(Path(entry) / name) if kind == "explicit" else None
        result = LOOKUP.resolve_git(selected, env={"PATH": entry})
    assert result == str(expected)
    if os.name != "nt":
        assert Path(result).samefile(target)
        assert not Path(result).samefile(wrong.parent if target == desired.parent else wrong)


@pytest.mark.parametrize("kind", ["cache", "hint", "explicit"])
def test_parent_replaced_by_file_obeys_git_selection_policy(
    tmp_path: Path, kind: str,
) -> None:
    name = "git.exe" if os.name == "nt" else "git"
    first, second = tmp_path / "old" / name, tmp_path / "new" / name
    for binary in (first, second):
        binary.parent.mkdir()
        binary.touch()
        binary.chmod(0o755)
    env = {"PATH": os.pathsep.join((str(first.parent), str(second.parent)))}
    if kind == "hint":
        env["AGENT_GIT_DISCOVERED_EXECUTABLE"] = str(first)
    if kind == "explicit":
        env["AGENT_GIT_EXECUTABLE"] = str(first)
    assert Path(LOOKUP.resolve_git(env=env)) == first
    first.unlink()
    first.parent.rmdir()
    first.parent.write_text("not a directory", encoding="utf-8")
    if kind == "hint":
        LOOKUP._cache.clear()
    if kind == "explicit":
        with pytest.raises(FileNotFoundError, match="Explicit"):
            LOOKUP.resolve_git(env=env)
    else:
        assert Path(LOOKUP.resolve_git(env=env)) == second


@pytest.mark.parametrize("kind", ["missing-parent", "permission"])
def test_candidate_stat_distinguishes_absence_from_denial(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    error = NotADirectoryError("parent") if kind == "missing-parent" else PermissionError("denied")

    def stat(path: str) -> object:
        raise error

    monkeypatch.setattr(LOOKUP, "os", SimpleNamespace(stat=stat))
    if kind == "missing-parent":
        assert LOOKUP._usable("candidate") is False
    else:
        with pytest.raises(PermissionError):
            LOOKUP._usable("candidate")


def test_soft_capture_uses_the_existing_owned_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def owned(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(["git"], 0, "output", "")

    package = ModuleType("agent_procutil")
    package.__path__ = []
    provider = ModuleType("agent_procutil.owned_process")
    setattr(provider, "run_owned", owned)
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, provider.__name__, provider)

    def unexpected(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        pytest.fail("soft capture selected a different process owner")

    result = PROCESS.execute(
        ["git", "fetch"], cwd=None, env={}, capture=True, timeout=5,
        kill_tree=False, input_data=None, observation_deadline=None,
        run=unexpected, bounded_run=unexpected, window_options=lambda: {},
        soft_timeout=1,
    )
    assert result.stdout == "output"
    assert calls == [{"cwd": None, "env": {}, "timeout": 5, "soft_timeout": 1, "errors": "replace"}]


def test_foreground_fetch_keeps_caller_custody_without_a_completion_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def owned(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b"ready", b"non-utf8: \xff")

    package = ModuleType("agent_procutil")
    package.__path__ = []
    provider = ModuleType("agent_procutil.owned_process")
    setattr(provider, "run_caller_owned", owned)
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, provider.__name__, provider)

    def unexpected(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("foreground fetch lost its caller-owned process boundary")

    result = PROCESS.execute(
        ["git", "fetch"], cwd="selected", env={"PATH": "selected"},
        capture=True, timeout=None, kill_tree=True, input_data=None,
        observation_deadline=None, run=unexpected, bounded_run=unexpected,
        window_options=unexpected, soft_timeout=None,
    )
    assert result.stdout == "ready" and result.stderr == "non-utf8: \ufffd"
    assert len(calls) == 1
    argv, options = calls[0]
    assert argv == ["git", "fetch"] and options["timeout"] is None
    assert options["cwd"] == "selected" and options["env"] == {"PATH": "selected"}
    assert options["binary"] is True
    stdout_limit, stderr_limit = options["max_stdout_bytes"], options["max_stderr_bytes"]
    assert isinstance(stdout_limit, int) and stdout_limit > 0
    assert isinstance(stderr_limit, int) and stderr_limit > 0


@pytest.mark.parametrize("kind", ["input", "uncaptured"])
def test_soft_capture_refuses_incompatible_modes_before_launch(kind: str) -> None:
    def unexpected(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        pytest.fail("invalid soft capture reached process creation")

    with pytest.raises(ValueError, match="captured text output"):
        PROCESS.execute(
            ["git", "fetch"], cwd=None, env={}, capture=kind != "uncaptured", timeout=5,
            kill_tree=False, input_data=b"input" if kind == "input" else None,
            observation_deadline=None, run=unexpected, bounded_run=unexpected,
            window_options=lambda: {}, soft_timeout=1,
        )
